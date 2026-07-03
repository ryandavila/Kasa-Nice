import asyncio
import contextlib
import datetime
import random
from unittest.mock import AsyncMock

import pytest
from conftest import FakeDevice
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import vacation
from api.group_store import GroupStore
from api.kasa_service import DeviceRegistry
from api.schemas import VacationConfig
from api.vacation import VacationEngine, resolve_device_ids
from api.vacation_store import VacationStore

# Jan 1 2024 is a Monday. A concrete evening the engine reasons about; all
# instants are built through ``astimezone`` so they're aware local datetimes,
# matching what ``now_fn`` yields in production (as the scheduler tests do).
JAN1 = datetime.date(2024, 1, 1)


def _local(hour: int, minute: int = 0) -> datetime.datetime:
    """A Jan-1-2024 wall-clock instant as an aware local datetime."""
    return datetime.datetime(2024, 1, 1, hour, minute).astimezone()


def _config(**over) -> VacationConfig:
    base = {
        "enabled": True,
        "device_ids": ["10.0.0.1", "10.0.0.2"],
        "start_time": "19:00",
        "end_time": "23:00",
        "min_interval_minutes": 10,
        "max_interval_minutes": 20,
    }
    return VacationConfig(**{**base, **over})


class _StaticStore:
    """A stand-in vacation store that always returns one fixed config.

    The engine reads the config fresh each tick via ``load``; tests that don't
    exercise persistence use this so a single config drives the whole evening.
    """

    def __init__(self, config: VacationConfig) -> None:
        self._config = config

    def load(self) -> VacationConfig:
        return self._config


@pytest.fixture
def registry(monkeypatch):
    reg = DeviceRegistry()
    reg._devices = {
        "10.0.0.1": FakeDevice("10.0.0.1", alias="Lamp"),
        "10.0.0.2": FakeDevice("10.0.0.2", alias="Porch"),
        "10.0.0.3": FakeDevice("10.0.0.3", alias="Hall"),
    }
    # ``set_power_many`` binds the module-global ``registry`` in the groups
    # router; point it at the fake so the engine's fan-out hits these devices,
    # mirroring the scheduler tests.
    from api.routers import groups as groups_routes

    monkeypatch.setattr(groups_routes, "registry", reg)
    return reg


@pytest.fixture
def groups(tmp_path):
    return GroupStore(tmp_path / "groups.json")


def _engine(store, registry, groups, *, seed: int = 1, **over) -> VacationEngine:
    """An engine with a seeded RNG and no location, ready to be tick-driven."""
    return VacationEngine(
        store,
        registry,
        groups,
        AsyncMock(),
        rng=random.Random(seed),
        **over,
    )


# ── Store ─────────────────────────────────────────────────────────────────────


def test_missing_file_reads_defaults(tmp_path):
    cfg = VacationStore(tmp_path / "v.json").load()
    assert cfg.enabled is False
    assert cfg.end_time == "23:00"
    assert cfg.start_time is None


def test_corrupt_file_degrades_to_defaults(tmp_path):
    path = tmp_path / "v.json"
    path.write_text("{ not json")
    assert VacationStore(path).load().enabled is False


def test_save_round_trips(tmp_path):
    store = VacationStore(tmp_path / "v.json")
    store.save(_config(enabled=True, end_time="22:30"))
    reloaded = store.load()
    assert reloaded.enabled is True
    assert reloaded.end_time == "22:30"
    assert reloaded.device_ids == ["10.0.0.1", "10.0.0.2"]


def test_incoherent_stored_intervals_degrade_to_defaults(tmp_path):
    # A hand-edited file with max < min can't validate; the store degrades it to
    # defaults rather than handing the engine an impossible interval.
    path = tmp_path / "v.json"
    path.write_text('{"min_interval_minutes": 40, "max_interval_minutes": 5}')
    cfg = VacationStore(path).load()
    assert cfg.min_interval_minutes <= cfg.max_interval_minutes


