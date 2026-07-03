import asyncio
import contextlib
import datetime
import os
import time
from unittest.mock import AsyncMock

import pytest
from conftest import FakeChild, FakeDevice

from api import scheduler, solar
from api.group_store import GroupStore
from api.kasa_service import DeviceRegistry
from api.schedule_store import ScheduleStore
from api.schemas import SceneApplyResult

# Jan 1 2024 is a Monday, so weekday() == 0. Building the instant through
# ``astimezone`` gives the aware local datetime the scheduler compares against.
MONDAY_1830 = datetime.datetime(2024, 1, 1, 18, 30).astimezone()
TUESDAY_1830 = MONDAY_1830 + datetime.timedelta(days=1)

# A real location (New York City) for sunrise/sunset cases. Expected fire minutes
# are derived from ``solar`` and the machine's local zone, so the tests hold in
# any timezone rather than hard-coding a wall-clock time.
NYC = (40.7128, -74.0060)

# The solar date used by the sun-rule cases. A rule's ``days`` gate on the sun
# event's own (location-civil) weekday, NOT the server-local weekday of the fire
# minute — on a UTC server a summer NYC sunset lands after UTC midnight, one
# server-local weekday later.
SUN_DAY = datetime.date(2024, 6, 21)


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


# ── Sunrise / sunset rules ────────────────────────────────────────────────────


def _sun_local(kind: str, day: datetime.date) -> datetime.datetime:
    """Today's sunrise/sunset for NYC in the machine's local zone, minute-truncated."""
    fn = solar.sunrise if kind == "sunrise" else solar.sunset
    return fn(day, *NYC).astimezone().replace(second=0, microsecond=0)


@pytest.mark.parametrize("kind", ["sunrise", "sunset"])
def test_sun_rule_due_at_computed_local_minute(kind):
    fire = _sun_local(kind, SUN_DAY)
    rule = _rule(kind=kind, days=[SUN_DAY.weekday()], time=None)
    assert scheduler.rule_due_at(rule, fire, location=NYC) is True
    off = fire + datetime.timedelta(minutes=1)
    assert scheduler.rule_due_at(rule, off, location=NYC) is False


def test_sun_rule_never_due_without_location():
    fire = _sun_local("sunrise", SUN_DAY)
    rule = _rule(kind="sunrise", days=[SUN_DAY.weekday()], time=None)
    # No location -> the rule silently doesn't fire (the loop warns once).
    assert scheduler.rule_due_at(rule, fire, location=None) is False


def test_sun_rule_offset_shifts_fire_minute():
    base = _sun_local("sunset", SUN_DAY)
    fire = base - datetime.timedelta(minutes=30)
    # The offset shifts the fire minute but the weekday stays the sun event's own.
    rule = _rule(kind="sunset", days=[SUN_DAY.weekday()], time=None, offset_minutes=-30)
    assert scheduler.rule_due_at(rule, fire, location=NYC) is True
    # Without the offset it would be due at ``base``, so it must not be now.
    assert scheduler.rule_due_at(rule, base, location=NYC) is False


def test_sun_rule_respects_weekday():
    fire = _sun_local("sunrise", SUN_DAY)
    other_day = (SUN_DAY.weekday() + 1) % 7
    rule = _rule(kind="sunrise", days=[other_day], time=None)
    assert scheduler.rule_due_at(rule, fire, location=NYC) is False


@contextlib.contextmanager
def _server_tz(name: str):
    """Run the block as if the server's local zone were ``name`` (POSIX TZ)."""
    prev = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = prev
        time.tzset()


def test_sun_rule_fires_on_utc_server():
    """Regression: a UTC server (CI, and the Docker default) still fires NYC sunset.

    Summer NYC sunset is past midnight UTC — a different calendar date (and
    weekday) than the event's. The old date-string comparison silently never
    matched the time, and the old weekday gate checked the server-local (UTC)
    weekday, so a rule for the event's own weekday never fired either.
    """
    with _server_tz("UTC"):
        fire = _sun_local("sunset", SUN_DAY)
        # The event is past midnight UTC: the server-local weekday differs.
        assert fire.weekday() != SUN_DAY.weekday()
        rule = _rule(kind="sunset", days=[SUN_DAY.weekday()], time=None)
        assert scheduler.rule_due_at(rule, fire, location=NYC) is True
        assert (
            scheduler.rule_due_at(
                rule, fire + datetime.timedelta(minutes=1), location=NYC
            )
            is False
        )
        # A rule gated on the server-local (UTC) weekday must NOT fire: days mean
        # the location's day, not the server's.
        wrong = _rule(kind="sunset", days=[fire.weekday()], time=None)
        assert scheduler.rule_due_at(wrong, fire, location=NYC) is False

        early = fire - datetime.timedelta(minutes=30)
        rule = _rule(
            kind="sunset", days=[SUN_DAY.weekday()], time=None, offset_minutes=-30
        )
        assert scheduler.rule_due_at(rule, early, location=NYC) is True


