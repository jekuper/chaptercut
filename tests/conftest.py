"""Shared fixtures. Every fixture is synthetic; no personal data, no network."""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

from chaptercut.cache.manifest import SCHEMA_VERSION, Manifest, ManifestTrack
from chaptercut.cache.store import CacheKey, CacheStore
from chaptercut.pipeline.ytdlp import YtdlpFactory
from chaptercut.providers.base import MediaRef
from chaptercut.providers.registry import ProviderRegistry
from chaptercut.queue.repository import Repository
from chaptercut.settings import Settings

# A syntactically valid token that is not a real one.
FAKE_TOKEN = "123456789:AAHfake-test-token-not-a-real-secre"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    (root / "cache").mkdir(parents=True)
    (root / "work").mkdir(parents=True)
    return root


@pytest.fixture
def settings(data_dir: Path) -> Settings:
    return Settings(
        bot_token=FAKE_TOKEN,  # pyright: ignore[reportArgumentType]
        allowed_user_ids=[111, 222],  # pyright: ignore[reportArgumentType]
        admin_user_ids=[111],  # pyright: ignore[reportArgumentType]
        data_dir=data_dir,
        bot_api_url="http://bot-api:8081",
    )


@pytest.fixture
def cache(data_dir: Path) -> CacheStore:
    return CacheStore(data_dir / "cache")


@pytest.fixture
def registry() -> ProviderRegistry:
    return ProviderRegistry()


@pytest.fixture
def ytdlp_factory(data_dir: Path) -> YtdlpFactory:
    return YtdlpFactory(data_dir=data_dir)


def youtube_ref(video_id: str = "dQw4w9WgXcQ") -> MediaRef:
    return MediaRef(
        provider="youtube",
        media_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
    )


def tiktok_ref(media_id: str = "7123456789012345678", resolved: bool = True) -> MediaRef:
    url = (
        f"https://www.tiktok.com/@u/video/{media_id}"
        if resolved
        else f"https://vm.tiktok.com/{media_id}"
    )
    return MediaRef(provider="tiktok", media_id=media_id, url=url, resolved=resolved)


def key_for(video_id: str = "dQw4w9WgXcQ", provider: str = "youtube") -> CacheKey:
    return CacheKey(provider=provider, media_id=video_id)


@pytest.fixture
async def repo(data_dir: Path) -> AsyncIterator[Repository]:
    repository = await Repository.open(data_dir / "test.db")
    yield repository
    await repository.close()


@pytest.fixture
def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture
def requires_ffmpeg(ffmpeg_available: bool) -> Iterator[None]:
    if not ffmpeg_available:
        pytest.skip("ffmpeg and ffprobe are not on PATH")
    yield


def make_manifest(
    video_id: str = "dQw4w9WgXcQ", tracks: int = 2, provider: str = "youtube"
) -> Manifest:
    return Manifest(
        schema=SCHEMA_VERSION,
        provider=provider,
        video_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        title="Test Album",
        uploader="Test Channel",
        upload_date="2026-01-02",
        duration_ms=120_000,
        cover="cover.jpg",
        tracks=[
            ManifestTrack(
                n=index,
                file=f"{index:02d} - Track {index}.mp3",
                title=f"Track {index}",
                start_ms=(index - 1) * 60_000,
                end_ms=index * 60_000,
            )
            for index in range(1, tracks + 1)
        ],
        downloaded_at="2026-01-02T03:04:05Z",
    )


def populate_cache_dir(directory: Path, manifest: Manifest) -> Path:
    """Write the files a manifest claims exist, so it validates."""
    directory.mkdir(parents=True, exist_ok=True)
    for track in manifest.tracks:
        (directory / track.file).write_bytes(b"\xff\xfb\x90\x00" * 16)
    if manifest.cover:
        (directory / manifest.cover).write_bytes(b"\xff\xd8\xff\xe0jpeg")
    manifest.write(directory)
    return directory


def ytdlp_info(
    video_id: str = "dQw4w9WgXcQ",
    chapters: list[dict[str, Any]] | None = None,
    duration: float = 120.0,
) -> dict[str, Any]:
    return {
        "id": video_id,
        "title": "Test Album",
        "uploader": "Test Channel",
        "duration": duration,
        "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
        "upload_date": "20260102",
        "chapters": chapters or [],
        "thumbnails": [
            {"url": "https://example.invalid/small.jpg", "width": 120, "height": 90},
            {"url": "https://example.invalid/big.jpg", "width": 1280, "height": 720},
        ],
        "formats": [],
    }
