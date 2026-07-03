"""In-process stand-ins for ``python-kasa`` devices, no hardware or network.

Mimic just the surface the service and serializer touch. Two callers: the pytest
suite (``tests/conftest.py`` re-exports them) and the ``KASA_FAKE_DEVICES`` seam
that seeds the live registry for the browser e2e test. Kept outside ``tests/`` so
both share one definition.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kasa import Module

if TYPE_CHECKING:
    from ..kasa_service import DeviceRegistry


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
    consumption_today = 0.3
    consumption_this_month = 4.2
    voltage = 120.0

    def __init__(self, current_consumption: float = 12.5) -> None:
        # Instance (not class) attribute so each fake device can carry its own
        # wattage — the alerts e2e spec needs a device whose draw it controls
        # independently of every other metered fake.
        self.current_consumption = current_consumption
        # Count stats-table fetches so tests can assert the recorder's snapshot
        # read never triggers them — only /usage should.
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
        # python-kasa children carry a stable ``device_id`` (parent id + slot);
        # optional so tests can cover the alias fallback.
        if device_id is not None:
            self.device_id = device_id
        # Real children expose set_alias; CloudChild doesn't. Assign it only when
        # renamable so tests exercise both the rename and cloud-rejection paths.
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
        current_consumption: float = 12.5,
        children: list[FakeChild] | None = None,
        fail_update: bool = False,
        mac: str | None = None,
        renamable: bool = True,
        toggles_on_update: bool = False,
    ):
        self.host = host
        self._fail_update = fail_update
        # When set, every ``update`` flips ``is_on``. The KASA_FAKE_DEVICES seam
        # uses this to manufacture a server-side change the SSE stream surfaces
        # without a page reload (the smoke test's live-update case).
        self._toggles_on_update = toggles_on_update
        # Optional so most tests keep host-as-id; pass a MAC for MAC-based keying.
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
            self.modules[Module.Energy] = FakeEnergy(current_consumption)
        self.update_count = 0
        # Real devices expose set_alias, the cloud façade doesn't;
        # ``renamable=False`` models a cloud-only device for the rejection tests.
        if renamable:
            self.set_alias = self._apply_alias

    async def _apply_alias(self, alias: str) -> None:
        self.alias = alias

    async def update(self) -> None:
        if self._fail_update:
            raise RuntimeError("simulated update failure (auth/offline)")
        self.update_count += 1
        if self._toggles_on_update:
            self.is_on = not self.is_on

    async def turn_on(self) -> None:
        self.is_on = True

    async def turn_off(self) -> None:
        self.is_on = False


class FakeDiscover:
    """Drop-in for ``kasa.Discover``; serves canned results by target."""

    def __init__(self):
        self.broadcast: dict[str, FakeDevice] = {}
        self.targets: dict[str, dict[str, FakeDevice]] = {}
        self.credentials = None  # last value passed in

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


def _sample_devices() -> list[FakeDevice]:
    """A small, varied device set for the ``KASA_FAKE_DEVICES`` seam.

    A plain plug to toggle by hand, a colour bulb for variety, a plug that flips
    its own state on every read to drive the SSE live-update assertion, and two
    metered devices (realistic names/wattages) for the energy screenshots and the
    alerts e2e spec's power-threshold scenario.
    """
    return [
        FakeDevice("10.0.0.11", alias="Living Room Lamp", model="HS100"),
        FakeDevice(
            "10.0.0.12",
            alias="Reading Bulb",
            model="KL130",
            type_name="Bulb",
            is_on=True,
            is_color=True,
            is_dimmable=True,
        ),
        FakeDevice(
            "10.0.0.13",
            alias="Porch Light",
            model="HS100",
            toggles_on_update=True,
        ),
        FakeDevice(
            "10.0.0.14",
            alias="Office Desk",
            model="KP125",
            is_on=True,
            has_energy=True,
            current_consumption=42.0,
        ),
        # Steady, moderate draw so the alerts spec can set a threshold BELOW it
        # and observe a clean rising-edge "power_exceeded" alert on the very next
        # evaluator cycle, with no need to flip the device's wattage mid-test.
        FakeDevice(
            "10.0.0.15",
            alias="Kitchen Strip",
            model="KP303",
            type_name="Strip",
            is_on=True,
            has_energy=True,
            current_consumption=150.0,
        ),
    ]


def seed_registry(registry: DeviceRegistry) -> None:
    """Populate ``registry`` with the sample fakes instead of real discovery.

    Keys entries by ``stable_device_id`` (MAC-less fakes fall back to host), as
    discovery would. Imported here, not at module top, to avoid a circular import
    with ``kasa_service``.
    """
    from ..kasa_service import stable_device_id

    registry._devices = {stable_device_id(d): d for d in _sample_devices()}
