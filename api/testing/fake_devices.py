"""In-process stand-ins for ``python-kasa`` devices, with no hardware or network.

These fakes mimic just the surface of ``python-kasa`` that the service and
serializer touch. They serve two callers:

* the pytest suite (``tests/conftest.py`` re-exports them), and
* the ``KASA_FAKE_DEVICES`` runtime seam, which seeds the live registry with a
  few of them so the browser end-to-end smoke test can drive real API wiring
  without any Kasa hardware or cloud credentials.

Kept outside ``tests/`` so both callers share one definition instead of
duplicating it.
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
        toggles_on_update: bool = False,
    ):
        self.host = host
        self._fail_update = fail_update
        # When set, every state read (``update``) flips ``is_on``. Used by the
        # KASA_FAKE_DEVICES seam to manufacture a server-side change the browser
        # never initiated: the SSE stream re-reads devices on its own interval,
        # so this device's card toggles live — the exact thing the smoke test
        # asserts arrives over SSE without a page reload.
        self._toggles_on_update = toggles_on_update
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


def _sample_devices() -> list[FakeDevice]:
    """A small, varied device set for the ``KASA_FAKE_DEVICES`` seam.

    Covers the shapes the smoke test needs: a plain plug to toggle by hand, a
    dimmable colour bulb for card variety, and a plug that flips its own state on
    every read to drive the SSE live-update assertion.
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
    ]


def seed_registry(registry: DeviceRegistry) -> None:
    """Populate ``registry`` with the sample fakes instead of real discovery.

    Keys entries by ``stable_device_id`` (the MAC-less fakes fall back to their
    host), matching how discovery would register them. This seam lives inside
    the package and deliberately bypasses the network discovery path that
    ``run_startup_discovery`` would otherwise run. Imported here (not at module
    top) to avoid a circular import with ``kasa_service``.
    """
    from ..kasa_service import stable_device_id

    registry._devices = {stable_device_id(d): d for d in _sample_devices()}
