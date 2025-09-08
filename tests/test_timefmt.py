from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from chaptercut.util.timefmt import (
    format_bytes,
    format_duration,
    format_uptime,
    iso,
    parse_iso,
    utcnow,
)


def test_iso_is_utc_with_a_trailing_z() -> None:
    assert iso(datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)) == "2026-08-23T12:00:00Z"


def test_iso_converts_from_another_zone() -> None:
    moment = datetime(2026, 8, 23, 14, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    assert iso(moment) == "2026-08-23T12:00:00Z"


def test_iso_drops_microseconds() -> None:
    assert iso(datetime(2026, 8, 23, 12, 0, 0, 123456, tzinfo=UTC)) == "2026-08-23T12:00:00Z"


def test_iso_round_trip() -> None:
    moment = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)
    assert parse_iso(iso(moment)) == moment


def test_utcnow_is_aware() -> None:
    assert utcnow().tzinfo is not None


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (None, "0:00"),
        (-5, "0:00"),
        (0, "0:00"),
        (9, "0:09"),
        (61, "1:01"),
        (600, "10:00"),
        (3661, "1:01:01"),
        (36000, "10:00:00"),
    ],
)
def test_format_duration(seconds: float | None, expected: str) -> None:
    assert format_duration(seconds) == expected


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (None, "0 B"),
        (0, "0 B"),
        (512, "512 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1024 * 1024, "1.0 MB"),
        (210 * 1024 * 1024, "210 MB"),
        (1024**3, "1.0 GB"),
        (5 * 1024**4, "5120 GB"),
    ],
)
def test_format_bytes(size: float | None, expected: str) -> None:
    assert format_bytes(size) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0m"),
        (90, "1m"),
        (3600, "1h 0m"),
        (3660, "1h 1m"),
        (90000, "1d 1h 0m"),
    ],
)
def test_format_uptime(seconds: float, expected: str) -> None:
    assert format_uptime(seconds) == expected
