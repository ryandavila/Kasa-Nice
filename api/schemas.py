from typing import Literal

from pydantic import BaseModel, Field, field_validator

Hsv = tuple[int, int, int]


class ChildPlug(BaseModel):
    """A controllable outlet within a multi-plug power strip."""

    id: str = Field(
        description="Stable outlet id (python-kasa child device_id); alias is display."
    )
    alias: str
    is_on: bool


class Device(BaseModel):
    id: str = Field(
        description="Stable identifier (normalized MAC, or host when unavailable) "
        "used in API paths; survives the device changing IP."
    )
    alias: str
    host: str
    model: str
    device_type: str
    is_on: bool
    is_color: bool
    is_dimmable: bool
    has_emeter: bool
    brightness: int | None = None
    hsv: Hsv | None = None
    children: list[ChildPlug] = Field(default_factory=list)
    can_rename: bool = Field(
        default=True,
        description="Whether this device (and its outlets) can be renamed through "
        "the API. False for cloud-only devices (e.g. HS300 strips) whose façade "
        "has no set_alias; the UI hides the rename affordance for them.",
    )
    reachable: bool = Field(
        default=True,
        description="False for a known device that didn't answer discovery: it's "
        "shown from its last-known snapshot (or host-only identity) as a grayed, "
        "non-interactive card so it doesn't silently vanish from rooms/favorites. "
        "Defaults True so every live device and existing consumer is unaffected.",
    )


class UsageStat(BaseModel):
    """A single bar in an energy chart (a day or a month)."""

    label: str
    kwh: float
    cost: float | None = Field(
        default=None,
        description="Approximate cost (kWh × flat rate), or null when no rate is set.",
    )


class Usage(BaseModel):
    device_id: str
    current_power_w: float | None = Field(
        default=None, description="Instantaneous power draw in watts."
    )
    today_kwh: float | None = None
    month_kwh: float | None = None
    today_cost: float | None = Field(
        default=None, description="today_kwh × flat rate, or null when no rate is set."
    )
    month_cost: float | None = Field(
        default=None, description="month_kwh × flat rate, or null when no rate is set."
    )
    voltage: float | None = None
    daily: list[UsageStat] = Field(
        default_factory=list, description="Energy per day for the current month."
    )
    monthly: list[UsageStat] = Field(
        default_factory=list, description="Energy per month for the current year."
    )


class EnergySummary(BaseModel):
    """Whole-home energy totals aggregated across every metered device.

    Each field sums the per-device reading (null counts as zero); cost fields use
    the flat rate and stay null when unset. No metered devices => zero totals.
    """

    total_power_w: float = Field(
        description="Sum of live power draw across all metered devices, in watts."
    )
    today_kwh: float
    month_kwh: float
    today_cost: float | None = Field(
        default=None, description="today_kwh × flat rate, or null when no rate is set."
    )
    month_cost: float | None = Field(
        default=None, description="month_kwh × flat rate, or null when no rate is set."
    )
    device_count: int = Field(
        description="Number of metered devices included in the totals."
    )


class DiscoverRequest(BaseModel):
    target: str | None = Field(
        default=None,
        description="IP address to probe directly; broadcast discovery if omitted.",
    )


class SubnetScanRequest(BaseModel):
    subnet: str | None = Field(
        default=None,
        description="CIDR subnet to sweep by unicast, e.g. '192.168.1.0/24'. "
        "Falls back to the server's KASA_SCAN_SUBNET when omitted.",
    )


class ServerConfig(BaseModel):
    """Server-side configuration the frontend needs to render correctly."""

    scan_subnet: str | None = Field(
        default=None,
        description="Default CIDR the server sweeps, or null if unconfigured.",
    )
    energy_rate: float | None = Field(
        default=None,
        description="Flat cost per kWh applied to energy readings, or null if unset. "
        "A flat-rate approximation — no tiered or time-of-use billing.",
    )
    energy_currency: str = Field(
        default="$",
        description="Currency symbol/prefix shown alongside energy cost.",
    )


class ServerStatus(BaseModel):
    """Live server state the UI polls to reflect background work."""

    discovering: bool = Field(
        description="True while the initial network sweep is still running."
    )
    device_count: int = Field(description="Devices currently known to the server.")


