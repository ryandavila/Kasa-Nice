from pydantic import BaseModel, Field

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
