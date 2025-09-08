"""The whole audio pipeline, with yt-dlp and the network faked out.

ffmpeg is real, so the cutting, tagging, caching and packaging are exercised
end to end; only the download is a stub that copies a generated tone.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from mutagen.id3 import ID3

from chaptercut.cache.manifest import read_manifest
from chaptercut.cache.store import CacheStore
from chaptercut.pipeline import cover as cover_art
from chaptercut.pipeline.process import run_checked
from chaptercut.pipeline.runner import AudioResult, Pipeline, VideoResult
from chaptercut.pipeline.sink import RecordingSink
from chaptercut.pipeline.ytdlp import VideoInfo
from chaptercut.queue.models import ExtractType, Job, JobState, Phase
from chaptercut.settings import Settings
from chaptercut.util.timefmt import utcnow
from tests.conftest import ytdlp_info

pytestmark = pytest.mark.ffmpeg

VIDEO_ID = "dQw4w9WgXcQ"
TONE_SECONDS = 30

CHAPTERS = [
    {"start_time": 0, "end_time": 10, "title": "First Movement"},
    {"start_time": 10, "end_time": 20, "title": "Second: Movement"},
    {"start_time": 20, "title": "Finale"},
]


class FakeYtdlp:
    """Returns canned metadata and copies a prepared file instead of downloading."""

    def __init__(self, source: Path, info: dict[str, Any]) -> None:
        self.source = source
        self.info_dict = info
        self.audio_calls = 0
        self.video_calls = 0
        self.last_format_id: str | None = None

    async def info(self, url: str, timeout: float = 120.0) -> VideoInfo:
        return VideoInfo(self.info_dict)

    async def download_audio(
        self,
        url: str,
        destination: Path,
        timeout: float,
        bitrate: str = "",
        on_progress: Any = None,
    ) -> Path:
        self.audio_calls += 1
        target = destination.with_suffix(".mp3")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.source, target)
        return target

    async def download_video(
        self,
        url: str,
        destination: Path,
        timeout: float,
        format_id: str | None = None,
        on_progress: Any = None,
    ) -> Path:
        self.video_calls += 1
        self.last_format_id = format_id
        target = destination.with_suffix(".mp4")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.source, target)
        return target


@pytest.fixture(autouse=True)
def no_cover_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cover fetch is the pipeline's only network call; stub it out."""

    async def fake_fetch(url: str, timeout: float = 30.0) -> bytes | None:
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (1280, 720), (10, 20, 30)).save(buffer, format="JPEG")
        return buffer.getvalue()

    monkeypatch.setattr(cover_art, "fetch_bytes", fake_fetch)


@pytest.fixture
async def tone_mp3(tmp_path: Path, requires_ffmpeg: None) -> Path:
    path = tmp_path / "tone.mp3"
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


def a_job(kind: ExtractType = ExtractType.AUDIO, format_id: str | None = None) -> Job:
    return Job(
        job_id="job1234",
        req_id="req12345",
        user_id=111,
        chat_id=999,
        kind=kind,
        video_id=VIDEO_ID,
        url=f"https://www.youtube.com/watch?v={VIDEO_ID}",
        state=JobState.RUNNING,
        created_at=utcnow(),
        format_id=format_id,
    )


def build(
    settings: Settings, cache: CacheStore, tone: Path, **info_kwargs: Any
) -> tuple[Pipeline, FakeYtdlp]:
    ytdlp = FakeYtdlp(tone, ytdlp_info(VIDEO_ID, **info_kwargs))
    return Pipeline(settings, ytdlp, cache), ytdlp  # pyright: ignore[reportArgumentType]


async def test_audio_job_produces_tagged_tracks_a_cover_and_a_zip(
    settings: Settings, cache: CacheStore, tone_mp3: Path, data_dir: Path
) -> None:
    pipeline, ytdlp = build(settings, cache, tone_mp3, chapters=CHAPTERS)
    sink = RecordingSink()

    result = await pipeline.run(a_job(), data_dir / "work" / "job1234", sink)

    assert isinstance(result, AudioResult)
    assert result.from_cache is False
    assert result.track_count == 3
    assert [path.name for path in result.tracks] == [
        "01 - First Movement.mp3",
        "02 - Second Movement.mp3",
        "03 - Finale.mp3",
    ]
    assert result.cover is not None and result.cover.is_file()
    assert result.zip_path is not None and result.zip_path.is_file()

    tags = ID3(result.tracks[0])
    assert tags["TIT2"].text == ["First Movement"]
    assert tags["TALB"].text == ["Test Album"]
    assert tags.getall("APIC")

    assert ytdlp.audio_calls == 1
    assert sink.phases == [
        Phase.FETCH,
        Phase.DOWNLOAD,
        Phase.SPLIT,
        Phase.TAG,
        Phase.PACKAGE,
    ]


async def test_the_result_is_committed_to_the_cache_atomically(
    settings: Settings, cache: CacheStore, tone_mp3: Path, data_dir: Path
) -> None:
    pipeline, _ytdlp = build(settings, cache, tone_mp3, chapters=CHAPTERS)
    await pipeline.run(a_job(), data_dir / "work" / "job1234", RecordingSink())

    entry = cache.get(VIDEO_ID)
    assert entry is not None
    assert len(entry.manifest.tracks) == 3
    assert read_manifest(cache.path_for(VIDEO_ID)) is not None
    assert not cache.tmp_path_for(VIDEO_ID).exists()


