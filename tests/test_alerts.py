import asyncio
import contextlib
import sqlite3
import time
from pathlib import Path

import pytest
from conftest import FakeDevice
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import alerts
from api.alerts import (
    AlertCenter,
    AlertDraft,
    AlertEvaluator,
    AlertThresholdStore,
    DeviceReading,
    collect_readings,
    deliver_webhook,
    run_alert_evaluator,
)
from api.energy_history import EnergyHistoryStore
from api.kasa_service import DeviceRegistry
from api.routers import alerts as alerts_routes


def _reading(device_id="d1", *, alias="Plug", reachable=True, power_w=None):
    return DeviceReading(device_id, alias, reachable, power_w)


# ── Pure debounce / transition logic ──────────────────────────────────────────


def test_first_cycle_seeds_reachability_without_alerting():
    # A device that's already offline at startup must not fire "became
    # unreachable": the first sight only establishes the baseline.
    ev = AlertEvaluator()
    assert ev.evaluate([_reading(reachable=False)], {}) == []


def test_reachable_to_unreachable_fires_once():
    ev = AlertEvaluator()
    ev.evaluate([_reading(reachable=True)], {})  # baseline: reachable
    first = ev.evaluate([_reading(reachable=False)], {})
    assert [d.type for d in first] == ["device_unreachable"]
    # Still unreachable next cycle -> no repeat (debounced to the transition).
    assert ev.evaluate([_reading(reachable=False)], {}) == []


def test_recovery_fires_on_transition_back():
    ev = AlertEvaluator()
    ev.evaluate([_reading(reachable=True)], {})
    ev.evaluate([_reading(reachable=False)], {})
    recovered = ev.evaluate([_reading(reachable=True)], {})
    assert [d.type for d in recovered] == ["device_recovered"]


def test_power_fires_on_rising_edge_over_threshold():
    ev = AlertEvaluator()
    thresholds = {"d1": 30.0}
    assert ev.evaluate([_reading(power_w=10.0)], thresholds) == []  # under
    fired = ev.evaluate([_reading(power_w=42.0)], thresholds)  # over
    assert [d.type for d in fired] == ["power_exceeded"]
    assert fired[0].power_w == 42.0
    assert fired[0].threshold_w == 30.0
    # Still over next cycle -> no repeat until it drops back below.
    assert ev.evaluate([_reading(power_w=50.0)], thresholds) == []


def test_power_rearms_after_dropping_below_threshold():
    ev = AlertEvaluator()
    thresholds = {"d1": 30.0}
    ev.evaluate([_reading(power_w=42.0)], thresholds)  # fires, latches
    ev.evaluate([_reading(power_w=5.0)], thresholds)  # drops, re-arms
    fired = ev.evaluate([_reading(power_w=42.0)], thresholds)  # over again -> fires
    assert [d.type for d in fired] == ["power_exceeded"]


def test_missing_power_reading_holds_the_latch():
    # A transient None (no fresh sample) must not re-arm; otherwise the next
    # over-threshold reading would double-alert for one continuous incident.
    ev = AlertEvaluator()
    thresholds = {"d1": 30.0}
    ev.evaluate([_reading(power_w=42.0)], thresholds)  # fires, latches
    ev.evaluate([_reading(power_w=None)], thresholds)  # unknown -> hold
    assert ev.evaluate([_reading(power_w=42.0)], thresholds) == []  # no re-alert


def test_no_threshold_never_fires_power_alert():
    ev = AlertEvaluator()
    assert ev.evaluate([_reading(power_w=9999.0)], {}) == []


def test_clearing_threshold_rearms_for_next_add():
    ev = AlertEvaluator()
    ev.evaluate([_reading(power_w=42.0)], {"d1": 30.0})  # fires, latches
    ev.evaluate([_reading(power_w=42.0)], {})  # threshold removed -> unlatch
    fired = ev.evaluate([_reading(power_w=42.0)], {"d1": 30.0})  # re-added -> fires
    assert [d.type for d in fired] == ["power_exceeded"]


# ── Threshold store round-trip ────────────────────────────────────────────────


def test_threshold_store_roundtrip(tmp_path):
    store = AlertThresholdStore(tmp_path / "alerts.json")
    assert store.get_all() == {}
    saved = store.set_all({"d1": 30.0, "d2": 12.5})
    assert saved == {"d1": 30.0, "d2": 12.5}
    # A fresh instance reads the same persisted mapping.
    assert AlertThresholdStore(tmp_path / "alerts.json").get_all() == {
        "d1": 30.0,
        "d2": 12.5,
    }


def test_threshold_store_drops_non_positive_and_garbage(tmp_path):
    store = AlertThresholdStore(tmp_path / "alerts.json")
    saved = store.set_all({"d1": 30.0, "d2": 0, "d3": -5, "d4": "nan-ish"})
    assert saved == {"d1": 30.0}


def test_threshold_store_missing_file_is_empty(tmp_path):
    assert AlertThresholdStore(tmp_path / "nope.json").get_all() == {}


