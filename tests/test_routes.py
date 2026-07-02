from unittest.mock import AsyncMock

import pytest
from conftest import FakeChild, FakeDevice
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import routes
from api.group_store import GroupStore
from api.kasa_service import DeviceRegistry


@pytest.fixture
def client(monkeypatch):
    reg = DeviceRegistry()
    reg._devices = {
        "10.0.0.1": FakeDevice("10.0.0.1", alias="Plug"),
        "10.0.0.2": FakeDevice(
            "10.0.0.2", alias="Bulb", is_color=True, is_dimmable=True
        ),
        "10.0.0.3": FakeDevice(
            "10.0.0.3",
            alias="Strip",
            type_name="Strip",
            children=[FakeChild("Outlet 1"), FakeChild("Outlet 2")],
        ),
        "10.0.0.4": FakeDevice("10.0.0.4", alias="Meter", has_energy=True),
    }
    monkeypatch.setattr(routes, "registry", reg)

    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_config_defaults_have_no_energy_rate(client):
    body = client.get("/api/config").json()
    assert body["scan_subnet"] is None
    assert body["energy_rate"] is None
    assert body["energy_currency"] == "$"


def test_config_exposes_configured_energy_rate(monkeypatch):
    reg = DeviceRegistry(energy_rate=0.2, energy_currency="€")
    monkeypatch.setattr(routes, "registry", reg)
    app = FastAPI()
    app.include_router(routes.router)
    body = TestClient(app).get("/api/config").json()
    assert body["energy_rate"] == 0.2
    assert body["energy_currency"] == "€"


def test_status_reports_idle_and_device_count(client):
    body = client.get("/api/status").json()
    assert body["discovering"] is False
    assert body["device_count"] == 4


def test_status_reflects_active_discovery(monkeypatch):
    reg = DeviceRegistry()
    reg.discovering = True
    monkeypatch.setattr(routes, "registry", reg)
    app = FastAPI()
    app.include_router(routes.router)
    body = TestClient(app).get("/api/status").json()
    assert body["discovering"] is True
    assert body["device_count"] == 0


def test_list_devices(client):
    body = client.get("/api/devices").json()
    assert {d["id"] for d in body} == {"10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"}


def _client_with(reg, monkeypatch) -> TestClient:
    monkeypatch.setattr(routes, "registry", reg)
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_devices_endpoint_includes_unreachable_devices(tmp_path, monkeypatch):
    from api.device_store import DeviceSnapshotStore, HostStore

    reg = DeviceRegistry(
        HostStore(tmp_path / "hosts.json"),
        snapshot_store=DeviceSnapshotStore(tmp_path / "snap.json"),
    )
    reg._devices = {"10.0.0.1": FakeDevice("10.0.0.1", alias="Live")}
    # A persisted host with no live device and no snapshot -> host-only identity.
    reg._store.save({"10.0.0.1", "10.0.0.9"})
    client = _client_with(reg, monkeypatch)

    body = client.get("/api/devices").json()
    by_id = {d["id"]: d for d in body}
    assert by_id["10.0.0.1"]["reachable"] is True
    assert by_id["10.0.0.9"]["reachable"] is False  # shown, greyed


def test_control_on_unreachable_id_returns_404(tmp_path, monkeypatch):
    from api.device_store import DeviceSnapshotStore, HostStore

    reg = DeviceRegistry(
        HostStore(tmp_path / "hosts.json"),
        snapshot_store=DeviceSnapshotStore(tmp_path / "snap.json"),
    )
    reg._store.save({"10.0.0.9"})  # known but never live: unreachable
    client = _client_with(reg, monkeypatch)

    # Must fail fast with a clean 404, not hang on a network timeout.
    r = client.post("/api/devices/10.0.0.9/power", json={"on": True})
    assert r.status_code == 404


