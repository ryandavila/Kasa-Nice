"""Server-Sent Events stream of live device state.

The browser opens one ``EventSource`` to ``/api/events`` and the server pushes
the serialized device list whenever hardware is re-read, so changes made
elsewhere (the Kasa app, a physical switch) surface without polling.

A single shared :class:`_Broadcaster` drives ONE ``refresh_all`` loop for the
whole process and fans each frame out to per-connection queues, so hardware is
read once per interval no matter how many clients attach. The loop runs only
while at least one client is subscribed, so an idle server never polls.
"""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .kasa_service import registry, serialize_device
from .logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api")

# Seconds between server-side state re-reads pushed to clients.
_STREAM_INTERVAL = 5.0

# Sent instead of a data frame when nothing changed (or a refresh failed): keeps
# the connection and any proxy alive without re-sending an identical device list.
_KEEPALIVE = ": keepalive\n\n"


def _frame(devices: list) -> str:
    """Encode a device list as one SSE ``data:`` frame.

    Known-but-unreachable devices are appended (``reachable=False``) so the frame
    stays a flat JSON array the client already merges, just extended.
    """
    items = [serialize_device(d).model_dump() for d in devices]
    items += [d.model_dump() for d in registry.unreachable_devices()]
    payload = json.dumps(items)
    return f"data: {payload}\n\n"


class _Broadcaster:
    """One shared refresh loop that fans live state out to every subscriber.

    Connections :meth:`subscribe` for their own queue and await it; the single
    background loop does all the reads and pushes each frame to every queue. The
    loop is lazily started on the first subscriber and cancelled when the last
    leaves.
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._task: asyncio.Task | None = None
        # Last data frame fanned out; an unchanged frame is replaced by a
        # keepalive so identical device lists aren't re-sent.
        self._last_frame: str | None = None

    def subscribe(self) -> asyncio.Queue[str]:
        """Register a connection, starting the shared loop if it's the first."""
        queue: asyncio.Queue[str] = asyncio.Queue()
        self._subscribers.add(queue)
        if self._task is None:
            self._task = asyncio.create_task(self._run())
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        """Drop a connection, stopping the shared loop when the last one leaves."""
        self._subscribers.discard(queue)
        if not self._subscribers and self._task is not None:
            self._task.cancel()
            self._task = None
            # Fresh session starts clean, not suppressed against a departed one.
            self._last_frame = None

    def _fanout(self, item: str) -> None:
        # Unbounded queues never raise; each frame is full state, so a briefly
        # slow client just catches up back-to-back.
        for queue in self._subscribers:
            queue.put_nowait(item)

    def _publish(self, devices: list) -> None:
        """Fan out a data frame, or a keepalive when it matches the last one."""
        frame = _frame(devices)
        if frame == self._last_frame:
            self._fanout(_KEEPALIVE)
        else:
            self._last_frame = frame
            self._fanout(frame)

    async def publish_now(self) -> None:
        """Immediately push current cached state to all subscribers.

        Called after a control action so other clients update without waiting a
        tick. The handler already refreshed the affected device, so this
        serializes ``registry.all()`` without another read. No-op when nobody is
        subscribed.
        """
        if self._subscribers:
            self._publish(registry.all())

    async def _run(self) -> None:
        """Re-read hardware on the interval and fan the frame out to everyone."""
        while True:
            await asyncio.sleep(_STREAM_INTERVAL)
            try:
                devices = await registry.refresh_all()
            except Exception as e:  # noqa: BLE001 - a transient read error must not kill the loop
                logger.debug(f"Event stream refresh failed: {e}")
                # Keep connections alive until the next successful refresh.
                self._fanout(_KEEPALIVE)
                continue
            self._publish(devices)


broadcaster = _Broadcaster()


async def _stream(request: Request) -> AsyncIterator[str]:
    queue = broadcaster.subscribe()
    try:
        # Emit cached state immediately (bypassing change-suppression) so the UI
        # paints without waiting for the first re-read and is never left blank.
        yield _frame(registry.all())
        # Frames arrive at least every interval, so is_disconnected is re-checked
        # promptly; the finally unsubscribes on drop either way.
        while not await request.is_disconnected():
            yield await queue.get()
    finally:
        broadcaster.unsubscribe(queue)


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    """Stream live device state to the browser as Server-Sent Events."""
    return StreamingResponse(
        _stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Disable proxy buffering (e.g. nginx) so frames flush immediately.
            "X-Accel-Buffering": "no",
        },
    )
