"""Data directory layout helpers."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def dir_size(path: Path) -> int:
    """Total size of every regular file under `path`. Missing path means zero."""
    if not path.exists():
        return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


def rmtree_quiet(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free