def test_discover_broadcast_also_refreshes_cloud_devices(monkeypatch):
    # Broadcast discovery (no target) must re-attach cloud devices and return
    # them alongside local ones, so a strip onboarded later appears without a
    # restart. Stub the network-touching methods; all() returns both buckets.
    reg = DeviceRegistry()
    reg._devices = {"10.0.0.1": FakeDevice("10.0.0.1", alias="Plug")}
    reg._cloud_devices = {
        "10.0.0.9": FakeDevice("10.0.0.9", alias="Strip", type_name="Strip")
    }
    reg.discover_all = AsyncMock(return_value=list(reg._devices.values()))
    reg.attach_cloud = AsyncMock(return_value=list(reg._cloud_devices.values()))
    monkeypatch.setattr(routes, "registry", reg)
    app = FastAPI()
    app.include_router(routes.router)

    body = TestClient(app).post("/api/discover", json={}).json()

    reg.discover_all.assert_awaited_once()
    reg.attach_cloud.assert_awaited_once()
    assert {d["id"] for d in body} == {"10.0.0.1", "10.0.0.9"}  # local + cloud


def test_discover_target_does_not_attach_cloud(monkeypatch):
    # A targeted single-IP probe is unchanged: discover_target only, no cloud
    # re-attach and no broadcast.
    reg = DeviceRegistry()
    reg.discover_target = AsyncMock(
        return_value=[FakeDevice("10.0.0.5", alias="Found")]
    )
    reg.discover_all = AsyncMock()
    reg.attach_cloud = AsyncMock()
    monkeypatch.setattr(routes, "registry", reg)
    app = FastAPI()
    app.include_router(routes.router)

    body = TestClient(app).post("/api/discover", json={"target": "10.0.0.5"}).json()

    reg.discover_target.assert_awaited_once_with("10.0.0.5")
    reg.discover_all.assert_not_awaited()
    reg.attach_cloud.assert_not_awaited()
    assert {d["id"] for d in body} == {"10.0.0.5"}


def test_toggle_power(client):
    r = client.post("/api/devices/10.0.0.1/power", json={"on": True})
    assert r.status_code == 200
    assert r.json()["is_on"] is True


def test_control_action_nudges_broadcaster(client, monkeypatch):
    # A successful control action should push a fresh frame to other clients
    # immediately instead of leaving them to wait for the next refresh tick.
    nudge = AsyncMock()
    monkeypatch.setattr(routes.broadcaster, "publish_now", nudge)
    assert (
        client.post("/api/devices/10.0.0.1/power", json={"on": True}).status_code == 200
    )
    nudge.assert_awaited_once()


def test_failed_control_action_does_not_nudge(client, monkeypatch):
    # An unknown device 404s before any state changes, so there's nothing to push.
    nudge = AsyncMock()
    monkeypatch.setattr(routes.broadcaster, "publish_now", nudge)
    assert (
        client.post("/api/devices/9.9.9.9/power", json={"on": True}).status_code == 404
    )
    nudge.assert_not_awaited()


def test_power_unknown_device_404(client):
    r = client.post("/api/devices/9.9.9.9/power", json={"on": True})
    assert r.status_code == 404


def test_set_brightness(client):
    r = client.post("/api/devices/10.0.0.2/brightness", json={"value": 30})
    assert r.status_code == 200
    assert r.json()["brightness"] == 30


def test_brightness_on_non_dimmable_404(client):
    r = client.post("/api/devices/10.0.0.1/brightness", json={"value": 30})
    assert r.status_code == 404


def test_brightness_out_of_range_422(client):
    r = client.post("/api/devices/10.0.0.2/brightness", json={"value": 150})
    assert r.status_code == 422


def test_set_color_by_hex(client):
    r = client.post("/api/devices/10.0.0.2/color", json={"hex": "#ff0000"})
    assert r.status_code == 200
    assert r.json()["hsv"] == [0, 100, 100]


