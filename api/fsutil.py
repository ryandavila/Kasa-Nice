"""Crash-safe file writes shared by the JSON stores."""

import contextlib
import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` so readers see the old or the new file, never a torn mix.

    Writes to a sibling temp file, fsyncs, and renames over the target. A plain
    truncate-and-write can be interrupted mid-write, leaving JSON the stores'
    tolerant readers silently degrade to an empty document — which the next
    save would persist, wiping the data for good. Creates parent directories;
    raises ``OSError`` like a plain write, so callers keep their handling.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
