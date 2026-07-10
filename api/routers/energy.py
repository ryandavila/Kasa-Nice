import asyncio
import calendar
import datetime
import time

from fastapi import APIRouter, HTTPException

from ..energy_history import history
from ..group_store import groups
from ..kasa_service import (
    DeviceNotFoundError,
    EnergyUnsupportedError,
    _cost,
    registry,
    stable_device_id,
)
from ..schemas import (
    DailyEnergy,
    EnergyHistory,
    EnergyInsights,
    EnergySample,
    EnergySummary,
    IdleDevice,
    MonthProjection,
    RoomUsage,
    Usage,
    WeekComparison,
)

router = APIRouter(prefix="/api")


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
# worth flagging. 15W constant is roughly $1-2/month — the point where idle draw
# becomes real money — and stays above intentional always-on gear like routers
# and aquarium pumps, which a lower bar tags indiscriminately.
_IDLE_HOG_THRESHOLD_W = 15.0
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
