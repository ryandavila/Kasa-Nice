from fastapi import APIRouter, HTTPException
from kasa.exceptions import KasaException

from ..config import get_settings
from ..events import broadcaster
from ..kasa_service import (
    DeviceNotFoundError,
    RenameUnsupportedError,
    hex_to_hsv,
    registry,
    serialize_device,
    set_power_many,
    stable_device_id,
)
from ..schemas import (
    BrightnessRequest,
    ColorRequest,
    Device,
    DiscoverRequest,
    PowerRequest,
    PowerResult,
    RenameRequest,
    ServerConfig,
    ServerStatus,
    SubnetScanRequest,
)

router = APIRouter(prefix="/api")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/config", response_model=ServerConfig)
async def config() -> ServerConfig:
    return ServerConfig(
        scan_subnet=registry.scan_subnet,
        energy_rate=registry.energy_rate,
        energy_currency=registry.energy_currency,
        # Sunrise/sunset schedules need a location; the UI hints when it's missing.
        location_configured=get_settings().location is not None,
    )


@router.get("/status", response_model=ServerStatus)
async def status() -> ServerStatus:
    """Lightweight poll target: is the startup sweep still running?"""
    return ServerStatus(
        discovering=registry.discovering, device_count=len(registry.all())
    )


@router.get("/devices", response_model=list[Device])
async def list_devices() -> list[Device]:
    # Append known-but-unreachable devices so they stay visible (grayed) instead
    # of disappearing from rooms/favorites.
    return [
        serialize_device(d, reachable=registry.is_reachable(d)) for d in registry.all()
    ] + registry.unreachable_devices()


@router.get("/state", response_model=list[Device])
async def state() -> list[Device]:
    """Cached devices with live state refreshed from the hardware."""
    live = [
        serialize_device(d, reachable=registry.is_reachable(d))
        for d in await registry.refresh_all()
    ]
    return live + registry.unreachable_devices()


@router.post("/discover", response_model=list[Device])
async def discover(req: DiscoverRequest) -> list[Device]:
    if req.target:
        devices = await registry.discover_target(req.target)
        # A host that just answered flips to reachable; push the fresh frame so
        # grayed cards update now (the retry affordance relies on this).
        await broadcaster.publish_now()
        return [serialize_device(d) for d in devices]
    # Broadcast re-discovery: also refresh cloud-only devices (e.g. a strip
    # onboarded after startup) so they appear without a restart. attach_cloud() is
    # a no-op when the fallback is disabled.
    await registry.discover_all()
    await registry.attach_cloud()
    return [serialize_device(d) for d in registry.all()]


@router.post("/discover/subnet", response_model=list[Device])
async def discover_subnet(req: SubnetScanRequest) -> list[Device]:
    subnet = req.subnet or registry.scan_subnet
    if not subnet:
        raise HTTPException(
            status_code=422,
            detail="No subnet provided and KASA_SCAN_SUBNET is not set.",
        )
    try:
        devices = await registry.discover_subnet(subnet)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return [serialize_device(d) for d in devices]


@router.post("/devices/{device_id}/power", response_model=Device)
async def set_power(device_id: str, req: PowerRequest) -> Device:
    try:
        device = await registry.set_power(device_id, req.on)
    except DeviceNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Unknown device: {device_id}"
        ) from None
    # Push the change to other clients now instead of on their next tick.
    await broadcaster.publish_now()
    return serialize_device(device)


@router.post("/devices/{device_id}/brightness", response_model=Device)
async def set_brightness(device_id: str, req: BrightnessRequest) -> Device:
    try:
        device = await registry.set_brightness(device_id, req.value)
    except DeviceNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Unknown or non-dimmable device: {device_id}"
        ) from None
    await broadcaster.publish_now()
    return serialize_device(device)


@router.post("/devices/{device_id}/color", response_model=Device)
async def set_color(device_id: str, req: ColorRequest) -> Device:
    if req.hsv is not None:
        hsv = req.hsv
    elif req.hex is not None:
        hsv = hex_to_hsv(req.hex)
    else:
        raise HTTPException(status_code=422, detail="Provide either 'hex' or 'hsv'.")
    try:
        device = await registry.set_hsv(device_id, hsv)
    except DeviceNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Unknown or non-color device: {device_id}"
        ) from None
    await broadcaster.publish_now()
    return serialize_device(device)


@router.post("/devices/{device_id}/children/{child_id}/power", response_model=Device)
async def set_child_power(device_id: str, child_id: str, req: PowerRequest) -> Device:
    try:
        device = await registry.set_child_power(device_id, child_id, req.on)
    except DeviceNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Unknown child: {device_id}/{child_id}"
        ) from None
    await broadcaster.publish_now()
    return serialize_device(device)


@router.patch("/devices/{device_id}", response_model=Device)
async def rename_device(device_id: str, req: RenameRequest) -> Device:
    """Rename a device, pushing the new name to connected clients immediately.

    404 for an unknown id; 501 for cloud-only devices that can't be renamed (the
    UI hides the affordance via ``can_rename``, but a direct call is still
    rejected cleanly); 502 for a device I/O failure.
    """
    try:
        device = await registry.set_alias(device_id, req.alias)
    except DeviceNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Unknown device: {device_id}"
        ) from None
    except RenameUnsupportedError:
        raise HTTPException(
            status_code=501,
            detail=f"Device cannot be renamed through the app: {device_id}",
        ) from None
    except KasaException as e:
        raise HTTPException(status_code=502, detail=f"Device error: {e}") from None
    await broadcaster.publish_now()
    return serialize_device(device)


@router.patch("/devices/{device_id}/children/{child_id}", response_model=Device)
async def rename_child(device_id: str, child_id: str, req: RenameRequest) -> Device:
    """Rename one outlet of a strip; see ``rename_device`` for the error shape."""
    try:
        device = await registry.set_child_alias(device_id, child_id, req.alias)
    except DeviceNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Unknown child: {device_id}/{child_id}"
        ) from None
    except RenameUnsupportedError:
        raise HTTPException(
            status_code=501,
            detail=f"Outlet cannot be renamed through the app: {device_id}/{child_id}",
        ) from None
    except KasaException as e:
        raise HTTPException(status_code=502, detail=f"Device error: {e}") from None
    await broadcaster.publish_now()
    return serialize_device(device)


@router.post("/power", response_model=PowerResult)
async def set_all_power(req: PowerRequest) -> PowerResult:
    """Switch every known device at once (primarily 'everything off')."""
    ids = [stable_device_id(d) for d in registry.all()]
    result = await set_power_many(registry, ids, req.on)
    # Push even on partial failure: the devices that did switch changed state.
    await broadcaster.publish_now()
    return result
