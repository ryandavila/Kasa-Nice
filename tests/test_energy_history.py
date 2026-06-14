import asyncio
import contextlib
import datetime
import time
import types

import pytest
from conftest import FakeDevice
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import routes
from api.energy_history import EnergyHistoryStore, run_recorder
from api.kasa_service import DeviceRegistry, EnergyUnsupportedError
from api.schemas import Usage


@pytest.fixture
def store(tmp_path):
    return EnergyHistoryStore(tmp_path / "energy.db")


def _local_noon(d: datetime.date) -> int:
    """Epoch seconds at local noon on ``d`` — safely inside that local day."""
    return int(time.mktime((d.year, d.month, d.day, 12, 0, 0, 0, 0, -1)))


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


# ── Recorder ────────────────────────────────────────────────────────────────


def test_recorder_records_only_metered_devices(store):
    metered = FakeDevice("10.0.0.4", has_energy=True)
    plain = FakeDevice("10.0.0.1")

    class FakeRegistry:
        def all(self):
            return [metered, plain]

        async def get_usage(self, device_id):
            if device_id == metered.host:
                return Usage(
                    device_id=device_id,
                    current_power_w=9.0,
                    today_kwh=0.4,
                    month_kwh=2.0,
                )
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

        async def get_usage(self, device_id):
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
