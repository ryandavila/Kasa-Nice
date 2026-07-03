"""Background evaluation of server-side schedule rules.

A single asyncio task, started in ``main``'s lifespan, wakes once a minute and
fires any rule whose ``time`` and ``days`` match the current *local* wall clock.
Modelled on ``run_recorder``: resilient (a bad rule or failed cycle is logged,
never fatal) and cancellable for clean shutdown.

Firing routes through the same code paths the REST API uses: device rules through
``registry.set_power``, room rules through ``kasa_service.set_power_many``, then
one ``broadcaster.publish_now()``.

Tick strategy — a minute *cursor*, not ``sleep(60)``: a fixed sleep drifts off
the minute boundary, and a slow cycle or suspended process could make a scheduled
minute pass unseen. Instead sleep until just past the next boundary (recomputed
each loop so drift can't accumulate) and track the last minute evaluated. Each
wake processes every whole minute strictly after the cursor up to now — so a
minute is never missed (a short overrun catches up) nor double-fired (the cursor
only moves forward). A long gap (laptop sleep, container pause) is capped to the
current minute rather than replaying a backlog of toggles.
"""

import asyncio
import datetime

from . import solar
from .kasa_service import set_power_many
from .logging_config import get_logger
from .scene_service import SceneNotFoundError, apply_scene

logger = get_logger(__name__)

# Beyond this many missed minutes, assume the process was suspended (not merely
# slow) and act on the current minute only, not a backlog of stale actions.
_MAX_CATCHUP_MINUTES = 2

# Rule kinds this build evaluates. A kind outside this set (e.g. from a newer
# build, downgraded) is skipped rather than erroring the cycle. ``sunrise`` and
# ``sunset`` additionally require a configured location; ``once`` fires at a
# single datetime then auto-disables (see ``run_tick``).
_SUPPORTED_KINDS = frozenset({"fixed_time", "sunrise", "sunset", "once"})

# A ``(latitude, longitude)`` in decimal degrees, or None when unconfigured.
Location = tuple[float, float] | None

# Guards the one-time warning that sunrise/sunset rules can't fire without a
# location; flipped true after the first warn so the log isn't spammed each tick.
_warned_no_location = False


def _local_now() -> datetime.datetime:
    """Current time as a timezone-aware datetime in the server's local zone.

    Schedules are wall-clock, so evaluation uses local time and stays correct
    across DST shifts (which ``astimezone`` with the system zone handles).
    """
    return datetime.datetime.now().astimezone()


def _sun_event_date(
    kind: str, rule: dict, minute: datetime.datetime, location: Location
) -> datetime.date | None:
    """The solar date whose sunrise/sunset (+offset) lands in local ``minute``.

    Compared as UTC instants, so the server's timezone drops out: the offset is
    whole minutes, so firing means ``truncate(event) == minute - offset``.
    ``solar``'s event for date D falls within (D 00:00 UTC - 13h, +37h) at any
    longitude, so only four UTC dates can hit the target minute. None when
    location is unset, the sun doesn't cross the horizon that day (polar), or no
    date's event matches.

    The returned date is the one the event's *solar day* at the location spans
    (sun events sit within hours of solar noon, never near solar midnight), so
    its weekday is the location's civil weekday of the event — which is what the
    rule's ``days`` mean. The server-local weekday of ``minute`` is NOT that day
    on a server whose zone differs from the location's (the fd19353 scenario: a
    UTC server sees a summer NYC sunset after UTC midnight, one weekday later).
    """
    if location is None:
        return None
    lat, lon = location
    fn = solar.sunrise if kind == "sunrise" else solar.sunset
    offset = datetime.timedelta(minutes=rule.get("offset_minutes", 0) or 0)
    target = (minute - offset).astimezone(datetime.UTC)
    for delta in range(-2, 2):
        day = target.date() + datetime.timedelta(days=delta)
        event = fn(day, lat, lon)
        if event and event.replace(second=0, microsecond=0) == target:
            return day
    return None


def _once_target_hhmm(rule: dict) -> str | None:
    """The one-shot ``at`` normalised to 'YYYY-MM-DDTHH:MM', or None if unusable.

    Compared as a wall-clock string (not a datetime) so a naive ``at`` and the
    aware minute cursor never trip datetime's naive/aware comparison rule.
    """
    at = rule.get("at")
    if not at:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(at)
    except ValueError, TypeError:
        return None
    return parsed.strftime("%Y-%m-%dT%H:%M")


