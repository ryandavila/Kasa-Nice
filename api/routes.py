import asyncio
import time

from fastapi import APIRouter, HTTPException
from kasa.exceptions import KasaException

from .energy_history import history
from .events import broadcaster
from .group_store import groups
from .kasa_service import (
    DeviceNotFoundError,
    EnergyUnsupportedError,
    RenameUnsupportedError,
    _cost,
    hex_to_hsv,
    registry,
    serialize_device,
    stable_device_id,
)
from .schemas import (
    BrightnessRequest,
    ColorRequest,
    DailyEnergy,
    Device,
    DiscoverRequest,
    EnergyHistory,
    EnergySample,
    EnergySummary,
    Favorites,
    Group,
    GroupCreate,
    GroupUpdate,
    PowerRequest,
    PowerResult,
    RenameRequest,
    ServerConfig,
    ServerStatus,
    SubnetScanRequest,
    Usage,
)

router = APIRouter(prefix="/api")


async def _set_power_many(device_ids: list[str], on: bool) -> PowerResult:
    """Switch many devices concurrently, tolerating per-device failure.

    Fires every ``set_power`` at once and collects the outcomes: a device that
    errors or no longer exists in the registry is reported under ``failed``
    instead of aborting the batch. Callers publish the SSE update afterwards,
    since the devices that did switch changed state.
    """
    results = await asyncio.gather(
        *(registry.set_power(device_id, on) for device_id in device_ids),
        return_exceptions=True,
    )
    succeeded: list[str] = []
    failed: list[str] = []
    for device_id, result in zip(device_ids, results, strict=True):
        (failed if isinstance(result, Exception) else succeeded).append(device_id)
    return PowerResult(on=on, succeeded=succeeded, failed=failed)


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
    # Append known-but-unreachable devices so they stay visible (grayed) in the UI
    # rather than silently disappearing from their rooms and favorites.
    return [
        serialize_device(d) for d in registry.all()
    ] + registry.unreachable_devices()


@router.get("/state", response_model=list[Device])
async def state() -> list[Device]:
    """Cached devices with live state refreshed from the hardware."""
    live = [serialize_device(d) for d in await registry.refresh_all()]
    return live + registry.unreachable_devices()


@router.post("/discover", response_model=list[Device])
async def discover(req: DiscoverRequest) -> list[Device]:
    if req.target:
        devices = await registry.discover_target(req.target)
        # A previously-unreachable host that just answered flips to reachable; push
        # the fresh frame so every client's grayed card updates without waiting for
        # the next tick (the retry affordance relies on this).
        await broadcaster.publish_now()
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


@router.get("/energy/summary", response_model=EnergySummary)
async def energy_summary() -> EnergySummary:
    """Whole-home energy totals aggregated across every metered device.

    Per-device usage is read concurrently; a device that errors (offline, or with
    no energy monitoring) is skipped rather than failing the whole summary. Live
    ``get_usage`` is the source — it already supplies live power plus today/month
    totals for both local and cloud meters, so the history DB isn't needed here.
    Null per-device readings count as zero; costs use the flat rate and stay null
    when it's unset. With no metered devices the totals are zero (not a 404).
    """
    results = await asyncio.gather(
        *(registry.get_usage(stable_device_id(d)) for d in registry.all()),
        return_exceptions=True,
    )
    usages = [u for u in results if isinstance(u, Usage)]

    today_kwh = sum(u.today_kwh or 0.0 for u in usages)
    month_kwh = sum(u.month_kwh or 0.0 for u in usages)
    return EnergySummary(
        total_power_w=round(sum(u.current_power_w or 0.0 for u in usages), 1),
        today_kwh=round(today_kwh, 3),
        month_kwh=round(month_kwh, 3),
        today_cost=_cost(today_kwh, registry.energy_rate),
        month_cost=_cost(month_kwh, registry.energy_rate),
        device_count=len(usages),
    )


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

    404 for an unknown id; 501 for cloud-only devices that can't be renamed via
    this API (the frontend hides the affordance via ``can_rename``, but a direct
    call is still rejected cleanly rather than hanging or 500ing). A device I/O
    failure maps to the same ``{detail}`` shape the control endpoints return.
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
    result = await _set_power_many(ids, req.on)
    # Push even on partial failure: the devices that did switch changed state.
    await broadcaster.publish_now()
    return result


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


@router.post("/groups/{group_id}/power", response_model=PowerResult)
async def set_group_power(group_id: str, req: PowerRequest) -> PowerResult:
    """Switch every device in a room at once, tolerating per-device failure."""
    group = groups.get_group(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail=f"Unknown group: {group_id}")
    result = await _set_power_many(group["device_ids"], req.on)
    # Push even on partial failure: the devices that did switch changed state.
    await broadcaster.publish_now()
    return result


@router.get("/favorites", response_model=Favorites)
async def get_favorites() -> Favorites:
    return Favorites(device_ids=groups.get_favorites())


@router.put("/favorites", response_model=Favorites)
async def set_favorites(req: Favorites) -> Favorites:
    return Favorites(device_ids=groups.set_favorites(req.device_ids))
