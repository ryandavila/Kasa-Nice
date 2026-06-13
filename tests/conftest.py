"""Shared test doubles for the Kasa-Nice backend.

These fakes mimic just the surface of ``python-kasa`` that the service and
serializer touch, so tests run with no real devices or network.
"""

from kasa import Module


class FakeLight:
    def __init__(
        self, brightness: int = 50, hsv: tuple[int, int, int] = (120, 100, 100)
    ):
        self.brightness = brightness
        self.hsv = hsv

    async def set_brightness(self, value: int) -> None:
        self.brightness = value

    async def set_hsv(self, h: int, s: int, v: int) -> None:
        self.hsv = (h, s, v)


class FakeEnergy:
    current_consumption = 12.5
    consumption_today = 0.3
    consumption_this_month = 4.2
    voltage = 120.0

    async def get_daily_stats(self, *, kwh: bool = True) -> dict[int, float]:
        return {1: 0.1, 2: 0.25}

    async def get_monthly_stats(self, *, kwh: bool = True) -> dict[int, float]:
        return {1: 1.0, 6: 4.2}


class FakeChild:
    def __init__(self, alias: str, is_on: bool = False):
        self.alias = alias
        self.is_on = is_on

    async def turn_on(self) -> None:
        self.is_on = True

    async def turn_off(self) -> None:
        self.is_on = False


class FakeDeviceType:
    def __init__(self, name: str):
        self.name = name


class FakeDevice:
    """Stand-in for a python-kasa Device."""

    def __init__(
        self,
        host: str,
        *,
        alias: str = "Test Device",
        model: str = "HS100",
        type_name: str = "Plug",
        is_on: bool = False,
        is_color: bool = False,
        is_dimmable: bool = False,
        has_energy: bool = False,
        children: list[FakeChild] | None = None,
        fail_update: bool = False,
    ):
        self.host = host
        self._fail_update = fail_update
        self.alias = alias
        self.model = model
        self.device_type = FakeDeviceType(type_name)
        self.is_on = is_on
        self.children = children or []
        self.sys_info = {"is_color": int(is_color), "is_dimmable": int(is_dimmable)}
        self.modules: dict = {}
        if is_dimmable or is_color:
            self.modules[Module.Light] = FakeLight()
        if has_energy:
            self.modules[Module.Energy] = FakeEnergy()
        self.update_count = 0

    async def update(self) -> None:
        if self._fail_update:
            raise RuntimeError("simulated update failure (auth/offline)")
        self.update_count += 1

    async def turn_on(self) -> None:
        self.is_on = True

    async def turn_off(self) -> None:
        self.is_on = False


class FakeDiscover:
    """Drop-in for ``kasa.Discover``; serves canned results by target."""

    def __init__(self):
        self.broadcast: dict[str, FakeDevice] = {}
        self.targets: dict[str, dict[str, FakeDevice]] = {}
        self.credentials = None  # records the last value passed in

    async def discover(
        self, target: str | None = None, credentials=None
    ) -> dict[str, FakeDevice]:
        self.credentials = credentials
        if target is None:
            return dict(self.broadcast)
        return dict(self.targets.get(target, {}))
