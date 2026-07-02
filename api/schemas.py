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

    Each field sums the corresponding per-device reading; a device's null
    reading counts as zero. Cost fields use the flat rate and stay null when no
    rate is configured. With no metered devices the totals are simply zero.
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

    Per-device failures are tolerated, so the batch reports which device ids were
    switched and which couldn't be (unreachable, or no longer in the registry).
    """

    on: bool
    succeeded: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)


class RenameRequest(BaseModel):
    """A new display name for a device or one strip outlet.

    Trimmed and required non-empty: a blank or whitespace-only alias would leave
    the device unlabelable in the UI (and the underlying set_alias would persist
    the emptiness to the hardware). Capped well above any sensible name so it's a
    guard against unbounded input reaching the device, not a real-world limit.
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

# 0=Monday … 6=Sunday, matching Python's ``datetime.weekday()`` so the scheduler
# can compare a rule's days directly against the local clock with no remapping.
# The frontend presents a Mon–Sun picker over these same integers.
_HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


def _normalize_days(days: list[int]) -> list[int]:
    """Validate weekday ints and normalise to a sorted, de-duplicated list.

    JSON has no set type, so "set of ints" is persisted as a canonical list. A
    rule with no days would never fire (almost certainly a mistake), so at least
    one is required. Shared by the create/read/update schemas so they agree.
    """
    if not days:
        raise ValueError("at least one weekday is required")
    if any(d < 0 or d > 6 for d in days):
        raise ValueError("weekdays must be between 0 (Monday) and 6 (Sunday)")
    return sorted(set(days))


class ScheduleTarget(BaseModel):
    """What a rule acts on: a single device, or a whole room (group).

    A discriminated ``{type, id}`` rather than two optional id fields, so adding
    a future target kind stays a one-line ``Literal`` extension and can't produce
    an ambiguous "both set / neither set" payload.
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

    The ``kind`` discriminator is fixed to ``"fixed_time"`` in v1. It exists so
    later rule kinds (sunrise/sunset, one-shot timers) can be added as new
    ``kind`` values without reshaping — or breaking the deserialization of —
    existing v1 rules. Likewise ``action`` is a string enum today; a future
    brightness/colour action would arrive as an additional value, leaving on/off
    rules untouched.
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
    # Server-written; null until the rule first fires. Optional so older files
    # (and freshly-created rules) load without it.
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
        # Same rule as the other schemas, but tolerate the field being omitted.
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
