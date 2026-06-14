"""Server-Sent Events stream of live device state.

Replaces the frontend's fixed-interval polling of ``/state``: the browser opens
one ``EventSource`` to ``/api/events`` and the server pushes the serialized
device list every time it re-reads hardware, so changes made elsewhere (the
Kasa app, a physical switch) surface without the client hammering an endpoint on
a timer. Each connection drives its own refresh loop, which is fine at home
scale (a handful of browser tabs); ``refresh_all`` already throttles the costly
cloud round-trips internally.

Kept in its own module with its own ``APIRouter`` so the streaming concern stays
isolated and ``routes.py`` remains a flat list of plain REST handlers.
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

# Seconds between server-side state re-reads pushed to a connected client. Matches
# the old client poll cadence.
_STREAM_INTERVAL = 5.0


def _frame(devices: list) -> str:
    """Encode a device list as one SSE ``data:`` frame."""
    payload = json.dumps([serialize_device(d).model_dump() for d in devices])
    return f"data: {payload}\n\n"


async def _stream(request: Request) -> AsyncIterator[str]:
    # Emit cached state immediately so the UI paints without waiting a full
    # interval for the first hardware re-read.
    yield _frame(registry.all())
    while True:
        # is_disconnected lets us stop promptly when the tab closes instead of
        # discovering the dead socket only on the next failed write.
        if await request.is_disconnected():
            break
        await asyncio.sleep(_STREAM_INTERVAL)
        try:
            devices = await registry.refresh_all()
            yield _frame(devices)
        except Exception as e:  # noqa: BLE001 - a transient read error must not kill the stream
            logger.debug(f"Event stream refresh failed: {e}")
            # A comment frame keeps the connection (and any proxy) alive until the
            # next successful refresh.
            yield ": keepalive\n\n"


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
