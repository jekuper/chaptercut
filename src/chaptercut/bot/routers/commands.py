"""Command handlers."""

from __future__ import annotations

import time

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from chaptercut.bot import texts
from chaptercut.cache.store import CachedResult, CacheKey, CacheStore
from chaptercut.logging import get_logger
from chaptercut.pipeline import ffmpeg
from chaptercut.pipeline.ytdlp import YtdlpFactory
from chaptercut.providers.registry import ProviderRegistry
from chaptercut.queue.repository import Repository
from chaptercut.queue.worker import Worker
from chaptercut.settings import Settings
from chaptercut.util.timefmt import format_bytes, format_uptime, utcnow

log = get_logger(__name__)

router = Router(name="commands")

STARTED_AT = time.monotonic()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(texts.START)


@router.message(Command("help"))
async def cmd_help(message: Message, registry: ProviderRegistry, is_admin: bool = False) -> None:
    await message.answer(texts.help_text(registry.labels, is_admin), parse_mode="HTML")


@router.message(Command("status"))
async def cmd_status(
    message: Message,
    repo: Repository,
    worker: Worker,
    cache: CacheStore,
    ytdlp: YtdlpFactory,
    registry: ProviderRegistry,
) -> None:
    current = worker.current_job
    running = None
    if current is not None:
        phase = current.phase.value if current.phase else "working"
        label = texts.PHASE_LABELS.get(phase, phase)
        running = f"{current.provider}:{current.video_id} ({label})"

    await message.answer(
        texts.status_text(
            queue_length=await repo.queue_length(),
            running=running,
            cache_count=len(cache.entries()),
            cache_bytes=cache.usage_bytes(),
            uptime_seconds=time.monotonic() - STARTED_AT,
            ytdlp_version=await ytdlp.for_provider(registry.providers[0]).version(),
            ffmpeg_ok=await ffmpeg.available(),
            providers=registry.labels,
        )
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, repo: Repository, worker: Worker) -> None:
    user = message.from_user
    if user is None:  # pragma: no cover - channel posts have no user
        return
    cancelled = await repo.cancel_queued_for_user(user.id)
    if not cancelled:
        await message.answer(texts.CANCELLED_NONE)
        return
    note = ""
    current = worker.current_job
    if current is not None and current.user_id == user.id:
        note = texts.CANCELLED_RUNNING_NOTE
    await message.answer(texts.CANCELLED.format(count=len(cancelled)) + note)


@router.message(Command("cookies"))
async def cmd_cookies(
    message: Message,
    settings: Settings,
    ytdlp: YtdlpFactory,
    registry: ProviderRegistry,
    is_admin: bool = False,
) -> None:
    if not is_admin:
        await message.answer(texts.ADMIN_ONLY)
        return

    lines: list[str] = []
    for provider in registry:
        path = ytdlp.cookies_for(provider.name)
        if path is None:
            lines.append(f"{provider.label}: none")
            continue
        # Size and age only. The contents are credentials and never leave disk.
        stat = path.stat()
        age = utcnow().timestamp() - stat.st_mtime
        lines.append(f"{provider.label}: {format_bytes(stat.st_size)}, {format_uptime(age)} old")
    await message.answer("\n".join(lines) if lines else texts.COOKIES_MISSING)


@router.message(Command("cache"))
async def cmd_cache(
    message: Message,
    command: CommandObject,
    repo: Repository,
    cache: CacheStore,
    registry: ProviderRegistry,
    is_admin: bool = False,
) -> None:
    if not is_admin:
        await message.answer(texts.ADMIN_ONLY)
        return

    args = (command.args or "").split()
    if not args:
        usage = texts.CACHE_USAGE_LINE.format(
            count=len(cache.entries()), size=format_bytes(cache.usage_bytes())
        )
        await message.answer(f"{usage}\n{texts.CACHE_USAGE_HELP}", parse_mode="HTML")
        return

    if args[0] == "purge":
        await _cache_purge(message, repo, cache, registry, args[1:])
        return

    await _cache_show(message, cache, registry, args[0])


def _resolve(cache: CacheStore, registry: ProviderRegistry, token: str) -> list[CachedResult]:
    """Find cache entries for a URL, a `provider:id` pair, or a bare id."""
    ref = registry.match(token)
    if ref is not None:
        entry = cache.get(CacheKey(provider=ref.provider, media_id=ref.media_id))
        return [entry] if entry is not None else []

    provider_name, separator, media_id = token.partition(":")
    if separator and registry.find(provider_name) is not None:
        entry = cache.get(CacheKey(provider=provider_name, media_id=media_id))
        return [entry] if entry is not None else []

    return cache.find_by_media_id(token)


async def _cache_show(
    message: Message, cache: CacheStore, registry: ProviderRegistry, token: str
) -> None:
    found = _resolve(cache, registry, token)
    if not found:
        await message.answer(texts.CACHE_NOT_CACHED.format(video_id=texts.esc(token)))
        return
    if len(found) > 1:
        keys = ", ".join(str(entry.key) for entry in found)
        await message.answer(texts.CACHE_AMBIGUOUS.format(keys=texts.esc(keys)))
        return

    entry = found[0]
    await message.answer(
        texts.cache_entry_text(
            title=entry.manifest.title,
            provider=entry.manifest.provider,
            video_id=entry.manifest.video_id,
            tracks=len(entry.manifest.tracks),
            size_bytes=entry.size_bytes,
            downloaded_at=entry.manifest.downloaded_at,
        ),
        parse_mode="HTML",
    )


async def _cache_purge(
    message: Message,
    repo: Repository,
    cache: CacheStore,
    registry: ProviderRegistry,
    args: list[str],
) -> None:
    if not args:
        await message.answer(texts.CACHE_USAGE_HELP, parse_mode="HTML")
        return

    if args[0] == "all":
        keys = [entry.key for entry in cache.entries()]
        count = cache.clear()
        for key in keys:
            await repo.forget_cache_entry(key)
        log.info("cache.purged_all", count=count)
        await message.answer(texts.CACHE_PURGED_ALL.format(count=count))
        return

    found = _resolve(cache, registry, args[0])
    if not found:
        await message.answer(texts.CACHE_NOT_CACHED.format(video_id=texts.esc(args[0])))
        return
    if len(found) > 1:
        keys = ", ".join(str(entry.key) for entry in found)
        await message.answer(texts.CACHE_AMBIGUOUS.format(keys=texts.esc(keys)))
        return

    key = found[0].key
    cache.delete(key)
    await repo.forget_cache_entry(key)
    log.info("cache.purged", key=str(key))
    await message.answer(texts.CACHE_PURGED.format(video_id=texts.esc(str(key))))
