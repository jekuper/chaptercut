# mutagen ships no type information for ID3 frames, so strict mode cannot see
# through `tags.add`. Suppressed for this file only.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

"""ID3v2.4 tagging.

Provenance goes in the frames built for it. The predecessor wrote the source
URL and download date into the album field, so every player displayed a URL
where the album name belongs; TALB here is the video title and nothing else.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mutagen.id3 import (
    APIC,
    COMM,
    ID3,
    TALB,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TRCK,
    TXXX,
)
from mutagen.mp3 import MP3

from chaptercut.pipeline.chapters import Track
from chaptercut.util.timefmt import iso

ID3_VERSION = (2, 4, 0)

TXXX_SOURCE_URL = "SOURCE_URL"
TXXX_DOWNLOADED_AT = "DOWNLOADED_AT"
TXXX_VIDEO_ID = "VIDEO_ID"

# Two seconds apart so players that sort by "date added" keep album order.
MTIME_STEP_SECONDS = 2


@dataclass(frozen=True, slots=True)
class TrackMeta:
    """Everything a track's tags need, shared across one video's tracks."""

    album: str
    artist: str
    video_id: str
    url: str
    year: str
    downloaded_at: datetime
    total_tracks: int


def _cover_frame(cover: Path | None) -> APIC | None:
    if cover is None or not cover.is_file():
        return None
    return APIC(
        encoding=3,
        mime="image/jpeg",
        type=3,  # front cover
        desc="Cover",
        data=cover.read_bytes(),
    )


def _comment_text(meta: TrackMeta) -> str:
    """The human-readable provenance most players show as "Comment"."""
    return f"Source: {meta.url}\nDownloaded: {meta.downloaded_at.date().isoformat()}"


def write_tags_sync(path: Path, track: Track, meta: TrackMeta, cover: Path | None) -> None:
    audio = MP3(path)
    audio.delete()
    tags = audio.tags if audio.tags is not None else ID3()

    tags.add(TIT2(encoding=3, text=[track.title]))
    tags.add(TPE1(encoding=3, text=[meta.artist]))
    tags.add(TALB(encoding=3, text=[meta.album]))
    tags.add(TPE2(encoding=3, text=[meta.artist]))
    tags.add(TRCK(encoding=3, text=[f"{track.index}/{meta.total_tracks}"]))
    if meta.year:
        tags.add(TDRC(encoding=3, text=[meta.year]))
    tags.add(COMM(encoding=3, lang="eng", desc="", text=[_comment_text(meta)]))
    tags.add(TXXX(encoding=3, desc=TXXX_SOURCE_URL, text=[meta.url]))
    tags.add(TXXX(encoding=3, desc=TXXX_DOWNLOADED_AT, text=[iso(meta.downloaded_at)]))
    tags.add(TXXX(encoding=3, desc=TXXX_VIDEO_ID, text=[meta.video_id]))

    frame = _cover_frame(cover)
    if frame is not None:
        tags.add(frame)

    audio.tags = tags
    audio.save(path, v2_version=ID3_VERSION[1])


async def write_tags(path: Path, track: Track, meta: TrackMeta, cover: Path | None) -> None:
    await asyncio.to_thread(write_tags_sync, path, track, meta, cover)


def apply_mtimes(paths: list[Path], downloaded_at: datetime) -> None:
    """Stagger mtimes in track order. Re-applied when serving from cache, since
    copying and zipping reset them."""
    base = downloaded_at.timestamp()
    for index, path in enumerate(paths):
        if not path.exists():
            continue
        stamp = base + index * MTIME_STEP_SECONDS
        os.utime(path, (stamp, stamp))
