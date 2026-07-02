"""Device alerts: unreachable/recovered and power-draw threshold detectors.

Two v1 detectors, evaluated together on an interval by a background task
(``run_alert_evaluator``) modelled on ``energy_history.run_recorder`` — resilient
(a bad cycle is logged, never fatal) and cancellable for clean shutdown. Skipped
under ``KASA_FAKE_DEVICES`` like the recorder/scheduler.

Delivery is twofold: every alert is appended to a bounded in-memory ring buffer
(served by ``GET /api/alerts/recent``) and, when ``KASA_ALERT_WEBHOOK_URL`` is
set, POSTed to that URL ntfy-compatibly. The ring buffer is **not** persisted
across restarts in v1 (a restart starts with an empty history); the thresholds
that drive the power detector *are* persisted (a small JSON store like favorites).

Debounce is the point of the design: one incident yields one alert, not one per
cycle. The decision lives in ``AlertEvaluator`` — a pure state machine with no
clock or I/O, so the transition logic is unit-testable — which diffs each cycle's
readings against the last: reachability fires on the edge into *and* out of the
bad state, while the power detector fires only on the rising edge over the
threshold and re-arms once draw drops back below it.
"""

import asyncio
import json
import sqlite3
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import httpx

from .config import get_settings
from .logging_config import get_logger
from .schemas import Alert, AlertType

logger = get_logger(__name__)

# Newest N alerts kept in memory. A deque with maxlen evicts the oldest, so the
# buffer is self-bounding and never needs pruning. Not persisted (see module doc).
_RING_MAXLEN = 100

# A power reading is ignored once it's older than this: the recorder samples on
# its own (coarser) interval, so a device that dropped off long ago shouldn't
# still trip a wattage alert from a stale row. Generous relative to the default
# 300s sample interval so a live device is never missed.
_POWER_STALENESS_SECONDS = 15 * 60

# Webhook POSTs are best-effort background work; a short timeout keeps a slow
# endpoint from stalling the evaluation loop.
_WEBHOOK_TIMEOUT_SECONDS = 10.0


class DeviceReading(NamedTuple):
    """One device's state for a single evaluation cycle.

    ``power_w`` is None when no recent reading is available (unmetered, or no
    fresh sample); the power detector treats that as "unknown" and leaves its
    armed state untouched rather than re-arming on a transient gap.
    """

    device_id: str
    alias: str
    reachable: bool
    power_w: float | None


@dataclass(frozen=True)
class AlertDraft:
    """An alert the evaluator decided to fire, before id/timestamp are stamped.

    Kept separate from :class:`~api.schemas.Alert` so the evaluator stays pure
    (no clock, no uuid); :meth:`AlertCenter.emit` stamps the volatile fields.
    """

    type: AlertType
    device_id: str
    message: str
    power_w: float | None = None
    threshold_w: float | None = None


class AlertEvaluator:
    """Pure debounce state machine turning per-cycle readings into alert edges.

    Holds the previous cycle's per-device state so it can fire only on a
    *transition*. No clock or I/O, so a test can drive it with hand-built
    readings and assert exactly which alerts each cycle yields.
    """

    def __init__(self) -> None:
        # Last-seen reachability per device. Absent => never seen: the first sight
        # seeds the baseline silently, so an already-offline device at startup
        # doesn't spuriously alert as "just became unreachable".
        self._reachable: dict[str, bool] = {}
        # Whether each device is currently latched "over threshold". Latching is
        # what debounces: we alert on the rising edge and won't alert again until
        # draw drops back below the threshold (clearing the latch).
        self._over_threshold: dict[str, bool] = {}

    def evaluate(
        self, readings: list[DeviceReading], thresholds: dict[str, float]
    ) -> list[AlertDraft]:
        """Fold one cycle's readings into the state, returning alerts to fire."""
        drafts: list[AlertDraft] = []
        for reading in readings:
            drafts.extend(self._reachability(reading))
            drafts.extend(self._power(reading, thresholds.get(reading.device_id)))
        return drafts

    def _reachability(self, reading: DeviceReading) -> list[AlertDraft]:
        prev = self._reachable.get(reading.device_id)
        self._reachable[reading.device_id] = reading.reachable
        if prev is None or prev == reading.reachable:
            # First sight (seed baseline) or no change: nothing to announce.
            return []
        if prev and not reading.reachable:
            return [
                AlertDraft(
                    "device_unreachable",
                    reading.device_id,
                    f"{reading.alias} became unreachable",
                )
            ]
        return [
            AlertDraft(
                "device_recovered",
                reading.device_id,
                f"{reading.alias} is reachable again",
            )
        ]

    def _power(
        self, reading: DeviceReading, threshold: float | None
    ) -> list[AlertDraft]:
        if threshold is None:
            # No threshold configured (or it was just cleared): drop any latch so
            # re-adding a threshold can alert again on a still-high device.
            self._over_threshold.pop(reading.device_id, None)
            return []
        if reading.power_w is None:
            # Unknown draw this cycle — hold the latch, don't re-arm on a gap.
            return []
        over = reading.power_w > threshold
        was_over = self._over_threshold.get(reading.device_id, False)
        self._over_threshold[reading.device_id] = over
        if over and not was_over:
            return [
                AlertDraft(
                    "power_exceeded",
                    reading.device_id,
                    f"{reading.alias} is drawing {reading.power_w:g} W "
                    f"(over {threshold:g} W)",
                    power_w=reading.power_w,
                    threshold_w=threshold,
                )
            ]
        return []


