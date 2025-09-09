"""Command handlers, called directly. Admin gating is the important part."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from aiogram.filters import CommandObject
from aiogram.types import Chat, Message, User

from chaptercut.bot.routers.commands import (
    cmd_cache,
    cmd_cancel,
    cmd_cookies,
    cmd_help,
    cmd_start,
    cmd_status,
)
from chaptercut.cache.store import CacheStore
from chaptercut.queue.models import ExtractType, Job, JobState, Phase
from chaptercut.queue.repository import Repository
from chaptercut.settings import Settings
from chaptercut.util.timefmt import utcnow
from tests.conftest import make_manifest, populate_cache_dir

USER = 111
OTHER = 222
CHAT = 999
VIDEO_ID = "dQw4w9WgXcQ"


@pytest.fixture
def replies(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []

    async def answer(self: Message, text: str = "", **_: Any) -> Message:
        sent.append(text)
        return self

    monkeypatch.setattr(Message, "answer", answer)
    return sent


def a_message(user_id: int = USER) -> Message:
    return Message(
        message_id=1,
        date=datetime(2026, 1, 1),
        chat=Chat(id=CHAT, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Tester"),
        text="/cmd",
    )


class FakeWorker:
    def __init__(self, current: Job | None = None) -> None:
        self._current = current

    @property
    def current_job(self) -> Job | None:
        return self._current


class FakeYtdlp:
    async def version(self) -> str:
        return "2026.08.19"


def a_job(user_id: int = USER) -> Job:
    return Job(
        job_id="job1",
        req_id="req1",
        user_id=user_id,
        chat_id=CHAT,
        kind=ExtractType.AUDIO,
        video_id=VIDEO_ID,
        url="https://youtu.be/x",
        state=JobState.RUNNING,
        phase=Phase.SPLIT,
        created_at=utcnow(),
    )


def command(args: str | None) -> CommandObject:
    return CommandObject(prefix="/", command="cache", args=args)


async def test_start(replies: list[str]) -> None:
    await cmd_start(a_message())
    assert "Send me a YouTube link" in replies[0]


async def test_help_hides_the_admin_commands(replies: list[str]) -> None:
    await cmd_help(a_message(), is_admin=False)
    assert "/cache" not in replies[0]


async def test_help_shows_the_admin_commands_to_an_admin(replies: list[str]) -> None:
    await cmd_help(a_message(), is_admin=True)
    assert "/cache" in replies[0]
    assert "/cookies" in replies[0]


async def test_status_reports_the_queue_and_cache(
    repo: Repository, cache: CacheStore, replies: list[str]
) -> None:
    populate_cache_dir(cache.path_for(VIDEO_ID), make_manifest(VIDEO_ID))
    request = await repo.create_request(USER, CHAT, "https://youtu.be/x", VIDEO_ID)
    await repo.enqueue(request, ExtractType.AUDIO)

    await cmd_status(a_message(), repo, FakeWorker(), cache, FakeYtdlp())  # pyright: ignore[reportArgumentType]

    text = replies[0]
    assert "Queue: 1 waiting" in text
    assert "Running: nothing" in text
    assert "Cache: 1 video(s)" in text
    assert "2026.08.19" in text


async def test_status_names_the_running_job(
    repo: Repository, cache: CacheStore, replies: list[str]
) -> None:
    await cmd_status(a_message(), repo, FakeWorker(a_job()), cache, FakeYtdlp())  # pyright: ignore[reportArgumentType]
    assert f"Running: {VIDEO_ID} (Splitting)" in replies[0]


async def test_cancel_with_nothing_queued(repo: Repository, replies: list[str]) -> None:
    await cmd_cancel(a_message(), repo, FakeWorker())  # pyright: ignore[reportArgumentType]
    assert replies[0] == "You have nothing queued."


async def test_cancel_drops_the_users_queued_jobs(repo: Repository, replies: list[str]) -> None:
    for video_id in ("aaaaaaaaaaa", "bbbbbbbbbbb"):
        request = await repo.create_request(USER, CHAT, "https://youtu.be/x", video_id)
        await repo.enqueue(request, ExtractType.AUDIO)

    await cmd_cancel(a_message(), repo, FakeWorker())  # pyright: ignore[reportArgumentType]

    assert "Cancelled 2" in replies[0]
    assert await repo.queue_length() == 0


async def test_cancel_says_the_running_job_is_untouched(
    repo: Repository, replies: list[str]
) -> None:
    request = await repo.create_request(USER, CHAT, "https://youtu.be/x", VIDEO_ID)
    await repo.enqueue(request, ExtractType.AUDIO)

    await cmd_cancel(a_message(), repo, FakeWorker(a_job()))  # pyright: ignore[reportArgumentType]

    assert "already running was left alone" in replies[0]


async def test_cancel_does_not_mention_someone_elses_running_job(
    repo: Repository, replies: list[str]
) -> None:
    request = await repo.create_request(USER, CHAT, "https://youtu.be/x", VIDEO_ID)
    await repo.enqueue(request, ExtractType.AUDIO)

    await cmd_cancel(a_message(), repo, FakeWorker(a_job(user_id=OTHER)))  # pyright: ignore[reportArgumentType]

    assert "already running" not in replies[0]


async def test_cookies_is_admin_only(settings: Settings, replies: list[str]) -> None:
    await cmd_cookies(a_message(), settings, is_admin=False)
    assert replies == ["That command is for admins."]


async def test_cookies_reports_absence(settings: Settings, replies: list[str]) -> None:
    await cmd_cookies(a_message(), settings, is_admin=True)
    assert "No cookie file" in replies[0]


async def test_cookies_reports_size_and_age_only(
    settings: Settings, replies: list[str], tmp_path: Path
) -> None:
    path = tmp_path / "cookies.txt"
    secret = "# Netscape HTTP Cookie File\n.google.com\tTRUE\t/\tTRUE\t0\tSID\tSUPERSECRETVALUE\n"
    path.write_text(secret, encoding="utf-8")
    settings.cookies_file = path

    await cmd_cookies(a_message(), settings, is_admin=True)

    assert "Cookie file:" in replies[0]
    assert "SUPERSECRET" not in replies[0]
    assert "SID" not in replies[0]


async def test_cache_is_admin_only(repo: Repository, cache: CacheStore, replies: list[str]) -> None:
    await cmd_cache(a_message(), command(None), repo, cache, is_admin=False)
    assert replies == ["That command is for admins."]


async def test_cache_with_no_arguments_shows_usage(
    repo: Repository, cache: CacheStore, replies: list[str]
) -> None:
    await cmd_cache(a_message(), command(None), repo, cache, is_admin=True)
    assert "Cache: 0 video(s)" in replies[0]
    assert "/cache purge" in replies[0]


async def test_cache_lookup_of_a_missing_video(
    repo: Repository, cache: CacheStore, replies: list[str]
) -> None:
    await cmd_cache(a_message(), command(VIDEO_ID), repo, cache, is_admin=True)
    assert replies[0] == f"Not cached: {VIDEO_ID}"


async def test_cache_lookup_accepts_a_url(
    repo: Repository, cache: CacheStore, replies: list[str]
) -> None:
    populate_cache_dir(cache.path_for(VIDEO_ID), make_manifest(VIDEO_ID))
    await cmd_cache(
        a_message(),
        command(f"https://www.youtube.com/watch?v={VIDEO_ID}"),
        repo,
        cache,
        is_admin=True,
    )
    assert "Test Album" in replies[0]
    assert "2 track(s)" in replies[0]


async def test_cache_purge_one(repo: Repository, cache: CacheStore, replies: list[str]) -> None:
    manifest = make_manifest(VIDEO_ID)
    populate_cache_dir(cache.path_for(VIDEO_ID), manifest)
    await repo.record_cache_entry(manifest, 1000)

    await cmd_cache(a_message(), command(f"purge {VIDEO_ID}"), repo, cache, is_admin=True)

    assert replies[0] == f"Purged {VIDEO_ID}."
    assert cache.get(VIDEO_ID) is None
    assert await repo.cache_entry(VIDEO_ID) is None


async def test_cache_purge_a_missing_video(
    repo: Repository, cache: CacheStore, replies: list[str]
) -> None:
    await cmd_cache(a_message(), command(f"purge {VIDEO_ID}"), repo, cache, is_admin=True)
    assert replies[0] == f"Not cached: {VIDEO_ID}"


async def test_cache_purge_all(repo: Repository, cache: CacheStore, replies: list[str]) -> None:
    for video_id in ("aaaaaaaaaaa", "bbbbbbbbbbb"):
        manifest = make_manifest(video_id)
        populate_cache_dir(cache.path_for(video_id), manifest)
        await repo.record_cache_entry(manifest, 1000)

    await cmd_cache(a_message(), command("purge all"), repo, cache, is_admin=True)

    assert replies[0] == "Purged 2 cache entries."
    assert cache.entries() == []
    assert await repo.cache_count() == 0


async def test_cache_purge_without_a_target_shows_usage(
    repo: Repository, cache: CacheStore, replies: list[str]
) -> None:
    await cmd_cache(a_message(), command("purge"), repo, cache, is_admin=True)
    assert "/cache purge" in replies[0]
