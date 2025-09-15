"""The background worker: claim a job, run the pipeline, deliver, clean up.

Jobs are claimed from SQLite, so a restart mid-job loses nothing: the startup
re-queue puts it back. The scratch directory is removed in a `finally`, on
every path, which is what the predecessor's success-only cleanup did not do.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from chaptercut.bot import texts
from chaptercut.bot.deliver import Delivery, TooLargeError
from chaptercut.bot.progress import StatusMessage
from chaptercut.cache.store import CacheStore
from chaptercut.logging import get_logger
from chaptercut.pipeline.ffmpeg import FfmpegError
from chaptercut.pipeline.process import ProcessTimeout
from chaptercut.pipeline.runner import AudioResult, Pipeline, PipelineError, VideoResult
from chaptercut.pipeline.sink import NullSink, ProgressSink
from chaptercut.pipeline.ytdlp import YtdlpError
from chaptercut.queue.models import Job, JobState, Phase
from chaptercut.settings import Settings
from chaptercut.util.paths import rmtree_quiet

if TYPE_CHECKING:
    from chaptercut.queue.repository import Repository

log = get_logger(__name__)

IDLE_POLL_SECONDS = 30.0


class Worker:
    def __init__(
        self,
        settings: Settings,
        repo: Repository,
        pipeline: Pipeline,
        cache: CacheStore,
        bot: Bot,
    ) -> None:
        self.settings = settings
        self.repo = repo
        self.pipeline = pipeline
        self.cache = cache
        self.bot = bot
        self._wakeup = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._current: Job | None = None
        self._stopping = False

    @property
    def current_job(self) -> Job | None:
        return self._current

    def wake(self) -> None:
        """Called on enqueue so the worker starts immediately instead of polling."""
        self._wakeup.set()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="chaptercut-worker")

    async def stop(self, grace_seconds: float) -> None:
        """Let the running job finish, then stop. Past the grace period the job
        is marked interrupted so the next start re-queues it."""
        self._stopping = True
        self._wakeup.set()
        task = self._task
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=grace_seconds)
        except TimeoutError:
            log.warning("worker.grace_expired")
            if self._current is not None:
                await self.repo.mark_interrupted(self._current.job_id)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._task = None

    async def _loop(self) -> None:
        log.info("worker.started")
        while not self._stopping:
            # Cleared before the claim, not after: a wake that lands while we
            # are claiming must survive into the wait below, or the job sits
            # in the queue until the fallback poll fires.
            self._wakeup.clear()
            job = await self.repo.claim_next()
            if job is None:
                await self._idle()
                continue
            await self._run_job(job)
        log.info("worker.stopped")

    async def _idle(self) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wakeup.wait(), timeout=IDLE_POLL_SECONDS)

    async def _run_job(self, job: Job) -> None:
        self._current = job
        work_dir = self.settings.work_dir / job.job_id
        status = self._status_for(job)
        try:
            sink: ProgressSink = status if status is not None else NullSink()
            result = await self.pipeline.run(job, work_dir, sink)
            await self._deliver(job, result, status)
            await self.repo.finish(job.job_id, JobState.DONE)
        except asyncio.CancelledError:
            await self.repo.mark_interrupted(job.job_id)
            raise
        except Exception as exc:  # noqa: BLE001 - one job must never kill the loop
            await self._fail(job, exc, status)
        finally:
            rmtree_quiet(work_dir)
            self._current = None

    def _status_for(self, job: Job) -> StatusMessage | None:
        if job.status_msg_id is None:
            return None
        return StatusMessage(
            bot=self.bot,
            chat_id=job.chat_id,
            message_id=job.status_msg_id,
            title="",
        )

    async def _deliver(
        self,
        job: Job,
        result: AudioResult | VideoResult,
        status: StatusMessage | None,
    ) -> None:
        if status is not None:
            status.title = result.title
            await status.update(Phase.UPLOAD, force=True)
        await self.repo.set_phase(job.job_id, Phase.UPLOAD)

        delivery = Delivery(
            bot=self.bot,
            chat_id=job.chat_id,
            max_send_bytes=self.settings.max_send_bytes,
            multi_mode=self.settings.audio_multi_delivery,
        )
        if isinstance(result, AudioResult):
            if result.from_cache:
                await self.bot.send_message(job.chat_id, texts.CACHE_HIT)
            await delivery.send_audio_result(result)
            await self._record_cache(result)
        else:
            await delivery.send_video_result(result)

        if status is not None:
            await status.delete()

    async def _record_cache(self, result: AudioResult) -> None:
        size = sum(path.stat().st_size for path in result.tracks if path.is_file())
        if result.from_cache:
            await self.repo.touch_cache_entry(result.key)
        else:
            await self.repo.record_cache_entry(result.manifest, size)
        await self._evict_if_needed()

    async def _evict_if_needed(self) -> None:
        if await self.repo.cache_total_bytes() <= self.settings.cache_max_bytes:
            return
        for key in self.pipeline.evict_to_fit(await self.repo.cache_keys_by_age()):
            await self.repo.forget_cache_entry(key)

    async def _fail(self, job: Job, exc: Exception, status: StatusMessage | None) -> None:
        reason = _reason_for(exc)
        log.warning(
            "job.failed",
            job_id=job.job_id,
            provider=job.provider,
            video_id=job.video_id,
            error=type(exc).__name__,
            reason=reason,
        )
        await self.repo.finish(job.job_id, JobState.FAILED, error=reason)
        if status is None:
            return
        try:
            await status.finish(texts.FAILED.format(reason=texts.esc(reason)))
        except TelegramAPIError:  # pragma: no cover - the chat may be gone
            pass


def _reason_for(exc: Exception) -> str:
    """A short line the user can act on, never a stack trace or a raw stderr dump."""
    if isinstance(exc, TooLargeError):
        return str(exc)
    if isinstance(exc, YtdlpError):
        return texts.FAILED_BOT_CHECK if exc.bot_check else str(exc)
    if isinstance(exc, ProcessTimeout):
        return texts.FAILED_TIMEOUT
    if isinstance(exc, PipelineError | FfmpegError | ValueError):
        return str(exc)
    return "something went wrong on my side"
