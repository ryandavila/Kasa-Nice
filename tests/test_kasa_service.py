import asyncio

import pytest
from conftest import FakeChild, FakeDevice, FakeDiscover

from api import kasa_service
from api.device_store import DeviceSnapshotStore, HostStore
from api.energy_history import EnergyHistoryStore
from api.group_store import GroupStore
from api.kasa_service import (
    DeviceNotFoundError,
    DeviceRegistry,
    EnergyUnsupportedError,
    serialize_device,
)

# ── serialize_device ────────────────────────────────────────────────────────


def test_serialize_plain_plug():
    d = serialize_device(FakeDevice("10.0.0.2", alias="Lamp", is_on=True))
    assert d.id == "10.0.0.2"  # no MAC on the fake -> falls back to host as id
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


def test_serialize_uses_normalized_mac_as_id():
    # A MAC is the durable identity; host stays a separate connection/display field.
    d = serialize_device(FakeDevice("10.0.0.2", mac="AA:BB:CC:DD:EE:01"))
    assert d.id == "AABBCCDDEE01"
    assert d.host == "10.0.0.2"


def test_serialize_child_uses_stable_device_id():
    strip = FakeDevice(
        "10.0.0.5",
        type_name="Strip",
        children=[
            FakeChild("Outlet 1", is_on=True, device_id="STRIP_00"),
            FakeChild("Outlet 2", device_id="STRIP_01"),
        ],
    )
    d = serialize_device(strip)
    assert [c.id for c in d.children] == ["STRIP_00", "STRIP_01"]
    assert [c.alias for c in d.children] == [
        "Outlet 1",
        "Outlet 2",
    ]  # alias for display


def test_serialize_child_falls_back_to_alias_without_device_id():
    strip = FakeDevice("10.0.0.5", type_name="Strip", children=[FakeChild("Outlet 1")])
    assert serialize_device(strip).children[0].id == "Outlet 1"


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


# ── cloud poll throttling ────────────────────────────────────────────────────


def test_refresh_all_throttles_cloud_but_refreshes_local_every_poll():
    reg = DeviceRegistry()
    reg._cloud_poll_interval = 9999  # effectively never re-poll cloud in the test
    local = FakeDevice("10.0.0.1")
    cloud = FakeDevice("cloud-1")
    reg._devices = {local.host: local}
    reg._cloud_devices = {cloud.host: cloud}

    for _ in range(3):
        asyncio.run(reg.refresh_all())

    assert local.update_count == 3  # local refreshed every poll
    assert cloud.update_count == 1  # cloud refreshed once, then throttled


def test_refresh_all_polls_cloud_again_once_interval_elapses():
    reg = DeviceRegistry()
    reg._cloud_poll_interval = 0  # no throttle: cloud refreshes every poll
    cloud = FakeDevice("cloud-1")
    reg._cloud_devices = {cloud.host: cloud}

    asyncio.run(reg.refresh_all())
    asyncio.run(reg.refresh_all())

    assert cloud.update_count == 2


def test_refresh_all_returns_cloud_devices_even_when_throttled():
    reg = DeviceRegistry()
    reg._cloud_poll_interval = 9999
    reg._cloud_devices = {"cloud-1": FakeDevice("cloud-1")}

    asyncio.run(reg.refresh_all())  # first poll refreshes + stamps
    returned = asyncio.run(reg.refresh_all())  # second skips the refresh

    assert [d.host for d in returned] == ["cloud-1"]  # still listed for the UI


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


# ── stable child power matching ──────────────────────────────────────────────


def _strip_registry(child: FakeChild) -> DeviceRegistry:
    reg = DeviceRegistry()
    reg._devices = {"s": FakeDevice("s", type_name="Strip", children=[child])}
    return reg


def test_set_child_power_matches_stable_id():
    child = FakeChild("Outlet 1", device_id="STRIP_00")
    reg = _strip_registry(child)
    asyncio.run(reg.set_child_power("s", "STRIP_00", True))
    assert child.is_on is True


def test_set_child_power_alias_fallback_for_legacy_ids():
    # Ids saved by an older client addressed outlets by alias; keep them working.
    child = FakeChild("Outlet 1", device_id="STRIP_00")
    reg = _strip_registry(child)
    asyncio.run(reg.set_child_power("s", "Outlet 1", True))
    assert child.is_on is True


def test_set_child_power_unknown_child_raises():
    reg = _strip_registry(FakeChild("Outlet 1", device_id="STRIP_00"))
    with pytest.raises(DeviceNotFoundError):
        asyncio.run(reg.set_child_power("s", "nope", True))


# ── stable MAC-based ids in the registry ─────────────────────────────────────


def test_registry_keys_device_by_stable_mac_id(fake_discover):
    fake_discover.broadcast = {
        "10.0.0.5": FakeDevice("10.0.0.5", mac="AA:BB:CC:DD:EE:01")
    }
    reg = DeviceRegistry()
    asyncio.run(reg.discover_all())

    assert set(reg._devices) == {"AABBCCDDEE01"}  # keyed by MAC, not host
    assert reg.get("AABBCCDDEE01").host == "10.0.0.5"  # host kept for connection


