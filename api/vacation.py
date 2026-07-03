"""Presence simulation ("vacation mode"): make an empty home look occupied.

While enabled, a single background asyncio task — started in ``main``'s lifespan
alongside the scheduler and modelled on it (resilient, cancellable, clock- and
RNG-injectable) — flips a configured set of lights on and off at randomized
times inside a nightly active window, then turns everything off when the window
closes.

Design constraints that shaped this module:

* **Per-device jitter, not one global rhythm.** Each device keeps its OWN next
  toggle instant, drawn independently as ``now + uniform(min, max)`` minutes from
  its previous toggle. Because the draws are independent, two devices never fall
  into lockstep, so the pattern doesn't read as mechanical (all lights blinking
  together every N minutes is the tell-tale sign of an automated house).

* **Never fight the scheduler or a person.** The engine remembers the state it
  last *itself* wrote to each device (``_expected``). Before a scheduled toggle
  it re-reads the live state; if that no longer matches what the engine last set
  — someone (a schedule rule, the Kasa app, a wall switch, another API caller)
  changed it since — the engine backs off that device for a cooldown so it isn't
  yanked back mid-interaction. This needs no snapshot store or new persisted
  field: the engine is the only actor that "expects" its own last write, so a
  mismatch is a reliable, cheap signal that some other source intervened. A
  device the engine hasn't touched yet has no expectation and is simply adopted.

* **Deterministic testability.** ``now_fn`` and ``rng`` are injected exactly as
  the scheduler injects ``now_fn``, and the schedule/step logic is factored into
  pure helpers, so a test can advance a fake clock across a whole evening with a
  seeded ``random.Random`` and assert the switch pattern, the all-off at window
  close, and the manual-change skip — with no real clock, sleeps, or hardware.

The window boundaries are computed per-day from the config: ``end_time`` is a
fixed local 'HH:MM'; ``start_time`` is either a fixed 'HH:MM' or, when unset,
today's sunset for the configured location (falling back to 19:00 when no
location is configured — the same fallback the feature spec calls for).
"""

import asyncio
import datetime
import random

from . import solar
from .kasa_service import DeviceRegistry, set_power_many
from .logging_config import get_logger
from .schemas import VacationConfig

logger = get_logger(__name__)

# A ``(latitude, longitude)`` in decimal degrees, or None when unconfigured —
# same shape the scheduler uses; redeclared here to keep the module standalone.
Location = tuple[float, float] | None

# Fallback window start when ``start_time`` is unset and no location is
# configured, so sunset can't be computed. A reasonable "lights come on" hour.
_DEFAULT_START = datetime.time(19, 0)

# How long the engine leaves a device alone after detecting an external change
# to it. Long enough to cover a person actively using the light or a schedule
# rule's action, short enough that the device rejoins the simulation the same
# evening. Expressed in minutes.
_MANUAL_COOLDOWN_MINUTES = 10

# How often the loop wakes to check for due toggles / the window edge. The
# simulation's granularity is coarse (minutes between switches), so a minute
# cursor like the scheduler's is plenty and keeps the loop cheap.
_TICK_SECONDS = 30.0


def _local_now() -> datetime.datetime:
    """Current time as a timezone-aware local datetime (see scheduler._local_now)."""
    return datetime.datetime.now().astimezone()


def _window_start(
    config: VacationConfig, day: datetime.date, location: Location
) -> datetime.datetime:
    """The local datetime the active window opens on ``day``.

    A fixed ``start_time`` wins. Otherwise use today's sunset for the configured
    location; when no location is set (or the sun doesn't set that day, e.g.
    polar) fall back to a fixed 19:00 so the window always has a start.
    """
    if config.start_time is not None:
        start = datetime.time.fromisoformat(config.start_time)
        return _combine_local(day, start)
    if location is not None:
        event = solar.sunset(day, *location)
        if event is not None:
            # solar returns UTC; the window is reasoned about in local time.
            return event.astimezone().replace(second=0, microsecond=0)
    return _combine_local(day, _DEFAULT_START)


def _window_end(config: VacationConfig, day: datetime.date) -> datetime.datetime:
    """The local datetime the active window closes on ``day``."""
    return _combine_local(day, datetime.time.fromisoformat(config.end_time))


def _combine_local(day: datetime.date, when: datetime.time) -> datetime.datetime:
    """Combine a date and wall-clock time into an aware local datetime.

    ``astimezone`` on the naive combination resolves the local offset for that
    instant, so the boundary stays correct across DST like the scheduler's
    minute cursor does.
    """
    return datetime.datetime.combine(day, when).astimezone()