def test_color_requires_hex_or_hsv(client):
    r = client.post("/api/devices/10.0.0.2/color", json={})
    assert r.status_code == 422


def test_child_power(client):
    r = client.post("/api/devices/10.0.0.3/children/Outlet 1/power", json={"on": True})
    assert r.status_code == 200
    children = {c["alias"]: c["is_on"] for c in r.json()["children"]}
    assert children["Outlet 1"] is True
    assert children["Outlet 2"] is False


def test_child_power_unknown_child_404(client):
    r = client.post("/api/devices/10.0.0.3/children/Nope/power", json={"on": True})
    assert r.status_code == 404


# ── Rename (device & outlet) ──────────────────────────────────────────────────


def test_rename_device(client):
    r = client.patch("/api/devices/10.0.0.1", json={"alias": "Desk Lamp"})
    assert r.status_code == 200
    assert r.json()["alias"] == "Desk Lamp"
    # The stable id is MAC/host-based, so it must NOT change with the alias.
    assert r.json()["id"] == "10.0.0.1"


def test_rename_device_trims_whitespace(client):
    r = client.patch("/api/devices/10.0.0.1", json={"alias": "  Hallway  "})
    assert r.status_code == 200
    assert r.json()["alias"] == "Hallway"


def test_rename_device_nudges_broadcaster(client, monkeypatch):
    nudge = AsyncMock()
    monkeypatch.setattr(routes.broadcaster, "publish_now", nudge)
    assert (
        client.patch("/api/devices/10.0.0.1", json={"alias": "New"}).status_code == 200
    )
    nudge.assert_awaited_once()


def test_rename_unknown_device_404(client):
    r = client.patch("/api/devices/9.9.9.9", json={"alias": "Nope"})
    assert r.status_code == 404


def test_rename_empty_alias_422(client):
    assert client.patch("/api/devices/10.0.0.1", json={"alias": ""}).status_code == 422


def test_rename_whitespace_only_alias_422(client):
    # Passes min_length but is not a real label, so the validator rejects it.
    assert (
        client.patch("/api/devices/10.0.0.1", json={"alias": "   "}).status_code == 422
    )


def test_rename_child(client):
    r = client.patch("/api/devices/10.0.0.3/children/Outlet 1", json={"alias": "Lamp"})
    assert r.status_code == 200
    aliases = {c["alias"] for c in r.json()["children"]}
    assert aliases == {"Lamp", "Outlet 2"}


def test_rename_unknown_child_404(client):
    r = client.patch("/api/devices/10.0.0.3/children/Nope", json={"alias": "X"})
    assert r.status_code == 404


def test_local_device_can_rename_flag_true(client):
    body = client.get("/api/devices").json()
    plug = next(d for d in body if d["id"] == "10.0.0.1")
    assert plug["can_rename"] is True


def test_cloud_device_reports_cannot_rename_and_rejects_patch(monkeypatch):
    # A cloud-only device (no set_alias) must advertise can_rename=False and a
    # rename attempt must fail fast with a 501 — never hang or 500.
    reg = DeviceRegistry()
    reg._cloud_devices = {
        "CLOUDMAC": FakeDevice(
            "10.0.0.9", alias="Strip", type_name="Strip", renamable=False
        )
    }
    monkeypatch.setattr(routes, "registry", reg)
    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)

    body = c.get("/api/devices").json()
    assert body[0]["can_rename"] is False

    r = c.patch("/api/devices/CLOUDMAC", json={"alias": "Bench"})
    assert r.status_code == 501


def test_rename_cloud_child_rejected_501(monkeypatch):
    reg = DeviceRegistry()
    reg._cloud_devices = {
        "CLOUDMAC": FakeDevice(
            "10.0.0.9",
            alias="Strip",
            type_name="Strip",
            renamable=False,
            children=[FakeChild("Outlet 1", renamable=False)],
        )
    }
    monkeypatch.setattr(routes, "registry", reg)
    app = FastAPI()
    app.include_router(routes.router)
    r = TestClient(app).patch(
        "/api/devices/CLOUDMAC/children/Outlet 1", json={"alias": "Lamp"}
    )
    assert r.status_code == 501


