"""ZIP packaging for multi-track results."""

from __future__ import annotations

import asyncio
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

COPY_CHUNK = 1024 * 1024


def _zip_time(path: Path) -> tuple[int, int, int, int, int, int]:
    stamp = datetime.fromtimestamp(path.stat().st_mtime)
    return (stamp.year, stamp.month, stamp.day, stamp.hour, stamp.minute, stamp.second)


def make_zip_sync(files: list[Path], destination: Path, inner_folder: str) -> Path:
    """Store `files` under `inner_folder/` inside `destination`.

    ZIP_STORED, not DEFLATE: MP3 and JPEG are already compressed, so deflating
    costs CPU and saves nothing.
    """
    if not files:
        raise ValueError("refusing to build an empty zip")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in files:
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(f"{inner_folder}/{path.name}", date_time=_zip_time(path))
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o644 << 16
            info.file_size = path.stat().st_size
            # Streamed, so a large album never sits in memory twice.
            with path.open("rb") as source, archive.open(info, "w") as target:
                shutil.copyfileobj(source, target, COPY_CHUNK)
    return destination


async def make_zip(files: list[Path], destination: Path, inner_folder: str) -> Path:
    return await asyncio.to_thread(make_zip_sync, files, destination, inner_folder)
