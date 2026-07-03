"""Backup & restore: one downloadable document for every JSON store, plus a
separate streamed copy of the SQLite energy-history database.

Design choices, in one place since they span every store:

* **One JSON document, not a zip.** Every JSON-persisted store (rooms/favorites,
  scenes, schedules, alert thresholds, known devices) is small text, so folding
  them into one versioned object keeps "back up everything" a single GET with no
  archive format to version separately from the data itself.
* **Energy history is out-of-band.** The SQLite DB can be large and is
  append-only; it's streamed as its own file (see ``energy_db``) via a
  ``sqlite3`` backup-API snapshot rather than loaded into the JSON blob.
* **Validate everything before writing anything.** Restore parses the whole
  payload through :class:`~api.schemas.BackupDocument` first — including an
  unknown ``backup_version`` — so a malformed or foreign file is rejected with a
  clear 4xx and zero partial writes, never a half-restored server.
* **Known devices span two files.** ``HostStore`` (the host set) and
  ``DeviceSnapshotStore`` (last-known identity per host) are exposed as one
  ``known_devices`` list via ``DeviceRegistry.known_devices_export`` /
  ``restore_known_devices``, which also deliberately leaves live device
  connections untouched — see that method's docstring.
"""

import datetime
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import ValidationError

from ..alerts import alert_thresholds
from ..energy_history import history
from ..events import broadcaster
from ..group_store import groups
from ..kasa_service import registry
from ..logging_config import get_logger
from ..scene_store import scenes
from ..schedule_store import schedules
from ..schemas import (
    CURRENT_BACKUP_VERSION,
    BackupDocument,
    Device,
    Group,
    KnownDevice,
    Scene,
    Schedule,
)
from ._helpers import _validated_rows

logger = get_logger(__name__)

router = APIRouter(prefix="/api/backup")

try:
    _APP_VERSION = version("kasa-nice")
except PackageNotFoundError:  # not installed (e.g. running from a bare checkout)
    _APP_VERSION = "0.0.0"

# Filenames offered via Content-Disposition. Static (not timestamped): the
# browser's own "(1)"-style dedup on repeat downloads is enough, and a fixed
# name makes the restore file picker's "did I pick the right file" check easy.
_JSON_FILENAME = "kasa-nice-backup.json"
_ENERGY_DB_FILENAME = "kasa-nice-energy-history.db"


def _build_document() -> BackupDocument:
    """Assemble the current contents of every JSON store into one document.

    Rows are re-validated through their response models on the way out, same as
    every list endpoint (``_validated_rows``), so a hand-edited/corrupt row is
    dropped with a warning rather than poisoning the whole backup.
    """
    hosts, snapshots = registry.known_devices_export()
    known_devices = [KnownDevice(host=h, snapshot=snapshots.get(h)) for h in hosts]
    return BackupDocument(
        backup_version=CURRENT_BACKUP_VERSION,
        created_at=datetime.datetime.now(datetime.UTC),
        app_version=_APP_VERSION,
        groups=_validated_rows(groups.list_groups(), Group, "group"),
        favorites=groups.get_favorites(),
        scenes=_validated_rows(scenes.list_scenes(), Scene, "scene"),
        schedules=_validated_rows(schedules.list_rules(), Schedule, "schedule"),
        alert_thresholds=alert_thresholds.get_all(),
        known_devices=known_devices,
    )


@router.get("", response_model=BackupDocument)
async def download_backup() -> Response:
    """Every JSON store as one downloadable, versioned document."""
    doc = _build_document()
    return Response(
        content=doc.model_dump_json(indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{_JSON_FILENAME}"'},
    )


def _reject_bad_snapshot(known: KnownDevice) -> None:
    """422 with a clear message if a known-device snapshot isn't a valid Device.

    ``KnownDevice.snapshot`` is left as a raw dict in the schema (see its
    docstring) so this route can surface a precise error instead of a generic
    "invalid backup" — restore must fail loudly here, not silently drop the host.
    """
    if known.snapshot is None:
        return
    try:
        Device(**known.snapshot)
    except ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid known-device snapshot for host {known.host!r}: {e}",
        ) from None


