import asyncio
import contextlib
import datetime
from unittest.mock import AsyncMock

import pytest
from conftest import FakeChild, FakeDevice

from api import scheduler
from api.group_store import GroupStore
from api.kasa_service import DeviceRegistry
from api.schedule_store import ScheduleStore

# Jan 1 2024 is a Monday, so weekday() == 0. Building the instant through
# ``astimezone`` gives the aware local datetime the scheduler compares against.
MONDAY_1830 = datetime.datetime(2024, 1, 1, 18, 30).astimezone()
TUESDAY_1830 = MONDAY_1830 + datetime.timedelta(days=1)


def _rule(**over) -> dict:
    base = {
        "id": "r1",
        "kind": "fixed_time",
        "enabled": True,
        "time": "18:30",
        "days": [0],  # Monday
        "target": {"type": "device", "id": "10.0.0.1"},
        "action": "on",
    }
    return {**base, **over}


# ── Pure due-rule computation ─────────────────────────────────────────────────


def test_rule_due_on_matching_minute():
    assert scheduler.rule_due_at(_rule(), MONDAY_1830) is True


def test_rule_not_due_on_wrong_day():
    assert scheduler.rule_due_at(_rule(), TUESDAY_1830) is False


def test_rule_not_due_on_wrong_time():
    assert scheduler.rule_due_at(_rule(time="18:31"), MONDAY_1830) is False


def test_disabled_rule_never_due():
    assert scheduler.rule_due_at(_rule(enabled=False), MONDAY_1830) is False


def test_unsupported_kind_skipped():
    # A rule kind a newer build might write is ignored, not run or errored.
    assert scheduler.rule_due_at(_rule(kind="sunrise"), MONDAY_1830) is False


def test_due_rules_filters_the_list():
    rules = [_rule(id="a"), _rule(id="b", time="09:00"), _rule(id="c", enabled=False)]
    assert [r["id"] for r in scheduler.due_rules(rules, MONDAY_1830)] == ["a"]


# ── Minute cursor / catch-up (drift handling) ─────────────────────────────────


def test_first_run_evaluates_current_minute_only():
    assert scheduler.minutes_to_evaluate(None, MONDAY_1830) == [MONDAY_1830]


def test_same_minute_yields_nothing_no_double_fire():
    assert scheduler.minutes_to_evaluate(MONDAY_1830, MONDAY_1830) == []


def test_short_gap_catches_up_missed_minutes():
    later = MONDAY_1830 + datetime.timedelta(minutes=2)
    got = scheduler.minutes_to_evaluate(MONDAY_1830, later, max_catchup=2)
    assert got == [
        MONDAY_1830 + datetime.timedelta(minutes=1),
        MONDAY_1830 + datetime.timedelta(minutes=2),
    ]


def test_long_gap_collapses_to_current_minute():
    # A suspended process shouldn't replay an hour of stale on/off toggles.
    later = MONDAY_1830 + datetime.timedelta(hours=1)
    assert scheduler.minutes_to_evaluate(MONDAY_1830, later, max_catchup=2) == [later]


# ── Firing ────────────────────────────────────────────────────────────────────


@pytest.fixture
def registry(monkeypatch):
    reg = DeviceRegistry()
    reg._devices = {
        "10.0.0.1": FakeDevice("10.0.0.1", alias="Plug"),
        "10.0.0.2": FakeDevice("10.0.0.2", alias="Lamp"),
        "10.0.0.3": FakeDevice(
            "10.0.0.3", type_name="Strip", children=[FakeChild("O1")]
        ),
    }
    # Device rules use this injected registry; room rules reuse routes'
    # ``_set_power_many``, which binds the module-global ``registry`` — the same
    # single object in production. Point both at the fake so the two firing paths
    # agree under test, mirroring how test_routes swaps the registry in.
    from api import routes

    monkeypatch.setattr(routes, "registry", reg)
    return reg


@pytest.fixture
def groups(tmp_path):
    return GroupStore(tmp_path / "groups.json")


def test_fire_device_rule_turns_on(registry, groups):
    async def go():
        result = await scheduler.fire_rule(_rule(), registry=registry, groups=groups)
        assert result == "ok"
        assert registry.get("10.0.0.1").is_on is True

    asyncio.run(go())


