"""Persistence for known device hosts.

Discovery is cached in memory, so devices added by IP (when UDP broadcast can't
reach them) would vanish on restart. This persists the known hosts to a small
JSON file so they can be re-probed on startup.
"""

import json
from pathlib import Path

from .fsutil import atomic_write_text
from .logging_config import get_logger

logger = get_logger(__name__)


class HostStore:
    """Reads and writes the set of known device hosts to a JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> set[str]:
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return set()
        except (OSError, ValueError) as e:
            logger.warning(f"Could not read host store {self.path}: {e}")
            return set()
        return {str(h) for h in data} if isinstance(data, list) else set()

    def save(self, hosts: set[str]) -> None:
        try:
            atomic_write_text(self.path, json.dumps(sorted(hosts), indent=2))
        except OSError as e:
            logger.warning(f"Could not write host store {self.path}: {e}")


class DeviceSnapshotStore:
    """Persists a small last-known identity record per host, keyed by LAN IP.

    So a device that stops answering discovery stays visible (it still occupies
    rooms/favorites): on a successful read we stash a minimal identity snapshot
    (id, alias, model, host, device_type, strip child ids/aliases); on a later
    failure the registry serves it with ``reachable=False`` instead of dropping
    the device.

    A separate file from ``HostStore`` so its string-list format stays untouched.
    Keyed by host (the registry's key for a persisted-but-unreachable device);
    the stable id lives inside each record so rooms/favorites still match.
    Records are opaque JSON dicts (a serialized ``Device``) — neither validated
    nor interpreted here, keeping this free of a schema import.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            logger.warning(f"Could not read snapshot store {self.path}: {e}")
            return {}
        # Ignore anything but the expected {host: record} mapping (a hand-edited
        # or older file), as HostStore does for its list.
        if not isinstance(data, dict):
            return {}
        return {str(h): r for h, r in data.items() if isinstance(r, dict)}

    def save(self, snapshots: dict[str, dict]) -> None:
        try:
            # Sorted by host for a stable, diff-friendly file.
            atomic_write_text(
                self.path, json.dumps(dict(sorted(snapshots.items())), indent=2)
            )
        except OSError as e:
            logger.warning(f"Could not write snapshot store {self.path}: {e}")
