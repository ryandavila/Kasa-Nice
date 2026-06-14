from unittest.mock import AsyncMock

import pytest
from conftest import FakeChild, FakeDevice
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import routes
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


def test_usage(client):
    r = client.get("/api/devices/10.0.0.4/usage")
    assert r.status_code == 200
    body = r.json()
    assert body["current_power_w"] == 12.5
    assert [s["label"] for s in body["monthly"]] == ["Jan", "Jun"]


def test_usage_without_emeter_404(client):
    assert client.get("/api/devices/10.0.0.1/usage").status_code == 404