# ── Latest-power lookup (EnergyHistoryStore glue) ──────────────────────────────


def _seed_samples(path, rows):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE samples (device_id TEXT, ts INTEGER, power_w REAL, "
        "today_kwh REAL, month_kwh REAL)"
    )
    conn.executemany(
        "INSERT INTO samples (device_id, ts, power_w) VALUES (?, ?, ?)", rows
    )
    conn.commit()
    conn.close()


def _staleness() -> float:
    return alerts._POWER_STALENESS_SECONDS


def test_latest_power_returns_most_recent_per_device(tmp_path):
    db = tmp_path / "energy.db"
    now = 1_000_000
    _seed_samples(
        db,
        [
            ("d1", now - 100, 10.0),
            ("d1", now - 10, 25.0),  # newest for d1
            ("d2", now - 5, 40.0),
        ],
    )
    store = EnergyHistoryStore(db)
    assert store.latest_power_by_device(_staleness(), now=now) == {
        "d1": 25.0,
        "d2": 40.0,
    }


def test_latest_power_ignores_stale_and_null(tmp_path):
    db = tmp_path / "energy.db"
    now = 1_000_000
    _seed_samples(
        db,
        [
            ("old", now - 10_000_000, 99.0),  # far older than the staleness window
            ("d1", now - 10, None),  # null power ignored
            ("d1", now - 20, 7.0),  # newest non-null wins
        ],
    )
    store = EnergyHistoryStore(db)
    assert store.latest_power_by_device(_staleness(), now=now) == {"d1": 7.0}


def test_latest_power_missing_db_is_empty(tmp_path):
    store = EnergyHistoryStore(tmp_path / "absent.db")
    assert store.latest_power_by_device(_staleness()) == {}


# ── collect_readings (registry + DB glue) ─────────────────────────────────────


def test_collect_readings_marks_reachable_and_attaches_power(tmp_path):
    db = tmp_path / "energy.db"
    # Seed at "now" so the sample is inside the staleness window without patching.
    _seed_samples(db, [("10.0.0.4", int(time.time()), 12.5)])
    reg = DeviceRegistry()
    reg._devices = {
        "10.0.0.1": FakeDevice("10.0.0.1", alias="Plug"),
        "10.0.0.4": FakeDevice("10.0.0.4", alias="Meter", has_energy=True),
    }
    readings = collect_readings(reg, EnergyHistoryStore(db))

    by_id = {r.device_id: r for r in readings}
    assert by_id["10.0.0.1"].reachable is True
    assert by_id["10.0.0.1"].power_w is None
    assert by_id["10.0.0.4"].power_w == 12.5


# ── AlertCenter ring buffer ───────────────────────────────────────────────────


def test_center_emits_newest_first_and_stamps():
    center = AlertCenter()
    a = center.emit(AlertDraft("device_unreachable", "d1", "one"), ts=1)
    b = center.emit(AlertDraft("device_recovered", "d1", "two"), ts=2)
    recent = center.recent()
    assert [r.id for r in recent] == [b.id, a.id]  # newest first
    assert recent[0].ts == 2 and recent[0].message == "two"


def test_center_ring_buffer_is_bounded():
    center = AlertCenter(maxlen=3)
    for i in range(5):
        center.emit(AlertDraft("power_exceeded", "d1", str(i)), ts=i)
    assert [r.message for r in center.recent()] == ["4", "3", "2"]  # oldest evicted


# ── Webhook delivery (fake httpx client) ──────────────────────────────────────


class _FakeResponse:
    def raise_for_status(self) -> None:
        pass


class _FakeClient:
    """Minimal stand-in for ``httpx.AsyncClient`` capturing the POST it receives."""

    calls: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, *, content=None, headers=None):
        _FakeClient.calls.append({"url": url, "content": content, "headers": headers})
        return _FakeResponse()


def test_webhook_posts_message_body_and_title_header():
    _FakeClient.calls = []
    alert = alerts.alert_center.__class__().emit(
        AlertDraft("power_exceeded", "d1", "Meter is drawing 42 W (over 30 W)"), ts=1
    )

    async def go():
        ok = await deliver_webhook(
            "https://ntfy.example/kasa", alert, client_factory=_FakeClient
        )
        assert ok is True

    asyncio.run(go())
    assert len(_FakeClient.calls) == 1
    call = _FakeClient.calls[0]
    assert call["url"] == "https://ntfy.example/kasa"
    assert call["content"] == "Meter is drawing 42 W (over 30 W)"
    assert call["headers"]["Title"] == "Power draw high"


def test_webhook_failure_is_swallowed():
    class _BoomClient(_FakeClient):
        async def post(self, url, *, content=None, headers=None):
            raise RuntimeError("network down")

    alert = alerts.alert_center.__class__().emit(
        AlertDraft("device_unreachable", "d1", "gone"), ts=1
    )

    async def go():
        return await deliver_webhook("https://x", alert, client_factory=_BoomClient)

    assert asyncio.run(go()) is False  # reported, never raised