# ── One-shot (once) rules ─────────────────────────────────────────────────────


def _once(**over) -> dict:
    base = {
        "id": "o1",
        "kind": "once",
        "enabled": True,
        "at": "2024-01-01T18:30",  # matches MONDAY_1830's local wall clock
        "target": {"type": "device", "id": "10.0.0.1"},
        "action": "on",
    }
    return {**base, **over}


def test_once_rule_due_at_its_minute():
    assert scheduler.rule_due_at(_once(), MONDAY_1830) is True


def test_once_rule_not_due_other_minute():
    assert scheduler.rule_due_at(_once(at="2024-01-01T18:31"), MONDAY_1830) is False


def test_once_rule_needs_no_days():
    # ``once`` has no weekday gate; it fires purely on its ``at`` minute.
    assert scheduler.rule_due_at(_once(), MONDAY_1830) is True


def test_once_rule_bad_at_is_never_due():
    assert scheduler.rule_due_at(_once(at="not-a-date"), MONDAY_1830) is False


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


# ── DST and clock-step edges ─────────────────────────────────────────────────


def test_spring_forward_catchup_relabels_the_minute():
    """Catch-up minutes are re-localized to the offset of their own instant.

    Regression: ``last + timedelta`` carried last's pre-jump offset, so the
    minute after the spring-forward jump rendered as a nonexistent 02:00 — a
    02:00 rule fired and the real 03:00 was never evaluated.
    """
    with _server_tz("America/New_York"):
        last = datetime.datetime(2026, 3, 8, 1, 59).astimezone()  # EST
        current = (last + datetime.timedelta(minutes=1)).astimezone()  # 03:00 EDT
        assert current.strftime("%H:%M") == "03:00"
        got = scheduler.minutes_to_evaluate(last, current)
        assert [m.strftime("%H:%M") for m in got] == ["03:00"]


def test_fall_back_repeated_label_fires_once(registry, groups, tmp_path):
    """The repeated fall-back hour must not fire a fixed_time rule twice.

    Both instants of the repeated hour carry the same wall-clock label, so the
    rule is *due* at both; the ``last_fired`` guard suppresses the second.
    """
    with _server_tz("America/New_York"):
        # 01:30 EDT and 01:30 EST, one real hour apart, same local label.
        first = datetime.datetime(2026, 11, 1, 5, 30, tzinfo=datetime.UTC).astimezone()
        repeat = datetime.datetime(2026, 11, 1, 6, 30, tzinfo=datetime.UTC).astimezone()
        assert first.strftime("%H:%M") == repeat.strftime("%H:%M") == "01:30"

        store = ScheduleStore(tmp_path / "s.json")
        created = store.create_rule(_rule(time="01:30", days=[first.weekday()]))
        broadcaster = AsyncMock()

        async def tick(minute):
            await scheduler.run_tick(
                store, minute, registry=registry, groups=groups, broadcaster=broadcaster
            )

        asyncio.run(tick(first))
        assert registry.get("10.0.0.1").is_on is True
        first_fired = store.get_rule(created["id"])["last_fired"]

        registry.get("10.0.0.1").is_on = False  # changed since; a re-fire would show
        asyncio.run(tick(repeat))
        assert registry.get("10.0.0.1").is_on is False  # repeat hour: not re-fired
        assert store.get_rule(created["id"])["last_fired"] == first_fired

        # An ordinary daily fire (same label, a day later) is not suppressed.
        next_day = repeat + datetime.timedelta(days=1)
        rule = store.get_rule(created["id"])
        assert scheduler._fired_at_this_label_recently(rule, next_day) is False