async def test_a_second_run_is_served_from_the_cache_without_downloading(
    settings: Settings, cache: CacheStore, tone_mp3: Path, data_dir: Path
) -> None:
    pipeline, ytdlp = build(settings, cache, tone_mp3, chapters=CHAPTERS)
    await pipeline.run(a_job(), data_dir / "work" / "job1", RecordingSink())

    second = await pipeline.run(a_job(), data_dir / "work" / "job2", RecordingSink())

    assert isinstance(second, AudioResult)
    assert second.from_cache is True
    assert second.track_count == 3
    assert ytdlp.audio_calls == 1


async def test_the_cached_result_keeps_the_original_download_date(
    settings: Settings, cache: CacheStore, tone_mp3: Path, data_dir: Path
) -> None:
    pipeline, _ytdlp = build(settings, cache, tone_mp3, chapters=CHAPTERS)
    first = await pipeline.run(a_job(), data_dir / "work" / "job1", RecordingSink())
    second = await pipeline.run(a_job(), data_dir / "work" / "job2", RecordingSink())

    assert isinstance(first, AudioResult)
    assert isinstance(second, AudioResult)
    assert second.manifest.downloaded_at == first.manifest.downloaded_at


async def test_a_video_without_chapters_becomes_a_single_track(
    settings: Settings, cache: CacheStore, tone_mp3: Path, data_dir: Path
) -> None:
    pipeline, _ytdlp = build(settings, cache, tone_mp3)
    result = await pipeline.run(a_job(), data_dir / "work" / "job1234", RecordingSink())

    assert isinstance(result, AudioResult)
    assert result.track_count == 1
    assert result.zip_path is None
    assert ID3(result.tracks[0])["TIT2"].text == ["Test Album"]


async def test_individual_delivery_mode_skips_the_zip(
    settings: Settings, cache: CacheStore, tone_mp3: Path, data_dir: Path
) -> None:
    settings.audio_multi_delivery = "individual"
    pipeline, _ytdlp = build(settings, cache, tone_mp3, chapters=CHAPTERS)
    result = await pipeline.run(a_job(), data_dir / "work" / "job1234", RecordingSink())

    assert isinstance(result, AudioResult)
    assert result.track_count == 3
    assert result.zip_path is None


async def test_track_mtimes_are_staggered_after_a_cache_hit(
    settings: Settings, cache: CacheStore, tone_mp3: Path, data_dir: Path
) -> None:
    pipeline, _ytdlp = build(settings, cache, tone_mp3, chapters=CHAPTERS)
    await pipeline.run(a_job(), data_dir / "work" / "job1", RecordingSink())

    # Flatten the mtimes the way a copy would, then serve from cache.
    for path in cache.path_for(VIDEO_ID).glob("*.mp3"):
        path.touch()
    result = await pipeline.run(a_job(), data_dir / "work" / "job2", RecordingSink())

    assert isinstance(result, AudioResult)
    stamps = [path.stat().st_mtime for path in result.tracks]
    assert stamps == sorted(stamps)
    assert stamps[0] != stamps[1]


async def test_video_job_returns_the_downloaded_file(
    settings: Settings, cache: CacheStore, tone_mp3: Path, data_dir: Path
) -> None:
    pipeline, ytdlp = build(settings, cache, tone_mp3)
    result = await pipeline.run(
        a_job(ExtractType.VIDEO, format_id="137"), data_dir / "work" / "job1234", RecordingSink()
    )

    assert isinstance(result, VideoResult)
    assert result.path.is_file()
    assert result.title == "Test Album"
    assert ytdlp.video_calls == 1
    assert ytdlp.last_format_id == "137"


async def test_a_video_job_does_not_populate_the_audio_cache(
    settings: Settings, cache: CacheStore, tone_mp3: Path, data_dir: Path
) -> None:
    pipeline, _ytdlp = build(settings, cache, tone_mp3)
    await pipeline.run(
        a_job(ExtractType.VIDEO, format_id="137"), data_dir / "work" / "job1234", RecordingSink()
    )
    assert cache.get(VIDEO_ID) is None


async def test_eviction_removes_the_least_recently_served_first(
    settings: Settings, cache: CacheStore, tone_mp3: Path, data_dir: Path
) -> None:
    pipeline, _ytdlp = build(settings, cache, tone_mp3, chapters=CHAPTERS)
    await pipeline.run(a_job(), data_dir / "work" / "job1", RecordingSink())

    settings.cache_max_bytes = 1
    removed = pipeline.evict_to_fit([VIDEO_ID])

    assert removed == [VIDEO_ID]
    assert cache.get(VIDEO_ID) is None


async def test_nothing_is_evicted_while_under_budget(
    settings: Settings, cache: CacheStore, tone_mp3: Path, data_dir: Path
) -> None:
    pipeline, _ytdlp = build(settings, cache, tone_mp3, chapters=CHAPTERS)
    await pipeline.run(a_job(), data_dir / "work" / "job1", RecordingSink())
    assert pipeline.evict_to_fit([VIDEO_ID]) == []
    assert cache.get(VIDEO_ID) is not None
