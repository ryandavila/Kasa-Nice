"""Persistence for the single vacation-mode (presence-simulation) config.

Unlike the group/schedule/scene stores this holds ONE document, not a list —
there's only ever one vacation policy — so the public surface is a get/replace
pair (``load`` / ``save``) rather than by-id CRUD, mirroring how the alert-
threshold store persists a single mapping.

Shape is validated at the API boundary (``schemas.VacationConfig``); this layer
just persists the dict with the tolerant load / atomic save of
``JsonDocumentStore``. A missing or corrupt file degrades to the built-in
defaults (vacation off), so a fresh install or a hand-edit typo can never crash
the engine or the endpoint — it simply behaves as "disabled".
"""

from typing import Any

from .config import get_settings
from .json_store import JsonDocumentStore
from .schemas import VacationConfig


class VacationStore(JsonDocumentStore):
    """Reads and writes the one vacation-mode config document to a JSON file."""

    _label = "vacation store"

    def _empty(self) -> dict:
        # The schema's field defaults are the source of truth for "unset", so an
        # empty document is just a default-constructed config (vacation off). This
        # keeps the default in exactly one place — the schema — and can't drift.
        return VacationConfig().model_dump()

    def _coerce(self, data: Any) -> dict:
        # A hand-edited file may hold the wrong shape or partial/typo'd values.
        # Re-validate through the schema, which fills missing fields from their
        # defaults and coerces types; anything unsalvageable degrades to defaults
        # rather than surfacing a broken document to the engine.
        if not isinstance(data, dict):
            return self._empty()
        try:
            return VacationConfig(**data).model_dump()
        except Exception:  # noqa: BLE001 - a bad file is data loss, not a crash
            return self._empty()

    def load(self) -> VacationConfig:
        """The current config as a validated model (defaults when unset/corrupt)."""
        return VacationConfig(**self._read())

    def save(self, config: VacationConfig) -> VacationConfig:
        """Persist a validated config, returning it for the caller to echo back."""
        self._write(config.model_dump())
        return config


# Module-level singleton. Lives at KASA_VACATION_FILE (default
# ./data/vacation.json); mount that path as a volume to keep the vacation policy.
vacation_store = VacationStore(get_settings().kasa_vacation_file)
