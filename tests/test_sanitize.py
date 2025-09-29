from __future__ import annotations

import pytest

from chaptercut.pipeline.sanitize import (
    MAX_FILENAME_LEN,
    safe_filename,
    safe_title,
    track_filename,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Some Song Name", "Some Song Name"),
        ("Some/Song:Name", "Some Song Name"),
        ("  padded  ", "padded"),
        ("Björk - Jóga", "Bjork - Joga"),
        ("Кино", "Kino"),
        ("a???b", "a b"),
        ("multi     space", "multi space"),
        ("...", "track"),
        ("", "track"),
    ],
)
def test_safe_filename(raw: str, expected: str) -> None:
    assert safe_filename(raw) == expected


def test_safe_filename_does_not_underscore_everything() -> None:
    # Spaces are readable and legal in a filename; underscores are neither
    # required nor nicer.
    assert "_" not in safe_filename("Some Song Name!")


def test_safe_filename_truncates() -> None:
    assert len(safe_filename("x" * 500)) == MAX_FILENAME_LEN


def test_safe_filename_avoids_windows_reserved_names() -> None:
    assert safe_filename("CON") == "track"
    assert safe_filename("con") == "track"


def test_safe_filename_uses_fallback() -> None:
    assert safe_filename("///", fallback="track_07") == "track_07"


def test_safe_title_keeps_unicode() -> None:
    assert safe_title("Björk  -  Jóga") == "Björk - Jóga"


def test_safe_title_strips_zero_width() -> None:
    assert safe_title("a​b") == "ab"


@pytest.mark.parametrize(
    ("index", "total", "title", "expected"),
    [
        (1, 9, "Intro", "01 - Intro.mp3"),
        (7, 12, "Track", "07 - Track.mp3"),
        (7, 120, "Track", "007 - Track.mp3"),
        (3, 10, "///", "03 - track_03.mp3"),
    ],
)
def test_track_filename(index: int, total: int, title: str, expected: str) -> None:
    assert track_filename(index, total, title) == expected