# ── Config validation ─────────────────────────────────────────────────────────


def test_max_below_min_is_rejected():
    with pytest.raises(ValueError, match="max_interval_minutes"):
        VacationConfig(min_interval_minutes=30, max_interval_minutes=10)


# ── Device resolution (explicit + rooms) ──────────────────────────────────────


def test_resolve_unions_devices_and_rooms(groups):
    room = groups.create_group("Living Room")
    groups.update_group(room["id"], device_ids=["10.0.0.2", "10.0.0.3"])
    cfg = _config(device_ids=["10.0.0.1"], room_ids=[room["id"]])
    # Explicit device first, then room members, de-duplicated in order.
    assert resolve_device_ids(cfg, groups) == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]


def test_resolve_dedupes_across_devices_and_rooms(groups):
    room = groups.create_group("Room")
    groups.update_group(room["id"], device_ids=["10.0.0.1"])
    cfg = _config(device_ids=["10.0.0.1"], room_ids=[room["id"]])
    assert resolve_device_ids(cfg, groups) == ["10.0.0.1"]


def test_resolve_ignores_unknown_room(groups):
    cfg = _config(device_ids=["10.0.0.1"], room_ids=["nope"])
    assert resolve_device_ids(cfg, groups) == ["10.0.0.1"]


# ── Window boundaries ─────────────────────────────────────────────────────────


def test_fixed_start_window():
    cfg = _config(start_time="19:00", end_time="23:00")
    assert vacation._current_window(cfg, _local(20), None) is not None
    assert vacation._current_window(cfg, _local(18, 59), None) is None
    assert vacation._current_window(cfg, _local(23), None) is None  # end exclusive


def test_unset_start_falls_back_to_1900_without_location():
    cfg = _config(start_time=None, end_time="23:00")
    # No location -> sunset can't be computed -> 19:00 default start.
    start = vacation._window_start(cfg, JAN1, None)
    assert (start.hour, start.minute) == (19, 0)


def test_unset_start_uses_sunset_with_location():
    from api import solar

    NYC = (40.7128, -74.0060)
    cfg = _config(start_time=None)
    start = vacation._window_start(cfg, JAN1, NYC)
    expected = solar.sunset(JAN1, *NYC).astimezone().replace(second=0, microsecond=0)
    assert start == expected


# ── A full simulated evening ──────────────────────────────────────────────────


async def _drive_evening(engine, config, *, step_minutes=5, span=("18:55", "23:05")):
    """Tick the engine minute-by-minute across a window and log every switch.

    Returns the ordered list of ``(minute_label, device_id, is_on)`` observed by
    reading device state after each tick — the switch pattern the assertions key
    off. Uses a fake clock so no real time passes.
    """
    start_h, start_m = map(int, span[0].split(":"))
    end_h, end_m = map(int, span[1].split(":"))
    cursor = _local(start_h, start_m)
    stop = _local(end_h, end_m)
    events: list[tuple[str, str, bool]] = []
    prev: dict[str, bool] = {}
    while cursor <= stop:
        await engine.run_tick(cursor, config)
        for device_id in resolve_device_ids(config, engine._groups):
            state = engine._registry.get(device_id).is_on
            if prev.get(device_id) != state:
                events.append((cursor.strftime("%H:%M"), device_id, state))
                prev[device_id] = state
        cursor += datetime.timedelta(minutes=step_minutes)
    return events


def test_evening_switches_then_all_off_at_close(registry, groups):
    cfg = _config(start_time="19:00", end_time="23:00")
    engine = _engine(_StaticStore(cfg), registry, groups, seed=7)

    events = asyncio.run(_drive_evening(engine, cfg))

    # Something switched during the window (the simulation isn't inert).
    in_window = [e for e in events if "19:00" <= e[0] < "23:00"]
    assert in_window, "expected at least one toggle inside the window"

    # Both configured devices ended OFF once the window closed.
    assert registry.get("10.0.0.1").is_on is False
    assert registry.get("10.0.0.2").is_on is False

    # The all-off is recorded at/after the 23:00 boundary, not before.
    closing = [e for e in events if e[0] >= "23:00" and e[2] is False]
    assert closing, "expected an all-off at window close"


