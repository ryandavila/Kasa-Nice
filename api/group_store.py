"""Persistence for user-defined device groups (rooms) and favorites.

Discovery gives a flat list of devices; this lets the user organize them into
named rooms and star the ones they reach for most. Groups and favorites are a
pure UI concern, decoupled from discovery — they reference stable device ids
(normalized MAC, or host when no MAC is available; see ``stable_device_id``)
and may name a device that is currently offline or absent, which is fine and
intended. Stored in one small JSON file, mirroring the tolerant load/save
style of ``HostStore``.
"""

import json
import uuid
from pathlib import Path

from .config import get_settings
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

    def get_group(self, group_id: str) -> dict | None:
        """Return a group by id, or None if the id is unknown."""
        for group in self._read()["groups"]:
            if group["id"] == group_id:
                return group
        return None

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

    def migrate_device_id(self, old_id: str, new_id: str) -> bool:
        """Re-key a device across every room and the favorites list, in place.

        A one-time repair for data written when devices were keyed by LAN IP:
        wherever a room or the favorites still reference ``old_id`` (the device's
        former host), swap in ``new_id`` (its stable id) so the room membership and
        favorite star follow the device across a DHCP IP change. De-duplicates in
        case both ids were somehow present. Returns whether anything changed, and
        only writes when it did, so re-running is cheap and harmless.
        """
        data = self._read()
        changed = False
        for group in data["groups"]:
            ids = group.get("device_ids", [])
            if old_id in ids:
                group["device_ids"] = _dedupe(
                    [new_id if i == old_id else i for i in ids]
                )
                changed = True
        if old_id in data["favorites"]:
            data["favorites"] = _dedupe(
                [new_id if i == old_id else i for i in data["favorites"]]
            )
            changed = True
        if changed:
            self._write(data)
        return changed

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
groups = GroupStore(get_settings().kasa_groups_file)
