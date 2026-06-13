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