def test_registry_persists_host_not_stable_id(tmp_path, fake_discover):
    fake_discover.targets["10.0.0.5"] = {
        "10.0.0.5": FakeDevice("10.0.0.5", mac="AA:BB:CC:DD:EE:01")
    }
    store = HostStore(tmp_path / "hosts.json")
    reg = DeviceRegistry(store)

    asyncio.run(reg.discover_target("10.0.0.5"))

    # The host store keeps the IP (to re-probe), even though the id is the MAC.
    assert store.load() == {"10.0.0.5"}


def test_rediscovery_at_new_ip_keeps_one_identity(fake_discover):
    reg = DeviceRegistry()
    fake_discover.broadcast = {
        "10.0.0.5": FakeDevice("10.0.0.5", mac="AA:BB:CC:DD:EE:01")
    }
    asyncio.run(reg.discover_all())
    # DHCP hands the same device a new IP; it must update its slot, not fork.
    fake_discover.broadcast = {
        "10.0.0.9": FakeDevice("10.0.0.9", mac="AA:BB:CC:DD:EE:01")
    }
    asyncio.run(reg.discover_all())

    assert set(reg._devices) == {"AABBCCDDEE01"}
    assert reg.get("AABBCCDDEE01").host == "10.0.0.9"


# ── one-time lazy migration of IP-keyed data ─────────────────────────────────


def test_discovery_migrates_ip_keyed_group_and_history(tmp_path, fake_discover):
    gs = GroupStore(tmp_path / "groups.json")
    hs = EnergyHistoryStore(tmp_path / "energy.db")
    room = gs.create_group("Living Room")
    gs.update_group(room["id"], device_ids=["10.0.0.5"])
    gs.set_favorites(["10.0.0.5"])
    hs.record("10.0.0.5", 5.0, 0.1, 1.0)

    fake_discover.broadcast = {
        "10.0.0.5": FakeDevice("10.0.0.5", mac="AA:BB:CC:DD:EE:01")
    }
    reg = DeviceRegistry(group_store=gs, history_store=hs)
    asyncio.run(reg.discover_all())

    assert gs.list_groups()[0]["device_ids"] == ["AABBCCDDEE01"]  # room follows
    assert gs.get_favorites() == ["AABBCCDDEE01"]  # star follows
    assert hs.recent_samples("AABBCCDDEE01", 0)  # history re-keyed
    assert hs.recent_samples("10.0.0.5", 0) == []  # nothing left under the old IP


def test_migration_runs_at_most_once_per_process(tmp_path, fake_discover):
    gs = GroupStore(tmp_path / "groups.json")
    hs = EnergyHistoryStore(tmp_path / "energy.db")
    fake_discover.broadcast = {
        "10.0.0.5": FakeDevice("10.0.0.5", mac="AA:BB:CC:DD:EE:01")
    }
    reg = DeviceRegistry(group_store=gs, history_store=hs)
    asyncio.run(reg.discover_all())  # migrates (nothing to move yet), sets the guard

    # A stale IP-keyed favorite reappears; the guard means it is NOT touched again.
    gs.set_favorites(["10.0.0.5"])
    asyncio.run(reg.discover_all())

    assert gs.get_favorites() == ["10.0.0.5"]


# ── known-but-unreachable devices stay visible ───────────────────────────────


def _reg_with_stores(tmp_path) -> DeviceRegistry:
    return DeviceRegistry(
        HostStore(tmp_path / "hosts.json"),
        snapshot_store=DeviceSnapshotStore(tmp_path / "snap.json"),
    )


def test_serialize_marks_live_device_reachable():
    # Existing consumers rely on live devices defaulting to reachable=True.
    assert serialize_device(FakeDevice("10.0.0.2")).reachable is True


def test_unreachable_device_served_from_snapshot(tmp_path, fake_discover):
    # A device is read once (so a snapshot is stored), then a re-scan finds nothing.
    fake_discover.broadcast = {
        "10.0.0.5": FakeDevice("10.0.0.5", alias="Lamp", model="HS100")
    }
    reg = _reg_with_stores(tmp_path)
    asyncio.run(reg.discover_all())

    fake_discover.broadcast = {}  # the device drops off the network
    asyncio.run(reg.discover_all())

    assert "10.0.0.5" not in reg._devices  # no longer live
    unreachable = reg.unreachable_devices()
    assert [d.host for d in unreachable] == ["10.0.0.5"]
    assert unreachable[0].alias == "Lamp"  # identity from the snapshot
    assert unreachable[0].model == "HS100"
    assert unreachable[0].reachable is False


