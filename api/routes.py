import asyncio
import calendar
import datetime
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
from .schedule_store import schedules
from .schemas import (
    BrightnessRequest,
    ColorRequest,
    DailyEnergy,
    Device,
    DiscoverRequest,
    EnergyHistory,
    EnergyInsights,
    EnergySample,
    EnergySummary,
    Favorites,
    Group,
    GroupCreate,
    GroupUpdate,
    IdleDevice,
    MonthProjection,
    PowerRequest,
    PowerResult,
    RenameRequest,
    RoomUsage,
    Schedule,
    ScheduleCreate,
    ScheduleUpdate,
    ServerConfig,
    ServerStatus,
    SubnetScanRequest,
    Usage,
    WeekComparison,
)

router = APIRouter(prefix="/api")


async def _set_power_many(device_ids: list[str], on: bool) -> PowerResult:
    """Switch many devices concurrently, tolerating per-device failure.

    A device that errors or no longer exists is reported under ``failed`` instead
    of aborting the batch. Callers publish the SSE update afterwards.
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
    # Append known-but-unreachable devices so they stay visible (grayed) instead
    # of disappearing from rooms/favorites.
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

    Per-device usage is read concurrently via ``get_usage``; a device that errors
    (offline, or no metering) is skipped rather than failing the summary. Null
    readings count as zero; costs use the flat rate and stay null when unset. No
    metered devices => zero totals (not a 404).
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
    result = await _set_power_many(ids, req.on)
    # Push even on partial failure: the devices that did switch changed state.
    await broadcaster.publish_now()
    return result


@router.get("/devices/{device_id}/history", response_model=EnergyHistory)
async def device_history(
    device_id: str, hours: int = 24, days: int = 30
) -> EnergyHistory:
    """Persisted energy history: recent power samples plus daily totals.

    Recorded independently of discovery, so it outlives a device's presence in
    the registry. 404 only when the device is unknown AND has no samples.
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


# ── Energy insights ─────────────────────────────────────────────────────────

# Median overnight draw above this (watts) marks a device as a "vampire" load
# worth flagging — roughly the standby of a small always-on appliance.
_IDLE_HOG_THRESHOLD_W = 2.0
# Window for the idle-draw median: long enough to smooth nightly variation, short
# enough to reflect the current setup rather than months-old behaviour.
_IDLE_WINDOW_DAYS = 14


def _local_midnight_ts(d: datetime.date) -> int:
    """Epoch seconds at local midnight on ``d`` (DST-correct via ``mktime``)."""
    return int(time.mktime((d.year, d.month, d.day, 0, 0, 0, 0, 0, -1)))


