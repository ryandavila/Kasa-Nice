"""Persistence for server-side schedule rules ("timers").

A rule says "at HH:MM on {days}, turn {a device|a room} {on|off}". Rules live on
the server so they fire for both local and cloud devices and keep running with no
browser open — the frontend is just an editor over this store.

One small JSON file, tolerant load/save like ``GroupStore`` (a read problem
degrades to an empty document). Rule *shape* is validated at the API boundary
(``schemas.py``); this layer just persists dicts and does id bookkeeping.

Rules carry a ``kind`` discriminator (``"fixed_time"``, ``"sunrise"``,
``"sunset"``, or ``"once"``). Since this store passes dicts through untouched, a
rule written by a newer build (with a kind the scheduler skips) survives a
downgrade intact instead of being dropped, and an old v1 file (fixed_time rules
with none of the newer fields) loads and round-trips unchanged.
"""

import json
import uuid
from pathlib import Path
from typing import Any

from .config import get_settings
from .logging_config import get_logger

logger = get_logger(__name__)


class ScheduleStore:
    """Reads and writes schedule rules to a JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict:
        """Load the raw document, degrading to an empty one on any problem."""
        empty: dict = {"schedules": []}
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return empty
        except (OSError, ValueError) as e:
            logger.warning(f"Could not read schedule store {self.path}: {e}")
            return empty
        if not isinstance(data, dict):
            return empty
        schedules = data.get("schedules")
        return {"schedules": schedules if isinstance(schedules, list) else []}

    def _write(self, data: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=2))
        except OSError as e:
            logger.warning(f"Could not write schedule store {self.path}: {e}")

    def list_rules(self) -> list[dict]:
        return self._read()["schedules"]

    def get_rule(self, rule_id: str) -> dict | None:
        """Return a rule by id, or None if the id is unknown."""
        for rule in self._read()["schedules"]:
            if rule.get("id") == rule_id:
                return rule
        return None

    def create_rule(self, rule: dict) -> dict:
        """Persist a new rule, assigning it a fresh id and no fire history."""
        data = self._read()
        # Server owns id and ``last_fired`` so the client can't spoof either.
        stored = {**rule, "id": uuid.uuid4().hex, "last_fired": None}
        data["schedules"].append(stored)
        self._write(data)
        return stored

    def update_rule(self, rule_id: str, fields: dict[str, Any]) -> dict | None:
        """Merge ``fields`` into a rule; returns it, or None if the id is unknown.

        Only keys present in ``fields`` are touched (partial PATCH); ``id`` is
        never overwritten, so a stray ``id`` in the payload can't re-key a rule.
        """
        data = self._read()
        for rule in data["schedules"]:
            if rule.get("id") == rule_id:
                rule.update({k: v for k, v in fields.items() if k != "id"})
                self._write(data)
                return rule
        return None

    def delete_rule(self, rule_id: str) -> bool:
        """Delete a rule; returns whether it existed."""
        data = self._read()
        kept = [r for r in data["schedules"] if r.get("id") != rule_id]
        if len(kept) == len(data["schedules"]):
            return False
        data["schedules"] = kept
        self._write(data)
        return True

    def mark_fired(self, rule_id: str, ts: int, result: str) -> dict | None:
        """Record a rule's most recent firing (unix ts + human-readable result).

        Called by the scheduler after each attempt so the UI can show "last run".
        Best-effort: a write failure is swallowed, since losing the audit note
        must never abort a rule.
        """
        return self.update_rule(rule_id, {"last_fired": {"ts": ts, "result": result}})


# Module-level singleton. Lives at KASA_SCHEDULES_FILE (default
# ./data/schedules.json); mount that path as a volume to keep schedule rules.
schedules = ScheduleStore(get_settings().kasa_schedules_file)
