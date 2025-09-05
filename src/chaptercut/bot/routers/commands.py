"""Command handlers."""

from __future__ import annotations

import time

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from chaptercut.bot import texts
from chaptercut.cache.store import CacheStore
from chaptercut.logging import get_logger
from chaptercut.pipeline import ffmpeg
from chaptercut.pipeline.ytdlp import Ytdlp
from chaptercut.queue.repository import Repository
from chaptercut.queue.worker import Worker
from chaptercut.settings import Settings
from chaptercut.util.timefmt import format_bytes, format_uptime, utcnow
from chaptercut.util.youtube import extract_video_id

log = get_logger(__name__)

router = Router(name="commands")

STARTED_AT = time.monotonic()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(texts.START)


@router.message(Command("help"))
async def cmd_help(message: Message, is_admin: bool = False) -> None:
    await message.answer(texts.HELP + (texts.HELP_ADMIN if is_admin else ""), parse_mode="HTML")


@router.message(Command("status"))
async def cmd_status(
    message: Message,
    repo: Repository,
    worker: Worker,
    cache: CacheStore,
    ytdlp: Ytdlp,
) -> None:
    current = worker.current_job
    running = None
    if current is not None:
        phase = current.phase.value if current.phase else "working"
        label = texts.PHASE_LABELS.get(phase, phase)
        running = f"{current.video_id} ({label})"

    await message.answer(
        texts.status_text(
            queue_length=await repo.queue_length(),
            running=running,
            cache_count=len(cache.entries()),
            cache_bytes=cache.usage_bytes(),
            uptime_seconds=time.monotonic() - STARTED_AT,
            ytdlp_version=await ytdlp.version(),
            ffmpeg_ok=await ffmpeg.available(),
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
async def cmd_cookies(message: Message, settings: Settings, is_admin: bool = False) -> None:
    if not is_admin:
        await message.answer(texts.ADMIN_ONLY)
        return
    path = settings.active_cookies_file()
    if path is None:
        await message.answer(texts.COOKIES_MISSING)
        return
    # Size and age only. The contents are credentials and never leave the disk.
    stat = path.stat()
    age = utcnow().timestamp() - stat.st_mtime
    await message.answer(
        texts.COOKIES_STATUS.format(size=format_bytes(stat.st_size), age=format_uptime(age))
    )


@router.message(Command("cache"))
async def cmd_cache(
    message: Message,
    command: CommandObject,
    repo: Repository,
    cache: CacheStore,
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
        await _cache_purge(message, repo, cache, args[1:])
        return

    video_id = extract_video_id(args[0]) or args[0]
    entry = cache.get(video_id)
    if entry is None:
        await message.answer(texts.CACHE_NOT_CACHED.format(video_id=video_id))
        return
    await message.answer(
        texts.cache_entry_text(
            title=entry.manifest.title,
            video_id=video_id,
            tracks=len(entry.manifest.tracks),
            size_bytes=entry.size_bytes,
            downloaded_at=entry.manifest.downloaded_at,
        ),
        parse_mode="HTML",
    )


async def _cache_purge(
    message: Message, repo: Repository, cache: CacheStore, args: list[str]
) -> None:
    if not args:
        await message.answer(texts.CACHE_USAGE_HELP, parse_mode="HTML")
        return

    if args[0] == "all":
        video_ids = [video_id for video_id, _ in cache.entries()]
        count = cache.clear()
        for video_id in video_ids:
            await repo.forget_cache_entry(video_id)
        log.info("cache.purged_all", count=count)
        await message.answer(texts.CACHE_PURGED_ALL.format(count=count))
        return

    video_id = extract_video_id(args[0]) or args[0]
    if not cache.delete(video_id):
        await message.answer(texts.CACHE_NOT_CACHED.format(video_id=video_id))
        return
    await repo.forget_cache_entry(video_id)
    log.info("cache.purged", video_id=video_id)
    await message.answer(texts.CACHE_PURGED.format(video_id=video_id))