# ── Evaluator loop (resilience, cancellation, webhook dispatch) ───────────────


def test_run_loop_emits_and_dispatches_then_cancels(tmp_path, monkeypatch):
    center = AlertCenter()
    evaluator = AlertEvaluator()
    thresholds = AlertThresholdStore(tmp_path / "alerts.json")
    thresholds.set_all({"10.0.0.4": 5.0})

    db = tmp_path / "energy.db"
    # Seed at "now" so the sample is inside the staleness window without patching.
    _seed_samples(db, [("10.0.0.4", int(time.time()), 12.5)])

    reg = DeviceRegistry()
    reg._devices = {"10.0.0.4": FakeDevice("10.0.0.4", alias="Meter", has_energy=True)}

    delivered: list = []

    async def fake_deliver(url, alert):
        delivered.append((url, alert.message))
        return True

    async def drive():
        task = asyncio.create_task(
            run_alert_evaluator(
                reg,
                evaluator,
                center,
                thresholds,
                interval=0.01,
                history=EnergyHistoryStore(db),
                webhook_url="https://hook",
                deliver=fake_deliver,
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(drive())

    # 12.5 W over the 5 W threshold: exactly one alert (debounced across cycles).
    assert [a.type for a in center.recent()] == ["power_exceeded"]
    assert len(delivered) == 1
    assert delivered[0][0] == "https://hook"


def test_run_loop_holds_off_while_discovering(tmp_path, monkeypatch):
    """No cycle runs mid-discovery, and evaluation starts once it finishes.

    Regression: the first cycle used to race startup discovery, see the
    still-empty registry, seed every known device as unreachable, and then fire
    a spurious "recovered" alert (and webhook) per device on every restart.
    """
    center = AlertCenter()
    evaluator = AlertEvaluator()
    thresholds = AlertThresholdStore(tmp_path / "alerts.json")
    thresholds.set_all({"10.0.0.4": 5.0})

    db = tmp_path / "energy.db"
    # Seed at "now" so the sample is inside the staleness window without patching.
    _seed_samples(db, [("10.0.0.4", int(time.time()), 12.5)])

    reg = DeviceRegistry()
    reg._devices = {"10.0.0.4": FakeDevice("10.0.0.4", alias="Meter", has_energy=True)}
    reg.discovering = True

    async def drive():
        task = asyncio.create_task(
            run_alert_evaluator(
                reg,
                evaluator,
                center,
                thresholds,
                interval=0.005,
                history=EnergyHistoryStore(db),
            )
        )
        await asyncio.sleep(0.05)
        assert center.recent() == []  # held off: no baseline, no alerts yet
        reg.discovering = False
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(drive())
    # Evaluation resumed after the sweep: exactly one (debounced) power alert.
    assert [a.type for a in center.recent()] == ["power_exceeded"]


def test_run_loop_survives_a_failing_cycle():
    center = AlertCenter()

    class BoomRegistry:
        def all(self):
            raise RuntimeError("registry on fire")

    async def drive():
        task = asyncio.create_task(
            run_alert_evaluator(
                BoomRegistry(),
                AlertEvaluator(),
                center,
                AlertThresholdStore("/nonexistent/alerts.json"),
                interval=0.005,
                history=EnergyHistoryStore(Path("/nonexistent/energy.db")),
            )
        )
        await asyncio.sleep(0.05)
        assert not task.done()  # kept looping through the failures
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(drive())


# ── Routes ────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch, tmp_path):
    # Fresh, isolated ring buffer + threshold store wired into the routes.
    center = AlertCenter()
    store = AlertThresholdStore(tmp_path / "alerts.json")
    monkeypatch.setattr(alerts_routes, "alert_center", center)
    monkeypatch.setattr(alerts_routes, "alert_thresholds", store)
    app = FastAPI()
    app.include_router(alerts_routes.router)
    return TestClient(app), center, store


def test_recent_alerts_endpoint_returns_newest_first(client):
    c, center, _ = client
    center.emit(AlertDraft("device_unreachable", "d1", "one"), ts=1)
    center.emit(AlertDraft("device_recovered", "d1", "two"), ts=2)
    body = c.get("/api/alerts/recent").json()
    assert [a["message"] for a in body] == ["two", "one"]
    assert body[0]["type"] == "device_recovered"


def test_get_thresholds_defaults_empty(client):
    c, _, _ = client
    assert c.get("/api/alerts/thresholds").json() == {"thresholds": {}}


def test_put_thresholds_full_replace_and_sanitizes(client):
    c, _, store = client
    r = c.put(
        "/api/alerts/thresholds",
        json={"thresholds": {"d1": 30.0, "d2": -1}},
    )
    assert r.status_code == 200
    assert r.json() == {"thresholds": {"d1": 30.0}}  # non-positive dropped
    assert store.get_all() == {"d1": 30.0}
    # A second PUT fully replaces (mirrors PUT /favorites).
    c.put("/api/alerts/thresholds", json={"thresholds": {"d3": 5.0}})
    assert store.get_all() == {"d3": 5.0}
