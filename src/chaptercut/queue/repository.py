"""Data access for requests, jobs, and cache bookkeeping.

Everything the worker and the handlers need from SQLite goes through here, so
there is exactly one place that knows the schema.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from chaptercut.cache.manifest import Manifest
from chaptercut.cache.store import CacheKey
from chaptercut.logging import get_logger
from chaptercut.providers.base import MediaRef
from chaptercut.queue.db import connect
from chaptercut.queue.models import (
    Destination,
    ExtractType,
    Job,
    JobState,
    Phase,
    Request,
)
from chaptercut.util.timefmt import iso, utcnow

log = get_logger(__name__)

REQUEST_TTL = timedelta(hours=1)
ID_BYTES = 6  # 8 urlsafe characters


def new_id() -> str:
    return secrets.token_urlsafe(ID_BYTES)


class Repository:
    """Thin async wrapper over the SQLite schema."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn

    @classmethod
    async def open(cls, path: Path) -> Repository:
        return cls(await connect(path))

    async def close(self) -> None:
        await self.conn.close()

    async def _fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        async with self.conn.execute(sql, params) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None  # pyright: ignore[reportArgumentType]

    async def _fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        async with self.conn.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]  # pyright: ignore[reportArgumentType]

    # --- requests ---------------------------------------------------------

    async def create_request(self, ref: MediaRef, user_id: int, chat_id: int) -> Request:
        now = utcnow()
        request = Request(
            req_id=new_id(),
            user_id=user_id,
            chat_id=chat_id,
            url=ref.url,
            provider=ref.provider,
            video_id=ref.media_id,
            created_at=now,
            expires_at=now + REQUEST_TTL,
        )
        await self.conn.execute(
            """INSERT INTO requests
               (req_id, user_id, chat_id, url, provider, video_id, extract_type,
                formats_json, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)""",
            (
                request.req_id,
                user_id,
                chat_id,
                request.url,
                request.provider,
                request.video_id,
                iso(request.created_at),
                iso(request.expires_at),
            ),
        )
        return request

    async def get_request(self, req_id: str) -> Request | None:
        row = await self._fetchone("SELECT * FROM requests WHERE req_id = ?", (req_id,))
        if row is None:
            return None
        request = Request.from_row(row)
        return None if request.expires_at <= utcnow() else request

    async def set_request_type(self, req_id: str, extract_type: ExtractType) -> None:
        await self.conn.execute(
            "UPDATE requests SET extract_type = ? WHERE req_id = ?",
            (extract_type.value, req_id),
        )

    async def set_request_destination(self, req_id: str, destination: Destination) -> None:
        await self.conn.execute(
            "UPDATE requests SET destination = ? WHERE req_id = ?",
            (destination.value, req_id),
        )

    async def set_request_format_choice(self, req_id: str, format_id: str) -> None:
        await self.conn.execute(
            "UPDATE requests SET format_id = ? WHERE req_id = ?", (format_id, req_id)
        )

    async def set_request_formats(self, req_id: str, formats_json: str) -> None:
        await self.conn.execute(
            "UPDATE requests SET formats_json = ? WHERE req_id = ?",
            (formats_json, req_id),
        )

    async def purge_expired_requests(self) -> int:
        cursor = await self.conn.execute(
            "DELETE FROM requests WHERE expires_at <= ?", (iso(utcnow()),)
        )
        return cursor.rowcount

    # --- jobs -------------------------------------------------------------

    async def enqueue(
        self,
        request: Request,
        kind: ExtractType,
        status_msg_id: int | None = None,
        format_id: str | None = None,
    ) -> Job:
        job = Job(
            job_id=new_id(),
            req_id=request.req_id,
            user_id=request.user_id,
            chat_id=request.chat_id,
            status_msg_id=status_msg_id,
            kind=kind,
            destination=request.destination,
            provider=request.provider,
            video_id=request.video_id,
            url=request.url,
            format_id=format_id,
            state=JobState.QUEUED,
            phase=Phase.QUEUED,
            created_at=utcnow(),
        )
        await self.conn.execute(
            """INSERT INTO jobs
               (job_id, req_id, user_id, chat_id, status_msg_id, kind, destination,
                provider, video_id, url, format_id, state, phase, error,
                created_at, started_at, finished_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL)""",
            (
                job.job_id,
                job.req_id,
                job.user_id,
                job.chat_id,
                job.status_msg_id,
                job.kind.value,
                job.destination.value,
                job.provider,
                job.video_id,
                job.url,
                job.format_id,
                job.state.value,
                Phase.QUEUED.value,
                iso(job.created_at),
            ),
        )
        log.info(
            "job.enqueued",
            job_id=job.job_id,
            provider=job.provider,
            video_id=job.video_id,
            kind=job.kind.value,
        )
        return job

    async def get_job(self, job_id: str) -> Job | None:
        row = await self._fetchone("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        return Job.from_row(row) if row else None

    async def claim_next(self) -> Job | None:
        """Take the oldest queued job and flip it to running in one transaction."""
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = await self._fetchone(
                "SELECT * FROM jobs WHERE state = ? ORDER BY created_at, rowid LIMIT 1",
                (JobState.QUEUED.value,),
            )
            if row is None:
                await self.conn.execute("COMMIT")
                return None
            job = Job.from_row(row)
            started = utcnow()
            await self.conn.execute(
                "UPDATE jobs SET state = ?, started_at = ?, phase = ? WHERE job_id = ?",
                (JobState.RUNNING.value, iso(started), Phase.FETCH.value, job.job_id),
            )
            await self.conn.execute("COMMIT")
        except Exception:
            await self.conn.execute("ROLLBACK")
            raise
        job.state = JobState.RUNNING
        job.started_at = started
        job.phase = Phase.FETCH
        return job

    async def set_phase(self, job_id: str, phase: Phase) -> None:
        await self.conn.execute("UPDATE jobs SET phase = ? WHERE job_id = ?", (phase.value, job_id))

    async def set_status_msg(self, job_id: str, message_id: int) -> None:
        await self.conn.execute(
            "UPDATE jobs SET status_msg_id = ? WHERE job_id = ?", (message_id, job_id)
        )

    async def finish(self, job_id: str, state: JobState, error: str | None = None) -> None:
        await self.conn.execute(
            "UPDATE jobs SET state = ?, error = ?, finished_at = ?, phase = ? WHERE job_id = ?",
            (state.value, error, iso(utcnow()), Phase.DONE.value, job_id),
        )
        log.info("job.finished", job_id=job_id, state=state.value, error=error)

    async def queue_position(self, job_id: str) -> int:
        """1-based position among queued jobs; 0 if the job is not queued."""
        row = await self._fetchone(
            """SELECT COUNT(*) AS ahead FROM jobs
               WHERE state = ?
                 AND (created_at, rowid) <=
                     (SELECT created_at, rowid FROM jobs WHERE job_id = ?)""",
            (JobState.QUEUED.value, job_id),
        )
        return int(row["ahead"]) if row else 0

    async def queue_length(self) -> int:
        row = await self._fetchone(
            "SELECT COUNT(*) AS n FROM jobs WHERE state = ?", (JobState.QUEUED.value,)
        )
        return int(row["n"]) if row else 0

    async def running_jobs(self) -> list[Job]:
        rows = await self._fetchall(
            "SELECT * FROM jobs WHERE state = ? ORDER BY started_at", (JobState.RUNNING.value,)
        )
        return [Job.from_row(row) for row in rows]

    async def queued_jobs_for_user(self, user_id: int) -> list[Job]:
        rows = await self._fetchall(
            "SELECT * FROM jobs WHERE user_id = ? AND state = ? ORDER BY created_at",
            (user_id, JobState.QUEUED.value),
        )
        return [Job.from_row(row) for row in rows]

    async def cancel_queued_for_user(self, user_id: int) -> list[Job]:
        """Cancel the user's queued jobs. A running job is never touched."""
        jobs = await self.queued_jobs_for_user(user_id)
        if jobs:
            await self.conn.execute(
                "UPDATE jobs SET state = ?, finished_at = ? WHERE user_id = ? AND state = ?",
                (JobState.CANCELLED.value, iso(utcnow()), user_id, JobState.QUEUED.value),
            )
        return jobs

    async def requeue_interrupted(self) -> list[Job]:
        """On startup, anything left running or interrupted goes back in the queue."""
        rows = await self._fetchall(
            "SELECT * FROM jobs WHERE state IN (?, ?)",
            (JobState.RUNNING.value, JobState.INTERRUPTED.value),
        )
        if rows:
            await self.conn.execute(
                "UPDATE jobs SET state = ?, phase = ?, started_at = NULL WHERE state IN (?, ?)",
                (
                    JobState.QUEUED.value,
                    Phase.QUEUED.value,
                    JobState.RUNNING.value,
                    JobState.INTERRUPTED.value,
                ),
            )
            log.info("job.requeued_interrupted", count=len(rows))
        return [Job.from_row(row) for row in rows]

    async def mark_interrupted(self, job_id: str) -> None:
        await self.conn.execute(
            "UPDATE jobs SET state = ?, finished_at = ? WHERE job_id = ?",
            (JobState.INTERRUPTED.value, iso(utcnow()), job_id),
        )

    # --- cache bookkeeping ------------------------------------------------

    async def record_cache_entry(self, manifest: Manifest, size_bytes: int) -> None:
        now = iso(utcnow())
        await self.conn.execute(
            """INSERT INTO cache_entries
                 (provider, video_id, title, bytes, tracks, downloaded_at, last_served_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(provider, video_id) DO UPDATE SET
                 title = excluded.title,
                 bytes = excluded.bytes,
                 tracks = excluded.tracks,
                 downloaded_at = excluded.downloaded_at,
                 last_served_at = excluded.last_served_at""",
            (
                manifest.provider,
                manifest.video_id,
                manifest.title,
                size_bytes,
                len(manifest.tracks),
                manifest.downloaded_at,
                now,
            ),
        )

    async def touch_cache_entry(self, key: CacheKey) -> None:
        await self.conn.execute(
            "UPDATE cache_entries SET last_served_at = ? WHERE provider = ? AND video_id = ?",
            (iso(utcnow()), key.provider, key.media_id),
        )

    async def forget_cache_entry(self, key: CacheKey) -> None:
        await self.conn.execute(
            "DELETE FROM cache_entries WHERE provider = ? AND video_id = ?",
            (key.provider, key.media_id),
        )

    async def cache_entry(self, key: CacheKey) -> dict[str, Any] | None:
        return await self._fetchone(
            "SELECT * FROM cache_entries WHERE provider = ? AND video_id = ?",
            (key.provider, key.media_id),
        )

    async def cache_keys_by_age(self) -> list[CacheKey]:
        """Least recently served first: the eviction order."""
        rows = await self._fetchall(
            "SELECT provider, video_id FROM cache_entries "
            "ORDER BY COALESCE(last_served_at, downloaded_at, '')"
        )
        return [CacheKey(provider=str(r["provider"]), media_id=str(r["video_id"])) for r in rows]

    async def cache_total_bytes(self) -> int:
        row = await self._fetchone("SELECT COALESCE(SUM(bytes), 0) AS total FROM cache_entries")
        return int(row["total"]) if row else 0

    async def cache_count(self) -> int:
        row = await self._fetchone("SELECT COUNT(*) AS n FROM cache_entries")
        return int(row["n"]) if row else 0
