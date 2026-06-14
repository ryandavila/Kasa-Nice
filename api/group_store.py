"""Persistence for user-defined device groups (rooms) and favorites.

Discovery gives a flat list of devices; this lets the user organize them into
named rooms and star the ones they reach for most. Groups and favorites are a
pure UI concern, decoupled from discovery — they reference device ids (the
device ``host``) and may name a device that is currently offline or absent,
which is fine and intended. Stored in one small JSON file, mirroring the
tolerant load/save style of ``HostStore``.
"""

import json
import os
import uuid
from pathlib import Path

from .logging_config import get_logger

logger = get_logger(__name__)


def _dedupe(ids: list[str]) -> list[str]:
    """Drop duplicate ids, preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


class GroupStore:
    """Reads and writes device groups and favorites to a JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict:
        """Load the raw document, degrading to an empty one on any problem."""
        empty: dict = {"groups": [], "favorites": []}
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return empty
        except (OSError, ValueError) as e:
            logger.warning(f"Could not read group store {self.path}: {e}")
            return empty
        if not isinstance(data, dict):
            return empty
        groups = data.get("groups")
        favorites = data.get("favorites")
        return {
            "groups": groups if isinstance(groups, list) else [],
            "favorites": [str(f) for f in favorites]
            if isinstance(favorites, list)
            else [],
        }

    def _write(self, data: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=2))
        except OSError as e:
            logger.warning(f"Could not write group store {self.path}: {e}")

    def list_groups(self) -> list[dict]:
        return self._read()["groups"]

    def create_group(self, name: str) -> dict:
        data = self._read()
        group = {"id": uuid.uuid4().hex, "name": name, "device_ids": []}
        data["groups"].append(group)
        self._write(data)
        return group

    def update_group(
        self,
        group_id: str,
        *,
        name: str | None = None,
        device_ids: list[str] | None = None,
    ) -> dict | None:
        """Partially update a group; returns it, or None if the id is unknown."""
        data = self._read()
        for group in data["groups"]:
            if group["id"] == group_id:
                if name is not None:
                    group["name"] = name
                if device_ids is not None:
                    group["device_ids"] = _dedupe([str(d) for d in device_ids])
                self._write(data)
                return group
        return None

    def delete_group(self, group_id: str) -> bool:
        """Delete a group; returns whether it existed."""
        data = self._read()
        kept = [g for g in data["groups"] if g["id"] != group_id]
        if len(kept) == len(data["groups"]):
            return False
        data["groups"] = kept
        self._write(data)
        return True

    def get_favorites(self) -> list[str]:
        return self._read()["favorites"]

    def set_favorites(self, device_ids: list[str]) -> list[str]:
        data = self._read()
        data["favorites"] = _dedupe([str(d) for d in device_ids])
        self._write(data)
        return data["favorites"]


# Module-level singleton, mirroring the registry/host-store pattern. Lives at
# KASA_GROUPS_FILE (default ./data/groups.json); mount that path as a volume to
# keep rooms and favorites across container rebuilds.
_GROUPS_FILE = Path(os.getenv("KASA_GROUPS_FILE", "data/groups.json"))
groups = GroupStore(_GROUPS_FILE)