def rule_due_at(
    rule: dict, minute: datetime.datetime, *, location: Location = None
) -> bool:
    """Whether ``rule`` should fire during the given local ``minute``.

    Pure, so the decision is testable without a clock. ``minute`` is truncated to
    the minute; a rule matches when enabled and of a supported kind, then per
    kind: a fixed_time rule's weekday+HH:MM match; a sunrise/sunset rule's weekday
    matches and today's (offset) sun time equals the minute; a one-shot rule's
    ``at`` equals the minute. Sun rules never match without ``location``.
    """
    if not rule.get("enabled", False):
        return False
    kind = rule.get("kind", "fixed_time")
    if kind not in _SUPPORTED_KINDS:
        return False

    if kind == "once":
        return _once_target_hhmm(rule) == minute.strftime("%Y-%m-%dT%H:%M")

    if kind == "fixed_time":
        if minute.weekday() not in rule.get("days", []):
            return False
        return rule.get("time") == minute.strftime("%H:%M")

    # sunrise / sunset: gate the weekday on the matched event's own solar date,
    # not on ``minute``'s server-local weekday — the event can fall on a
    # different server-local calendar day than the location's (see
    # ``_sun_event_date``), and ``days`` mean the location's day.
    event_date = _sun_event_date(kind, rule, minute, location)
    if event_date is None:
        return False
    return event_date.weekday() in rule.get("days", [])


def due_rules(
    rules: list[dict], minute: datetime.datetime, *, location: Location = None
) -> list[dict]:
    """The subset of ``rules`` due to fire during local ``minute`` (pure)."""
    return [r for r in rules if rule_due_at(r, minute, location=location)]


