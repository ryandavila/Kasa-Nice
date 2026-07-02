"""Persistence for server-side schedule rules ("timers").

A schedule rule says "at HH:MM on {days of week}, turn {a device|a room}
{on|off}". Rules live on the server (not in the browser) so they fire uniformly
for both locally-controlled and cloud-fallback devices, and keep running while
no browser is open — the frontend is just an editor over this store.

Stored in one small JSON file, mirroring the tolerant load/save style of
``GroupStore``: any read problem degrades to an empty document rather than
raising, so a corrupt or missing file can never take the server down. The rule
*shape* is validated at the API boundary (pydantic in ``schemas.py``); this
layer just persists dicts and does the id bookkeeping.

Forward compatibility: rules carry a ``kind`` discriminator (``"fixed_time"`` in
v1). Newer versions may add kinds (sunrise/sunset, one-shot timers) or richer
actions; because this store passes rule dicts through untouched, a rule written
by a newer build survives a downgrade here intact (the scheduler simply skips
kinds it doesn't understand) instead of being dropped on the next write.
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
        # Server owns the id and ``last_fired`` so the client can't spoof either;
        # everything else is the validated payload the caller passes in.
        stored = {**rule, "id": uuid.uuid4().hex, "last_fired": None}
        data["schedules"].append(stored)
        self._write(data)
        return stored

    def update_rule(self, rule_id: str, fields: dict[str, Any]) -> dict | None:
        """Merge ``fields`` into a rule; returns it, or None if the id is unknown.

        Only the keys present in ``fields`` are touched (a partial PATCH), and
        ``id`` is never overwritten so a stray ``id`` in the payload can't re-key
        a rule out from under the caller.
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

        Called by the scheduler after each attempt so the UI can show "last run"
        and whether it succeeded. Best-effort like the rest of the store: a write
        failure is swallowed, since losing the audit note must never abort a rule.
        """
        return self.update_rule(rule_id, {"last_fired": {"ts": ts, "result": result}})


# Module-level singleton, mirroring the group/host-store pattern. Lives at
# KASA_SCHEDULES_FILE (default ./data/schedules.json); mount that path as a
# volume to keep schedule rules across container rebuilds.
schedules = ScheduleStore(get_settings().kasa_schedules_file)
