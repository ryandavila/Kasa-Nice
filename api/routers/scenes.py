from fastapi import APIRouter, HTTPException

from ..kasa_service import DeviceNotFoundError, registry, serialize_device
from ..scene_service import SceneNotFoundError, apply_scene
from ..scene_store import scenes
from ..schemas import Scene, SceneApplyResult, SceneCreate, SceneUpdate
from ._helpers import _validated_rows

router = APIRouter(prefix="/api")


def _snapshot_entries(device_ids: list[str]) -> list[dict]:
    """Capture the CURRENT state of each device id as scene entries.

    Reads live state from the cached device (via ``serialize_device``), recording
    on/off plus brightness/hsv where the device supports them. A device that's not
    in the registry is skipped rather than saved with a guessed state — you can't
    snapshot state you don't have.
    """
    entries: list[dict] = []
    for device_id in device_ids:
        try:
            device = registry.get(device_id)
        except DeviceNotFoundError:
            continue
        snap = serialize_device(device)
        state: dict = {"on": snap.is_on}
        if snap.is_dimmable and snap.brightness is not None:
            state["brightness"] = snap.brightness
        if snap.is_color and snap.hsv is not None:
            state["hsv"] = list(snap.hsv)
        entries.append({"device_id": device_id, "state": state})
    return entries


@router.get("/scenes", response_model=list[Scene])
async def list_scenes() -> list[Scene]:
    return _validated_rows(scenes.list_scenes(), Scene, "scene")


@router.post("/scenes", response_model=Scene, status_code=201)
async def create_scene(req: SceneCreate) -> Scene:
    # Two paths, validated to be mutually exclusive by the schema: explicit
    # entries, or snapshot the current state of the given device ids.
    if req.entries is not None:
        entries = [e.model_dump() for e in req.entries]
    else:
        entries = _snapshot_entries(req.device_ids or [])
    return Scene(**scenes.create_scene(req.name, entries))


@router.patch("/scenes/{scene_id}", response_model=Scene)
async def update_scene(scene_id: str, req: SceneUpdate) -> Scene:
    entries = None if req.entries is None else [e.model_dump() for e in req.entries]
    updated = scenes.update_scene(scene_id, name=req.name, entries=entries)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Unknown scene: {scene_id}")
    return Scene(**updated)


@router.delete("/scenes/{scene_id}", status_code=204)
async def delete_scene(scene_id: str) -> None:
    if not scenes.delete_scene(scene_id):
        raise HTTPException(status_code=404, detail=f"Unknown scene: {scene_id}")


@router.post("/scenes/{scene_id}/apply", response_model=SceneApplyResult)
async def apply_scene_route(scene_id: str) -> SceneApplyResult:
    """Apply a scene, tolerating per-device failure (the service nudges SSE)."""
    try:
        return await apply_scene(scene_id)
    except SceneNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Unknown scene: {scene_id}"
        ) from None
