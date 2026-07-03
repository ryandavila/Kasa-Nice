from fastapi import APIRouter, HTTPException

from ..events import broadcaster
from ..group_store import groups
from ..kasa_service import registry, set_power_many
from ..schemas import (
    Favorites,
    Group,
    GroupCreate,
    GroupUpdate,
    PowerRequest,
    PowerResult,
)

router = APIRouter(prefix="/api")


@router.get("/groups", response_model=list[Group])
async def list_groups() -> list[Group]:
    return [Group(**g) for g in groups.list_groups()]


@router.post("/groups", response_model=Group, status_code=201)
async def create_group(req: GroupCreate) -> Group:
    return Group(**groups.create_group(req.name))


@router.patch("/groups/{group_id}", response_model=Group)
async def update_group(group_id: str, req: GroupUpdate) -> Group:
    updated = groups.update_group(group_id, name=req.name, device_ids=req.device_ids)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Unknown group: {group_id}")
    return Group(**updated)


@router.delete("/groups/{group_id}", status_code=204)
async def delete_group(group_id: str) -> None:
    if not groups.delete_group(group_id):
        raise HTTPException(status_code=404, detail=f"Unknown group: {group_id}")


@router.post("/groups/{group_id}/power", response_model=PowerResult)
async def set_group_power(group_id: str, req: PowerRequest) -> PowerResult:
    """Switch every device in a room at once, tolerating per-device failure."""
    group = groups.get_group(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail=f"Unknown group: {group_id}")
    result = await set_power_many(registry, group["device_ids"], req.on)
    # Push even on partial failure: the devices that did switch changed state.
    await broadcaster.publish_now()
    return result


@router.get("/favorites", response_model=Favorites)
async def get_favorites() -> Favorites:
    return Favorites(device_ids=groups.get_favorites())


@router.put("/favorites", response_model=Favorites)
async def set_favorites(req: Favorites) -> Favorites:
    return Favorites(device_ids=groups.set_favorites(req.device_ids))