def test_per_device_jitter_desynchronizes_switches(registry, groups):
    # Two devices drawing independently from the same RNG should not toggle in
    # lockstep every tick — their switch minutes differ across the evening.
    cfg = _config(start_time="19:00", end_time="23:00")
    engine = _engine(_StaticStore(cfg), registry, groups, seed=3)
    events = asyncio.run(_drive_evening(engine, cfg, step_minutes=1))

    d1 = {e[0] for e in events if e[1] == "10.0.0.1" and "19:00" <= e[0] < "23:00"}
    d2 = {e[0] for e in events if e[1] == "10.0.0.2" and "19:00" <= e[0] < "23:00"}
    # They fire on different minutes (not a single shared cadence).
    assert d1 and d2
    assert d1 != d2


def test_no_switching_before_window_opens(registry, groups):
    cfg = _config(start_time="19:00", end_time="23:00")
    engine = _engine(_StaticStore(cfg), registry, groups)
    # A tick well before the window must not touch anything.
    asyncio.run(engine.run_tick(_local(17), cfg))
    assert registry.get("10.0.0.1").is_on is False
    assert engine.next_switch_ts() is None


def test_disabled_config_is_inert(registry, groups):
    cfg = _config(enabled=False, start_time="19:00", end_time="23:00")
    engine = _engine(_StaticStore(cfg), registry, groups)
    asyncio.run(engine.run_tick(_local(20), cfg))
    assert registry.get("10.0.0.1").is_on is False
    engine._broadcaster.publish_now.assert_not_awaited()


# ── Manual-change skip (never fight the scheduler / a person) ─────────────────


def test_manual_change_backs_off_device(registry, groups):
    """A device the engine turned on, then someone else flipped, is left alone.

    The engine adopts the new live state and cools down instead of yanking it
    back on its next scheduled toggle.
    """
    cfg = _config(device_ids=["10.0.0.1"], start_time="19:00", end_time="23:00")
    engine = _engine(_StaticStore(cfg), registry, groups, seed=1)

    # First tick in the window arms a jittered first toggle (no switch yet).
    asyncio.run(engine.run_tick(_local(19, 0), cfg))
    planned = engine._next_toggle["10.0.0.1"]

    # Let the engine make its first real toggle so it has an expectation.
    asyncio.run(engine.run_tick(planned, cfg))
    expected_state = engine._expected["10.0.0.1"]
    assert registry.get("10.0.0.1").is_on is expected_state

    # Someone else flips it the other way between toggles.
    manual_state = not expected_state
    registry.get("10.0.0.1").is_on = manual_state

    # Its next scheduled toggle arrives; the engine must NOT override the person.
    next_toggle = engine._next_toggle["10.0.0.1"]
    asyncio.run(engine.run_tick(next_toggle, cfg))
    assert registry.get("10.0.0.1").is_on is manual_state  # untouched
    # And it's now in a cooldown, having adopted the manual state.
    assert engine._cooldown_until["10.0.0.1"] > next_toggle
    assert engine._expected["10.0.0.1"] is manual_state


