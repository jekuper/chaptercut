from __future__ import annotations

from pathlib import Path

from chaptercut.cache.store import CacheKey
from chaptercut.providers.base import MediaRef
from chaptercut.queue.models import ExtractType, JobState, Phase
from chaptercut.queue.repository import Repository
from chaptercut.util.timefmt import iso, utcnow
from tests.conftest import make_manifest

USER = 111
CHAT = 999


async def a_request(
    repo: Repository,
    user_id: int = USER,
    video_id: str = "aaaaaaaaaaa",
    provider: str = "youtube",
):
    ref = MediaRef(
        provider=provider,
        media_id=video_id,
        url=f"https://example.invalid/{provider}/{video_id}",
    )
    return await repo.create_request(ref, user_id=user_id, chat_id=CHAT)


async def test_request_round_trip(repo: Repository) -> None:
    request = await a_request(repo)
    loaded = await repo.get_request(request.req_id)
    assert loaded is not None
    assert loaded.video_id == "aaaaaaaaaaa"
    assert loaded.extract_type is None


async def test_unknown_request_is_none(repo: Repository) -> None:
    assert await repo.get_request("nope") is None


async def test_expired_request_is_treated_as_missing(repo: Repository) -> None:
    request = await a_request(repo)
    await repo.conn.execute(
        "UPDATE requests SET expires_at = ? WHERE req_id = ?",
        (iso(utcnow().replace(year=2000)), request.req_id),
    )
    assert await repo.get_request(request.req_id) is None


async def test_purge_expired_requests(repo: Repository) -> None:
    keep = await a_request(repo, video_id="aaaaaaaaaaa")
    drop = await a_request(repo, video_id="bbbbbbbbbbb")
    await repo.conn.execute(
        "UPDATE requests SET expires_at = ? WHERE req_id = ?",
        (iso(utcnow().replace(year=2000)), drop.req_id),
    )
    assert await repo.purge_expired_requests() == 1
    assert await repo.get_request(keep.req_id) is not None


async def test_set_type_and_formats(repo: Repository) -> None:
    request = await a_request(repo)
    await repo.set_request_type(request.req_id, ExtractType.VIDEO)
    await repo.set_request_formats(
        request.req_id,
        '[{"format_id": "137", "height": 1080, "ext": "mp4",'
        ' "size_bytes": 5, "needs_audio": true}]',
    )
    loaded = await repo.get_request(request.req_id)
    assert loaded is not None
    assert loaded.extract_type is ExtractType.VIDEO
    assert [option.height for option in loaded.formats] == [1080]


async def test_malformed_formats_json_degrades_to_empty(repo: Repository) -> None:
    request = await a_request(repo)
    await repo.set_request_formats(request.req_id, "{not json")
    loaded = await repo.get_request(request.req_id)
    assert loaded is not None
    assert loaded.formats == []


async def test_enqueue_and_claim_is_fifo(repo: Repository) -> None:
    first = await repo.enqueue(await a_request(repo, video_id="aaaaaaaaaaa"), ExtractType.AUDIO)
    second = await repo.enqueue(await a_request(repo, video_id="bbbbbbbbbbb"), ExtractType.AUDIO)

    claimed = await repo.claim_next()
    assert claimed is not None
    assert claimed.job_id == first.job_id
    assert claimed.state is JobState.RUNNING
    assert claimed.started_at is not None

    assert (await repo.claim_next()).job_id == second.job_id  # pyright: ignore[reportOptionalMemberAccess]
    assert await repo.claim_next() is None


async def test_claimed_job_is_no_longer_queued(repo: Repository) -> None:
    await repo.enqueue(await a_request(repo), ExtractType.AUDIO)
    assert await repo.queue_length() == 1
    await repo.claim_next()
    assert await repo.queue_length() == 0
    assert len(await repo.running_jobs()) == 1


