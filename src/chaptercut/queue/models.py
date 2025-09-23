"""Rows of the SQLite schema, as plain dataclasses."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Self

from chaptercut.pipeline.formats import FormatOption, options_from_json, options_to_json
from chaptercut.util.jsonish import dict_list
from chaptercut.util.timefmt import parse_iso


class ExtractType(StrEnum):
    AUDIO = "audio"
    VIDEO = "video"


class Destination(StrEnum):
    TELEGRAM = "telegram"
    SERVER = "server"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class Phase(StrEnum):
    QUEUED = "queued"
    FETCH = "fetch"
    DOWNLOAD = "download"
    SPLIT = "split"
    TAG = "tag"
    PACKAGE = "package"
    UPLOAD = "upload"
    DONE = "done"


TERMINAL_STATES = {JobState.DONE, JobState.FAILED, JobState.CANCELLED}


def _dt(value: Any) -> datetime | None:
    return parse_iso(str(value)) if value else None


@dataclass(slots=True)
class Request:
    req_id: str
    user_id: int
    chat_id: int
    url: str
    provider: str
    video_id: str
    created_at: datetime
    expires_at: datetime
    extract_type: ExtractType | None = None
    formats_json: str | None = None
    destination: Destination = Destination.TELEGRAM
    chosen_format_id: str | None = None

    @property
    def formats(self) -> list[FormatOption]:
        if not self.formats_json:
            return []
        try:
            return options_from_json(dict_list(json.loads(self.formats_json)))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return []

    @staticmethod
    def encode_formats(options: list[FormatOption]) -> str:
        return json.dumps(options_to_json(options))

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Self:
        created = _dt(row["created_at"])
        expires = _dt(row["expires_at"])
        if created is None or expires is None:  # pragma: no cover - NOT NULL columns
            raise ValueError("request row is missing timestamps")
        raw_type = row["extract_type"]
        raw_destination = row["destination"]
        return cls(
            req_id=str(row["req_id"]),
            user_id=int(row["user_id"]),
            chat_id=int(row["chat_id"]),
            url=str(row["url"]),
            provider=str(row["provider"]),
            video_id=str(row["video_id"]),
            extract_type=ExtractType(raw_type) if raw_type else None,
            formats_json=row["formats_json"],
            destination=Destination(raw_destination) if raw_destination else Destination.TELEGRAM,
            chosen_format_id=row["format_id"],
            created_at=created,
            expires_at=expires,
        )


@dataclass(slots=True)
class Job:
    job_id: str
    req_id: str | None
    user_id: int
    chat_id: int
    kind: ExtractType
    provider: str
    video_id: str
    url: str
    state: JobState
    created_at: datetime
    destination: Destination = Destination.TELEGRAM
    status_msg_id: int | None = None
    format_id: str | None = None
    phase: Phase | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Self:
        created = _dt(row["created_at"])
        if created is None:  # pragma: no cover - NOT NULL column
            raise ValueError("job row is missing created_at")
        return cls(
            job_id=str(row["job_id"]),
            req_id=row["req_id"],
            user_id=int(row["user_id"]),
            chat_id=int(row["chat_id"]),
            status_msg_id=row["status_msg_id"],
            kind=ExtractType(row["kind"]),
            provider=str(row["provider"]),
            video_id=str(row["video_id"]),
            url=str(row["url"]),
            format_id=row["format_id"],
            state=JobState(row["state"]),
            destination=Destination(row["destination"])
            if row["destination"]
            else Destination.TELEGRAM,
            phase=Phase(row["phase"]) if row["phase"] else None,
            error=row["error"],
            created_at=created,
            started_at=_dt(row["started_at"]),
            finished_at=_dt(row["finished_at"]),
        )