def _current_window(
    config: VacationConfig, now: datetime.datetime, location: Location
) -> tuple[datetime.datetime, datetime.datetime] | None:
    """Today's (start, end) window if ``now`` is inside it, else None (pure).

    The window is a single evening block that does NOT cross midnight (start <
    end, both same local day) — multi-window/overnight schedules are explicitly
    out of scope. ``now`` is in the window when ``start <= now < end``.
    """
    start = _window_start(config, now.date(), location)
    end = _window_end(config, now.date())
    if start <= now < end:
        return (start, end)
    return None


def _draw_next(
    now: datetime.datetime, config: VacationConfig, rng: random.Random
) -> datetime.datetime:
    """Draw the next toggle instant for a device: now + uniform(min, max) minutes.

    Pure given ``rng``, so a seeded generator makes the whole evening's schedule
    reproducible in tests. Minutes (not seconds) because the simulation is
    deliberately coarse — a home doesn't flick lights second-by-second.
    """
    gap = rng.uniform(config.min_interval_minutes, config.max_interval_minutes)
    return now + datetime.timedelta(minutes=gap)


def resolve_device_ids(config: VacationConfig, groups) -> list[str]:
    """The full device set: explicit ``device_ids`` plus every room's members.

    Resolved fresh from the group store at call time (not cached) so editing a
    room while vacation mode runs takes effect on the next tick. De-duplicated,
    preserving first-seen order (explicit devices first, then room members).
    """
    seen: set[str] = set()
    out: list[str] = []
    for device_id in config.device_ids:
        if device_id not in seen:
            seen.add(device_id)
            out.append(device_id)
    for room_id in config.room_ids:
        group = groups.get_group(room_id)
        if group is None:
            continue
        for device_id in group.get("device_ids", []):
            if device_id not in seen:
                seen.add(device_id)
                out.append(device_id)
    return out


