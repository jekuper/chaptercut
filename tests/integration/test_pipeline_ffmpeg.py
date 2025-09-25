"""End-to-end split, tag, and package, against a generated sine wave.

Marked `ffmpeg` because it shells out; `pytest -m "not ffmpeg"` skips it.
The source is synthesised here, so the test never touches the network.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from mutagen.id3 import ID3, TALB
from mutagen.mp3 import MP3

from chaptercut.pipeline.chapters import chapters_from_info
from chaptercut.pipeline.ffmpeg import FfmpegError, cut, probe_duration
from chaptercut.pipeline.package import make_zip_sync
from chaptercut.pipeline.process import run_checked
from chaptercut.pipeline.sanitize import track_filename
from chaptercut.pipeline.tagging import TrackMeta, apply_mtimes, write_tags_sync
from tests.conftest import ytdlp_info

pytestmark = pytest.mark.ffmpeg

DOWNLOADED_AT = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)
TONE_SECONDS = 30


@pytest.fixture
async def tone_mp3(tmp_path: Path, requires_ffmpeg: None) -> Path:
    """A 30 second 440 Hz tone encoded as MP3."""
    path = tmp_path / "source.mp3"
    await run_checked(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={TONE_SECONDS}",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(path),
        ],
        timeout=120,
    )
    return path


async def test_probe_duration_matches_the_generated_length(tone_mp3: Path) -> None:
    assert await probe_duration(tone_mp3) == pytest.approx(TONE_SECONDS, abs=0.5)


async def test_probe_duration_rejects_a_non_media_file(
    tmp_path: Path, requires_ffmpeg: None
) -> None:
    path = tmp_path / "not-audio.mp3"
    path.write_text("hello", encoding="utf-8")
    with pytest.raises(FfmpegError):
        await probe_duration(path)


async def test_cut_produces_a_track_of_the_requested_length(tone_mp3: Path, tmp_path: Path) -> None:
    out = tmp_path / "cut.mp3"
    await cut(tone_mp3, out, start=5.0, end=15.0)
    assert await probe_duration(out) == pytest.approx(10.0, abs=0.5)


async def test_cut_is_a_stream_copy(tone_mp3: Path, tmp_path: Path) -> None:
    # A stream copy keeps the source bitrate exactly; a re-encode would not.
    out = tmp_path / "cut.mp3"
    await cut(tone_mp3, out, start=0.0, end=10.0)
    assert MP3(out).info.bitrate == MP3(tone_mp3).info.bitrate


async def test_cut_strips_the_source_metadata(tone_mp3: Path, tmp_path: Path) -> None:
    tagged = MP3(tone_mp3)
    if tagged.tags is None:
        tagged.add_tags()
    assert tagged.tags is not None
    tagged.tags.add(TALB(encoding=3, text=["Leftover Album"]))
    tagged.save()
    assert ID3(tone_mp3).getall("TALB") != []

    out = tmp_path / "cut.mp3"
    await cut(tone_mp3, out, start=0.0, end=5.0)

    # ffmpeg still stamps its own TSSE encoder frame; nothing from the source survives.
    assert MP3(out).tags is not None
    assert MP3(out).tags.getall("TALB") == []


async def test_an_empty_range_is_refused(tone_mp3: Path, tmp_path: Path) -> None:
    with pytest.raises(FfmpegError, match="empty range"):
        await cut(tone_mp3, tmp_path / "cut.mp3", start=5.0, end=5.0)


async def test_full_split_tag_and_package(tone_mp3: Path, tmp_path: Path) -> None:
    info = ytdlp_info(
        chapters=[
            {"start_time": 0, "end_time": 10, "title": "First Movement"},
            {"start_time": 10, "end_time": 20, "title": "Second: Movement"},
            {"start_time": 20, "title": "Finale"},
        ],
    )
    duration = await probe_duration(tone_mp3)
    tracks = chapters_from_info(info, duration=duration)
    assert len(tracks) == 3

    out_dir = tmp_path / "out"
    meta = TrackMeta(
        album="Test Album",
        artist="Test Channel",
        video_id="dQw4w9WgXcQ",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        year="2026",
        downloaded_at=DOWNLOADED_AT,
        total_tracks=len(tracks),
    )

    written: list[Path] = []
    for track in tracks:
        destination = out_dir / track_filename(track.index, len(tracks), track.title)
        await cut(tone_mp3, destination, track.start, track.end)
        write_tags_sync(destination, track, meta, None)
        written.append(destination)

    apply_mtimes(written, DOWNLOADED_AT)

    # Filenames are sanitized and numbered.
    assert [path.name for path in written] == [
        "01 - First Movement.mp3",
        "02 - Second Movement.mp3",
        "03 - Finale.mp3",
    ]

    # Every track carries its own title and a shared album.
    for index, path in enumerate(written, start=1):
        tags = ID3(path)
        assert str(tags["TRCK"].text[0]) == f"{index}/3"
        assert tags["TALB"].text == ["Test Album"]
    assert ID3(written[0])["TIT2"].text == ["First Movement"]

    # The last chapter runs to the real end of the file.
    assert await probe_duration(written[-1]) == pytest.approx(duration - 20, abs=0.5)

    archive = make_zip_sync(written, tmp_path / "album.zip", "Test Album")
    assert archive.is_file()
    assert archive.stat().st_size > 0
