"""Process entry point: build everything, run, shut down cleanly."""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from types import FrameType

from aiogram import Bot, Dispatcher

from chaptercut.bot.factory import create_bot, create_dispatcher
from chaptercut.bot.fileserver import FileServerClient
from chaptercut.cache.store import CacheStore
from chaptercut.logging import configure_logging, get_logger
from chaptercut.pipeline.runner import Pipeline
from chaptercut.pipeline.ytdlp import YtdlpFactory
from chaptercut.providers.registry import ProviderRegistry
from chaptercut.queue.repository import Repository
from chaptercut.queue.worker import Worker
from chaptercut.settings import Settings, load_settings
from chaptercut.util.paths import rmtree_quiet
from chaptercut.util.timefmt import utcnow

log = get_logger(__name__)

HEARTBEAT_SECONDS = 30.0


def sweep_startup(settings: Settings, cache: CacheStore) -> None:
    """Remove everything a previous run may have left half-finished.

    Without this the predecessor accumulated hundreds of megabytes of orphaned
    scratch files and, worse, cache directories that looked valid but were not.
    """
    removed_work = 0
    if settings.work_dir.is_dir():
        for entry in settings.work_dir.iterdir():
            rmtree_quiet(entry) if entry.is_dir() else entry.unlink(missing_ok=True)
            removed_work += 1
    removed_cache = cache.sweep()
    log.info("startup.swept", work=removed_work, cache=removed_cache)


async def heartbeat(settings: Settings) -> None:
    """Touch a file the compose healthcheck watches for staleness."""
    while True:
        try:
            settings.heartbeat_path.write_text(utcnow().isoformat(), encoding="utf-8")
        except OSError as exc:  # pragma: no cover - disk full or read-only volume
            log.warning("heartbeat.failed", error=type(exc).__name__)
        await asyncio.sleep(HEARTBEAT_SECONDS)


async def amain() -> int:
    settings = load_settings()
    configure_logging(settings.log_level, settings.log_json)
    settings.ensure_dirs()

    cache = CacheStore(settings.cache_dir)
    sweep_startup(settings, cache)

    repo = await Repository.open(settings.db_path)
    requeued = await repo.requeue_interrupted()
    await repo.purge_expired_requests()

    registry = ProviderRegistry.enabled(settings.enabled_providers)
    ytdlp = YtdlpFactory(
        data_dir=settings.data_dir,
        default_cookies=settings.active_cookies_file(),
        extra_args=settings.ytdlp_extra_arg_list,
    )
    pipeline = Pipeline(settings, ytdlp, cache, registry)

    fileserver: FileServerClient | None = None
    if settings.fileserver_enabled:
        fileserver = FileServerClient(
            base_url=settings.fileserver_url,
            token=settings.fileserver_token.get_secret_value(),
            ca_file=settings.fileserver_ca,
        )

    bot = create_bot(settings)
    worker = Worker(settings, repo, pipeline, cache, bot, fileserver)
    dispatcher = create_dispatcher(
        settings,
        repo=repo,
        worker=worker,
        cache=cache,
        ytdlp=ytdlp,
        pipeline=pipeline,
        registry=registry,
        fileserver=fileserver,
    )

    log.info(
        "startup",
        api=settings.bot_api_url,
        local=settings.bot_api_local,
        requeued=len(requeued),
        providers=registry.names,
        fileserver=settings.fileserver_enabled,
        cookies=[p.name for p in registry if ytdlp.cookies_for(p.name) is not None],
    )

    worker.start()
    if requeued:
        worker.wake()

    heartbeat_task = asyncio.create_task(heartbeat(settings), name="chaptercut-heartbeat")
    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)

    polling = asyncio.create_task(
        dispatcher.start_polling(bot, handle_signals=False),  # pyright: ignore[reportUnknownMemberType]
        name="chaptercut-polling",
    )
    done, _pending = await asyncio.wait(
        [polling, asyncio.create_task(stop_event.wait())],
        return_when=asyncio.FIRST_COMPLETED,
    )

    log.info("shutdown.begin")
    await _shutdown(dispatcher, bot, worker, repo, heartbeat_task, polling, settings)

    for task in done:
        if task is polling and not task.cancelled() and task.exception() is not None:
            log.error("polling.failed", error=str(task.exception()))
            return 1
    return 0


async def _shutdown(
    dispatcher: Dispatcher,
    bot: Bot,
    worker: Worker,
    repo: Repository,
    heartbeat_task: asyncio.Task[None],
    polling: asyncio.Task[None],
    settings: Settings,
) -> None:
    # Stop taking new work first, then give the running job its grace period.
    with contextlib.suppress(Exception):
        await dispatcher.stop_polling()
    polling.cancel()
    heartbeat_task.cancel()
    await asyncio.gather(polling, heartbeat_task, return_exceptions=True)

    await worker.stop(settings.shutdown_grace_seconds)
    await repo.close()
    with contextlib.suppress(Exception):
        await bot.session.close()
    log.info("shutdown.done")


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Windows: add_signal_handler is unavailable, fall back to signal().
            def on_signal(_signum: int, _frame: FrameType | None) -> None:
                stop_event.set()

            signal.signal(sig, on_signal)


def run() -> None:
    try:
        sys.exit(asyncio.run(amain()))
    except KeyboardInterrupt:  # pragma: no cover - interactive use
        sys.exit(0)


if __name__ == "__main__":  # pragma: no cover
    run()
