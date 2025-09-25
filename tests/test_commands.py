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
    cmd_files,
    cmd_help,
    cmd_start,
    cmd_status,
)
from chaptercut.cache.store import CacheKey, CacheStore
from chaptercut.providers.registry import ProviderRegistry
from chaptercut.queue.models import ExtractType, Job, JobState, Phase
from chaptercut.queue.repository import Repository
from chaptercut.util.timefmt import utcnow
from tests.conftest import make_manifest, populate_cache_dir, youtube_ref

USER = 111
OTHER = 222
CHAT = 999
VIDEO_ID = "dQw4w9WgXcQ"
TIKTOK_ID = "7123456789012345678"
KEY = CacheKey("youtube", VIDEO_ID)
TIKTOK_KEY = CacheKey("tiktok", TIKTOK_ID)


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
    """Stands in for the factory and the client it hands back."""

    def __init__(self, cookies: dict[str, Path] | None = None) -> None:
        self.cookies = cookies or {}

    def for_provider(self, provider: Any) -> FakeYtdlp:
        return self

    def cookies_for(self, provider: str) -> Path | None:
        return self.cookies.get(provider)

    async def version(self) -> str:
        return "2026.08.19"


def a_job(user_id: int = USER, provider: str = "youtube") -> Job:
    return Job(
        job_id="job1",
        req_id="req1",
        user_id=user_id,
        chat_id=CHAT,
        kind=ExtractType.AUDIO,
        provider=provider,
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
    assert "Send me a link" in replies[0]


async def test_help_hides_the_admin_commands(
    replies: list[str], registry: ProviderRegistry
) -> None:
    await cmd_help(a_message(), registry, is_admin=False)
    assert "/cache" not in replies[0]


async def test_help_shows_the_admin_commands_to_an_admin(
    replies: list[str], registry: ProviderRegistry
) -> None:
    await cmd_help(a_message(), registry, is_admin=True)
    assert "/cache" in replies[0]
    assert "/cookies" in replies[0]


async def test_help_lists_the_enabled_sites(replies: list[str], registry: ProviderRegistry) -> None:
    await cmd_help(a_message(), registry)
    assert "YouTube, TikTok" in replies[0]


async def test_help_reflects_a_restricted_registry(replies: list[str]) -> None:
    await cmd_help(a_message(), ProviderRegistry.enabled(["tiktok"]))
    assert "TikTok" in replies[0]
    assert "YouTube" not in replies[0]


async def test_status_reports_the_queue_and_cache(
    repo: Repository, cache: CacheStore, replies: list[str], registry: ProviderRegistry
) -> None:
    populate_cache_dir(cache.path_for(KEY), make_manifest(VIDEO_ID))
    request = await repo.create_request(youtube_ref(VIDEO_ID), user_id=USER, chat_id=CHAT)
    await repo.enqueue(request, ExtractType.AUDIO)

    await cmd_status(a_message(), repo, FakeWorker(), cache, FakeYtdlp(), registry)  # pyright: ignore[reportArgumentType]

    text = replies[0]
    assert "Queue: 1 waiting" in text
    assert "Running: nothing" in text
    assert "Cache: 1 video(s)" in text
    assert "2026.08.19" in text
    assert "Sites: YouTube, TikTok" in text


async def test_status_names_the_running_job_with_its_provider(
    repo: Repository, cache: CacheStore, replies: list[str], registry: ProviderRegistry
) -> None:
    await cmd_status(a_message(), repo, FakeWorker(a_job()), cache, FakeYtdlp(), registry)  # pyright: ignore[reportArgumentType]
    assert f"Running: youtube:{VIDEO_ID} (Splitting)" in replies[0]


async def test_cancel_with_nothing_queued(repo: Repository, replies: list[str]) -> None:
    await cmd_cancel(a_message(), repo, FakeWorker())  # pyright: ignore[reportArgumentType]
    assert replies[0] == "You have nothing queued."


async def test_cancel_drops_the_users_queued_jobs(repo: Repository, replies: list[str]) -> None:
    for video_id in ("aaaaaaaaaaa", "bbbbbbbbbbb"):
        request = await repo.create_request(youtube_ref(video_id), user_id=USER, chat_id=CHAT)
        await repo.enqueue(request, ExtractType.AUDIO)

    await cmd_cancel(a_message(), repo, FakeWorker())  # pyright: ignore[reportArgumentType]

    assert "Cancelled 2" in replies[0]
    assert await repo.queue_length() == 0


async def test_cancel_says_the_running_job_is_untouched(
    repo: Repository, replies: list[str]
) -> None:
    request = await repo.create_request(youtube_ref(VIDEO_ID), user_id=USER, chat_id=CHAT)
    await repo.enqueue(request, ExtractType.AUDIO)

    await cmd_cancel(a_message(), repo, FakeWorker(a_job()))  # pyright: ignore[reportArgumentType]

    assert "already running was left alone" in replies[0]


async def test_cancel_does_not_mention_someone_elses_running_job(
    repo: Repository, replies: list[str]
) -> None:
    request = await repo.create_request(youtube_ref(VIDEO_ID), user_id=USER, chat_id=CHAT)
    await repo.enqueue(request, ExtractType.AUDIO)

    await cmd_cancel(a_message(), repo, FakeWorker(a_job(user_id=OTHER)))  # pyright: ignore[reportArgumentType]

    assert "already running" not in replies[0]


# --- cookies -----------------------------------------------------------------


async def test_cookies_is_admin_only(
    settings: Any, replies: list[str], registry: ProviderRegistry
) -> None:
    await cmd_cookies(a_message(), settings, FakeYtdlp(), registry, is_admin=False)  # pyright: ignore[reportArgumentType]
    assert replies == ["That command is for admins."]


async def test_cookies_reports_one_line_per_site(
    settings: Any, replies: list[str], registry: ProviderRegistry
) -> None:
    await cmd_cookies(a_message(), settings, FakeYtdlp(), registry, is_admin=True)  # pyright: ignore[reportArgumentType]
    assert replies[0] == "YouTube: none\nTikTok: none"


async def test_cookies_reports_size_and_age_but_never_contents(
    settings: Any, replies: list[str], registry: ProviderRegistry, tmp_path: Path
) -> None:
    path = tmp_path / "cookies-youtube.txt"
    secret = "# Netscape HTTP Cookie File\n.google.com\tTRUE\t/\tTRUE\t0\tSID\tSUPERSECRETVALUE\n"
    path.write_text(secret, encoding="utf-8")

    await cmd_cookies(
        a_message(),
        settings,
        FakeYtdlp(cookies={"youtube": path}),  # pyright: ignore[reportArgumentType]
        registry,
        is_admin=True,
    )

    assert "YouTube:" in replies[0]
    assert "TikTok: none" in replies[0]
    assert "SUPERSECRET" not in replies[0]
    assert "SID" not in replies[0]


# --- cache -------------------------------------------------------------------


async def test_cache_is_admin_only(
    repo: Repository, cache: CacheStore, replies: list[str], registry: ProviderRegistry
) -> None:
    await cmd_cache(a_message(), command(None), repo, cache, registry, is_admin=False)
    assert replies == ["That command is for admins."]


async def test_cache_with_no_arguments_shows_usage(
    repo: Repository, cache: CacheStore, replies: list[str], registry: ProviderRegistry
) -> None:
    await cmd_cache(a_message(), command(None), repo, cache, registry, is_admin=True)
    assert "Cache: 0 video(s)" in replies[0]
    assert "/cache purge" in replies[0]


async def test_cache_lookup_of_a_missing_video(
    repo: Repository, cache: CacheStore, replies: list[str], registry: ProviderRegistry
) -> None:
    await cmd_cache(a_message(), command(VIDEO_ID), repo, cache, registry, is_admin=True)
    assert replies[0] == f"Not cached: {VIDEO_ID}"


async def test_cache_lookup_accepts_a_youtube_url(
    repo: Repository, cache: CacheStore, replies: list[str], registry: ProviderRegistry
) -> None:
    populate_cache_dir(cache.path_for(KEY), make_manifest(VIDEO_ID))
    await cmd_cache(
        a_message(),
        command(f"https://www.youtube.com/watch?v={VIDEO_ID}"),
        repo,
        cache,
        registry,
        is_admin=True,
    )
    assert "Test Album" in replies[0]
    assert "2 track(s)" in replies[0]
    assert f"youtube:{VIDEO_ID}" in replies[0]


async def test_cache_lookup_accepts_a_tiktok_url(
    repo: Repository, cache: CacheStore, replies: list[str], registry: ProviderRegistry
) -> None:
    populate_cache_dir(
        cache.path_for(TIKTOK_KEY), make_manifest(TIKTOK_ID, tracks=1, provider="tiktok")
    )
    await cmd_cache(
        a_message(),
        command(f"https://www.tiktok.com/@u/video/{TIKTOK_ID}"),
        repo,
        cache,
        registry,
        is_admin=True,
    )
    assert f"tiktok:{TIKTOK_ID}" in replies[0]


async def test_cache_lookup_accepts_a_provider_qualified_id(
    repo: Repository, cache: CacheStore, replies: list[str], registry: ProviderRegistry
) -> None:
    populate_cache_dir(
        cache.path_for(TIKTOK_KEY), make_manifest(TIKTOK_ID, tracks=1, provider="tiktok")
    )
    await cmd_cache(
        a_message(), command(f"tiktok:{TIKTOK_ID}"), repo, cache, registry, is_admin=True
    )
    assert f"tiktok:{TIKTOK_ID}" in replies[0]


async def test_a_bare_id_cached_on_two_sites_asks_which(
    repo: Repository, cache: CacheStore, replies: list[str], registry: ProviderRegistry
) -> None:
    shared = "abc123"
    populate_cache_dir(cache.path_for(CacheKey("youtube", shared)), make_manifest(shared))
    populate_cache_dir(
        cache.path_for(CacheKey("tiktok", shared)), make_manifest(shared, provider="tiktok")
    )

    await cmd_cache(a_message(), command(shared), repo, cache, registry, is_admin=True)

    assert "several sites" in replies[0]
    assert "youtube:abc123" in replies[0]
    assert "tiktok:abc123" in replies[0]


async def test_cache_purge_one(
    repo: Repository, cache: CacheStore, replies: list[str], registry: ProviderRegistry
) -> None:
    manifest = make_manifest(VIDEO_ID)
    populate_cache_dir(cache.path_for(KEY), manifest)
    await repo.record_cache_entry(manifest, 1000)

    await cmd_cache(a_message(), command(f"purge {VIDEO_ID}"), repo, cache, registry, is_admin=True)

    assert "Purged" in replies[0]
    assert cache.get(KEY) is None
    assert await repo.cache_entry(KEY) is None


async def test_purging_one_site_leaves_the_other(
    repo: Repository, cache: CacheStore, replies: list[str], registry: ProviderRegistry
) -> None:
    shared = "abc123"
    youtube, tiktok = CacheKey("youtube", shared), CacheKey("tiktok", shared)
    populate_cache_dir(cache.path_for(youtube), make_manifest(shared))
    populate_cache_dir(cache.path_for(tiktok), make_manifest(shared, provider="tiktok"))

    await cmd_cache(
        a_message(), command(f"purge youtube:{shared}"), repo, cache, registry, is_admin=True
    )

    assert cache.get(youtube) is None
    assert cache.get(tiktok) is not None


async def test_cache_purge_a_missing_video(
    repo: Repository, cache: CacheStore, replies: list[str], registry: ProviderRegistry
) -> None:
    await cmd_cache(a_message(), command(f"purge {VIDEO_ID}"), repo, cache, registry, is_admin=True)
    assert replies[0] == f"Not cached: {VIDEO_ID}"


async def test_cache_purge_all(
    repo: Repository, cache: CacheStore, replies: list[str], registry: ProviderRegistry
) -> None:
    for provider, video_id in (("youtube", "aaaaaaaaaaa"), ("tiktok", TIKTOK_ID)):
        manifest = make_manifest(video_id, provider=provider)
        populate_cache_dir(cache.path_for(CacheKey(provider, video_id)), manifest)
        await repo.record_cache_entry(manifest, 1000)

    await cmd_cache(a_message(), command("purge all"), repo, cache, registry, is_admin=True)

    assert replies[0] == "Purged 2 cache entries."
    assert cache.entries() == []
    assert await repo.cache_count() == 0


async def test_cache_purge_without_a_target_shows_usage(
    repo: Repository, cache: CacheStore, replies: list[str], registry: ProviderRegistry
) -> None:
    await cmd_cache(a_message(), command("purge"), repo, cache, registry, is_admin=True)
    assert "/cache purge" in replies[0]


# --- /files -------------------------------------------------------------------


class FakeFileServer:
    def __init__(self, files: list[Any] | None = None, error: Exception | None = None) -> None:
        self.files = files or []
        self.error = error
        self.purged: list[str] = []
        self.flushed = 0

    def _raise(self) -> None:
        if self.error is not None:
            raise self.error

    async def stats(self) -> Any:
        self._raise()
        from chaptercut.bot.fileserver import RemoteStats

        return RemoteStats(
            files=len(self.files),
            bytes=sum(item.size for item in self.files),
            retention_hours=24,
        )

    async def list_files(self) -> list[Any]:
        self._raise()
        return self.files

    async def purge(self, token: str) -> bool:
        self._raise()
        if any(item.token == token for item in self.files):
            self.purged.append(token)
            return True
        return False

    async def purge_all(self) -> int:
        self._raise()
        self.flushed = len(self.files)
        return self.flushed


def a_remote(name: str, token: str, size: int = 1024) -> Any:
    from chaptercut.bot.fileserver import RemoteFile

    return RemoteFile(
        url=f"https://f/d/{token}/{name}",
        token=token,
        filename=name,
        size=size,
        expires_at=None,
    )


async def test_files_is_admin_only(replies: list[str]) -> None:
    await cmd_files(a_message(), command(None), FakeFileServer(), is_admin=False)  # pyright: ignore[reportArgumentType]
    assert replies == ["That command is for admins."]


async def test_files_without_a_server_says_so(replies: list[str]) -> None:
    await cmd_files(a_message(), command(None), None, is_admin=True)
    assert replies == ["The file server is not configured."]


async def test_files_lists_what_is_stored(replies: list[str]) -> None:
    server = FakeFileServer([a_remote("a.zip", "tok1", 10), a_remote("b.mp3", "tok2", 20)])
    await cmd_files(a_message(), command(None), server, is_admin=True)  # pyright: ignore[reportArgumentType]

    assert "2 file(s)" in replies[0]
    assert "a.zip" in replies[0]
    assert "tok2" in replies[0]


async def test_files_reports_an_empty_server(replies: list[str]) -> None:
    await cmd_files(a_message(), command(None), FakeFileServer(), is_admin=True)  # pyright: ignore[reportArgumentType]
    assert "Nothing on the file server." in replies[0]


async def test_files_purge_one(replies: list[str]) -> None:
    server = FakeFileServer([a_remote("a.zip", "tok1")])
    await cmd_files(a_message(), command("purge tok1"), server, is_admin=True)  # pyright: ignore[reportArgumentType]

    assert server.purged == ["tok1"]
    assert "Deleted tok1" in replies[0]


async def test_files_purge_something_absent(replies: list[str]) -> None:
    await cmd_files(a_message(), command("purge nope"), FakeFileServer(), is_admin=True)  # pyright: ignore[reportArgumentType]
    assert "No such file: nope" in replies[0]


async def test_files_flush_everything(replies: list[str]) -> None:
    server = FakeFileServer([a_remote("a.zip", "tok1"), a_remote("b.zip", "tok2")])
    await cmd_files(a_message(), command("purge all"), server, is_admin=True)  # pyright: ignore[reportArgumentType]

    assert server.flushed == 2
    assert replies[0] == "Flushed 2 file(s) from the server."


async def test_files_rejects_an_unknown_subcommand(replies: list[str]) -> None:
    await cmd_files(a_message(), command("explode"), FakeFileServer(), is_admin=True)  # pyright: ignore[reportArgumentType]
    assert "/files purge" in replies[0]


async def test_a_down_server_does_not_break_the_command(replies: list[str]) -> None:
    from chaptercut.bot.fileserver import FileServerError

    server = FakeFileServer(error=FileServerError("could not reach the file server"))
    await cmd_files(a_message(), command(None), server, is_admin=True)  # pyright: ignore[reportArgumentType]

    assert "File server error" in replies[0]
    assert "could not reach" in replies[0]


async def test_status_reports_the_file_server(
    repo: Repository, cache: CacheStore, replies: list[str], registry: ProviderRegistry
) -> None:
    server = FakeFileServer([a_remote("a.zip", "tok1", 10)])
    await cmd_status(
        a_message(),
        repo,
        FakeWorker(),
        cache,
        FakeYtdlp(),
        registry,
        server,  # pyright: ignore[reportArgumentType]
    )
    assert "File server: ok, 1 file(s)" in replies[0]


async def test_status_says_when_the_file_server_is_absent(
    repo: Repository, cache: CacheStore, replies: list[str], registry: ProviderRegistry
) -> None:
    await cmd_status(a_message(), repo, FakeWorker(), cache, FakeYtdlp(), registry, None)  # pyright: ignore[reportArgumentType]
    assert "File server: not configured" in replies[0]
