import asyncio

import pytest
from conftest import FakeChild, FakeDevice, FakeDiscover

from api import kasa_service
from api.device_store import HostStore
from api.kasa_service import (
    DeviceNotFoundError,
    DeviceRegistry,
    EnergyUnsupportedError,
    serialize_device,
)

# ── serialize_device ────────────────────────────────────────────────────────


def test_serialize_plain_plug():
    d = serialize_device(FakeDevice("10.0.0.2", alias="Lamp", is_on=True))
    assert d.id == "10.0.0.2"
    assert d.alias == "Lamp"
    assert d.is_on is True
    assert d.is_dimmable is False
    assert d.is_color is False
    assert d.has_emeter is False
    assert d.brightness is None
    assert d.hsv is None
    assert d.children == []


def test_serialize_color_bulb_includes_light_state():
    d = serialize_device(
        FakeDevice("10.0.0.3", is_color=True, is_dimmable=True, type_name="Bulb")
    )
    assert d.is_color is True
    assert d.is_dimmable is True
    assert d.brightness == 50
    assert d.hsv == (120, 100, 100)


def test_serialize_marks_emeter_from_energy_module():
    assert serialize_device(FakeDevice("10.0.0.4", has_energy=True)).has_emeter is True


def test_serialize_strip_children():
    strip = FakeDevice(
        "10.0.0.5",
        type_name="Strip",
        children=[FakeChild("Outlet 1", is_on=True), FakeChild("Outlet 2")],
    )
    d = serialize_device(strip)
    assert [c.alias for c in d.children] == ["Outlet 1", "Outlet 2"]
    assert [c.is_on for c in d.children] == [True, False]


def test_serialize_falls_back_to_host_when_alias_missing():
    dev = FakeDevice("10.0.0.6")
    dev.alias = ""
    assert serialize_device(dev).alias == "10.0.0.6"


# ── get_usage ───────────────────────────────────────────────────────────────


def test_get_usage_returns_live_and_history():
    reg = DeviceRegistry()
    reg._devices = {"10.0.0.7": FakeDevice("10.0.0.7", has_energy=True)}
    usage = asyncio.run(reg.get_usage("10.0.0.7"))
    assert usage.current_power_w == 12.5
    assert usage.today_kwh == 0.3
    assert usage.month_kwh == 4.2
    assert usage.voltage == 120.0
    assert [s.label for s in usage.daily] == ["1", "2"]
    assert [s.label for s in usage.monthly] == ["Jan", "Jun"]
    assert usage.monthly[1].kwh == 4.2


def test_get_usage_cost_is_null_without_a_rate():
    reg = DeviceRegistry()  # no energy rate configured
    reg._devices = {"10.0.0.7": FakeDevice("10.0.0.7", has_energy=True)}
    usage = asyncio.run(reg.get_usage("10.0.0.7"))
    assert usage.today_cost is None
    assert usage.month_cost is None
    assert all(s.cost is None for s in usage.daily)
    assert all(s.cost is None for s in usage.monthly)


def test_get_usage_computes_cost_from_flat_rate():
    reg = DeviceRegistry(energy_rate=0.2)
    reg._devices = {"10.0.0.7": FakeDevice("10.0.0.7", has_energy=True)}
    usage = asyncio.run(reg.get_usage("10.0.0.7"))
    # today_kwh=0.3, month_kwh=4.2 (from FakeEnergy) × $0.2/kWh.
    assert usage.today_cost == round(0.3 * 0.2, 2)
    assert usage.month_cost == round(4.2 * 0.2, 2)
    # Per-bar cost: daily {1: 0.1, 2: 0.25}; monthly {1: 1.0, 6: 4.2}.
    assert usage.daily[0].cost == round(0.1 * 0.2, 2)
    assert usage.monthly[1].cost == round(4.2 * 0.2, 2)


def test_get_usage_unknown_device():
    with pytest.raises(DeviceNotFoundError):
        asyncio.run(DeviceRegistry().get_usage("nope"))


def test_get_usage_without_emeter():
    reg = DeviceRegistry()
    reg._devices = {"10.0.0.8": FakeDevice("10.0.0.8")}
    with pytest.raises(EnergyUnsupportedError):
        asyncio.run(reg.get_usage("10.0.0.8"))


# ── persistence ─────────────────────────────────────────────────────────────


@pytest.fixture
def fake_discover(monkeypatch):
    fake = FakeDiscover()
    monkeypatch.setattr(kasa_service, "Discover", fake)
    return fake