class VacationEngine:
    """Runs the presence simulation loop and answers status queries.

    Holds only in-memory run state (each device's next toggle instant, the state
    the engine last wrote, and per-device cooldowns); the config itself lives in
    the ``VacationStore``. One instance is created at startup and its ``run`` is
    launched as a background task, mirroring the scheduler.
    """

    def __init__(
        self,
        store,
        registry: DeviceRegistry,
        groups,
        broadcaster,
        *,
        location: Location = None,
        now_fn=_local_now,
        rng: random.Random | None = None,
    ) -> None:
        self._store = store
        self._registry = registry
        self._groups = groups
        self._broadcaster = broadcaster
        self._location = location
        self._now_fn = now_fn
        # Injected so tests are deterministic; defaults to a fresh, unseeded RNG.
        self._rng = rng or random.Random()

        # Per-device next planned toggle instant. Populated when a device enters
        # the active window; cleared when the window closes so a new evening
        # re-draws a fresh schedule.
        self._next_toggle: dict[str, datetime.datetime] = {}
        # The on/off state the engine last wrote per device, used to detect an
        # external change (live state != expected => someone else acted).
        self._expected: dict[str, bool] = {}
        # Until when (local datetime) a device is left alone after an external
        # change. Absent => no active cooldown.
        self._cooldown_until: dict[str, datetime.datetime] = {}
        # True while the loop currently considers the window open, so the
        # window-close all-off runs exactly once per evening (on the edge).
        self._window_open = False

    # ── Status (read by the REST layer) ──────────────────────────────────────

    def next_switch_ts(self) -> int | None:
        """Unix seconds of the soonest planned toggle across devices, or None."""
        if not self._next_toggle:
            return None
        return int(min(self._next_toggle.values()).timestamp())

    def is_active(self, config: VacationConfig) -> bool:
        """Whether mode is enabled and the current local time is in the window."""
        if not config.enabled:
            return False
        return _current_window(config, self._now_fn(), self._location) is not None

    # ── One tick of the simulation (pure-ish: state in, actions out) ──────────

    def _externally_changed(self, device_id: str) -> bool:
        """Whether the device's live state differs from what the engine last set.

        A device the engine hasn't written yet (no expectation) counts as not
        externally changed — it's simply adopted on first touch. A missing or
        unreadable device is treated as "not changed" so a transient read miss
        doesn't spuriously trigger a cooldown; the toggle attempt itself, routed
        through the tolerant fan-out, will just report the failure.
        """
        expected = self._expected.get(device_id)
        if expected is None:
            return False
        try:
            device = self._registry.get(device_id)
        except KeyError:
            return False
        return bool(device.is_on) != expected

    def _due_devices(
        self, now: datetime.datetime, device_ids: list[str], config: VacationConfig
    ) -> list[str]:
        """Devices whose planned toggle instant has arrived (and aren't cooling).

        A device with no scheduled instant yet is scheduled here (first sight in
        the window) but not toggled this tick, so its first switch is itself
        jittered rather than every device firing at the window's opening minute.
        """
        due: list[str] = []
        for device_id in device_ids:
            cooldown = self._cooldown_until.get(device_id)
            if cooldown is not None and now < cooldown:
                continue
            planned = self._next_toggle.get(device_id)
            if planned is None:
                # First time seen this window: arm a jittered first toggle.
                self._next_toggle[device_id] = _draw_next(now, config, self._rng)
                continue
            if now >= planned:
                due.append(device_id)
        return due

    async def _toggle(
        self, now: datetime.datetime, device_id: str, config: VacationConfig
    ) -> None:
        """Flip one device unless an external change means we should back off.

        On an external change: record a cooldown, adopt the new live state as the
        expectation, and re-draw the next toggle so the device rejoins later
        without a burst. Otherwise flip relative to the engine's own expectation
        (defaulting a never-seen device to "turn on"), write it, and re-draw.
        """
        if self._externally_changed(device_id):
            self._cooldown_until[device_id] = now + datetime.timedelta(
                minutes=_MANUAL_COOLDOWN_MINUTES
            )
            try:
                self._expected[device_id] = bool(self._registry.get(device_id).is_on)
            except KeyError:
                self._expected.pop(device_id, None)
            self._next_toggle[device_id] = _draw_next(now, config, self._rng)
            return

        target = not self._expected.get(device_id, False)
        result = await set_power_many(self._registry, [device_id], target)
        if device_id in result.succeeded:
            self._expected[device_id] = target
        # Re-draw regardless of success: a device that failed this tick shouldn't
        # be retried every tick (that would hammer an offline device); it gets a
        # fresh jittered slot like any other.
        self._next_toggle[device_id] = _draw_next(now, config, self._rng)

    async def _close_window(self, device_ids: list[str]) -> bool:
        """Turn every configured device off at window close; clear run state.

        Returns whether anything was switched (so the caller can publish). Skips
        a device an external actor is mid-interaction with, matching the never-
        fight rule — the point is to leave the house dark for the night, not to
        override a person who just turned a light on.
        """
        to_off = [d for d in device_ids if not self._externally_changed(d)]
        switched = False
        if to_off:
            result = await set_power_many(self._registry, to_off, False)
            switched = bool(result.succeeded)
        # New evening starts with a fresh schedule and no stale expectations.
        self._next_toggle.clear()
        self._expected.clear()
        self._cooldown_until.clear()
        return switched

    async def run_tick(self, now: datetime.datetime, config: VacationConfig) -> None:
        """Advance the simulation by one tick given the current time and config.

        The single place the loop's decisions live, so it's driven directly by
        tests with a fake ``now`` and a seeded RNG. Publishes one SSE nudge when
        anything actually switched (a due toggle, or the window-close all-off).
        """
        device_ids = resolve_device_ids(config, self._groups)
        in_window = (
            config.enabled and _current_window(config, now, self._location) is not None
        )

        if not in_window:
            # Fire the all-off exactly on the falling edge (was open, now closed),
            # then stay quiet until the next window opens.
            if self._window_open:
                self._window_open = False
                if await self._close_window(device_ids):
                    await self._broadcaster.publish_now()
            return

        self._window_open = True
        switched = False
        for device_id in self._due_devices(now, device_ids, config):
            await self._toggle(now, device_id, config)
            switched = True
        if switched:
            await self._broadcaster.publish_now()

    async def run(self) -> None:
        """Wake every tick and advance the simulation until cancelled.

        Resilient and cancellable exactly like ``run_scheduler``: a failed cycle
        is logged and the loop continues, cancellation propagates for clean
        shutdown. Reads the config fresh each tick so a PUT takes effect without
        a restart (the same live-reload the scheduler gives schedule edits).
        """
        while True:
            try:
                await self.run_tick(self._now_fn(), self._store.load())
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - the engine must never crash startup
                logger.error(f"Vacation cycle failed: {e}")
            await asyncio.sleep(_TICK_SECONDS)


# Module-level singleton wired to the shared registry/groups/broadcaster and the
# configured location, mirroring how the scheduler is constructed. ``main``'s
# lifespan launches ``engine.run`` as a background task, and the vacation router
# reads ``engine`` for live status (next planned switch, active flag). Built here
# (not in the router) so both the loop and the endpoint share one instance.
def _build_engine() -> VacationEngine:
    from .config import get_settings
    from .events import broadcaster
    from .group_store import groups
    from .kasa_service import registry

    return VacationEngine(
        vacation_store,
        registry,
        groups,
        broadcaster,
        location=get_settings().location,
    )


# Imported lazily inside the factory to avoid an import cycle at module load
# (kasa_service imports schemas; this module imports kasa_service).
from .vacation_store import vacation_store  # noqa: E402

engine = _build_engine()
