from fastapi import APIRouter, HTTPException

from .kasa_service import (
    DeviceNotFoundError,
    EnergyUnsupportedError,
    hex_to_hsv,
    registry,
    serialize_device,
)
from .schemas import (
    BrightnessRequest,
    ColorRequest,
    Device,
    DiscoverRequest,
    PowerRequest,
    ServerConfig,
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
