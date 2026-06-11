"""Atomic file write utilities for pluck.

All file writes in pluck use the same pattern: write to a temp file, then
``os.replace`` to the target.  This prevents corruption from concurrent writes
and crash mid-write.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


def atomic_write(path: Path, content: str, backup: bool = False) -> None:
    """Write text content to a file atomically.

    Parameters
    ----------
    path:
        Target file path.
    content:
        Text content to write.
    backup:
        If ``True``, create a ``.bak`` copy of any existing file before writing.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))

    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp", prefix=".pluck_", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise
    finally:
        if os.path.exists(tmp_path):
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)


def atomic_write_json(
    path: Path,
    data: dict[str, Any],
    *,
    indent: int = 2,
    backup: bool = True,
) -> None:
    """Write JSON data to a file atomically.

    Parameters
    ----------
    path:
        Target file path.
    data:
        Data to serialize as JSON.
    indent:
        JSON indentation level.
    backup:
        If ``True`` (default), create a ``.json.bak`` copy of any existing file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(".json.bak"))

    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp", prefix=".pluck_", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise
    finally:
        if os.path.exists(tmp_path):
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)


def safe_load_json(path: Path) -> dict[str, Any]:
    """Load JSON file with fallback to backup on corruption.

    Returns an empty dict if the file does not exist.
    """
    path = Path(path)
    if not path.exists():
        return {}

    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        backup = path.with_suffix(".json.bak")
        if backup.exists():
            import logging

            logging.getLogger(__name__).warning(
                "Corrupted JSON: %s, restoring from backup", path
            )
            with open(backup, encoding="utf-8") as f:
                return json.load(f)  # type: ignore[no-any-return]
        raise