class PowerRequest(BaseModel):
    on: bool


class PowerResult(BaseModel):
    """Outcome of a fan-out power action across many devices.

    Reports which device ids switched and which couldn't (unreachable, or no
    longer in the registry).
    """

    on: bool
    succeeded: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)


class RenameRequest(BaseModel):
    """A new display name for a device or one strip outlet.

    Trimmed and required non-empty (a blank alias would leave the device
    unlabelable). ``max_length`` guards against unbounded input, not a real limit.
    """

    alias: str = Field(min_length=1, max_length=60)

    @field_validator("alias")
    @classmethod
    def _trimmed_nonempty(cls, v: str) -> str:
        # Reject whitespace-only names, which pass min_length but aren't a label.
        v = v.strip()
        if not v:
            raise ValueError("alias must not be blank")
        return v


class BrightnessRequest(BaseModel):
    value: int = Field(ge=0, le=100)


class ColorRequest(BaseModel):
    hex: str | None = Field(default=None, pattern=r"^#?[0-9a-fA-F]{6}$")
    hsv: Hsv | None = None


# ── Groups (rooms) & favorites ──────────────────────────────────────────────


class Group(BaseModel):
    """A user-defined room: a named, ordered set of device ids."""

    id: str
    name: str
    device_ids: list[str] = Field(default_factory=list)


class GroupCreate(BaseModel):
    name: str = Field(min_length=1)


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    device_ids: list[str] | None = None


class Favorites(BaseModel):
    """The device ids the user has starred for quick access."""

    device_ids: list[str] = Field(default_factory=list)


# ── Schedules (timers) ────────────────────────────────────────────────────────

# 0=Monday … 6=Sunday, matching ``datetime.weekday()`` so the scheduler compares
# a rule's days against the local clock with no remapping.
_HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


def _normalize_days(days: list[int]) -> list[int]:
    """Validate weekday ints and normalise to a sorted, de-duplicated list.

    Persisted as a canonical list (JSON has no set). At least one day is required
    (a rule with none would never fire). Shared by all schedule schemas.
    """
    if not days:
        raise ValueError("at least one weekday is required")
    if any(d < 0 or d > 6 for d in days):
        raise ValueError("weekdays must be between 0 (Monday) and 6 (Sunday)")
    return sorted(set(days))


class ScheduleTarget(BaseModel):
    """What a rule acts on: a single device, or a whole room (group).

    A discriminated ``{type, id}`` (not two optional id fields), so a future
    target kind is a one-line ``Literal`` extension with no ambiguous payload.
    """

    type: Literal["device", "room"]
    id: str = Field(min_length=1, description="Device id or group id, per ``type``.")


class LastFired(BaseModel):
    """Audit note for a rule's most recent firing (server-written, read-only)."""

    ts: int = Field(description="Unix epoch seconds of the last firing attempt.")
    result: str = Field(
        description="Human-readable outcome, e.g. 'ok' or 'partial: 1 failed'."
    )


class Schedule(BaseModel):
    """A fixed-time rule: at ``time`` on ``days``, apply ``action`` to ``target``.

    ``kind`` is fixed to ``"fixed_time"`` in v1, left open so later rule kinds
    (sunrise/sunset, one-shot timers) can be added without breaking existing
    rules; likewise ``action`` can gain values, leaving on/off rules untouched.
    """

    id: str
    kind: Literal["fixed_time"] = "fixed_time"
    enabled: bool = True
    time: str = Field(pattern=_HHMM, description="Local wall-clock time, 'HH:MM'.")
    days: list[int] = Field(
        description="Weekdays the rule fires on; 0=Monday … 6=Sunday."
    )
    target: ScheduleTarget
    action: Literal["on", "off"]
    # Server-written; null until first fired. Optional so older files load.
    last_fired: LastFired | None = None

    @field_validator("days")
    @classmethod
    def _validate_days(cls, days: list[int]) -> list[int]:
        return _normalize_days(days)