def test_usage(client):
    r = client.get("/api/devices/10.0.0.4/usage")
    assert r.status_code == 200
    body = r.json()
    assert body["current_power_w"] == 12.5
    assert [s["label"] for s in body["monthly"]] == ["Jan", "Jun"]


def test_usage_without_emeter_404(client):
    assert client.get("/api/devices/10.0.0.1/usage").status_code == 404


# ── Whole-home energy summary ─────────────────────────────────────────────────


def _summary_app(reg, monkeypatch):
    monkeypatch.setattr(routes, "registry", reg)
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_energy_summary_sums_metered_devices(monkeypatch):
    # Two metered devices; non-metered devices are excluded from every total.
    reg = DeviceRegistry()
    reg._devices = {
        "10.0.0.1": FakeDevice("10.0.0.1", alias="Plug"),  # no emeter
        "10.0.0.4": FakeDevice("10.0.0.4", alias="Meter", has_energy=True),
        "10.0.0.5": FakeDevice("10.0.0.5", alias="Meter2", has_energy=True),
    }
    body = _summary_app(reg, monkeypatch).get("/api/energy/summary").json()
    # FakeEnergy: 12.5 W, 0.3 kWh today, 4.2 kWh month — summed over two meters.
    assert body["device_count"] == 2
    assert body["total_power_w"] == 25.0
    assert body["today_kwh"] == 0.6
    assert body["month_kwh"] == 8.4