def test_unreachable_snapshot_keeps_stable_mac_id(tmp_path, fake_discover):
    # The snapshot must carry the SAME stable id as the live device, so rooms and
    # favorites keyed to it still match while it's offline.
    fake_discover.broadcast = {
        "10.0.0.5": FakeDevice("10.0.0.5", mac="AA:BB:CC:DD:EE:01")
    }
    reg = _reg_with_stores(tmp_path)
    asyncio.run(reg.discover_all())
    fake_discover.broadcast = {}
    asyncio.run(reg.discover_all())

    assert [d.id for d in reg.unreachable_devices()] == ["AABBCCDDEE01"]


def test_unreachable_snapshot_survives_a_restart(tmp_path, fake_discover):
    # A fresh registry (new process) seeds snapshots from disk, so a host that's
    # offline at startup is still shown from its last-known identity.
    fake_discover.broadcast = {"10.0.0.5": FakeDevice("10.0.0.5", alias="Lamp")}
    reg = _reg_with_stores(tmp_path)
    asyncio.run(reg.discover_all())

    fake_discover.broadcast = {}
    reg2 = _reg_with_stores(tmp_path)  # simulate a restart against the same files
    asyncio.run(reg2.discover_all())

    unreachable = reg2.unreachable_devices()
    assert [d.alias for d in unreachable] == ["Lamp"]  # restored from disk


def test_never_read_host_falls_back_to_host_identity(tmp_path, fake_discover):
    # A host persisted by a previous session that never answered: no snapshot, so
    # host-only identity with the host as a deterministic placeholder id.
    store = HostStore(tmp_path / "hosts.json")
    store.save({"10.0.0.9"})
    fake_discover.broadcast = {}
    reg = DeviceRegistry(store, snapshot_store=DeviceSnapshotStore(tmp_path / "s.json"))
    asyncio.run(reg.discover_all())

    unreachable = reg.unreachable_devices()
    assert len(unreachable) == 1
    assert unreachable[0].id == "10.0.0.9"
    assert unreachable[0].alias == "10.0.0.9"
    assert unreachable[0].device_type == "Unknown"
    assert unreachable[0].reachable is False


def test_ip_change_does_not_emit_a_phantom_unreachable_twin(tmp_path, fake_discover):
    # The device is live at a new IP under its stable MAC id; its old IP lingers in
    # the host store but must NOT surface as a separate unreachable device.
    reg = _reg_with_stores(tmp_path)
    fake_discover.broadcast = {
        "10.0.0.5": FakeDevice("10.0.0.5", mac="AA:BB:CC:DD:EE:01")
    }
    asyncio.run(reg.discover_all())
    fake_discover.broadcast = {
        "10.0.0.9": FakeDevice("10.0.0.9", mac="AA:BB:CC:DD:EE:01")
    }
    asyncio.run(reg.discover_all())

    assert reg.unreachable_devices() == []  # no twin for the stale 10.0.0.5


def test_control_on_unreachable_id_fails_cleanly(tmp_path, fake_discover):
    # An unreachable device isn't in the registry, so control raises immediately
    # (a clean 404 at the route) rather than hanging on a network timeout.
    fake_discover.broadcast = {"10.0.0.5": FakeDevice("10.0.0.5", alias="Lamp")}
    reg = _reg_with_stores(tmp_path)
    asyncio.run(reg.discover_all())
    fake_discover.broadcast = {}
    asyncio.run(reg.discover_all())

    unreachable_id = reg.unreachable_devices()[0].id
    with pytest.raises(DeviceNotFoundError):
        asyncio.run(reg.set_power(unreachable_id, True))


def test_recovery_flips_unreachable_back_to_reachable(tmp_path, fake_discover):
    # Probing the host again (the retry path) makes it live and clears it from the
    # unreachable list.
    fake_discover.broadcast = {"10.0.0.5": FakeDevice("10.0.0.5", alias="Lamp")}
    reg = _reg_with_stores(tmp_path)
    asyncio.run(reg.discover_all())
    fake_discover.broadcast = {}
    asyncio.run(reg.discover_all())
    assert reg.unreachable_devices()  # currently offline

    # The device answers a single-target probe again.
    fake_discover.targets["10.0.0.5"] = {"10.0.0.5": FakeDevice("10.0.0.5")}
    asyncio.run(reg.discover_target("10.0.0.5"))

    assert "10.0.0.5" in reg._devices
    assert reg.unreachable_devices() == []
    assert serialize_device(reg.get("10.0.0.5")).reachable is True


def test_migration_never_crashes_discovery(tmp_path, fake_discover):
    class BoomStore:
        def migrate_device_id(self, *_a):
            raise RuntimeError("store on fire")

    fake_discover.broadcast = {
        "10.0.0.5": FakeDevice("10.0.0.5", mac="AA:BB:CC:DD:EE:01")
    }
    reg = DeviceRegistry(group_store=BoomStore(), history_store=BoomStore())

    asyncio.run(reg.discover_all())  # must not raise despite the failing stores

    assert reg.get("AABBCCDDEE01").host == "10.0.0.5"
