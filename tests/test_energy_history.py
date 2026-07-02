import asyncio
import contextlib
import datetime
import time
import types

import pytest
from conftest import FakeDevice
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kasa import Module

from api import routes
from api.energy_history import EnergyHistoryStore, run_recorder
from api.kasa_service import DeviceRegistry, EnergySnapshot, EnergyUnsupportedError


@pytest.fixture
def store(tmp_path):
    return EnergyHistoryStore(tmp_path / "energy.db")


def _local_noon(d: datetime.date) -> int:
    """Epoch seconds at local noon on ``d`` — safely inside that local day."""
    return int(time.mktime((d.year, d.month, d.day, 12, 0, 0, 0, 0, -1)))


def _local_midnight(d: datetime.date) -> int:
    """Epoch seconds at local midnight on ``d`` (a local-day boundary)."""
    return int(time.mktime((d.year, d.month, d.day, 0, 0, 0, 0, 0, -1)))


def _local_hour(d: datetime.date, hour: int) -> int:
    """Epoch seconds at local ``hour`` on ``d`` — for time-of-day (idle) buckets."""
    return int(time.mktime((d.year, d.month, d.day, hour, 0, 0, 0, 0, -1)))


# ── Store ───────────────────────────────────────────────────────────────────


def test_recent_samples_filters_by_since(store):
    now = int(time.time())
    store.record("10.0.0.4", 5.0, 0.1, 1.0, ts=now - 10_000)
    store.record("10.0.0.4", 8.0, 0.2, 1.1, ts=now - 100)
    recent = store.recent_samples("10.0.0.4", now - 1_000)
    assert recent == [(now - 100, 8.0)]


def test_recent_samples_empty_for_unknown_device(store):
    assert store.recent_samples("9.9.9.9", 0) == []


def test_daily_totals_takes_max_per_local_day(store):
    today = datetime.date.today()
    two_days_ago = today - datetime.timedelta(days=2)
    # Two readings on the same local day: the later, higher one is the day's total
    # (today_kwh climbs until it resets at midnight).
    store.record("10.0.0.4", 5.0, 0.2, 1.0, ts=_local_noon(today))
    store.record("10.0.0.4", 5.0, 0.5, 1.0, ts=_local_noon(today) + 3600)
    store.record("10.0.0.4", 5.0, 1.0, 1.0, ts=_local_noon(two_days_ago))

    totals = dict(store.daily_totals("10.0.0.4", days=7))
    assert totals[today.isoformat()] == 0.5
    assert totals[two_days_ago.isoformat()] == 1.0


def test_daily_totals_ignores_null_today_kwh(store):
    today = datetime.date.today()
    store.record("10.0.0.4", 5.0, None, None, ts=_local_noon(today))
    assert store.daily_totals("10.0.0.4", days=7) == []


def test_prune_drops_old_samples(store):
    now = int(time.time())
    store.record("10.0.0.4", 5.0, 0.1, 1.0, ts=now - 10_000)
    store.record("10.0.0.4", 8.0, 0.2, 1.1, ts=now - 100)
    store.prune(now - 1_000)
    assert store.recent_samples("10.0.0.4", 0) == [(now - 100, 8.0)]


def test_migrate_device_id_repoints_samples(store):
    now = int(time.time())
    store.record("10.0.0.4", 5.0, 0.1, 1.0, ts=now - 100)
    store.migrate_device_id("10.0.0.4", "AABBCCDDEE01")
    # History follows the device to its stable id; nothing left under the old IP.
    assert store.recent_samples("AABBCCDDEE01", 0) == [(now - 100, 5.0)]
    assert store.recent_samples("10.0.0.4", 0) == []


# ── Insights aggregation ─────────────────────────────────────────────────────


def test_today_kwh_by_device_takes_todays_max(store):
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    # Two readings today: the later, higher one is today's total. A second device
    # and a prior day confirm grouping and the date filter.
    store.record("dev-a", 5.0, 0.2, 1.0, ts=_local_noon(today))
    store.record("dev-a", 5.0, 0.7, 1.0, ts=_local_noon(today) + 3600)
    store.record("dev-b", 5.0, 0.3, 1.0, ts=_local_noon(today))
    store.record("dev-a", 5.0, 9.9, 1.0, ts=_local_noon(yesterday))  # excluded
    assert store.today_kwh_by_device() == {"dev-a": 0.7, "dev-b": 0.3}


def test_today_kwh_by_device_empty_when_no_data(store):
    assert store.today_kwh_by_device() == {}


def test_month_kwh_by_device_sums_daily_maxima(store):
    today = datetime.date.today()
    first = today.replace(day=1)
    second = first + datetime.timedelta(days=1)  # always in the same month
    prev_month = first - datetime.timedelta(days=1)  # last day of previous month
    # Day 1 total is its max (0.6); day 2 adds 0.5 => 1.1 for the month.
    store.record("dev-a", 5.0, 0.4, 2.0, ts=_local_noon(first))
    store.record("dev-a", 5.0, 0.6, 2.0, ts=_local_noon(first) + 3600)
    store.record("dev-a", 5.0, 0.5, 2.0, ts=_local_noon(second))
    store.record("dev-a", 5.0, 9.9, 2.0, ts=_local_noon(prev_month))  # other month
    assert store.month_kwh_by_device()["dev-a"] == pytest.approx(1.1)


