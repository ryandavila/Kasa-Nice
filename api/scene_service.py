"""Applying scenes: fan a saved per-device state out concurrently.

Kept separate from the route so a scene can be applied by id from other server
code — the Schedules feature drives scenes through :func:`apply_scene` directly,
never over HTTP. The route is a thin wrapper that translates the not-found case
into a 404.

Mirrors ``routes._set_power_many``: fan out per device with
``asyncio.gather(return_exceptions=True)`` so one unreachable device doesn't
abort the rest, report per-device success/failure, then nudge the SSE stream.
"""

import asyncio

from .events import broadcaster
from .kasa_service import registry
from .scene_store import scenes
from .schemas import SceneApplyResult


class SceneNotFoundError(KeyError):
    """Raised when a scene id isn't in the store; the route maps this to a 404."""


async def _apply_entry(entry: dict) -> None:
    """Drive one device to an entry's saved state; raises if any part fails.

    Power is always set. Brightness and colour are applied only when the entry
    leaves the device on: they're meaningless on an off light, and pushing them
    would silently switch it back on. A failure of any step propagates, so the
    caller counts the whole device as failed.
    """
    device_id = entry["device_id"]
    state = entry.get("state", {})
    on = bool(state.get("on"))
    await registry.set_power(device_id, on)
    if not on:
        return
    brightness = state.get("brightness")
    if brightness is not None:
        await registry.set_brightness(device_id, brightness)
    hsv = state.get("hsv")
    if hsv is not None:
        # Persisted as a JSON list; the registry/light API wants a tuple.
        await registry.set_hsv(device_id, tuple(hsv))


async def apply_scene(scene_id: str) -> SceneApplyResult:
    """Apply a saved scene, tolerating per-device failure.

    Fans the entries out concurrently; a device that errors or no longer exists
    is reported under ``failed`` instead of aborting the batch. Raises
    :class:`SceneNotFoundError` for an unknown id (the route turns that into a
    404). This is the seam Schedules calls — importable and awaitable without
    going through HTTP.
    """
    scene = scenes.get_scene(scene_id)
    if scene is None:
        raise SceneNotFoundError(scene_id)
    entries: list[dict] = scene.get("entries", [])
    results = await asyncio.gather(
        *(_apply_entry(entry) for entry in entries),
        return_exceptions=True,
    )
    succeeded: list[str] = []
    failed: list[str] = []
    for entry, result in zip(entries, results, strict=True):
        (failed if isinstance(result, Exception) else succeeded).append(
            entry["device_id"]
        )
    # Push the aggregate change even on partial failure: devices did move.
    await broadcaster.publish_now()
    return SceneApplyResult(succeeded=succeeded, failed=failed)
