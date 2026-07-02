"""Shared test doubles for the Kasa-Nice backend.

These fakes mimic just the surface of ``python-kasa`` that the service and
serializer touch, so tests run with no real devices or network.
"""

import pytest
from kasa import Module

from api import config


@pytest.fixture(autouse=True)
def _isolated_settings():
    """Keep configuration hermetic across the whole suite.

    A developer's real repo-root ``.env`` must never change test outcomes, so we
    seed a settings instance built from the process environment only
    (``_env_file=None``) and drop it afterwards. Any code that reaches for
    ``get_settings()`` during a test therefore sees a clean, dotenv-free slate;
    tests that exercise env parsing build their own ``Settings`` and pass it in.
    """
    config.set_settings(config.Settings(_env_file=None))
    yield
    config.reset_settings()


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

    def __init__(self) -> None:
        # Count stats-table fetches so tests can assert the recorder's light
        # snapshot read never triggers them — only the /usage path should.
        self.daily_stats_calls = 0
        self.monthly_stats_calls = 0

    async def get_daily_stats(self, *, kwh: bool = True) -> dict[int, float]:
        self.daily_stats_calls += 1
        return {1: 0.1, 2: 0.25}

    async def get_monthly_stats(self, *, kwh: bool = True) -> dict[int, float]:
        self.monthly_stats_calls += 1
        return {1: 1.0, 6: 4.2}


class FakeChild:
    def __init__(
        self,
        alias: str,
        is_on: bool = False,
        device_id: str | None = None,
        *,
        renamable: bool = True,
    ):
        self.alias = alias
        self.is_on = is_on
        # python-kasa child devices carry a stable ``device_id`` (parent id + slot).
        # Optional so tests can also cover the alias fallback when it's absent.
        if device_id is not None:
            self.device_id = device_id
        # Real python-kasa children expose set_alias; the cloud façade's CloudChild
        # doesn't. Assigning it only when renamable lets tests exercise both the
        # rename path and the cloud-style rejection (hasattr is False otherwise).
        if renamable:
            self.set_alias = self._apply_alias

    async def _apply_alias(self, alias: str) -> None:
        self.alias = alias

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
        mac: str | None = None,
        renamable: bool = True,
    ):
        self.host = host
        self._fail_update = fail_update
        # Optional so most tests keep host-as-id (the MAC-absent fallback); tests
        # exercising stable ids pass a MAC to get MAC-based keying.
        if mac is not None:
            self.mac = mac
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
        # Mirror python-kasa: real devices expose set_alias, the cloud façade does
        # not. ``renamable=False`` models a cloud-only device so tests can cover
        # the can_rename=False serialization and the rename-rejection path.
        if renamable:
            self.set_alias = self._apply_alias

    async def _apply_alias(self, alias: str) -> None:
        self.alias = alias

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

    async def discover_single(self, host, *, credentials=None, **kwargs) -> FakeDevice:
        self.credentials = credentials
        entry = self.targets.get(host)
        if not entry:
            raise TimeoutError(host)  # no device at this address
        return next(iter(entry.values()))