@router.post("/restore", response_model=BackupDocument)
async def restore_backup(doc: BackupDocument) -> BackupDocument:
    """Replace every JSON store's contents from a backup document.

    ``doc`` is already fully validated by FastAPI/pydantic against
    :class:`BackupDocument` before this body runs — including
    ``backup_version`` — so an invalid or foreign-shaped payload never reaches
    here. The one field pydantic can't fully validate up front (a known-device
    snapshot, deliberately typed as a raw dict) is checked explicitly, still
    before any store is touched, so a bad snapshot can't leave a
    partially-restored server. Each store's own replace is a single atomic
    write, so at worst one store's write can fail independently — there is no
    multi-store transaction, matching every store's existing best-effort
    persistence model.
    """
    for known in doc.known_devices:
        _reject_bad_snapshot(known)

    groups.replace_all([g.model_dump() for g in doc.groups], doc.favorites)
    scenes.replace_all([s.model_dump() for s in doc.scenes])
    schedules.replace_all([s.model_dump() for s in doc.schedules])
    alert_thresholds.set_all(doc.alert_thresholds)
    registry.restore_known_devices(
        [k.host for k in doc.known_devices],
        {k.host: k.snapshot for k in doc.known_devices if k.snapshot is not None},
    )

    # Nudge every connected client to refresh now rather than on their next poll,
    # matching every other mutating route.
    await broadcaster.publish_now()
    return doc


@contextmanager
def _consistent_snapshot(src: Path) -> Iterator[Path]:
    """Yield a point-in-time copy of the (possibly WAL-mode, live-written) DB.

    Plain ``shutil.copy`` of a WAL database can copy a half-checkpointed state;
    ``sqlite3.Connection.backup`` instead performs SQLite's own online backup
    (its designed answer to "copy a live database"), so the copy is always a
    valid, consistent snapshot regardless of concurrent writes from the
    recorder. Written to a temp file so the streamed response has no in-memory
    buffering of the whole DB; the caller (this context manager) removes it
    afterwards even if streaming fails partway.
    """
    fd, tmp_name = tempfile.mkstemp(suffix=".db", prefix="kasa-nice-energy-")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with (
            sqlite3.connect(src) as source_conn,
            sqlite3.connect(tmp_path) as dest_conn,
        ):
            source_conn.backup(dest_conn)
        yield tmp_path
    finally:
        tmp_path.unlink(missing_ok=True)


@router.get("/energy.db")
async def energy_db() -> Response:
    """Stream a consistent point-in-time copy of the energy-history SQLite file.

    Kept out of the JSON backup document (see module docstring): sampling and
    pruning run concurrently against the live file, so a plain file copy could
    catch it mid-write. FastAPI streams the temp file and closes it; the
    ``background`` cleanup below removes it once the response finishes sending,
    so the temp copy doesn't leak even though it outlives this handler's frame
    (the copy must survive until the last byte is streamed, not just until the
    handler returns).
    """
    if not history.path.exists():
        raise HTTPException(status_code=404, detail="No energy history recorded yet.")
    with _consistent_snapshot(history.path) as tmp_path:
        # Read the bytes now (inside the context manager, before cleanup) rather
        # than streaming the temp file async and unlinking it in a background
        # task: the DB is typically small (SQLite metadata + samples), so paying
        # one synchronous read keeps the temp-file lifetime trivially correct
        # instead of racing a background delete against an in-flight stream.
        data = tmp_path.read_bytes()
    return Response(
        content=data,
        media_type="application/vnd.sqlite3",
        headers={
            "Content-Disposition": f'attachment; filename="{_ENERGY_DB_FILENAME}"'
        },
    )
