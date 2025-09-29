"""Worker behaviour with a stub pipeline and a stub bot.

The point of these is the job lifecycle: cleanup on every path, failure
messages, and the restart re-queue, not the media processing itself.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from chaptercut.bot.deliver import TooLargeError
from chaptercut.cache.store import CacheKey, CacheStore
from chaptercut.pipeline.process import ProcessTimeout
from chaptercut.pipeline.runner import AudioResult
from chaptercut.pipeline.sink import ProgressSink
from chaptercut.pipeline.ytdlp import YtdlpError
from chaptercut.queue.models import Destination, ExtractType, Job, JobState
from chaptercut.queue.repository import Repository
from chaptercut.queue.worker import Worker, _reason_for
from chaptercut.settings import Settings
from chaptercut.util.timefmt import utcnow
from tests.conftest import make_manifest, youtube_ref


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.edits: list[str] = []
        self.deleted = 0

    async def send_message(self, chat_id: int, text: str, **_: Any) -> None:
        self.messages.append(text)

    async def edit_message_text(self, text: str, **_: Any) -> None:
        self.edits.append(text)

    async def delete_message(self, **_: Any) -> None:
        self.deleted += 1


class FakePipeline:
    """Stands in for Pipeline: records the work dir, then succeeds or raises."""

    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.seen_work_dirs: list[Path] = []
        self.delivered = False

    async def run(self, job: Job, work_root: Path, sink: ProgressSink) -> Any:
        work_root.mkdir(parents=True, exist_ok=True)
        (work_root / "scratch.bin").write_bytes(b"x" * 1024)
        self.seen_work_dirs.append(work_root)
        if self.error is not None:
            raise self.error
        return self.result

    def evict_to_fit(self, order: list[CacheKey]) -> list[CacheKey]:
        return []


class FakeDelivery:
    instances: list[FakeDelivery] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.audio_sent = 0
        self.video_sent = 0
        FakeDelivery.instances.append(self)

    async def send_audio_result(self, result: Any) -> list[Any]:
        self.audio_sent += 1
        return []

    async def send_video_result(self, result: Any) -> Any:
        self.video_sent += 1
        return None


@pytest.fixture(autouse=True)
def stub_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeDelivery.instances.clear()
    monkeypatch.setattr("chaptercut.queue.worker.Delivery", FakeDelivery)


def audio_result(
    cache: CacheStore,
    video_id: str = "aaaaaaaaaaa",
    cached: bool = False,
    provider: str = "youtube",
) -> Any:
    manifest = make_manifest(video_id, tracks=2, provider=provider)
    key = CacheKey(provider, video_id)
    directory = cache.path_for(key)
    directory.mkdir(parents=True, exist_ok=True)
    tracks = []
    for track in manifest.tracks:
        path = directory / track.file
        path.write_bytes(b"audio" * 64)
        tracks.append(path)
    return AudioResult(
        video_id=video_id,
        title=manifest.title,
        uploader=manifest.uploader,
        duration=120.0,
        manifest=manifest,
        directory=directory,
        tracks=tracks,
        cover=None,
        thumbnail=None,
        zip_path=None,
        from_cache=cached,
        key=key,
    )


async def enqueue(repo: Repository, kind: ExtractType = ExtractType.AUDIO) -> Job:
    request = await repo.create_request(youtube_ref("aaaaaaaaaaa"), user_id=111, chat_id=999)
    return await repo.enqueue(request, kind, status_msg_id=42)


def build_worker(
    settings: Settings, repo: Repository, cache: CacheStore, pipeline: Any
) -> tuple[Worker, FakeBot]:
    bot = FakeBot()
    return Worker(settings, repo, pipeline, cache, bot), bot  # pyright: ignore[reportArgumentType]


async def test_a_successful_job_is_marked_done(
    settings: Settings, repo: Repository, cache: CacheStore
) -> None:
    job = await enqueue(repo)
    pipeline = FakePipeline(result=audio_result(cache))
    worker, _bot = build_worker(settings, repo, cache, pipeline)

    claimed = await repo.claim_next()
    assert claimed is not None
    await worker._run_job(claimed)

    reloaded = await repo.get_job(job.job_id)
    assert reloaded is not None
    assert reloaded.state is JobState.DONE
    assert FakeDelivery.instances[0].audio_sent == 1


async def test_the_work_directory_is_removed_on_success(
    settings: Settings, repo: Repository, cache: CacheStore
) -> None:
    await enqueue(repo)
    pipeline = FakePipeline(result=audio_result(cache))
    worker, _bot = build_worker(settings, repo, cache, pipeline)

    claimed = await repo.claim_next()
    assert claimed is not None
    await worker._run_job(claimed)

    assert pipeline.seen_work_dirs and not pipeline.seen_work_dirs[0].exists()


async def test_the_work_directory_is_removed_on_failure(
    settings: Settings, repo: Repository, cache: CacheStore
) -> None:
    # Cleanup has to run on the failure path too, or every ordinary failure
    # leaks the whole working directory.
    await enqueue(repo)
    pipeline = FakePipeline(error=YtdlpError("video unavailable"))
    worker, _bot = build_worker(settings, repo, cache, pipeline)

    claimed = await repo.claim_next()
    assert claimed is not None
    await worker._run_job(claimed)

    assert pipeline.seen_work_dirs and not pipeline.seen_work_dirs[0].exists()


async def test_a_failure_is_recorded_and_shown(
    settings: Settings, repo: Repository, cache: CacheStore
) -> None:
    job = await enqueue(repo)
    worker, bot = build_worker(
        settings, repo, cache, FakePipeline(error=YtdlpError("video unavailable"))
    )

    claimed = await repo.claim_next()
    assert claimed is not None
    await worker._run_job(claimed)

    reloaded = await repo.get_job(job.job_id)
    assert reloaded is not None
    assert reloaded.state is JobState.FAILED
    assert reloaded.error == "video unavailable"
    assert any("video unavailable" in edit for edit in bot.edits)


async def test_one_failing_job_does_not_stop_the_next(
    settings: Settings, repo: Repository, cache: CacheStore
) -> None:
    await enqueue(repo)
    await enqueue(repo)
    worker, _bot = build_worker(settings, repo, cache, FakePipeline(error=RuntimeError("boom")))

    first = await repo.claim_next()
    assert first is not None
    await worker._run_job(first)
    second = await repo.claim_next()
    assert second is not None
    await worker._run_job(second)

    assert await repo.queue_length() == 0


async def test_cancellation_marks_the_job_interrupted(
    settings: Settings, repo: Repository, cache: CacheStore
) -> None:
    job = await enqueue(repo)
    worker, _bot = build_worker(settings, repo, cache, FakePipeline(error=asyncio.CancelledError()))

    claimed = await repo.claim_next()
    assert claimed is not None
    with pytest.raises(asyncio.CancelledError):
        await worker._run_job(claimed)

    reloaded = await repo.get_job(job.job_id)
    assert reloaded is not None
    assert reloaded.state is JobState.INTERRUPTED
    # And a restart puts it back in the queue.
    assert [j.job_id for j in await repo.requeue_interrupted()] == [job.job_id]


async def test_a_cache_hit_is_announced_and_recorded(
    settings: Settings, repo: Repository, cache: CacheStore
) -> None:
    await enqueue(repo)
    result = audio_result(cache, cached=True)
    await repo.record_cache_entry(result.manifest, size_bytes=100)
    worker, bot = build_worker(settings, repo, cache, FakePipeline(result=result))

    claimed = await repo.claim_next()
    assert claimed is not None
    await worker._run_job(claimed)

    assert bot.messages == ["Served from cache"]


async def test_a_fresh_result_is_recorded_in_the_cache_table(
    settings: Settings, repo: Repository, cache: CacheStore
) -> None:
    await enqueue(repo)
    worker, bot = build_worker(settings, repo, cache, FakePipeline(result=audio_result(cache)))

    claimed = await repo.claim_next()
    assert claimed is not None
    await worker._run_job(claimed)

    entry = await repo.cache_entry(CacheKey("youtube", "aaaaaaaaaaa"))
    assert entry is not None
    assert entry["tracks"] == 2
    assert bot.messages == []


async def test_the_status_message_is_deleted_on_success(
    settings: Settings, repo: Repository, cache: CacheStore
) -> None:
    await enqueue(repo)
    worker, bot = build_worker(settings, repo, cache, FakePipeline(result=audio_result(cache)))

    claimed = await repo.claim_next()
    assert claimed is not None
    await worker._run_job(claimed)

    assert bot.deleted == 1


async def test_a_wake_during_the_claim_is_not_lost(
    settings: Settings, repo: Repository, cache: CacheStore
) -> None:
    # The event is cleared before claiming, so a wake arriving between an
    # empty claim and the wait still returns immediately.
    worker, _bot = build_worker(settings, repo, cache, FakePipeline())
    worker.wake()
    await asyncio.wait_for(worker._idle(), timeout=1.0)


async def test_the_loop_picks_up_a_job_after_a_wake(
    settings: Settings, repo: Repository, cache: CacheStore
) -> None:
    pipeline = FakePipeline(result=audio_result(cache))
    worker, _bot = build_worker(settings, repo, cache, pipeline)
    worker.start()
    try:
        job = await enqueue(repo)
        worker.wake()
        for _ in range(200):
            await asyncio.sleep(0.01)
            reloaded = await repo.get_job(job.job_id)
            if reloaded is not None and reloaded.state is JobState.DONE:
                break
        else:
            pytest.fail("the worker never picked the job up")
    finally:
        await worker.stop(grace_seconds=2.0)


async def test_stop_ends_the_loop(settings: Settings, repo: Repository, cache: CacheStore) -> None:
    worker, _bot = build_worker(settings, repo, cache, FakePipeline())
    worker.start()
    await worker.stop(grace_seconds=2.0)
    assert worker._task is None


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (YtdlpError("no such video"), "no such video"),
        (YtdlpError("blocked", bot_check=True), "signed-in session"),
        (ProcessTimeout(["yt-dlp"], 1800), "took too long"),
        (TooLargeError(2_000_000_000, 1_900_000_000), "over the"),
        (ValueError("cannot build a track list"), "cannot build a track list"),
        (RuntimeError("internal detail"), "something went wrong"),
    ],
)
def test_failure_reasons_are_short_and_actionable(error: Exception, expected: str) -> None:
    assert expected in _reason_for(error)


def test_failure_reasons_never_leak_stderr() -> None:
    error = YtdlpError("download failed", stderr="Traceback: /home/user/secret/path")
    assert "secret" not in _reason_for(error)


# --- delivery destinations ----------------------------------------------------


class FakeFileServer:
    def __init__(self, fail: Exception | None = None) -> None:
        self.uploaded: list[Path] = []
        self.fail = fail

    async def upload(self, path: Path, filename: str | None = None, on_progress: Any = None):
        if self.fail is not None:
            raise self.fail
        self.uploaded.append(path)
        from chaptercut.bot.fileserver import RemoteFile

        return RemoteFile(
            url=f"https://files.invalid/d/tok/{path.name}",
            token="tok",
            filename=path.name,
            size=path.stat().st_size if path.is_file() else 0,
            expires_at=utcnow() + timedelta(hours=24),
        )


def build_worker_with_server(
    settings: Settings, repo: Repository, cache: CacheStore, pipeline: Any, server: Any
) -> tuple[Worker, FakeBot]:
    bot = FakeBot()
    return Worker(settings, repo, pipeline, cache, bot, server), bot  # pyright: ignore[reportArgumentType]


async def enqueue_to(
    repo: Repository, destination: Destination, kind: ExtractType = ExtractType.AUDIO
) -> Job:
    request = await repo.create_request(youtube_ref("aaaaaaaaaaa"), user_id=111, chat_id=999)
    await repo.set_request_destination(request.req_id, destination)
    request.destination = destination
    return await repo.enqueue(request, kind, status_msg_id=42)


async def test_choosing_the_server_uploads_and_sends_a_link(
    settings: Settings, repo: Repository, cache: CacheStore
) -> None:
    await enqueue_to(repo, Destination.SERVER)
    result = audio_result(cache)
    server = FakeFileServer()
    worker, bot = build_worker_with_server(
        settings, repo, cache, FakePipeline(result=result), server
    )

    claimed = await repo.claim_next()
    assert claimed is not None
    await worker._run_job(claimed)

    assert len(server.uploaded) == 1
    assert FakeDelivery.instances[0].audio_sent == 0
    assert any("https://files.invalid/d/tok/" in message for message in bot.messages)


async def test_choosing_telegram_does_not_touch_the_server(
    settings: Settings, repo: Repository, cache: CacheStore
) -> None:
    await enqueue_to(repo, Destination.TELEGRAM)
    server = FakeFileServer()
    worker, _bot = build_worker_with_server(
        settings, repo, cache, FakePipeline(result=audio_result(cache)), server
    )

    claimed = await repo.claim_next()
    assert claimed is not None
    await worker._run_job(claimed)

    assert server.uploaded == []
    assert FakeDelivery.instances[0].audio_sent == 1


async def test_too_big_for_telegram_falls_back_to_the_server(
    settings: Settings, repo: Repository, cache: CacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The reason the server exists: over the limit is a reroute, not a failure.
    await enqueue_to(repo, Destination.TELEGRAM)
    server = FakeFileServer()
    worker, bot = build_worker_with_server(
        settings, repo, cache, FakePipeline(result=audio_result(cache)), server
    )

    async def refuse(self: Any, result: Any) -> None:
        raise TooLargeError(2_000_000_000, 1_900_000_000)

    monkeypatch.setattr(FakeDelivery, "send_audio_result", refuse)

    claimed = await repo.claim_next()
    assert claimed is not None
    await worker._run_job(claimed)

    job = await repo.get_job(claimed.job_id)
    assert job is not None and job.state is JobState.DONE
    assert len(server.uploaded) == 1
    assert any("Too big for Telegram" in message for message in bot.messages)


async def test_without_a_server_too_big_is_still_a_failure(
    settings: Settings, repo: Repository, cache: CacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    await enqueue_to(repo, Destination.TELEGRAM)
    worker, _bot = build_worker_with_server(
        settings, repo, cache, FakePipeline(result=audio_result(cache)), None
    )

    async def refuse(self: Any, result: Any) -> None:
        raise TooLargeError(2_000_000_000, 1_900_000_000)

    monkeypatch.setattr(FakeDelivery, "send_audio_result", refuse)

    claimed = await repo.claim_next()
    assert claimed is not None
    await worker._run_job(claimed)

    job = await repo.get_job(claimed.job_id)
    assert job is not None
    assert job.state is JobState.FAILED
    assert "over the" in (job.error or "")


async def test_an_upload_failure_fails_the_job_with_a_clear_reason(
    settings: Settings, repo: Repository, cache: CacheStore
) -> None:
    from chaptercut.bot.fileserver import FileServerError

    await enqueue_to(repo, Destination.SERVER)
    server = FakeFileServer(fail=FileServerError("could not reach the file server"))
    worker, _bot = build_worker_with_server(
        settings, repo, cache, FakePipeline(result=audio_result(cache)), server
    )

    claimed = await repo.claim_next()
    assert claimed is not None
    await worker._run_job(claimed)

    job = await repo.get_job(claimed.job_id)
    assert job is not None
    assert job.state is JobState.FAILED
    assert job.error == "could not reach the file server"


async def test_the_server_gets_the_zip_for_a_multi_track_result(
    settings: Settings, repo: Repository, cache: CacheStore, tmp_path: Path
) -> None:
    # One link, not one per track.
    await enqueue_to(repo, Destination.SERVER)
    result = audio_result(cache)
    zip_path = tmp_path / "Test Album.zip"
    zip_path.write_bytes(b"zip")
    result.zip_path = zip_path

    server = FakeFileServer()
    worker, _bot = build_worker_with_server(
        settings, repo, cache, FakePipeline(result=result), server
    )

    claimed = await repo.claim_next()
    assert claimed is not None
    await worker._run_job(claimed)

    assert server.uploaded == [zip_path]
