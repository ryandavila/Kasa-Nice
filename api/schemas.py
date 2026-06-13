from pydantic import BaseModel, Field

Hsv = tuple[int, int, int]


class ChildPlug(BaseModel):
    """A controllable outlet within a multi-plug power strip."""

    id: str
    alias: str
    is_on: bool


class Device(BaseModel):
    id: str = Field(
        description="Stable identifier (the device host) used in API paths."
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


class Usage(BaseModel):
    device_id: str
    current_power_w: float | None = Field(
        default=None, description="Instantaneous power draw in watts."
    )
    today_kwh: float | None = None
    month_kwh: float | None = None
    voltage: float | None = None
    daily: list[UsageStat] = Field(
        default_factory=list, description="Energy per day for the current month."
    )
    monthly: list[UsageStat] = Field(
        default_factory=list, description="Energy per month for the current year."
    )


class DiscoverRequest(BaseModel):
    target: str | None = Field(
        default=None,
        description="IP address to probe directly; broadcast discovery if omitted.",
    )


class PowerRequest(BaseModel):
    on: bool


class BrightnessRequest(BaseModel):
    value: int = Field(ge=0, le=100)


class ColorRequest(BaseModel):
    hex: str | None = Field(default=None, pattern=r"^#?[0-9a-fA-F]{6}$")
    hsv: Hsv | None = None
