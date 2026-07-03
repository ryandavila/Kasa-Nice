"""Route tests for GET /api/energy/insights.

Seeds a temp SQLite store with LOCAL-time timestamps (so date/week/month bucketing
is deterministic regardless of when the suite runs), wires it plus an isolated
group store and registry into the routes, and asserts the assembled response.
"""

import calendar
import datetime
import time

import pytest
from conftest import FakeDevice
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import routes
from api.energy_history import EnergyHistoryStore
from api.group_store import GroupStore
from api.kasa_service import DeviceRegistry


def _local_noon(d: datetime.date) -> int:
    return int(time.mktime((d.year, d.month, d.day, 12, 0, 0, 0, 0, -1)))


def _local_hour(d: datetime.date, hour: int) -> int:
    return int(time.mktime((d.year, d.month, d.day, hour, 0, 0, 0, 0, -1)))


@pytest.fixture
def store(tmp_path):
    return EnergyHistoryStore(tmp_path / "energy.db")


@pytest.fixture
def group_store(tmp_path):
    return GroupStore(tmp_path / "groups.json")


def _client(reg, store, group_store, monkeypatch) -> TestClient:
    monkeypatch.setattr(routes, "registry", reg)
    monkeypatch.setattr(routes, "history", store)
    monkeypatch.setattr(routes, "groups", group_store)
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_insights_empty_db_is_zeros_and_nulls(store, group_store, monkeypatch):
    body = (
        _client(DeviceRegistry(), store, group_store, monkeypatch)
        .get("/api/energy/insights")
        .json()
    )
    assert body["projection"] == {
        "month_to_date_kwh": 0.0,
        "projected_kwh": 0.0,
        "month_to_date_cost": None,
        "projected_cost": None,
    }
    assert body["rooms"] == []
    assert body["week"] == {"this_week_kwh": 0.0, "last_week_kwh": 0.0}
    assert body["idle"] == []


def test_insights_projection_and_costs_with_rate(store, group_store, monkeypatch):
    today = datetime.date.today()
    first = today.replace(day=1)
    store.record("10.0.0.4", 5.0, 0.5, ts=_local_noon(first))
    if first != today:
        store.record("10.0.0.4", 5.0, 0.3, ts=_local_noon(today))
    mtd = 0.5 + (0.3 if first != today else 0.0)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    expected_proj = round(mtd / today.day * days_in_month, 3)

    reg = DeviceRegistry(energy_rate=0.2)
    body = (
        _client(reg, store, group_store, monkeypatch).get("/api/energy/insights").json()
    )
    proj = body["projection"]
    assert proj["month_to_date_kwh"] == pytest.approx(mtd)
    assert proj["projected_kwh"] == pytest.approx(expected_proj)
    assert proj["month_to_date_cost"] == pytest.approx(round(mtd * 0.2, 2))
    assert proj["projected_cost"] == pytest.approx(round(expected_proj * 0.2, 2))


def test_insights_rooms_rollup_with_unassigned_bucket(store, group_store, monkeypatch):
    today = datetime.date.today()
    store.record("dev-a", 5.0, 0.4, ts=_local_noon(today))
    store.record("dev-b", 5.0, 0.6, ts=_local_noon(today))
    gid = group_store.create_group("Kitchen")["id"]
    group_store.update_group(gid, device_ids=["dev-a"])

    body = (
        _client(DeviceRegistry(), store, group_store, monkeypatch)
        .get("/api/energy/insights")
        .json()
    )
    rooms = {r["name"]: r for r in body["rooms"]}
    assert rooms["Kitchen"]["today_kwh"] == pytest.approx(0.4)
    # dev-b belongs to no room, so it surfaces under the synthetic bucket.
    assert rooms["Unassigned"]["group_id"] == "unassigned"
    assert rooms["Unassigned"]["today_kwh"] == pytest.approx(0.6)


def test_insights_room_costs_null_without_rate(store, group_store, monkeypatch):
    today = datetime.date.today()
    store.record("dev-a", 5.0, 0.4, ts=_local_noon(today))
    gid = group_store.create_group("Den")["id"]
    group_store.update_group(gid, device_ids=["dev-a"])
    body = (
        _client(DeviceRegistry(), store, group_store, monkeypatch)
        .get("/api/energy/insights")
        .json()
    )
    den = next(r for r in body["rooms"] if r["name"] == "Den")
    assert den["today_cost"] is None
    assert den["month_cost"] is None


def test_insights_week_over_week(store, group_store, monkeypatch):
    today = datetime.date.today()
    this_monday = today - datetime.timedelta(days=today.weekday())
    prev_week_day = this_monday - datetime.timedelta(days=3)  # in the previous week
    store.record("dev-a", 5.0, 0.5, ts=_local_noon(this_monday))
    store.record("dev-a", 5.0, 0.9, ts=_local_noon(prev_week_day))
    body = (
        _client(DeviceRegistry(), store, group_store, monkeypatch)
        .get("/api/energy/insights")
        .json()
    )
    assert body["week"]["this_week_kwh"] == pytest.approx(0.5)
    assert body["week"]["last_week_kwh"] == pytest.approx(0.9)


def test_insights_flags_idle_hog_with_live_alias(store, group_store, monkeypatch):
    day = datetime.date.today() - datetime.timedelta(days=1)
    for hour, watts in ((2, 4.0), (3, 6.0), (4, 8.0)):  # median 6 > 2W threshold
        store.record("10.0.0.4", watts, None, ts=_local_hour(day, hour))
    reg = DeviceRegistry()
    reg._devices = {"10.0.0.4": FakeDevice("10.0.0.4", alias="Fridge", has_energy=True)}

    body = (
        _client(reg, store, group_store, monkeypatch).get("/api/energy/insights").json()
    )
    assert len(body["idle"]) == 1
    hog = body["idle"][0]
    assert hog["device_id"] == "10.0.0.4"
    assert hog["alias"] == "Fridge"  # labelled from the live registry
    assert hog["idle_w"] == pytest.approx(6.0)
    assert hog["is_idle_hog"] is True


def test_insights_idle_falls_back_to_id_and_flags_low_draw(
    store, group_store, monkeypatch
):
    day = datetime.date.today() - datetime.timedelta(days=1)
    # A device gone from the registry: labelled by its id. 0.5W is below the
    # threshold, so it's listed but not flagged.
    store.record("ghost", 0.5, None, ts=_local_hour(day, 3))
    body = (
        _client(DeviceRegistry(), store, group_store, monkeypatch)
        .get("/api/energy/insights")
        .json()
    )
    assert body["idle"][0]["alias"] == "ghost"
    assert body["idle"][0]["is_idle_hog"] is False
