"""SQLite connection and schema migrations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite

from chaptercut.logging import get_logger

log = get_logger(__name__)

SCHEMA_VERSION = 1

MIGRATIONS: list[str] = [
    # v1: requests, jobs, cache_entries
    """
    CREATE TABLE IF NOT EXISTS requests (
      req_id        TEXT PRIMARY KEY,
      user_id       INTEGER NOT NULL,
      chat_id       INTEGER NOT NULL,
      url           TEXT NOT NULL,
      video_id      TEXT NOT NULL,
      extract_type  TEXT,
      formats_json  TEXT,
      created_at    TEXT NOT NULL,
      expires_at    TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS requests_expiry_idx ON requests(expires_at);

    CREATE TABLE IF NOT EXISTS jobs (
      job_id          TEXT PRIMARY KEY,
      req_id          TEXT REFERENCES requests(req_id) ON DELETE SET NULL,
      user_id         INTEGER NOT NULL,
      chat_id         INTEGER NOT NULL,
      status_msg_id   INTEGER,
      kind            TEXT NOT NULL,
      video_id        TEXT NOT NULL,
      url             TEXT NOT NULL,
      format_id       TEXT,
      state           TEXT NOT NULL,
      phase           TEXT,
      error           TEXT,
      created_at      TEXT NOT NULL,
      started_at      TEXT,
      finished_at     TEXT
    );
    CREATE INDEX IF NOT EXISTS jobs_state_idx ON jobs(state, created_at);

    CREATE TABLE IF NOT EXISTS cache_entries (
      video_id        TEXT PRIMARY KEY,
      title           TEXT,
      bytes           INTEGER,
      tracks          INTEGER,
      downloaded_at   TEXT,
      last_served_at  TEXT
    );
    CREATE INDEX IF NOT EXISTS cache_served_idx ON cache_entries(last_served_at);
    """,
]


def _row_to_dict(cursor: aiosqlite.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


async def connect(path: Path) -> aiosqlite.Connection:
    """Open the database, apply migrations, and return a dict-row connection."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(path, isolation_level=None)
    conn.row_factory = _row_to_dict  # pyright: ignore[reportAttributeAccessIssue]
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA synchronous=NORMAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.execute("PRAGMA busy_timeout=5000")
    await migrate(conn)
    return conn


async def migrate(conn: aiosqlite.Connection) -> None:
    async with conn.execute("PRAGMA user_version") as cursor:
        row = await cursor.fetchone()
    current = int(dict(row).get("user_version", 0)) if row else 0  # pyright: ignore[reportArgumentType]

    for version in range(current, len(MIGRATIONS)):
        log.info("db.migrate", to_version=version + 1)
        await conn.executescript(MIGRATIONS[version])
        await conn.execute(f"PRAGMA user_version={version + 1}")