class AlertThresholdStore:
    """Per-device power-draw thresholds (device_id -> watts), one JSON file.

    A full-replace store like the favorites list: ``set_all`` overwrites the whole
    mapping. Tolerant load/save (a read problem degrades to empty), and only
    positive watt values are kept — a zero/negative threshold isn't meaningful.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    @staticmethod
    def _sanitize(raw: object) -> dict[str, float]:
        """Coerce a loaded/submitted mapping to ``{device_id: positive watts}``."""
        if not isinstance(raw, dict):
            return {}
        clean: dict[str, float] = {}
        for device_id, watts in raw.items():
            try:
                value = float(watts)
            except TypeError, ValueError:
                continue
            if value > 0:
                clean[str(device_id)] = value
        return clean

    def _read(self) -> dict[str, float]:
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            logger.warning(f"Could not read alert store {self.path}: {e}")
            return {}
        return self._sanitize(
            data.get("thresholds") if isinstance(data, dict) else None
        )

    def _write(self, thresholds: dict[str, float]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"thresholds": thresholds}, indent=2))
        except OSError as e:
            logger.warning(f"Could not write alert store {self.path}: {e}")

    def get_all(self) -> dict[str, float]:
        return self._read()

    def set_all(self, thresholds: dict[str, float]) -> dict[str, float]:
        """Replace the whole mapping; returns the sanitized, persisted version."""
        clean = self._sanitize(thresholds)
        self._write(clean)
        return clean


class AlertCenter:
    """In-memory ring buffer of recent alerts plus outbound webhook delivery.

    Stamps each :class:`AlertDraft` with an id and timestamp, appends it to the
    bounded deque, and (best-effort) POSTs it to the configured webhook. The
    deque is newest-last internally; :meth:`recent` returns newest-first for the
    UI.
    """

    def __init__(self, maxlen: int = _RING_MAXLEN) -> None:
        self._alerts: deque[Alert] = deque(maxlen=maxlen)

    def emit(self, draft: AlertDraft, *, ts: int | None = None) -> Alert:
        """Stamp a draft into a stored :class:`Alert` and buffer it."""
        alert = Alert(
            id=uuid.uuid4().hex,
            ts=int(time.time()) if ts is None else ts,
            type=draft.type,
            device_id=draft.device_id,
            message=draft.message,
            power_w=draft.power_w,
            threshold_w=draft.threshold_w,
        )
        self._alerts.append(alert)
        return alert

    def recent(self) -> list[Alert]:
        """Buffered alerts, newest first."""
        return list(reversed(self._alerts))


def _alert_title(alert: Alert) -> str:
    """A short webhook ``Title`` header per alert type (body carries the detail)."""
    return {
        "device_unreachable": "Device unreachable",
        "device_recovered": "Device recovered",
        "power_exceeded": "Power draw high",
    }.get(alert.type, "Alert")


# The async httpx client used to POST webhooks, injectable so tests pass a fake
# without monkeypatching the module. Matches ``httpx.AsyncClient``'s constructor.
ClientFactory = Callable[..., httpx.AsyncClient]


async def deliver_webhook(
    url: str, alert: Alert, *, client_factory: ClientFactory = httpx.AsyncClient
) -> bool:
    """POST one alert to ``url`` ntfy-compatibly. Never raises; returns success.

    Body is the plain-text message and ``Title`` is a short per-type header, which
    is exactly what ntfy renders. A network/HTTP failure is logged and swallowed
    so a flaky endpoint can never disrupt evaluation.
    """
    try:
        async with client_factory(timeout=_WEBHOOK_TIMEOUT_SECONDS) as client:
            response = await client.post(
                url, content=alert.message, headers={"Title": _alert_title(alert)}
            )
            response.raise_for_status()
    except Exception as e:  # noqa: BLE001 - webhook delivery must never be fatal
        logger.warning(f"Alert webhook to {url} failed: {e}")
        return False
    return True


def latest_power_readings(db_path: Path, *, now: int | None = None) -> dict[str, float]:
    """Latest non-null power (watts) per device from the energy-history samples DB.

    Reads the same SQLite DB the recorder writes (``KASA_ENERGY_HISTORY_FILE``)
    directly, rather than through ``EnergyHistoryStore`` which has no
    "latest per device" query — a candidate to consolidate into that store later.
    Own connection per call (safe across threads); a missing DB/table degrades to
    an empty map. Rows older than ``_POWER_STALENESS_SECONDS`` are ignored so a
    long-gone device can't trip a wattage alert from a stale reading.
    """
    now = int(time.time()) if now is None else now
    cutoff = now - _POWER_STALENESS_SECONDS
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT device_id, power_w FROM samples AS s "
                "WHERE power_w IS NOT NULL AND ts >= ? AND ts = ("
                "  SELECT MAX(ts) FROM samples WHERE device_id = s.device_id "
                "  AND power_w IS NOT NULL AND ts >= ?)",
                (cutoff, cutoff),
            ).fetchall()
    except sqlite3.Error as e:
        logger.debug(f"Could not read power samples for alerts: {e}")
        return {}
    return {str(device_id): float(power) for device_id, power in rows}


def collect_readings(registry, db_path: Path) -> list[DeviceReading]:
    """Assemble this cycle's per-device readings from the registry and samples DB.

    Reachability is authoritative and live (``registry.all()`` are reachable,
    ``unreachable_devices()`` are the known-but-offline ones); power is the latest
    recorded sample, so this adds no device I/O of its own.
    """
    from .kasa_service import stable_device_id

    powers = latest_power_readings(db_path)
    readings: list[DeviceReading] = []
    for device in registry.all():
        device_id = stable_device_id(device)
        alias = getattr(device, "alias", None) or device.host
        readings.append(DeviceReading(device_id, alias, True, powers.get(device_id)))
    for snapshot in registry.unreachable_devices():
        readings.append(
            DeviceReading(snapshot.id, snapshot.alias, False, powers.get(snapshot.id))
        )
    return readings


async def run_alert_evaluator(
    registry,
    evaluator: AlertEvaluator,
    center: AlertCenter,
    thresholds: AlertThresholdStore,
    *,
    interval: float,
    db_path: Path,
    webhook_url: str | None = None,
    deliver: Callable[[str, Alert], Awaitable[bool]] = deliver_webhook,
) -> None:
    """Evaluate the alert detectors on an interval until cancelled.

    Background task launched at startup alongside the recorder/scheduler.
    Resilient: the transition logic is contained in ``evaluator``, a failed cycle
    is logged and the loop continues, and cancellation propagates for clean
    shutdown. ``deliver`` is injectable so tests can assert webhook dispatch
    without a real HTTP client.
    """
    while True:
        try:
            readings = collect_readings(registry, db_path)
            for draft in evaluator.evaluate(readings, thresholds.get_all()):
                alert = center.emit(draft)
                logger.info(f"Alert: {alert.message}")
                if webhook_url:
                    await deliver(webhook_url, alert)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - the evaluator must never crash startup
            logger.error(f"Alert evaluation cycle failed: {e}")
        await asyncio.sleep(interval)


# Module-level singletons built from the shared settings, mirroring the other
# stores. Thresholds live at KASA_ALERTS_FILE (default ./data/alerts.json); mount
# that path as a volume to keep them. The ring buffer and evaluator state are
# in-memory only (reset on restart).
alert_thresholds = AlertThresholdStore(get_settings().kasa_alerts_file)
alert_center = AlertCenter()
alert_evaluator = AlertEvaluator()
