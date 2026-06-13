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


def test_offline_known_host_is_not_forgotten(tmp_path, fake_discover):
    store = HostStore(tmp_path / "hosts.json")
    store.save({"10.0.0.9"})
    fake_discover.broadcast = {}  # nothing on the network; host is offline

    reg = DeviceRegistry(store)
    asyncio.run(reg.discover_all())

    assert "10.0.0.9" not in reg._devices  # couldn't reach it now
    assert store.load() == {"10.0.0.9"}  # but still remembered for next time
