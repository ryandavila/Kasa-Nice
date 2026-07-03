"""Persistent energy-history recording.

The python-kasa Energy module only exposes what a device remembers (a rolling
month of daily totals, a year of monthly totals), lost on factory reset. To keep
longer trends, a background task samples each metered device on an interval and
appends readings to a small SQLite DB, which the ``/history`` endpoint serves.

Stdlib ``sqlite3`` needs no extra dependency. A fresh connection per operation
keeps the store safe to call from both the recorder's asyncio task and FastAPI's
request threadpool without sharing a connection across threads.
"""

import asyncio
import sqlite3
import time
from pathlib import Path

from .config import get_settings
from .logging_config import get_logger

logger = get_logger(__name__)


class EnergyHistoryStore:
    """Append-only store of periodic energy samples, backed by SQLite."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        """Open a connection, ensuring the schema exists.

        The idempotent DDL runs on every call (cheap next to the per-call
        connection itself) rather than behind a once-latch: connecting recreates
        a deleted DB file but not its tables, so a latch would leave every
        operation failing with "no such table" until restart. WAL mode tolerates
        the recorder writing while a request reads.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
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
        return conn

    # NOTE: the table's ``month_kwh`` column is legacy — every month aggregate is
    # recomputed from daily maxima of ``today_kwh`` (see ``month_kwh_by_device``),
    # so nothing writes or reads it anymore. It stays in the DDL only so existing
    # DBs and downgrades keep working.

    def record(
        self,
        device_id: str,
        power_w: float | None,
        today_kwh: float | None,
        ts: int | None = None,
    ) -> None:
        """Append one reading. Best-effort: an IO error is logged, not raised."""
        ts = int(time.time()) if ts is None else ts
        self.record_many([(device_id, ts, power_w, today_kwh)])

    def record_many(
        self, rows: list[tuple[str, int, float | None, float | None]]
    ) -> None:
        """Append a batch of ``(device_id, ts, power_w, today_kwh)`` readings.

        One connection and one transaction for the whole batch, so a recorder
        cycle costs one commit instead of one per device. Best-effort like
        :meth:`record`.
        """
        if not rows:
            return
        try:
            with self._connect() as conn:
                conn.executemany(
                    "INSERT INTO samples (device_id, ts, power_w, today_kwh) "
                    "VALUES (?, ?, ?, ?)",
                    rows,
                )
        except sqlite3.Error as e:
            logger.warning(f"Could not record energy samples: {e}")

    def latest_power_by_device(
        self, max_age_seconds: float, *, now: int | None = None
    ) -> dict[str, float]:
        """Latest non-null power (watts) per device, ignoring rows older than
        ``max_age_seconds``.

        Single pass: SQLite's bare-column-with-MAX semantics select ``power_w``
        from each device's max-``ts`` row. Serves the alert evaluator; degrades
        to an empty map like the other queries, so a missing DB/table can't
        break alerting.
        """
        now = int(time.time()) if now is None else now
        cutoff = now - int(max_age_seconds)
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT device_id, power_w, MAX(ts) FROM samples "
                    "WHERE ts >= ? AND power_w IS NOT NULL GROUP BY device_id",
                    (cutoff,),
                ).fetchall()
        except sqlite3.Error as e:
            logger.debug(f"Could not read latest power by device: {e}")
            return {}
        return {str(device_id): float(power) for device_id, power, _ in rows}

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

        ``today_kwh`` resets at local midnight, so a day's energy is the max seen
        on that local date (the reading just before reset). Days with no usable
        reading are omitted.
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

    # ── Insights aggregation ─────────────────────────────────────────────────
    # Queries backing GET /api/energy/insights. Each opens its own connection,
    # returns an empty result on any SQLite error (best-effort, like the rest of
    # the store), and buckets by LOCAL date/time — ``today_kwh`` resets at local
    # midnight, so all date math must agree with the device's own day boundary.

    def today_kwh_by_device(self) -> dict[str, float]:
        """Energy used so far today per device (kWh), keyed by device id.

        A day's energy is the max ``today_kwh`` seen on the local current date
        (the reading just before the midnight reset); ``date('now','localtime')``
        pins "today" to the same local day the device resets on. Devices with no
        reading today are absent.
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT device_id, MAX(today_kwh) FROM samples "
                    "WHERE today_kwh IS NOT NULL "
                    "AND date(ts, 'unixepoch', 'localtime') = date('now', 'localtime') "
                    "GROUP BY device_id"
                ).fetchall()
        except sqlite3.Error as e:
            logger.warning(f"Could not read today's energy by device: {e}")
            return {}
        return {device_id: float(kwh) for device_id, kwh in rows}

    def month_kwh_by_device(self) -> dict[str, float]:
        """Energy this calendar month per device (kWh), keyed by device id.

        Sums each device's per-local-day totals (max ``today_kwh`` per day) over
        the days whose local month matches the current one. Summing daily maxima
        — rather than trusting the device's own ``month_kwh`` — keeps the figure
        consistent with the daily/week views and survives a device forgetting its
        month total. Devices with no reading this month are absent.
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT device_id, SUM(day_max) FROM ("
                    "  SELECT device_id, MAX(today_kwh) AS day_max FROM samples"
                    "  WHERE today_kwh IS NOT NULL"
                    "    AND strftime('%Y-%m', ts, 'unixepoch', 'localtime')"
                    "      = strftime('%Y-%m', 'now', 'localtime')"
                    "  GROUP BY device_id, date(ts, 'unixepoch', 'localtime')"
                    ") GROUP BY device_id"
                ).fetchall()
        except sqlite3.Error as e:
            logger.warning(f"Could not read this month's energy by device: {e}")
            return {}
        return {device_id: float(kwh) for device_id, kwh in rows}

    def home_kwh_between(self, start_ts: int, end_ts: int) -> float:
        """Whole-home kWh recorded in the half-open window ``[start_ts, end_ts)``.

        Sums every device's per-local-day energy (daily max of ``today_kwh``)
        across each day in the window. The caller passes local-midnight bounds;
        used for week-over-week totals where those bounds are consecutive Mondays.
        """
        try:
            with self._connect() as conn:
                (total,) = conn.execute(
                    "SELECT COALESCE(SUM(day_max), 0) FROM ("
                    "  SELECT MAX(today_kwh) AS day_max FROM samples"
                    "  WHERE ts >= ? AND ts < ? AND today_kwh IS NOT NULL"
                    "  GROUP BY device_id, date(ts, 'unixepoch', 'localtime')"
                    ")",
                    (start_ts, end_ts),
                ).fetchone()
        except sqlite3.Error as e:
            logger.warning(f"Could not sum home energy for {start_ts}-{end_ts}: {e}")
            return 0.0
        return float(total or 0.0)

    def idle_draw(self, days: int = 14) -> dict[str, float]:
        """Median overnight power draw per device (watts), keyed by device id.

        Looks at samples between 01:00 and 05:00 local time — deep night, when
        nothing should be in active use — over the last ``days`` days, so a high
        value flags a device burning power while idle (a "vampire" load). Uses the
        median, not the mean, so an occasional spike (e.g. a fridge compressor
        cycling on) doesn't inflate the reading. Median is computed in SQL: a
        window function ranks each device's readings by power, then the middle row
        (or the mean of the middle two, for an even count) is averaged out.
        """
        cutoff = int(time.time()) - days * 86400
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT device_id, AVG(power_w) FROM ("
                    "  SELECT device_id, power_w,"
                    "    ROW_NUMBER() OVER ("
                    "      PARTITION BY device_id ORDER BY power_w) AS rn,"
                    "    COUNT(*) OVER (PARTITION BY device_id) AS cnt"
                    "  FROM samples"
                    "  WHERE ts >= ? AND power_w IS NOT NULL"
                    "    AND time(ts, 'unixepoch', 'localtime') >= '01:00:00'"
                    "    AND time(ts, 'unixepoch', 'localtime') < '05:00:00'"
                    ") WHERE rn IN ((cnt + 1) / 2, (cnt + 2) / 2) "
                    "GROUP BY device_id",
                    (cutoff,),
                ).fetchall()
        except sqlite3.Error as e:
            logger.warning(f"Could not compute idle draw: {e}")
            return {}
        return {device_id: float(median) for device_id, median in rows}

    def migrate_device_id(self, old_id: str, new_id: str) -> None:
        """Re-point samples recorded under ``old_id`` to ``new_id``.

        One-time repair for history recorded when devices were keyed by LAN IP:
        re-pointing keeps the energy chart continuous across a DHCP change instead
        of stranding history under a dead IP. Best-effort — a failure is logged,
        never raised, so it can't disrupt the discovery that triggers it.
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

    Background task launched at startup. Resilient: a per-device read failure is
    skipped, the cycle is wrapped so a bug can't kill the loop, and cancellation
    propagates for clean shutdown. Devices are sampled concurrently (one slow
    cloud strip mustn't stall — and time-skew — everyone else's sample), the
    cycle's rows are written in one batch off the event loop, and pruning runs
    at most daily rather than every cycle.
    """
    from .kasa_service import EnergyUnsupportedError, stable_device_id

    async def sample(device_id: str) -> tuple | None:
        try:
            # Snapshot, not get_usage: stores only the live scalars, so it
            # skips the daily/monthly stats-table fetches get_usage does.
            snapshot = await registry.read_energy_snapshot(device_id)
        except EnergyUnsupportedError:
            return None  # no energy meter; nothing to record
        except Exception as e:  # noqa: BLE001 - one bad device shouldn't stop the cycle
            logger.debug(f"Energy sample for {device_id} failed: {e}")
            return None
        return (device_id, int(time.time()), snapshot.power_w, snapshot.today_kwh)

    last_prune = float("-inf")
    while True:
        try:
            # Key by stable id (not LAN IP) so history survives a DHCP change
            # and lines up with the ids the API serves.
            ids = [stable_device_id(d) for d in registry.all()]
            results = await asyncio.gather(*(sample(device_id) for device_id in ids))
            rows = [row for row in results if row is not None]
            await asyncio.to_thread(store.record_many, rows)
            now = time.time()
            if now - last_prune >= 86400:
                last_prune = now
                await asyncio.to_thread(store.prune, int(now) - _RETENTION_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - the recorder must never crash startup
            logger.error(f"Energy recorder cycle failed: {e}")
        await asyncio.sleep(interval)


# Module-level singleton. DB at KASA_ENERGY_HISTORY_FILE (default
# ./data/energy_history.db); mount that path as a volume to keep history.
history = EnergyHistoryStore(get_settings().kasa_energy_history_file)
