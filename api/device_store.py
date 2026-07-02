"""Persistence for known device hosts.

Discovery is cached in memory, so devices added by IP (when UDP broadcast can't
reach them) would vanish on restart. This stores the set of known hosts to a
small JSON file so they can be re-probed on startup.
"""

import json
from pathlib import Path

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
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(sorted(hosts), indent=2))
        except OSError as e:
            logger.warning(f"Could not write host store {self.path}: {e}")


class DeviceSnapshotStore:
    """Persists a small last-known identity record per host, keyed by LAN IP.

    A device that stops answering discovery must not silently vanish from the UI
    (it still occupies rooms/favorites), so when a device is successfully read we
    stash a minimal identity snapshot here — id, alias, model, host, device_type,
    and a strip's child ids/aliases. On a later failure the registry serves that
    snapshot with ``reachable=False`` instead of dropping the device entirely.

    Kept as its own file alongside the ``HostStore`` (rather than folded into it)
    so the host store's plain string-list format — and every consumer of it —
    stays untouched. Keyed by host because that's the key the registry already has
    for a persisted-but-unreachable device (see ``HostStore``); the durable stable
    id lives *inside* each record so rooms/favorites still match.

    Records are opaque JSON dicts (a serialized ``Device``); this store neither
    validates nor interprets them, mirroring ``HostStore``'s dumb-persistence role
    and keeping it free of a schema import.
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
        # Tolerate anything but the expected {host: record} mapping (a hand-edited
        # or older file) by ignoring it, exactly as HostStore does for its list.
        if not isinstance(data, dict):
            return {}
        return {str(h): r for h, r in data.items() if isinstance(r, dict)}

    def save(self, snapshots: dict[str, dict]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Sorted by host for a stable, diff-friendly file (matches HostStore).
            self.path.write_text(json.dumps(dict(sorted(snapshots.items())), indent=2))
        except OSError as e:
            logger.warning(f"Could not write snapshot store {self.path}: {e}")
