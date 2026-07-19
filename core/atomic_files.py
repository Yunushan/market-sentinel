from __future__ import annotations

import os
import tempfile
from pathlib import Path


def fsync_parent_directory(path: Path) -> None:
    """Persist a completed atomic replacement on POSIX filesystems."""
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path.parent, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_text(path: Path, content: str) -> Path:
    """Write text through an exclusive temporary file and atomically publish it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_parent_directory(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return path