async def test_queue_position(repo: Repository) -> None:
    first = await repo.enqueue(await a_request(repo, video_id="aaaaaaaaaaa"), ExtractType.AUDIO)
    second = await repo.enqueue(await a_request(repo, video_id="bbbbbbbbbbb"), ExtractType.AUDIO)
    third = await repo.enqueue(await a_request(repo, video_id="ccccccccccc"), ExtractType.AUDIO)
    assert await repo.queue_position(first.job_id) == 1
    assert await repo.queue_position(second.job_id) == 2
    assert await repo.queue_position(third.job_id) == 3


async def test_finish_records_state_and_error(repo: Repository) -> None:
    job = await repo.enqueue(await a_request(repo), ExtractType.AUDIO)
    await repo.finish(job.job_id, JobState.FAILED, error="boom")
    loaded = await repo.get_job(job.job_id)
    assert loaded is not None
    assert loaded.state is JobState.FAILED
    assert loaded.error == "boom"
    assert loaded.finished_at is not None


async def test_set_phase_and_status_message(repo: Repository) -> None:
    job = await repo.enqueue(await a_request(repo), ExtractType.AUDIO)
    await repo.set_phase(job.job_id, Phase.SPLIT)
    await repo.set_status_msg(job.job_id, 4242)
    loaded = await repo.get_job(job.job_id)
    assert loaded is not None
    assert loaded.phase is Phase.SPLIT
    assert loaded.status_msg_id == 4242


async def test_cancel_only_touches_the_users_queued_jobs(repo: Repository) -> None:
    mine = await repo.enqueue(await a_request(repo, video_id="aaaaaaaaaaa"), ExtractType.AUDIO)
    theirs = await repo.enqueue(
        await a_request(repo, user_id=222, video_id="bbbbbbbbbbb"), ExtractType.AUDIO
    )
    running = await repo.claim_next()
    assert running is not None and running.job_id == mine.job_id

    mine_queued = await repo.enqueue(
        await a_request(repo, video_id="ccccccccccc"), ExtractType.AUDIO
    )
    cancelled = await repo.cancel_queued_for_user(USER)

    assert [job.job_id for job in cancelled] == [mine_queued.job_id]
    assert (await repo.get_job(mine.job_id)).state is JobState.RUNNING  # pyright: ignore[reportOptionalMemberAccess]
    assert (await repo.get_job(theirs.job_id)).state is JobState.QUEUED  # pyright: ignore[reportOptionalMemberAccess]


async def test_cancel_with_nothing_queued(repo: Repository) -> None:
    assert await repo.cancel_queued_for_user(USER) == []


async def test_restart_requeues_running_and_interrupted_jobs(repo: Repository) -> None:
    job = await repo.enqueue(await a_request(repo), ExtractType.AUDIO)
    await repo.claim_next()
    other = await repo.enqueue(await a_request(repo, video_id="bbbbbbbbbbb"), ExtractType.AUDIO)
    await repo.mark_interrupted(other.job_id)

    requeued = await repo.requeue_interrupted()

    assert {j.job_id for j in requeued} == {job.job_id, other.job_id}
    assert await repo.queue_length() == 2
    reloaded = await repo.get_job(job.job_id)
    assert reloaded is not None
    assert reloaded.state is JobState.QUEUED
    assert reloaded.started_at is None
    assert reloaded.phase is Phase.QUEUED


async def test_done_jobs_survive_a_restart_untouched(repo: Repository) -> None:
    job = await repo.enqueue(await a_request(repo), ExtractType.AUDIO)
    await repo.finish(job.job_id, JobState.DONE)
    assert await repo.requeue_interrupted() == []
    assert (await repo.get_job(job.job_id)).state is JobState.DONE  # pyright: ignore[reportOptionalMemberAccess]