def test_clock_step_back_does_not_refire(monkeypatch, tmp_path):
    """After an NTP step-back, minutes already evaluated must not fire again."""
    monkeypatch.setattr(scheduler, "_seconds_until_next_minute", lambda now: 0.001)
    fire = AsyncMock(return_value="ok")
    monkeypatch.setattr(scheduler, "fire_rule", fire)
    store = ScheduleStore(tmp_path / "s.json")
    store.create_rule(_rule())  # Monday 18:30

    # ``now_fn`` is read twice per cycle (the tick and the sleep computation).
    stepped_back = MONDAY_1830 - datetime.timedelta(minutes=5)
    ticks = iter([MONDAY_1830, MONDAY_1830, stepped_back, stepped_back])
    done = asyncio.Event()

    def now_fn():
        try:
            return next(ticks)
        except StopIteration:
            # Clock has re-advanced to the already-fired minute; hold there.
            done.set()
            return MONDAY_1830

    async def drive():
        task = asyncio.create_task(
            scheduler.run_scheduler(store, None, None, AsyncMock(), now_fn=now_fn)
        )
        await asyncio.wait_for(done.wait(), timeout=2)
        await asyncio.sleep(0.02)  # several more cycles at the re-advanced minute
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(drive())
    # Without the forward-only cursor, the step-back rewound it and the
    # re-advance replayed 18:30, double-firing the rule.
    fire.assert_awaited_once()


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


# ── Scene-action firing (apply_scene is monkeypatched, no real scenes) ─────────


def _scene_rule(**over) -> dict:
    base = {
        "id": "sc1",
        "kind": "fixed_time",
        "enabled": True,
        "time": "18:30",
        "days": [0],
        "action": "scene",
        "scene_id": "scene-1",
    }
    return {**base, **over}


def test_fire_scene_rule_calls_apply_scene(registry, groups, monkeypatch):
    seen = {}

    async def fake_apply(scene_id):
        seen["id"] = scene_id
        return SceneApplyResult(succeeded=["d1", "d2"], failed=[])

    monkeypatch.setattr(scheduler, "apply_scene", fake_apply)

    async def go():
        result = await scheduler.fire_rule(
            _scene_rule(), registry=registry, groups=groups
        )
        assert result == "ok"
        assert seen["id"] == "scene-1"

    asyncio.run(go())


def test_fire_scene_rule_partial_failure(registry, groups, monkeypatch):
    async def fake_apply(scene_id):
        return SceneApplyResult(succeeded=["d1"], failed=["d2"])

    monkeypatch.setattr(scheduler, "apply_scene", fake_apply)

    async def go():
        result = await scheduler.fire_rule(
            _scene_rule(), registry=registry, groups=groups
        )
        assert result == "partial: 1 failed"

    asyncio.run(go())


def test_fire_scene_rule_unknown_scene_is_error_not_raise(
    registry, groups, monkeypatch
):
    async def fake_apply(scene_id):
        raise scheduler.SceneNotFoundError(scene_id)

    monkeypatch.setattr(scheduler, "apply_scene", fake_apply)

    async def go():
        rule = _scene_rule(scene_id="gone")
        result = await scheduler.fire_rule(rule, registry=registry, groups=groups)
        assert result == "error: unknown scene"

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


def test_tick_once_rule_auto_disables_but_is_kept(registry, groups, tmp_path):
    store = ScheduleStore(tmp_path / "s.json")
    created = store.create_rule(_once(at="2024-01-01T18:30"))  # due at MONDAY_1830
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

    rule = store.get_rule(created["id"])
    # Kept (not deleted) so the user sees it ran, but disabled so it won't repeat.
    assert rule is not None
    assert rule["enabled"] is False
    assert rule["last_fired"]["result"] == "ok"
    assert registry.get("10.0.0.1").is_on is True


def test_tick_marks_missed_once_rule(registry, groups, tmp_path):
    """A one-shot whose minute passed unseen is disabled as "missed", not fired.

    Regression: it used to stay enabled forever, armed for a minute that will
    never come again (its exact-label match can't hit a past minute).
    """
    store = ScheduleStore(tmp_path / "s.json")
    created = store.create_rule(_once(at="2024-01-01T18:20"))  # 10 min before tick
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

    rule = store.get_rule(created["id"])
    assert rule["enabled"] is False
    assert rule["last_fired"]["result"] == "missed"
    assert registry.get("10.0.0.1").is_on is False  # it did NOT fire late
    broadcaster.publish_now.assert_not_awaited()


def test_tick_warns_once_for_sun_rule_without_location(
    registry, groups, tmp_path, monkeypatch
):
    # Reset the module-level guard so the warning path is exercised deterministically.
    monkeypatch.setattr(scheduler, "_warned_no_location", False)
    store = ScheduleStore(tmp_path / "s.json")
    store.create_rule(_rule(kind="sunrise", days=[0], time=None))
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
    # The guard flips so subsequent ticks don't re-warn; nothing fired.
    assert scheduler._warned_no_location is True
    broadcaster.publish_now.assert_not_awaited()


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