class ScheduleCreate(BaseModel):
    """Fields a client supplies to create a rule; server assigns id/last_fired."""

    kind: Literal["fixed_time"] = "fixed_time"
    enabled: bool = True
    time: str = Field(pattern=_HHMM)
    days: list[int]
    target: ScheduleTarget
    action: Literal["on", "off"]

    @field_validator("days")
    @classmethod
    def _validate_days(cls, days: list[int]) -> list[int]:
        return _normalize_days(days)


class ScheduleUpdate(BaseModel):
    """Partial update of a rule; every field is optional (omitted = unchanged)."""

    enabled: bool | None = None
    time: str | None = Field(default=None, pattern=_HHMM)
    days: list[int] | None = None
    target: ScheduleTarget | None = None
    action: Literal["on", "off"] | None = None

    @field_validator("days")
    @classmethod
    def _validate_days(cls, days: list[int] | None) -> list[int] | None:
        # As the other schemas, but tolerate the field being omitted.
        return None if days is None else _normalize_days(days)


# ── Persisted energy history ────────────────────────────────────────────────


class EnergySample(BaseModel):
    """One recorded power reading: unix epoch seconds and watts (null if unread)."""

    ts: int
    power_w: float | None = None


class DailyEnergy(BaseModel):
    """A persisted day's total energy (and optional flat-rate cost)."""

    date: str = Field(description="Local date, ISO 'YYYY-MM-DD'.")
    kwh: float
    cost: float | None = None


class EnergyHistory(BaseModel):
    """Recorded history for a device: recent power samples and daily totals."""

    device_id: str
    samples: list[EnergySample] = Field(default_factory=list)
    daily: list[DailyEnergy] = Field(default_factory=list)


# ── Energy insights ─────────────────────────────────────────────────────────
# Derived views over the recorded samples (see GET /api/energy/insights):
# a month-end projection, per-room rollups, a week-over-week delta, and idle draw.


class MonthProjection(BaseModel):
    """Month-to-date energy plus a naive linear month-end projection.

    The projection extrapolates the month-to-date daily average across every day
    of the month (``month_to_date / days_elapsed × days_in_month``): a rough
    forecast, not a bill — it assumes the rest of the month looks like so far.
    """

    month_to_date_kwh: float = Field(
        description="Whole-home energy used so far this calendar month, in kWh."
    )
    projected_kwh: float = Field(
        description="Extrapolated whole-home energy for the full month, in kWh."
    )
    month_to_date_cost: float | None = Field(
        default=None, description="MTD kWh × flat rate, or null when no rate is set."
    )
    projected_cost: float | None = Field(
        default=None,
        description="Projected kWh × flat rate, or null when no rate is set.",
    )


class RoomUsage(BaseModel):
    """Today/month energy rolled up over the devices in one room.

    Sums per-device usage across a room's members. The synthetic ``group_id``
    ``"unassigned"`` collects metered devices that belong to no room.
    """

    group_id: str
    name: str
    today_kwh: float
    month_kwh: float
    today_cost: float | None = None
    month_cost: float | None = None


class WeekComparison(BaseModel):
    """Whole-home kWh for the current ISO week vs the previous full week.

    Weeks start Monday, local time. The UI derives the delta; both raw totals are
    surfaced so it can show either an absolute or a percentage change.
    """

    this_week_kwh: float
    last_week_kwh: float


class IdleDevice(BaseModel):
    """A device's overnight (01:00–05:00 local) median standing power draw."""

    device_id: str
    alias: str = Field(
        description="Live alias when the device is still known; else its id."
    )
    idle_w: float = Field(description="Median overnight power draw, in watts.")
    is_idle_hog: bool = Field(
        description="True when the idle draw exceeds the vampire-load threshold."
    )


class EnergyInsights(BaseModel):
    """Derived energy insights across all recorded devices.

    Assembled from ``EnergyHistoryStore`` aggregates plus room membership; costs
    use the flat rate and stay null when it is unset. Empty history => zero totals
    and empty lists (never a 404).
    """

    projection: MonthProjection
    rooms: list[RoomUsage] = Field(default_factory=list)
    week: WeekComparison
    idle: list[IdleDevice] = Field(default_factory=list)
