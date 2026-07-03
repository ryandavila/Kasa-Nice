"""Persistence for named scenes.

A scene is a saved per-device state ("entries") applied as one action — e.g.
"Movie night" dims the lamp and turns off the overhead. Like rooms and schedules
it's a UI convenience decoupled from discovery: entries reference stable device
ids and may name a device that's momentarily offline.

One small JSON file, tolerant load/save like ``GroupStore``/``ScheduleStore`` (a
read problem degrades to an empty document). Entry *shape* is validated at the
API boundary (``schemas.py``); this layer just persists dicts and does id
bookkeeping, so it passes entries through untouched.
"""

import json
import uuid
from pathlib import Path

from .config import get_settings
from .fsutil import atomic_write_text
from .logging_config import get_logger

logger = get_logger(__name__)


class SceneStore:
    """Reads and writes scenes to a JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict:
        """Load the raw document, degrading to an empty one on any problem."""
        empty: dict = {"scenes": []}
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return empty
        except (OSError, ValueError) as e:
            logger.warning(f"Could not read scene store {self.path}: {e}")
            return empty
        if not isinstance(data, dict):
            return empty
        scenes = data.get("scenes")
        return {"scenes": scenes if isinstance(scenes, list) else []}

    def _write(self, data: dict) -> None:
        try:
            atomic_write_text(self.path, json.dumps(data, indent=2))
        except OSError as e:
            logger.warning(f"Could not write scene store {self.path}: {e}")

    def list_scenes(self) -> list[dict]:
        return self._read()["scenes"]

    def get_scene(self, scene_id: str) -> dict | None:
        """Return a scene by id, or None if the id is unknown."""
        for scene in self._read()["scenes"]:
            if scene.get("id") == scene_id:
                return scene
        return None

    def create_scene(self, name: str, entries: list[dict]) -> dict:
        """Persist a new scene, assigning it a fresh id.

        ``entries`` are already validated dicts (from the API layer, either
        explicit or snapshotted); the server owns the id so a client can't spoof
        it.
        """
        data = self._read()
        scene = {"id": uuid.uuid4().hex, "name": name, "entries": entries}
        data["scenes"].append(scene)
        self._write(data)
        return scene

    def update_scene(
        self,
        scene_id: str,
        *,
        name: str | None = None,
        entries: list[dict] | None = None,
    ) -> dict | None:
        """Partially update a scene; returns it, or None if the id is unknown.

        Only supplied fields are touched (partial PATCH), so a rename leaves the
        entries intact and vice-versa; ``id`` is never overwritten.
        """
        data = self._read()
        for scene in data["scenes"]:
            if scene.get("id") == scene_id:
                if name is not None:
                    scene["name"] = name
                if entries is not None:
                    scene["entries"] = entries
                self._write(data)
                return scene
        return None

    def delete_scene(self, scene_id: str) -> bool:
        """Delete a scene; returns whether it existed."""
        data = self._read()
        kept = [s for s in data["scenes"] if s.get("id") != scene_id]
        if len(kept) == len(data["scenes"]):
            return False
        data["scenes"] = kept
        self._write(data)
        return True


# Module-level singleton. Lives at KASA_SCENES_FILE (default ./data/scenes.json);
# mount that path as a volume to keep scenes across rebuilds.
scenes = SceneStore(get_settings().kasa_scenes_file)