def test_fire_device_rule_turns_off(registry, groups):
    registry.get("10.0.0.1").is_on = True

    async def go():
        result = await scheduler.fire_rule(
            _rule(action="off"), registry=registry, groups=groups
        )
        assert result == "ok"
        assert registry.get("10.0.0.1").is_on is False

    asyncio.run(go())


def test_fire_device_rule_missing_device_is_error_not_raise(registry, groups):
    async def go():
        rule = _rule(target={"type": "device", "id": "9.9.9.9"})
        result = await scheduler.fire_rule(rule, registry=registry, groups=groups)
        assert result.startswith("error")

    asyncio.run(go())


def test_fire_room_rule_reuses_fanout(registry, groups):
    g = groups.create_group("Living Room")
    groups.update_group(g["id"], device_ids=["10.0.0.1", "10.0.0.2"])

    async def go():
        rule = _rule(target={"type": "room", "id": g["id"]})
        result = await scheduler.fire_rule(rule, registry=registry, groups=groups)
        assert result == "ok"
        assert registry.get("10.0.0.1").is_on is True
        assert registry.get("10.0.0.2").is_on is True

    asyncio.run(go())


def test_fire_room_rule_partial_failure(registry, groups):
    g = groups.create_group("Room")
    # One id is not in the registry -> reported as failed by the fan-out.
    groups.update_group(g["id"], device_ids=["10.0.0.1", "9.9.9.9"])

    async def go():
        rule = _rule(target={"type": "room", "id": g["id"]})
        result = await scheduler.fire_rule(rule, registry=registry, groups=groups)
        assert result == "partial: 1 failed"
        assert registry.get("10.0.0.1").is_on is True

    asyncio.run(go())


def test_fire_unknown_room_is_error(registry, groups):
    async def go():
        rule = _rule(target={"type": "room", "id": "nope"})
        result = await scheduler.fire_rule(rule, registry=registry, groups=groups)
        assert result == "error: unknown room"

    asyncio.run(go())


# ── Tick (fire due rules + record + notify) ───────────────────────────────────


def test_tick_fires_due_records_and_publishes(registry, groups, tmp_path):
    store = ScheduleStore(tmp_path / "s.json")
    created = store.create_rule(_rule())  # Monday 18:30, device on
    store.create_rule(_rule(time="09:00"))  # not due at 18:30
    broadcaster = AsyncMock()

    async def go():
        await scheduler.run_tick(
            store,
            MONDAY_1830,
            registry=registry,
            groups=groups,
            broadcaster=broadcaster,
        )

    asyncio.run(go())

    assert registry.get("10.0.0.1").is_on is True
    fired = store.get_rule(created["id"])["last_fired"]
    assert fired["result"] == "ok"
    assert fired["ts"] == int(MONDAY_1830.timestamp())
    broadcaster.publish_now.assert_awaited_once()


def test_tick_with_nothing_due_does_not_publish(registry, groups, tmp_path):
    store = ScheduleStore(tmp_path / "s.json")
    store.create_rule(_rule(time="09:00"))
    broadcaster = AsyncMock()

    async def go():
        await scheduler.run_tick(
            store,
            MONDAY_1830,
            registry=registry,
            groups=groups,
            broadcaster=broadcaster,
        )

    asyncio.run(go())
    broadcaster.publish_now.assert_not_awaited()


# ── Loop resilience & cancellation (mirrors run_recorder's tests) ─────────────


def test_scheduler_survives_a_failing_cycle_and_is_cancellable(monkeypatch):
    # Spin fast so several cycles run in a blink.
    monkeypatch.setattr(scheduler, "_seconds_until_next_minute", lambda now: 0.005)

    class BoomStore:
        def list_rules(self):
            raise RuntimeError("store on fire")

    async def drive():
        task = asyncio.create_task(
            scheduler.run_scheduler(BoomStore(), None, None, AsyncMock())
        )
        await asyncio.sleep(0.05)  # several failing cycles
        assert not task.done()  # the loop swallowed the errors and kept going
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(drive())
