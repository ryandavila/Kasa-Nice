"""Background evaluation of server-side schedule rules.

A single asyncio task, started in ``main``'s lifespan, wakes once a minute and
fires any rule whose ``time`` and ``days`` match the current *local* wall clock.
Modelled on ``run_recorder``: resilient (a bad rule or failed cycle is logged,
never fatal) and cancellable for clean shutdown.

Firing routes through the same code paths the REST API uses: device rules through
``registry.set_power``, room rules through ``routes._set_power_many``, then one
``broadcaster.publish_now()``.

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

from .logging_config import get_logger
from .routes import _set_power_many

logger = get_logger(__name__)

# Beyond this many missed minutes, assume the process was suspended (not merely
# slow) and act on the current minute only, not a backlog of stale actions.
_MAX_CATCHUP_MINUTES = 2

# The only rule kind v1 runs; others (from a newer build, downgraded) are skipped
# rather than erroring the cycle.
_SUPPORTED_KIND = "fixed_time"


def _local_now() -> datetime.datetime:
    """Current time as a timezone-aware datetime in the server's local zone.

    Schedules are wall-clock, so evaluation uses local time and stays correct
    across DST shifts (which ``astimezone`` with the system zone handles).
    """
    return datetime.datetime.now().astimezone()


def rule_due_at(rule: dict, minute: datetime.datetime) -> bool:
    """Whether ``rule`` should fire during the given local ``minute``.

    Pure, so the decision is testable without a clock. ``minute`` is truncated to
    the minute; a rule matches when enabled, of a supported kind, its weekday is
    listed, and its HH:MM equals the minute's.
    """
    if not rule.get("enabled", False):
        return False
    if rule.get("kind", _SUPPORTED_KIND) != _SUPPORTED_KIND:
        return False
    if minute.weekday() not in rule.get("days", []):
        return False
    return rule.get("time") == minute.strftime("%H:%M")


def due_rules(rules: list[dict], minute: datetime.datetime) -> list[dict]:
    """The subset of ``rules`` due to fire during local ``minute`` (pure)."""
    return [r for r in rules if rule_due_at(r, minute)]


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
    return [last + datetime.timedelta(minutes=i) for i in range(1, gap + 1)]


async def fire_rule(rule: dict, *, registry, groups) -> str:
    """Apply one rule's action to its target; return a short result string.

    Never raises: a device error, missing device, or unknown room becomes a
    descriptive result recorded as the rule's ``last_fired``.
    """
    on = rule.get("action") == "on"
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
        result = await _set_power_many(group["device_ids"], on)
        if not result.failed:
            return "ok"
        if not result.succeeded:
            return f"error: all {len(result.failed)} failed"
        return f"partial: {len(result.failed)} failed"

    return f"error: unknown target type {target_type!r}"


async def run_tick(store, minute: datetime.datetime, *, registry, groups, broadcaster):
    """Fire every rule due in ``minute``, record results, and push one update.

    Records each outcome via ``mark_fired`` (stamped with the scheduled minute,
    so it's deterministic) and, if anything was due, nudges the broadcaster once.
    """
    due = due_rules(store.list_rules(), minute)
    if not due:
        return
    ts = int(minute.timestamp())
    for rule in due:
        result = await fire_rule(rule, registry=registry, groups=groups)
        store.mark_fired(rule["id"], ts, result)
    await broadcaster.publish_now()


def _seconds_until_next_minute(now: datetime.datetime) -> float:
    """Seconds to sleep to land just past the next minute boundary.

    The 0.5s cushion ensures the new minute has fully ticked over before we
    evaluate; the 1s floor stops a near-boundary wake from busy-spinning.
    """
    into_minute = now.second + now.microsecond / 1_000_000
    return max(1.0, 60.0 - into_minute + 0.5)


async def run_scheduler(store, registry, groups, broadcaster, *, now_fn=_local_now):
    """Evaluate schedule rules once a minute until cancelled.

    Background task launched at startup. Resilient: a bad rule is contained in
    ``fire_rule``, a failed cycle is logged and the loop continues, cancellation
    propagates. ``now_fn`` is injectable so tests can drive it without a clock.
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
                )
            last_minute = current
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - the scheduler must never crash startup
            logger.error(f"Scheduler cycle failed: {e}")
        await asyncio.sleep(_seconds_until_next_minute(now_fn()))