def test_energy_summary_skips_erroring_device(monkeypatch):
    # One meter errors on read; the other still counts and no 500 is raised.
    reg = DeviceRegistry()
    reg._devices = {
        "10.0.0.4": FakeDevice("10.0.0.4", alias="Meter", has_energy=True),
        "10.0.0.5": FakeDevice("10.0.0.5", alias="Broken", has_energy=True),
    }
    real_get_usage = reg.get_usage

    async def flaky(device_id):
        if device_id == "10.0.0.5":
            raise RuntimeError("device offline")
        return await real_get_usage(device_id)

    monkeypatch.setattr(reg, "get_usage", flaky)
    r = _summary_app(reg, monkeypatch).get("/api/energy/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["device_count"] == 1
    assert body["today_kwh"] == 0.3


def test_energy_summary_costs_null_without_rate(monkeypatch):
    reg = DeviceRegistry()
    reg._devices = {"10.0.0.4": FakeDevice("10.0.0.4", has_energy=True)}
    body = _summary_app(reg, monkeypatch).get("/api/energy/summary").json()
    assert body["today_cost"] is None
    assert body["month_cost"] is None


def test_energy_summary_costs_populated_with_rate(monkeypatch):
    reg = DeviceRegistry(energy_rate=0.25)
    reg._devices = {"10.0.0.4": FakeDevice("10.0.0.4", has_energy=True)}
    body = _summary_app(reg, monkeypatch).get("/api/energy/summary").json()
    assert body["today_cost"] == round(0.3 * 0.25, 2)
    assert body["month_cost"] == round(4.2 * 0.25, 2)


def test_energy_summary_empty_registry_is_zeros(monkeypatch):
    body = _summary_app(DeviceRegistry(), monkeypatch).get("/api/energy/summary").json()
    assert body == {
        "total_power_w": 0.0,
        "today_kwh": 0.0,
        "month_kwh": 0.0,
        "today_cost": None,
        "month_cost": None,
        "device_count": 0,
    }


# ── Room & global power fan-out ───────────────────────────────────────────────


async def _boom() -> None:
    raise RuntimeError("device offline")


@pytest.fixture
def groups_store(monkeypatch, tmp_path):
    """A fresh, isolated group store wired into the routes for room-power tests."""
    store = GroupStore(tmp_path / "groups.json")
    monkeypatch.setattr(routes, "groups", store)
    return store


def _make_group(store, device_ids):
    g = store.create_group("Living Room")
    return store.update_group(g["id"], device_ids=device_ids)["id"]


def test_group_power_all_succeed(client, groups_store):
    gid = _make_group(groups_store, ["10.0.0.1", "10.0.0.2"])
    r = client.post(f"/api/groups/{gid}/power", json={"on": True})
    assert r.status_code == 200
    assert r.json() == {"on": True, "succeeded": ["10.0.0.1", "10.0.0.2"], "failed": []}
    assert routes.registry.get("10.0.0.1").is_on is True
    assert routes.registry.get("10.0.0.2").is_on is True


def test_group_power_partial_failure_stays_200(client, groups_store, monkeypatch):
    # One device errors when toggled; the others still switch and it's reported
    # under failed, not as a 500.
    monkeypatch.setattr(routes.registry.get("10.0.0.2"), "turn_on", _boom)
    gid = _make_group(groups_store, ["10.0.0.1", "10.0.0.2"])
    r = client.post(f"/api/groups/{gid}/power", json={"on": True})
    assert r.status_code == 200
    body = r.json()
    assert body["succeeded"] == ["10.0.0.1"]
    assert body["failed"] == ["10.0.0.2"]
    assert routes.registry.get("10.0.0.1").is_on is True


def test_group_power_missing_device_counts_as_failed(client, groups_store):
    # A device that's no longer in the registry is a failure, not a crash.
    gid = _make_group(groups_store, ["10.0.0.1", "9.9.9.9"])
    r = client.post(f"/api/groups/{gid}/power", json={"on": True})
    assert r.status_code == 200
    body = r.json()
    assert body["succeeded"] == ["10.0.0.1"]
    assert body["failed"] == ["9.9.9.9"]


def test_group_power_unknown_group_404(client, groups_store):
    assert client.post("/api/groups/nope/power", json={"on": True}).status_code == 404


def test_group_power_nudges_broadcaster(client, groups_store, monkeypatch):
    nudge = AsyncMock()
    monkeypatch.setattr(routes.broadcaster, "publish_now", nudge)
    gid = _make_group(groups_store, ["10.0.0.1"])
    assert client.post(f"/api/groups/{gid}/power", json={"on": True}).status_code == 200
    nudge.assert_awaited_once()


def test_group_power_nudges_broadcaster_on_partial_failure(
    client, groups_store, monkeypatch
):
    # Some devices changed, so other clients must be told even when one failed.
    nudge = AsyncMock()
    monkeypatch.setattr(routes.broadcaster, "publish_now", nudge)
    gid = _make_group(groups_store, ["10.0.0.1", "9.9.9.9"])
    assert client.post(f"/api/groups/{gid}/power", json={"on": True}).status_code == 200
    nudge.assert_awaited_once()


def test_all_power_switches_every_device(client):
    r = client.post("/api/power", json={"on": True})
    assert r.status_code == 200
    body = r.json()
    assert body["on"] is True
    assert set(body["succeeded"]) == {"10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"}
    assert body["failed"] == []
    assert all(d.is_on for d in routes.registry.all())


def test_all_power_partial_failure_stays_200(client, monkeypatch):
    monkeypatch.setattr(routes.registry.get("10.0.0.3"), "turn_off", _boom)
    r = client.post("/api/power", json={"on": False})
    assert r.status_code == 200
    body = r.json()
    assert "10.0.0.3" in body["failed"]
    assert "10.0.0.1" in body["succeeded"]


def test_all_power_nudges_broadcaster(client, monkeypatch):
    nudge = AsyncMock()
    monkeypatch.setattr(routes.broadcaster, "publish_now", nudge)
    assert client.post("/api/power", json={"on": False}).status_code == 200
    nudge.assert_awaited_once()