def test_close_window_skips_externally_changed_device(registry, groups):
    """The all-off at window close doesn't override a light a person just set."""
    cfg = _config(device_ids=["10.0.0.1"], start_time="19:00", end_time="23:00")
    engine = _engine(_StaticStore(cfg), registry, groups, seed=1)

    # Get the engine to own device 1's state (turn it on during the window).
    asyncio.run(engine.run_tick(_local(19, 0), cfg))
    asyncio.run(engine.run_tick(engine._next_toggle["10.0.0.1"], cfg))
    engine._expected["10.0.0.1"] = True
    registry.get("10.0.0.1").is_on = True

    # Someone turns it off manually right before close (matches expectation? no —
    # engine expects True, live is False => external change).
    registry.get("10.0.0.1").is_on = False

    # Window closes; the all-off should skip this device (it's externally changed)
    # and simply leave it as the person set it.
    asyncio.run(engine.run_tick(_local(23, 5), cfg))
    assert registry.get("10.0.0.1").is_on is False  # left as-is, not re-driven


# ── Status ────────────────────────────────────────────────────────────────────


def test_next_switch_ts_is_soonest_planned(registry, groups):
    cfg = _config(start_time="19:00", end_time="23:00")
    engine = _engine(_StaticStore(cfg), registry, groups, seed=1)
    asyncio.run(engine.run_tick(_local(19, 0), cfg))
    soonest = min(engine._next_toggle.values())
    assert engine.next_switch_ts() == int(soonest.timestamp())


def test_is_active_reflects_window_and_enabled(registry, groups):
    cfg = _config(start_time="19:00", end_time="23:00")
    engine = _engine(_StaticStore(cfg), registry, groups, now_fn=lambda: _local(20))
    assert engine.is_active(cfg) is True
    assert engine.is_active(_config(enabled=False)) is False
    off_window = VacationEngine(
        _StaticStore(cfg), registry, groups, AsyncMock(), now_fn=lambda: _local(12)
    )
    assert off_window.is_active(cfg) is False


# ── Loop resilience & cancellation (mirrors the scheduler's loop tests) ───────


def test_engine_survives_a_failing_cycle_and_is_cancellable(
    monkeypatch, registry, groups
):
    monkeypatch.setattr(vacation, "_TICK_SECONDS", 0.005)

    class BoomStore:
        def load(self):
            raise RuntimeError("store on fire")

    async def drive():
        engine = _engine(BoomStore(), registry, groups)
        task = asyncio.create_task(engine.run())
        await asyncio.sleep(0.05)  # several failing cycles
        assert not task.done()  # the loop swallowed the errors and kept going
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(drive())


# ── REST endpoints ────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, registry, groups, monkeypatch):
    """A TestClient over just the vacation router, wired to fresh temp state."""
    from api.routers import vacation as vacation_routes

    store = VacationStore(tmp_path / "v.json")
    engine = _engine(store, registry, groups, now_fn=lambda: _local(20))
    monkeypatch.setattr(vacation_routes, "vacation_store", store)
    monkeypatch.setattr(vacation_routes, "engine", engine)
    monkeypatch.setattr(vacation_routes, "groups", groups)

    app = FastAPI()
    app.include_router(vacation_routes.router)
    return TestClient(app)


def test_get_returns_defaults(client):
    body = client.get("/api/vacation").json()
    assert body["enabled"] is False
    assert body["active"] is False
    assert body["next_switch_ts"] is None
    assert body["resolved_device_ids"] == []


def test_put_persists_and_reports_status(client):
    payload = {
        "enabled": True,
        "device_ids": ["10.0.0.1"],
        "start_time": "19:00",
        "end_time": "23:00",
        "min_interval_minutes": 10,
        "max_interval_minutes": 20,
    }
    body = client.put("/api/vacation", json=payload).json()
    assert body["enabled"] is True
    # now_fn is 20:00, inside 19:00–23:00, so the engine reports active.
    assert body["active"] is True
    assert body["resolved_device_ids"] == ["10.0.0.1"]
    # It persisted: a fresh GET sees the same config.
    assert client.get("/api/vacation").json()["device_ids"] == ["10.0.0.1"]


def test_put_rejects_incoherent_intervals(client):
    payload = {"min_interval_minutes": 40, "max_interval_minutes": 5, "enabled": True}
    assert client.put("/api/vacation", json=payload).status_code == 422
