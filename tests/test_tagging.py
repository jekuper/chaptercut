"""Every frame from the tagging table, checked on a real MP3 file.

The fixture is a synthetic silent frame, not a downloaded track.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from mutagen.id3 import ID3

from chaptercut.pipeline.chapters import Track
from chaptercut.pipeline.tagging import (
    MTIME_STEP_SECONDS,
    TXXX_DOWNLOADED_AT,
    TXXX_SOURCE_URL,
    TXXX_VIDEO_ID,
    TrackMeta,
    apply_mtimes,
    write_tags_sync,
)

DOWNLOADED_AT = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)
URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# One silent MPEG-1 Layer III frame, 44.1 kHz, 128 kbps: 417 bytes on the wire.
SILENT_FRAME = b"\xff\xfb\x90\x64" + b"\x00" * 413


@pytest.fixture
def mp3_file(tmp_path: Path) -> Path:
    path = tmp_path / "01 - Intro.mp3"
    path.write_bytes(SILENT_FRAME * 40)
    return path


@pytest.fixture
def cover_file(tmp_path: Path) -> Path:
    from PIL import Image

    path = tmp_path / "cover.jpg"
    Image.new("RGB", (64, 64), (10, 20, 30)).save(path, format="JPEG")
    return path


@pytest.fixture
def meta() -> TrackMeta:
    return TrackMeta(
        album="Test Album",
        artist="Test Channel",
        video_id="dQw4w9WgXcQ",
        url=URL,
        year="2026",
        downloaded_at=DOWNLOADED_AT,
        total_tracks=12,
    )


@pytest.fixture
def track() -> Track:
    return Track(index=3, title="Nocturne in E flat", start=0.0, end=61.0)


def tag_all(path: Path, track: Track, meta: TrackMeta, cover: Path | None = None) -> ID3:
    write_tags_sync(path, track, meta, cover)
    return ID3(path)


def test_every_text_frame_is_written(mp3_file: Path, track: Track, meta: TrackMeta) -> None:
    tags = tag_all(mp3_file, track, meta)
    assert tags["TIT2"].text == ["Nocturne in E flat"]
    assert tags["TPE1"].text == ["Test Channel"]
    assert tags["TALB"].text == ["Test Album"]
    assert tags["TPE2"].text == ["Test Channel"]
    assert str(tags["TRCK"].text[0]) == "3/12"
    assert str(tags["TDRC"].text[0]) == "2026"


def test_album_is_the_video_title_not_a_url(mp3_file: Path, track: Track, meta: TrackMeta) -> None:
    # The predecessor wrote "<url> (<date>)" here, so players showed a URL.
    album = str(tag_all(mp3_file, track, meta)["TALB"].text[0])
    assert album == "Test Album"
    assert "http" not in album


def test_comment_frame_carries_readable_provenance(
    mp3_file: Path, track: Track, meta: TrackMeta
) -> None:
    tags = tag_all(mp3_file, track, meta)
    comment = tags.getall("COMM")[0]
    assert comment.lang == "eng"
    assert comment.desc == ""
    assert comment.text[0] == f"Source: {URL}\nDownloaded: 2026-08-23"


def test_machine_readable_txxx_frames(mp3_file: Path, track: Track, meta: TrackMeta) -> None:
    tags = tag_all(mp3_file, track, meta)
    by_desc = {frame.desc: frame.text[0] for frame in tags.getall("TXXX")}
    assert by_desc[TXXX_SOURCE_URL] == URL
    assert by_desc[TXXX_DOWNLOADED_AT] == "2026-08-23T12:00:00Z"
    assert by_desc[TXXX_VIDEO_ID] == "dQw4w9WgXcQ"


def test_cover_is_embedded_as_a_front_cover(
    mp3_file: Path, track: Track, meta: TrackMeta, cover_file: Path
) -> None:
    tags = tag_all(mp3_file, track, meta, cover_file)
    apic = tags.getall("APIC")[0]
    assert apic.mime == "image/jpeg"
    assert apic.type == 3
    assert apic.data == cover_file.read_bytes()


def test_no_cover_leaves_no_apic_frame(mp3_file: Path, track: Track, meta: TrackMeta) -> None:
    assert tag_all(mp3_file, track, meta, None).getall("APIC") == []


def test_missing_cover_file_is_tolerated(
    mp3_file: Path, track: Track, meta: TrackMeta, tmp_path: Path
) -> None:
    assert tag_all(mp3_file, track, meta, tmp_path / "gone.jpg").getall("APIC") == []


def test_tags_are_id3v24(mp3_file: Path, track: Track, meta: TrackMeta) -> None:
    assert tag_all(mp3_file, track, meta).version[:2] == (2, 4)


def test_empty_year_omits_the_date_frame(mp3_file: Path, track: Track, meta: TrackMeta) -> None:
    tags = tag_all(mp3_file, track, replace(meta, year=""))
    assert tags.getall("TDRC") == []


def test_retagging_replaces_rather_than_appends(
    mp3_file: Path, track: Track, meta: TrackMeta
) -> None:
    write_tags_sync(mp3_file, track, meta, None)
    second = Track(index=4, title="Different", start=0.0, end=10.0)
    tags = tag_all(mp3_file, second, meta)
    assert tags["TIT2"].text == ["Different"]
    assert len(tags.getall("TIT2")) == 1


def test_mtimes_are_staggered_in_track_order(tmp_path: Path) -> None:
    paths = [tmp_path / f"{i:02d}.mp3" for i in range(1, 4)]
    for path in paths:
        path.write_bytes(b"x")

    apply_mtimes(paths, DOWNLOADED_AT)

    stamps = [path.stat().st_mtime for path in paths]
    assert stamps == sorted(stamps)
    assert stamps[1] - stamps[0] == pytest.approx(MTIME_STEP_SECONDS, abs=1)
    assert stamps[0] == pytest.approx(DOWNLOADED_AT.timestamp(), abs=1)


def test_apply_mtimes_skips_missing_files(tmp_path: Path) -> None:
    apply_mtimes([tmp_path / "gone.mp3"], DOWNLOADED_AT)