@router.get("/energy/insights", response_model=EnergyInsights)
async def energy_insights() -> EnergyInsights:
    """Derived energy insights over the recorded sample history.

    Pure aggregation over the ``samples`` table (no device I/O), so it works for
    devices no longer present. Assembles four views — a naive month-end
    projection, per-room today/month rollups (with an "Unassigned" bucket for
    room-less devices), a week-over-week whole-home delta, and per-device idle
    draw. Costs use the flat rate and stay null when unset; empty history yields
    zeros and empty lists, never a 404.
    """
    rate = registry.energy_rate

    # Month-end projection: extrapolate the month-to-date daily average across the
    # whole calendar month. Local day-of-month is the number of days elapsed.
    month_by_device = history.month_kwh_by_device()
    today_by_device = history.today_kwh_by_device()
    month_to_date = round(sum(month_by_device.values()), 3)
    today = datetime.date.today()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    projected = (
        round(month_to_date / today.day * days_in_month, 3) if today.day else 0.0
    )
    projection = MonthProjection(
        month_to_date_kwh=month_to_date,
        projected_kwh=projected,
        month_to_date_cost=_cost(month_to_date, rate),
        projected_cost=_cost(projected, rate),
    )

    # Per-room rollups. Track which devices land in a room so the leftovers can be
    # gathered under a synthetic "Unassigned" bucket.
    rooms: list[RoomUsage] = []
    assigned: set[str] = set()
    for group in groups.list_groups():
        ids = group["device_ids"]
        assigned.update(ids)
        today_kwh = round(sum(today_by_device.get(i, 0.0) for i in ids), 3)
        month_kwh = round(sum(month_by_device.get(i, 0.0) for i in ids), 3)
        rooms.append(
            RoomUsage(
                group_id=group["id"],
                name=group["name"],
                today_kwh=today_kwh,
                month_kwh=month_kwh,
                today_cost=_cost(today_kwh, rate),
                month_cost=_cost(month_kwh, rate),
            )
        )
    # Metered devices in no room: surface their usage rather than hide it, but only
    # when there's something to show.
    unassigned_ids = (set(today_by_device) | set(month_by_device)) - assigned
    if unassigned_ids:
        u_today = round(sum(today_by_device.get(i, 0.0) for i in unassigned_ids), 3)
        u_month = round(sum(month_by_device.get(i, 0.0) for i in unassigned_ids), 3)
        if u_today or u_month:
            rooms.append(
                RoomUsage(
                    group_id="unassigned",
                    name="Unassigned",
                    today_kwh=u_today,
                    month_kwh=u_month,
                    today_cost=_cost(u_today, rate),
                    month_cost=_cost(u_month, rate),
                )
            )

    # Week-over-week: ISO weeks (Monday start) in local time.
    this_monday = today - datetime.timedelta(days=today.weekday())
    last_monday = this_monday - datetime.timedelta(days=7)
    next_monday = this_monday + datetime.timedelta(days=7)
    week = WeekComparison(
        this_week_kwh=round(
            history.home_kwh_between(
                _local_midnight_ts(this_monday), _local_midnight_ts(next_monday)
            ),
            3,
        ),
        last_week_kwh=round(
            history.home_kwh_between(
                _local_midnight_ts(last_monday), _local_midnight_ts(this_monday)
            ),
            3,
        ),
    )

    # Idle draw: label with the live alias where the device is still known, else
    # fall back to its id (samples outlive a device's presence in the registry).
    aliases = {stable_device_id(d): (d.alias or d.host) for d in registry.all()}
    idle = [
        IdleDevice(
            device_id=device_id,
            alias=aliases.get(device_id, device_id),
            idle_w=round(idle_w, 1),
            is_idle_hog=idle_w > _IDLE_HOG_THRESHOLD_W,
        )
        for device_id, idle_w in sorted(
            history.idle_draw(_IDLE_WINDOW_DAYS).items(),
            key=lambda kv: kv[1],
            reverse=True,
        )
    ]

    return EnergyInsights(projection=projection, rooms=rooms, week=week, idle=idle)


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


# ── Schedules (timers) ────────────────────────────────────────────────────────


@router.get("/schedules", response_model=list[Schedule])
async def list_schedules() -> list[Schedule]:
    # Re-validate through the model so a hand-edited/older file can't emit a
    # malformed rule to the client.
    return [Schedule(**s) for s in schedules.list_rules()]


@router.post("/schedules", response_model=Schedule, status_code=201)
async def create_schedule(req: ScheduleCreate) -> Schedule:
    # ``req`` is pydantic-validated; the store stamps id and null ``last_fired``.
    # The scheduler picks it up on its next minute tick — no restart needed.
    return Schedule(**schedules.create_rule(req.model_dump()))


@router.patch("/schedules/{schedule_id}", response_model=Schedule)
async def update_schedule(schedule_id: str, req: ScheduleUpdate) -> Schedule:
    # exclude_unset => true partial update: only sent fields are written, so an
    # enable/disable toggle leaves time/days/target untouched.
    updated = schedules.update_rule(schedule_id, req.model_dump(exclude_unset=True))
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Unknown schedule: {schedule_id}")
    return Schedule(**updated)


@router.delete("/schedules/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: str) -> None:
    if not schedules.delete_rule(schedule_id):
        raise HTTPException(status_code=404, detail=f"Unknown schedule: {schedule_id}")
