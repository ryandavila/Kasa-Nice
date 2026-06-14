import time

from fastapi import APIRouter, HTTPException

from .energy_history import history
from .group_store import groups
from .kasa_service import (
    DeviceNotFoundError,
    EnergyUnsupportedError,
    _cost,
    hex_to_hsv,
    registry,
    serialize_device,
)
from .schemas import (
    BrightnessRequest,
    ColorRequest,
    DailyEnergy,
    Device,
    DiscoverRequest,
    EnergyHistory,
    EnergySample,
    Favorites,
    Group,
    GroupCreate,
    GroupUpdate,
    PowerRequest,
    ServerConfig,
    ServerStatus,
    SubnetScanRequest,
    Usage,
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
    )


@router.get("/status", response_model=ServerStatus)
async def status() -> ServerStatus:
    """Lightweight poll target: is the startup sweep still running?"""
    return ServerStatus(
        discovering=registry.discovering, device_count=len(registry.all())
    )


@router.get("/devices", response_model=list[Device])
async def list_devices() -> list[Device]:
    return [serialize_device(d) for d in registry.all()]


@router.get("/state", response_model=list[Device])
async def state() -> list[Device]:
    """Cached devices with live state refreshed from the hardware."""
    return [serialize_device(d) for d in await registry.refresh_all()]


@router.post("/discover", response_model=list[Device])
async def discover(req: DiscoverRequest) -> list[Device]:
    if req.target:
        devices = await registry.discover_target(req.target)
        return [serialize_device(d) for d in devices]
    # Broadcast re-discovery from the UI's "Discover" button: refresh cloud-only
    # devices too (e.g. an HS300 strip onboarded after startup), so they appear
    # without a server restart. attach_cloud() is a no-op when the cloud fallback
    # is disabled, and returning registry.all() includes the attached devices.
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


@router.get("/devices/{device_id}/usage", response_model=Usage)
async def usage(device_id: str) -> Usage:
    try:
        return await registry.get_usage(device_id)
    except DeviceNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Unknown device: {device_id}"
        ) from None
    except EnergyUnsupportedError:
        raise HTTPException(
            status_code=404, detail=f"Device has no energy monitoring: {device_id}"
        ) from None


@router.post("/devices/{device_id}/power", response_model=Device)
async def set_power(device_id: str, req: PowerRequest) -> Device:
    try:
        device = await registry.set_power(device_id, req.on)
    except DeviceNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Unknown device: {device_id}"
        ) from None
    return serialize_device(device)


@router.post("/devices/{device_id}/brightness", response_model=Device)
async def set_brightness(device_id: str, req: BrightnessRequest) -> Device:
    try:
        device = await registry.set_brightness(device_id, req.value)
    except DeviceNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Unknown or non-dimmable device: {device_id}"
        ) from None
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
    return serialize_device(device)


@router.post("/devices/{device_id}/children/{child_id}/power", response_model=Device)
async def set_child_power(device_id: str, child_id: str, req: PowerRequest) -> Device:
    try:
        device = await registry.set_child_power(device_id, child_id, req.on)
    except DeviceNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Unknown child: {device_id}/{child_id}"
        ) from None
    return serialize_device(device)


@router.get("/devices/{device_id}/history", response_model=EnergyHistory)
async def device_history(
    device_id: str, hours: int = 24, days: int = 30
) -> EnergyHistory:
    """Persisted energy history: recent power samples plus daily totals.

    History is recorded independently of discovery, so it can outlive a device's
    presence in the registry. 404 only when the device is both unknown and has no
    recorded samples.
    """
    since = int(time.time()) - hours * 3600
    samples = history.recent_samples(device_id, since)
    daily = history.daily_totals(device_id, days)
    if not samples and not daily:
        try:
            registry.get(device_id)
        except DeviceNotFoundError:
            raise HTTPException(
                status_code=404, detail=f"Unknown device: {device_id}"
            ) from None
    return EnergyHistory(
        device_id=device_id,
        samples=[EnergySample(ts=ts, power_w=p) for ts, p in samples],
        daily=[
            DailyEnergy(date=d, kwh=k, cost=_cost(k, registry.energy_rate))
            for d, k in daily
        ],
    )


# ── Groups (rooms) & favorites ──────────────────────────────────────────────


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


@router.get("/favorites", response_model=Favorites)
async def get_favorites() -> Favorites:
    return Favorites(device_ids=groups.get_favorites())


@router.put("/favorites", response_model=Favorites)
async def set_favorites(req: Favorites) -> Favorites:
    return Favorites(device_ids=groups.set_favorites(req.device_ids))
