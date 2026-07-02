"""Persistent energy-history recording.

The python-kasa Energy module only exposes what a device itself remembers — a
rolling current month of daily totals and the current year of monthly totals,
all lost on a factory reset and capped by the device's own memory. To keep
longer trends (and to retain history across resets), a background task samples
each metered device on an interval and appends the readings to a small SQLite
database, which the ``/history`` endpoint then serves.

SQLite (stdlib ``sqlite3``) suits this naturally append-only time series and
needs no extra dependency. A fresh connection is opened per operation so the
store is safe to call from both the recorder's asyncio task and FastAPI's
request threadpool without sharing a connection across threads.
"""

import asyncio
import os
import sqlite3
import time
from pathlib import Path

from .logging_config import get_logger

logger = get_logger(__name__)


class EnergyHistoryStore:
    """Append-only store of periodic energy samples, backed by SQLite."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        """Open a connection, creating the schema on first use.

        Lazy init keeps construction side-effect-free (so tests and import don't
        touch the filesystem) and survives the DB file being deleted between
        calls. WAL mode tolerates the recorder writing while a request reads.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        if not self._ready:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS samples ("
                "device_id TEXT NOT NULL, ts INTEGER NOT NULL, "
                "power_w REAL, today_kwh REAL, month_kwh REAL)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_samples_device_ts "
                "ON samples (device_id, ts)"
            )
            conn.commit()
            self._ready = True
        return conn

    def record(
        self,
        device_id: str,
        power_w: float | None,
        today_kwh: float | None,
        month_kwh: float | None,
        ts: int | None = None,
    ) -> None:
        """Append one reading. Best-effort: an IO error is logged, not raised."""
        ts = int(time.time()) if ts is None else ts
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO samples (device_id, ts, power_w, today_kwh, "
                    "month_kwh) VALUES (?, ?, ?, ?, ?)",
                    (device_id, ts, power_w, today_kwh, month_kwh),
                )
        except sqlite3.Error as e:
            logger.warning(f"Could not record energy sample for {device_id}: {e}")

    def recent_samples(
        self, device_id: str, since_ts: int
    ) -> list[tuple[int, float | None]]:
        """``(ts, power_w)`` for ``device_id`` at or after ``since_ts``, oldest first."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT ts, power_w FROM samples WHERE device_id = ? AND ts >= ? "
                    "ORDER BY ts",
                    (device_id, since_ts),
                ).fetchall()
        except sqlite3.Error as e:
            logger.warning(f"Could not read energy samples for {device_id}: {e}")
            return []
        return [(int(ts), power) for ts, power in rows]

    def daily_totals(self, device_id: str, days: int) -> list[tuple[str, float]]:
        """``(iso_date, kwh)`` per local day for the last ``days`` days, oldest first.

        ``today_kwh`` resets at local midnight, so a day's energy is the largest
        value seen on that local date — the reading just before the reset. Days
        with no usable reading are omitted.
        """
        cutoff = int(time.time()) - days * 86400
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT date(ts, 'unixepoch', 'localtime') AS day, "
                    "MAX(today_kwh) FROM samples "
                    "WHERE device_id = ? AND ts >= ? AND today_kwh IS NOT NULL "
                    "GROUP BY day ORDER BY day",
                    (device_id, cutoff),
                ).fetchall()
        except sqlite3.Error as e:
            logger.warning(f"Could not aggregate energy history for {device_id}: {e}")
            return []
        return [(day, float(kwh)) for day, kwh in rows]

    def migrate_device_id(self, old_id: str, new_id: str) -> None:
        """Re-point samples recorded under ``old_id`` to ``new_id``.

        A one-time repair for history recorded when devices were keyed by LAN IP:
        once a device's stable id is known, its old IP-keyed rows are re-pointed so
        the energy chart stays continuous across a DHCP change instead of stranding
        the old history under a dead IP. Best-effort — a failure is logged, never
        raised, so it can't disrupt the discovery that triggers it.
        """
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE samples SET device_id = ? WHERE device_id = ?",
                    (new_id, old_id),
                )
        except sqlite3.Error as e:
            logger.warning(
                f"Could not migrate energy history {old_id} -> {new_id}: {e}"
            )

    def prune(self, older_than_ts: int) -> None:
        """Drop samples older than ``older_than_ts`` to cap database growth."""
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM samples WHERE ts < ?", (older_than_ts,))
        except sqlite3.Error as e:
            logger.warning(f"Could not prune energy history: {e}")


# Retain roughly three months of samples; older rows are pruned each cycle.
_RETENTION_SECONDS = 90 * 86400


async def run_recorder(registry, store: EnergyHistoryStore, interval: float) -> None:
    """Periodically sample every metered device and persist the readings.

    Launched as a background task at startup. Resilient by construction: a read
    failure on one device is logged and skipped, the whole cycle is wrapped so a
    bug can't kill the loop, and cancellation propagates for clean shutdown.
    """
    from .kasa_service import EnergyUnsupportedError, stable_device_id

    while True:
        try:
            for device in registry.all():
                # Record under the device's stable id (not its LAN IP), so history
                # survives a DHCP change and lines up with the ids the API serves.
                device_id = stable_device_id(device)
                try:
                    usage = await registry.get_usage(device_id)
                except EnergyUnsupportedError:
                    continue  # no energy meter; nothing to record
                except Exception as e:  # noqa: BLE001 - one bad device shouldn't stop the cycle
                    logger.debug(f"Energy sample for {device_id} failed: {e}")
                    continue
                store.record(
                    device_id,
                    usage.current_power_w,
                    usage.today_kwh,
                    usage.month_kwh,
                )
            store.prune(int(time.time()) - _RETENTION_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - the recorder must never crash startup
            logger.error(f"Energy recorder cycle failed: {e}")
        await asyncio.sleep(interval)


def load_sample_interval() -> float:
    """Seconds between recorder cycles (env ``KASA_ENERGY_SAMPLE_INTERVAL``).

    Defaults to 300s; floored at 10s so a misconfigured tiny value can't busy-loop.
    Falls back to the default on a missing or invalid value.
    """
    raw = os.getenv("KASA_ENERGY_SAMPLE_INTERVAL")
    if raw is None or not raw.strip():
        return 300.0
    try:
        return max(10.0, float(raw))
    except ValueError:
        logger.warning(
            f"Ignoring invalid KASA_ENERGY_SAMPLE_INTERVAL={raw!r}; using 300s"
        )
        return 300.0


# Module-level singleton, mirroring the registry/host-store pattern. The DB lives
# at KASA_ENERGY_HISTORY_FILE (default ./data/energy_history.db); mount that path
# as a volume to keep history across container rebuilds.
_HISTORY_FILE = Path(os.getenv("KASA_ENERGY_HISTORY_FILE", "data/energy_history.db"))
history = EnergyHistoryStore(_HISTORY_FILE)