async def test_queue_survives_reopening_the_database(data_dir: Path) -> None:
    path = data_dir / "persist.db"
    first = await Repository.open(path)
    job = await first.enqueue(await a_request(first), ExtractType.AUDIO)
    await first.claim_next()
    await first.close()

    second = await Repository.open(path)
    try:
        assert await second.queue_length() == 0
        assert [j.job_id for j in await second.requeue_interrupted()] == [job.job_id]
        assert await second.queue_length() == 1
    finally:
        await second.close()


async def test_cache_entry_bookkeeping(repo: Repository) -> None:
    manifest = make_manifest("aaaaaaaaaaa", tracks=3)
    key = CacheKey("youtube", "aaaaaaaaaaa")
    await repo.record_cache_entry(manifest, size_bytes=4096)

    entry = await repo.cache_entry(key)
    assert entry is not None
    assert entry["tracks"] == 3
    assert entry["bytes"] == 4096
    assert await repo.cache_total_bytes() == 4096
    assert await repo.cache_count() == 1

    first_served = entry["last_served_at"]
    await repo.touch_cache_entry(key)
    touched = await repo.cache_entry(key)
    assert touched is not None
    assert touched["last_served_at"] >= first_served

    await repo.forget_cache_entry(key)
    assert await repo.cache_entry(key) is None


async def test_record_cache_entry_is_idempotent(repo: Repository) -> None:
    manifest = make_manifest("aaaaaaaaaaa", tracks=2)
    await repo.record_cache_entry(manifest, size_bytes=100)
    await repo.record_cache_entry(manifest, size_bytes=200)
    assert await repo.cache_count() == 1
    assert await repo.cache_total_bytes() == 200


async def test_eviction_order_is_least_recently_served_first(repo: Repository) -> None:
    await repo.record_cache_entry(make_manifest("aaaaaaaaaaa"), 10)
    await repo.record_cache_entry(make_manifest("bbbbbbbbbbb"), 10)
    await repo.conn.execute(
        "UPDATE cache_entries SET last_served_at = ? WHERE video_id = ?",
        ("2000-01-01T00:00:00Z", "bbbbbbbbbbb"),
    )
    order = await repo.cache_keys_by_age()
    assert order[0] == CacheKey("youtube", "bbbbbbbbbbb")


async def test_the_provider_round_trips_on_requests_and_jobs(repo: Repository) -> None:
    request = await a_request(repo, video_id="7123456789012345678", provider="tiktok")
    assert request.provider == "tiktok"

    loaded = await repo.get_request(request.req_id)
    assert loaded is not None and loaded.provider == "tiktok"

    job = await repo.enqueue(loaded, ExtractType.AUDIO)
    reloaded = await repo.get_job(job.job_id)
    assert reloaded is not None
    assert reloaded.provider == "tiktok"
    assert reloaded.video_id == "7123456789012345678"


async def test_the_same_id_on_two_providers_gets_two_cache_rows(repo: Repository) -> None:
    # The whole point of the composite key: ids are only unique within a site.
    shared = "abc123"
    await repo.record_cache_entry(make_manifest(shared, provider="youtube"), 10)
    await repo.record_cache_entry(make_manifest(shared, provider="tiktok"), 20)

    assert await repo.cache_count() == 2
    assert await repo.cache_total_bytes() == 30

    youtube = await repo.cache_entry(CacheKey("youtube", shared))
    tiktok = await repo.cache_entry(CacheKey("tiktok", shared))
    assert youtube is not None and youtube["bytes"] == 10
    assert tiktok is not None and tiktok["bytes"] == 20


async def test_forgetting_one_provider_leaves_the_other(repo: Repository) -> None:
    shared = "abc123"
    await repo.record_cache_entry(make_manifest(shared, provider="youtube"), 10)
    await repo.record_cache_entry(make_manifest(shared, provider="tiktok"), 20)

    await repo.forget_cache_entry(CacheKey("youtube", shared))

    assert await repo.cache_entry(CacheKey("youtube", shared)) is None
    assert await repo.cache_entry(CacheKey("tiktok", shared)) is not None