def test_discover_target_persists_host(tmp_path, fake_discover):
    fake_discover.targets["10.0.0.5"] = {"10.0.0.5": FakeDevice("10.0.0.5")}
    store = HostStore(tmp_path / "hosts.json")
    reg = DeviceRegistry(store)

    asyncio.run(reg.discover_target("10.0.0.5"))

    assert "10.0.0.5" in reg._devices
    assert store.load() == {"10.0.0.5"}


def test_known_host_reattached_on_startup(tmp_path, fake_discover):
    # A previous session persisted a host that broadcast won't find.
    store = HostStore(tmp_path / "hosts.json")
    store.save({"10.0.0.5"})
    fake_discover.broadcast = {}
    fake_discover.targets["10.0.0.5"] = {"10.0.0.5": FakeDevice("10.0.0.5")}

    reg = DeviceRegistry(store)
    asyncio.run(reg.discover_all())

    assert "10.0.0.5" in reg._devices


def test_run_startup_discovery_toggles_discovering_flag(fake_discover):
    fake_discover.broadcast = {"10.0.0.1": FakeDevice("10.0.0.1")}
    reg = DeviceRegistry()
    seen = {}
    original = reg.discover_all

    async def spy():
        seen["during"] = reg.discovering  # flag must be set while sweeping
        return await original()

    reg.discover_all = spy
    asyncio.run(reg.run_startup_discovery())

    assert seen["during"] is True
    assert reg.discovering is False  # cleared when done
    assert "10.0.0.1" in reg._devices


def test_run_startup_discovery_never_raises_and_clears_flag():
    reg = DeviceRegistry()

    async def boom():
        raise RuntimeError("network down")

    reg.discover_all = boom
    asyncio.run(reg.run_startup_discovery())  # must not propagate

    assert reg.discovering is False


def test_offline_known_host_is_not_forgotten(tmp_path, fake_discover):
    store = HostStore(tmp_path / "hosts.json")
    store.save({"10.0.0.9"})
    fake_discover.broadcast = {}  # nothing on the network; host is offline

    reg = DeviceRegistry(store)
    asyncio.run(reg.discover_all())

    assert "10.0.0.9" not in reg._devices  # couldn't reach it now
    assert store.load() == {"10.0.0.9"}  # but still remembered for next time


# ── resilience: an un-readable device must not break the list ────────────────


def test_unreadable_target_is_not_served(tmp_path, fake_discover):
    # A device answers discovery but can't be read (e.g. wrong credentials).
    fake_discover.targets["10.0.0.5"] = {
        "10.0.0.5": FakeDevice("10.0.0.5", fail_update=True)
    }
    reg = DeviceRegistry(HostStore(tmp_path / "hosts.json"))

    found = asyncio.run(reg.discover_target("10.0.0.5"))

    assert found == []  # not returned to the caller
    assert "10.0.0.5" not in reg._devices  # and never cached/served


def test_one_unreadable_device_does_not_drop_the_others(fake_discover):
    # Reproduces the 500: a device that raises on update() would also raise on
    # serialize_device(); it must be excluded so the rest still render.
    fake_discover.broadcast = {
        "10.0.0.1": FakeDevice("10.0.0.1", alias="Good"),
        "10.0.0.2": FakeDevice("10.0.0.2", alias="Bad", fail_update=True),
    }
    reg = DeviceRegistry()

    asyncio.run(reg.discover_all())

    assert set(reg._devices) == {"10.0.0.1"}
    assert [serialize_device(d).alias for d in reg.all()] == ["Good"]


# ── subnet sweep ─────────────────────────────────────────────────────────────


def test_discover_subnet_finds_caches_and_skips_unreadable(tmp_path, fake_discover):
    # Two hosts in a /30; one reads back, one fails update (e.g. wrong creds).
    fake_discover.targets["10.0.0.1"] = {"10.0.0.1": FakeDevice("10.0.0.1", alias="A")}
    fake_discover.targets["10.0.0.2"] = {
        "10.0.0.2": FakeDevice("10.0.0.2", alias="B", fail_update=True)
    }
    store = HostStore(tmp_path / "hosts.json")
    reg = DeviceRegistry(store)

    found = asyncio.run(reg.discover_subnet("10.0.0.0/30"))

    assert {d.host for d in found} == {"10.0.0.1"}  # B skipped, A served
    assert set(reg._devices) == {"10.0.0.1"}
    assert store.load() == {"10.0.0.1"}  # only the readable host is persisted


def test_discover_subnet_rejects_bad_cidr():
    with pytest.raises(ValueError, match="Invalid subnet"):
        asyncio.run(DeviceRegistry().discover_subnet("not-a-subnet"))
