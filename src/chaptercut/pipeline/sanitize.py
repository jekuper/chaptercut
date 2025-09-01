"""Filename and title sanitization.

Tag values keep their Unicode; only filenames are transliterated, because the
files travel through ZIPs, Telegram and arbitrary phone filesystems.
"""

from __future__ import annotations

import re

from unidecode import unidecode

MAX_FILENAME_LEN = 120

_ALLOWED = re.compile(r"[^A-Za-z0-9 ._-]")
_WHITESPACE = re.compile(r"\s+")
# Reserved on Windows; a track called "CON.mp3" is unopenable there.
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_filename(title: str, fallback: str = "track") -> str:
    """A readable ASCII filename stem. Spaces are kept; only illegal characters go."""
    text = unidecode(title or "")
    text = _ALLOWED.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip(" .")
    text = text[:MAX_FILENAME_LEN].strip(" .")
    if not text or text.upper() in _RESERVED:
        return fallback
    return text


def safe_title(title: str) -> str:
    """A tag value: Unicode preserved, only whitespace normalized."""
    return _WHITESPACE.sub(" ", (title or "").replace("​", "")).strip()


def track_filename(index: int, total: int, title: str) -> str:
    """`03 - Some Track.mp3`, zero-padded to the width of the track count."""
    width = max(2, len(str(total)))
    stem = safe_filename(title, fallback=f"track_{index:0{width}d}")
    return f"{index:0{width}d} - {stem}.mp3"