def minutes_to_evaluate(
    last: datetime.datetime | None,
    current: datetime.datetime,
    max_catchup: int = _MAX_CATCHUP_MINUTES,
) -> list[datetime.datetime]:
    """Minutes to evaluate this wake-up, given the cursor and now (pure).

    Every whole minute strictly after ``last`` through ``current``, so an overrun
    catches missed minutes and a repeat wake in the same minute yields nothing. A
    gap larger than ``max_catchup`` collapses to just ``current``.
    """
    if last is None:
        return [current]
    gap = int((current - last).total_seconds() // 60)
    if gap <= 0:
        return []  # same minute (or clock stepped back): already handled
    if gap > max_catchup:
        return [current]  # suspended too long — don't replay a backlog
    # ``last + timedelta`` is the right *instant* but carries last's fixed UTC
    # offset; re-localize each one so its wall-clock label is correct for that
    # instant. Otherwise the minute after a DST jump keeps the old offset and
    # renders as a nonexistent (or wrong-hour) local time — firing a 02:00 rule
    # on spring-forward and skipping the real 03:00. Equality is unaffected
    # (aware datetimes compare as instants), only the label changes.
    return [
        (last + datetime.timedelta(minutes=i)).astimezone() for i in range(1, gap + 1)
    ]


def _fired_at_this_label_recently(rule: dict, minute: datetime.datetime) -> bool:
    """Whether ``rule`` already fired at this wall-clock label within two hours.

    During the DST fall-back the repeated hour makes every wall-clock label
    occur at two instants an hour apart, so a fixed_time rule matching by label
    would fire twice. The rule's ``last_fired`` timestamp is enough to detect
    the repeat: same HH:MM label, under two hours ago. A daily rule can't
    legitimately fire at the same label twice within that window, and an edited
    ``time`` changes the label, so nothing real is suppressed.
    """
    last = (rule.get("last_fired") or {}).get("ts")
    if not last:
        return False
    delta = minute.timestamp() - last
    if not (0 < delta <= 2 * 3600):
        return False
    fired_label = datetime.datetime.fromtimestamp(last).astimezone().strftime("%H:%M")
    return fired_label == minute.strftime("%H:%M")


def _missed_once_rules(rules: list[dict], minute: datetime.datetime) -> list[dict]:
    """Enabled one-shots whose ``at`` is already strictly before ``minute``.

    A one-shot missed while the process was down or suspended (beyond the
    catch-up window) would otherwise stay enabled forever, matching no future
    minute — invisible dead weight the UI shows as an armed rule. The labels'
    fixed 'YYYY-MM-DDTHH:MM' shape makes string comparison chronological.
    """
    current = minute.strftime("%Y-%m-%dT%H:%M")
    missed: list[dict] = []
    for rule in rules:
        if not rule.get("enabled", False) or rule.get("kind") != "once":
            continue
        target = _once_target_hhmm(rule)
        if target is not None and target < current:
            missed.append(rule)
    return missed


def _fanout_result(succeeded: list, failed: list) -> str:
    """Summarise a fan-out (device list) outcome as a short result string."""
    if not failed:
        return "ok"
    if not succeeded:
        return f"error: all {len(failed)} failed"
    return f"partial: {len(failed)} failed"


async def fire_rule(rule: dict, *, registry, groups) -> str:
    """Apply one rule's action to its target; return a short result string.

    Never raises: a device error, missing device, unknown room, or unknown scene
    becomes a descriptive result recorded as the rule's ``last_fired``.
    """
    action = rule.get("action")

    if action == "scene":
        # Scenes own their device list, so there's no target. Reuse the scene
        # service's partial-failure-tolerant fan-out; an unknown/deleted scene is
        # reported, never fatal to the loop.
        scene_id = rule.get("scene_id")
        try:
            result = await apply_scene(scene_id)
        except SceneNotFoundError:
            logger.warning(
                f"Schedule {rule.get('id')} references unknown scene {scene_id}"
            )
            return "error: unknown scene"
        except Exception as e:  # noqa: BLE001 - report, never crash the tick
            logger.warning(f"Schedule {rule.get('id')} scene {scene_id} failed: {e}")
            return f"error: {e}"
        return _fanout_result(result.succeeded, result.failed)

    on = action == "on"
    target = rule.get("target") or {}
    target_type = target.get("type")
    target_id = target.get("id")

    if target_type == "device":
        try:
            await registry.set_power(target_id, on)
        except Exception as e:  # noqa: BLE001 - report, never crash the tick
            logger.warning(f"Schedule {rule.get('id')} device {target_id} failed: {e}")
            return f"error: {e}"
        return "ok"

    if target_type == "room":
        group = groups.get_group(target_id)
        if group is None:
            logger.warning(
                f"Schedule {rule.get('id')} targets unknown room {target_id}"
            )
            return "error: unknown room"
        result = await set_power_many(registry, group["device_ids"], on)
        return _fanout_result(result.succeeded, result.failed)

    return f"error: unknown target type {target_type!r}"


def _warn_once_no_location(rules: list[dict]) -> None:
    """Warn (once) if a sun rule can't fire because no location is configured.

    Sunrise/sunset rules silently never fire without a location; a single log
    line makes that visible without spamming every minute.
    """
    global _warned_no_location
    if _warned_no_location:
        return
    if any(
        r.get("enabled", False) and r.get("kind") in ("sunrise", "sunset")
        for r in rules
    ):
        logger.warning(
            "Sunrise/sunset schedule(s) exist but KASA_LATITUDE/KASA_LONGITUDE "
            "are unset; these rules will not fire until a location is configured."
        )
        _warned_no_location = True


async def run_tick(
    store,
    minute: datetime.datetime,
    *,
    registry,
    groups,
    broadcaster,
    location: Location = None,
):
    """Fire every rule due in ``minute``, record results, and push one update.

    Records each outcome (stamped with the scheduled minute, so it's
    deterministic) and, if anything was due, nudges the broadcaster once. A
    one-shot (``once``) rule is disabled after firing but kept, with its
    ``last_fired`` note, so the user sees that it ran rather than it vanishing;
    a one-shot whose minute was missed entirely (sleep/restart past the catch-up
    window) is likewise disabled with a "missed" note instead of staying armed
    forever for a minute that will never come again.
    """
    rules = store.list_rules()
    if location is None:
        _warn_once_no_location(rules)
    ts = int(minute.timestamp())
    for rule in _missed_once_rules(rules, minute):
        store.update_rule(
            rule["id"],
            {"enabled": False, "last_fired": {"ts": ts, "result": "missed"}},
        )
    fired_any = False
    for rule in due_rules(rules, minute, location=location):
        if _fired_at_this_label_recently(rule, minute):
            continue  # DST fall-back repeat of an already-fired label
        result = await fire_rule(rule, registry=registry, groups=groups)
        fields: dict = {"last_fired": {"ts": ts, "result": result}}
        if rule.get("kind") == "once":
            fields["enabled"] = False
        store.update_rule(rule["id"], fields)
        fired_any = True
    if fired_any:
        await broadcaster.publish_now()


def _seconds_until_next_minute(now: datetime.datetime) -> float:
    """Seconds to sleep to land just past the next minute boundary.

    The 0.5s cushion ensures the new minute has fully ticked over before we
    evaluate; the 1s floor stops a near-boundary wake from busy-spinning.
    """
    into_minute = now.second + now.microsecond / 1_000_000
    return max(1.0, 60.0 - into_minute + 0.5)


async def run_scheduler(
    store,
    registry,
    groups,
    broadcaster,
    *,
    location: Location = None,
    now_fn=_local_now,
):
    """Evaluate schedule rules once a minute until cancelled.

    Background task launched at startup. Resilient: a bad rule is contained in
    ``fire_rule``, a failed cycle is logged and the loop continues, cancellation
    propagates. ``location`` (the configured lat/long) lets sunrise/sunset rules
    resolve; ``now_fn`` is injectable so tests can drive it without a clock.
    """
    last_minute: datetime.datetime | None = None
    while True:
        try:
            current = now_fn().replace(second=0, microsecond=0)
            for minute in minutes_to_evaluate(last_minute, current):
                await run_tick(
                    store,
                    minute,
                    registry=registry,
                    groups=groups,
                    broadcaster=broadcaster,
                    location=location,
                )
            # Never move the cursor backward: after an NTP step-back the old
            # cursor already covers the replayed minutes, so keeping it prevents
            # re-firing them as the clock re-advances.
            if last_minute is None or current > last_minute:
                last_minute = current
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - the scheduler must never crash startup
            logger.error(f"Scheduler cycle failed: {e}")
        await asyncio.sleep(_seconds_until_next_minute(now_fn()))