def test_home_kwh_between_sums_devices_and_days(store):
    day0 = datetime.date(2024, 6, 3)
    day1 = datetime.date(2024, 6, 4)
    before = datetime.date(2024, 6, 2)
    # dev-a spans two days (0.5 + 0.7), dev-b adds 0.2; a day before the window is
    # excluded by the half-open bound.
    store.record("dev-a", 5.0, 0.5, 1.0, ts=_local_noon(day0))
    store.record("dev-a", 5.0, 0.7, 1.0, ts=_local_noon(day1))
    store.record("dev-b", 5.0, 0.2, 1.0, ts=_local_noon(day0))
    store.record("dev-a", 5.0, 9.9, 1.0, ts=_local_noon(before))  # excluded
    start = _local_midnight(day0)
    end = _local_midnight(day0 + datetime.timedelta(days=7))
    assert store.home_kwh_between(start, end) == pytest.approx(1.4)


def test_home_kwh_between_empty_window_is_zero(store):
    assert store.home_kwh_between(0, 1) == 0.0


def test_idle_draw_medians_overnight_window_only(store):
    day = datetime.date.today() - datetime.timedelta(days=1)  # inside the 14d window
    # Overnight (01:00–05:00) readings 1/3/5 -> median 3; a daytime spike is
    # ignored so it can't skew the standing-draw figure.
    store.record("dev-a", 1.0, None, None, ts=_local_hour(day, 2))
    store.record("dev-a", 3.0, None, None, ts=_local_hour(day, 3))
    store.record("dev-a", 5.0, None, None, ts=_local_hour(day, 4))
    store.record("dev-a", 99.0, None, None, ts=_local_hour(day, 14))
    assert store.idle_draw(days=14) == {"dev-a": pytest.approx(3.0)}


def test_idle_draw_even_count_averages_middle_two(store):
    day = datetime.date.today() - datetime.timedelta(days=1)
    store.record("dev-a", 1.0, None, None, ts=_local_hour(day, 2))
    store.record("dev-a", 3.0, None, None, ts=_local_hour(day, 3))
    # Median of [1, 3] is the mean of the two middle rows = 2.
    assert store.idle_draw(days=14) == {"dev-a": pytest.approx(2.0)}


def test_idle_draw_excludes_samples_outside_window(store):
    old = datetime.date.today() - datetime.timedelta(days=20)  # beyond 14 days
    store.record("dev-a", 5.0, None, None, ts=_local_hour(old, 3))
    assert store.idle_draw(days=14) == {}


# ── Recorder ────────────────────────────────────────────────────────────────


def test_recorder_records_only_metered_devices(store):
    metered = FakeDevice("10.0.0.4", has_energy=True)
    plain = FakeDevice("10.0.0.1")

    class FakeRegistry:
        def all(self):
            return [metered, plain]

        async def read_energy_snapshot(self, device_id):
            if device_id == metered.host:
                return EnergySnapshot(power_w=9.0, today_kwh=0.4, month_kwh=2.0)
            raise EnergyUnsupportedError(device_id)

    async def drive():
        task = asyncio.create_task(run_recorder(FakeRegistry(), store, interval=0.01))
        await asyncio.sleep(0.05)  # several cycles
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(drive())

    assert store.recent_samples(metered.host, 0)  # recorded
    assert store.recent_samples(plain.host, 0) == []  # skipped (no meter)


def test_recorder_survives_a_failing_device(store):
    boom = types.SimpleNamespace(host="10.0.0.7")

    class FakeRegistry:
        def all(self):
            return [boom]

        async def read_energy_snapshot(self, device_id):
            raise RuntimeError("device exploded")

    async def drive():
        task = asyncio.create_task(run_recorder(FakeRegistry(), store, interval=0.01))
        await asyncio.sleep(0.03)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # Must not raise; nothing recorded for the failing device.
    asyncio.run(drive())
    assert store.recent_samples(boom.host, 0) == []


def test_recorder_never_fetches_stats_tables(store):
    # The recorder stores only scalars, so its per-cycle read must NOT pull the
    # device's daily/monthly history tables (wasted device I/O every 5 minutes).
    device = FakeDevice("10.0.0.4", has_energy=True)
    energy = device.modules[Module.Energy]
    reg = DeviceRegistry()
    reg._devices = {device.host: device}

    async def drive():
        task = asyncio.create_task(run_recorder(reg, store, interval=0.01))
        await asyncio.sleep(0.05)  # several cycles
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(drive())

    # Zero stats-table fetches across every cycle...
    assert energy.daily_stats_calls == 0
    assert energy.monthly_stats_calls == 0
    # ...yet the scalar readings were still recorded (matching FakeEnergy).
    samples = store.recent_samples(device.host, 0)
    assert samples and all(power == 12.5 for _ts, power in samples)


# ── API ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch, store):
    now = int(time.time())
    store.record("10.0.0.4", 9.0, 0.4, 2.0, ts=now - 60)
    reg = DeviceRegistry(energy_rate=0.2)
    reg._devices = {"10.0.0.4": FakeDevice("10.0.0.4", has_energy=True)}
    monkeypatch.setattr(routes, "history", store)
    monkeypatch.setattr(routes, "registry", reg)
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_history_returns_samples_and_daily_with_cost(client):
    body = client.get("/api/devices/10.0.0.4/history").json()
    assert body["device_id"] == "10.0.0.4"
    assert len(body["samples"]) == 1
    assert body["samples"][0]["power_w"] == 9.0
    assert body["daily"]
    day = body["daily"][-1]
    assert day["kwh"] == 0.4
    assert day["cost"] == pytest.approx(0.08)  # 0.4 kWh × $0.20


def test_history_unknown_device_404(client):
    assert client.get("/api/devices/9.9.9.9/history").status_code == 404
