"""Delivery: one path for every file, and a hard stop above the size limit."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aiogram.types import FSInputFile

from chaptercut.bot.deliver import Delivery, TooLargeError
from chaptercut.cache.store import CacheKey
from chaptercut.pipeline.runner import AudioResult, VideoResult
from tests.conftest import make_manifest

LIMIT = 1_000_000


class RecordingBot:
    def __init__(self) -> None:
        self.audio: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []
        self.videos: list[dict[str, Any]] = []

    async def send_audio(self, **kwargs: Any) -> str:
        self.audio.append(kwargs)
        return "audio-message"

    async def send_document(self, **kwargs: Any) -> str:
        self.documents.append(kwargs)
        return "document-message"

    async def send_video(self, **kwargs: Any) -> str:
        self.videos.append(kwargs)
        return "video-message"


def audio_result(tmp_path: Path, tracks: int, with_zip: bool, size: int = 1024) -> AudioResult:
    manifest = make_manifest("dQw4w9WgXcQ", tracks=tracks)
    directory = tmp_path / "cache"
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for track in manifest.tracks:
        path = directory / track.file
        path.write_bytes(b"a" * size)
        paths.append(path)
    cover = directory / "cover.jpg"
    cover.write_bytes(b"jpeg")
    thumbnail = tmp_path / "thumb.jpg"
    thumbnail.write_bytes(b"jpeg")

    zip_path = None
    if with_zip:
        zip_path = tmp_path / "Test Album.zip"
        zip_path.write_bytes(b"z" * size)

    return AudioResult(
        video_id="dQw4w9WgXcQ",
        title="Test Album",
        uploader="Test Channel",
        duration=120.0,
        manifest=manifest,
        directory=directory,
        tracks=paths,
        cover=cover,
        thumbnail=thumbnail,
        zip_path=zip_path,
        from_cache=False,
        key=CacheKey("youtube", "dQw4w9WgXcQ"),
    )


def video_result(tmp_path: Path, size: int = 1024) -> VideoResult:
    path = tmp_path / "video.mp4"
    path.write_bytes(b"v" * size)
    return VideoResult(
        video_id="dQw4w9WgXcQ",
        title="Test: Video",
        uploader="Test Channel",
        duration=212.0,
        path=path,
        width=1920,
        height=1080,
        thumbnail=None,
    )


def delivery(bot: RecordingBot, multi_mode: str = "zip") -> Delivery:
    return Delivery(bot=bot, chat_id=999, max_send_bytes=LIMIT, multi_mode=multi_mode)  # pyright: ignore[reportArgumentType]


async def test_a_single_track_goes_out_as_an_audio_message(tmp_path: Path) -> None:
    bot = RecordingBot()
    await delivery(bot).send_audio_result(audio_result(tmp_path, tracks=1, with_zip=False))

    assert len(bot.audio) == 1
    sent = bot.audio[0]
    assert sent["title"] == "Track 1"
    assert sent["performer"] == "Test Channel"
    assert sent["duration"] == 60
    assert isinstance(sent["audio"], FSInputFile)
    assert bot.documents == []


async def test_multiple_tracks_default_to_a_zip(tmp_path: Path) -> None:
    bot = RecordingBot()
    await delivery(bot).send_audio_result(audio_result(tmp_path, tracks=3, with_zip=True))

    assert len(bot.documents) == 1
    assert bot.audio == []
    assert bot.documents[0]["document"].filename == "Test Album.zip"


async def test_individual_mode_sends_one_message_per_track(tmp_path: Path) -> None:
    bot = RecordingBot()
    await delivery(bot, multi_mode="individual").send_audio_result(
        audio_result(tmp_path, tracks=3, with_zip=False)
    )

    assert len(bot.audio) == 3
    assert bot.documents == []
    assert [sent["title"] for sent in bot.audio] == ["Track 1", "Track 2", "Track 3"]


async def test_both_mode_sends_the_zip_and_the_tracks(tmp_path: Path) -> None:
    bot = RecordingBot()
    await delivery(bot, multi_mode="both").send_audio_result(
        audio_result(tmp_path, tracks=3, with_zip=True)
    )

    assert len(bot.documents) == 1
    assert len(bot.audio) == 3


async def test_video_carries_the_player_metadata(tmp_path: Path) -> None:
    bot = RecordingBot()
    await delivery(bot).send_video_result(video_result(tmp_path))

    sent = bot.videos[0]
    assert sent["width"] == 1920
    assert sent["height"] == 1080
    assert sent["duration"] == 212
    assert sent["supports_streaming"] is True
    assert sent["video"].filename == "Test Video.mp4"


async def test_an_oversized_track_is_refused(tmp_path: Path) -> None:
    bot = RecordingBot()
    with pytest.raises(TooLargeError, match="over the"):
        await delivery(bot).send_audio_result(
            audio_result(tmp_path, tracks=1, with_zip=False, size=LIMIT + 1)
        )
    assert bot.audio == []


async def test_an_oversized_zip_is_refused(tmp_path: Path) -> None:
    bot = RecordingBot()
    with pytest.raises(TooLargeError):
        await delivery(bot).send_audio_result(
            audio_result(tmp_path, tracks=3, with_zip=True, size=LIMIT + 1)
        )


async def test_an_oversized_video_is_refused(tmp_path: Path) -> None:
    bot = RecordingBot()
    with pytest.raises(TooLargeError):
        await delivery(bot).send_video_result(video_result(tmp_path, size=LIMIT + 1))
    assert bot.videos == []


async def test_the_size_error_names_both_numbers() -> None:
    message = str(TooLargeError(2_000_000_000, 1_900_000_000))
    assert "1.9 GB" in message
    assert "1.9 GB" in message


async def test_captions_are_html_escaped(tmp_path: Path) -> None:
    bot = RecordingBot()
    result = audio_result(tmp_path, tracks=1, with_zip=False)
    result.title = "<script>alert(1)</script>"
    await delivery(bot).send_audio_result(result)
    assert "<script>" not in bot.audio[0]["caption"]
    assert "&lt;script&gt;" in bot.audio[0]["caption"]


async def test_telegram_gets_the_small_thumbnail_not_the_full_cover(tmp_path: Path) -> None:
    # The cover is up to 1000x1000; Telegram rejects anything over 320x320.
    bot = RecordingBot()
    result = audio_result(tmp_path, tracks=1, with_zip=False)
    await delivery(bot).send_audio_result(result)

    assert result.thumbnail is not None
    assert bot.audio[0]["thumbnail"].path == result.thumbnail
    assert bot.audio[0]["thumbnail"].path != result.cover


async def test_the_zip_also_carries_the_small_thumbnail(tmp_path: Path) -> None:
    bot = RecordingBot()
    result = audio_result(tmp_path, tracks=3, with_zip=True)
    await delivery(bot).send_audio_result(result)
    assert bot.documents[0]["thumbnail"].path == result.thumbnail


async def test_a_missing_thumbnail_is_not_fatal(tmp_path: Path) -> None:
    bot = RecordingBot()
    result = audio_result(tmp_path, tracks=1, with_zip=False)
    result.thumbnail = None
    await delivery(bot).send_audio_result(result)
    assert bot.audio[0]["thumbnail"] is None
