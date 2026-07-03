"""Shared base for the small persistent JSON document stores.

Device groups, scenes, schedule rules, and alert thresholds are each a single
small JSON file — a UI convenience decoupled from discovery, small enough that
a user may hand-edit it. They all want the same two crash/tamper-safety
properties, which this base owns so the concrete stores don't each re-implement
(and drift on) them:

* **Tolerant load.** A missing file is a fresh, empty document — no warning,
  since the first run legitimately has no file yet. A file that won't read or
  parse (truncated, hand-edited into invalid JSON, bad permissions) is logged at
  WARNING and degrades to the empty document rather than crashing a request, as
  does a document of the wrong top-level shape. Every successful load is passed
  through the subclass's :meth:`_coerce`, so a partially-valid file (right keys,
  wrong value types) is normalised the same way on every read.

* **Atomic, warn-on-failure save.** Writes go through
  :func:`~api.fsutil.atomic_write_text` so a torn write can't leave JSON the
  tolerant reader would silently treat as empty and then overwrite for good; a
  failed write is logged at WARNING and swallowed so a persistence problem never
  aborts the caller.

Concrete stores subclass this, set :attr:`_label`, implement :meth:`_empty` and
:meth:`_coerce`, and layer their own semantics (by-id CRUD, sanitising,
migrations) on top of :meth:`_read` and :meth:`_write`. Load and save need not be
symmetric: :meth:`_read` returns whatever in-memory value the subclass wants and
:meth:`_write` persists whatever document the subclass hands it (e.g. the alert
store reads the inner mapping but writes it wrapped under a ``"thresholds"`` key).
"""

import json
from pathlib import Path
from typing import Any

from .fsutil import atomic_write_text
from .logging_config import get_logger

logger = get_logger(__name__)


class JsonDocumentStore:
    """Path handling plus tolerant load / atomic warn-on-failure save.

    Subclasses set :attr:`_label` (used in the read/write warning messages) and
    provide :meth:`_empty` and :meth:`_coerce`; everything else about a concrete
    store — its public methods and any bookkeeping — lives in the subclass.
    """

    # Human-readable name for this store, e.g. ``"group store"``, interpolated
    # into the read/write warning messages. Overridden by every subclass.
    _label = "JSON store"

    def __init__(self, path: Path) -> None:
        self.path = path

    def _empty(self) -> Any:
        """Return a fresh empty in-memory document (a new mutable each call)."""
        raise NotImplementedError

    def _coerce(self, data: Any) -> Any:
        """Normalise a successfully-parsed JSON value into the store's shape.

        Receives whatever ``json`` produced (any type — a hand-edited file may
        hold a list, string, or the wrong keys) and must return a value of the
        same shape as :meth:`_empty`, degrading anything unexpected to empty.
        """
        raise NotImplementedError

    def _read(self) -> Any:
        """Load and coerce the document, degrading to empty on any problem."""
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return self._empty()
        except (OSError, ValueError) as e:
            logger.warning(f"Could not read {self._label} {self.path}: {e}")
            return self._empty()
        return self._coerce(data)

    def _write(self, data: Any) -> None:
        """Atomically persist ``data`` as pretty JSON; warn and swallow on error."""
        try:
            atomic_write_text(self.path, json.dumps(data, indent=2))
        except OSError as e:
            logger.warning(f"Could not write {self._label} {self.path}: {e}")
