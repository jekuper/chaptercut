"""YouTube URL recognition and video-id extraction, without any network call."""

from __future__ import annotations

import re

# 11-char base64url video id.
_ID = r"(?P<id>[A-Za-z0-9_-]{11})"

_HOST = r"(?:https?://)?(?:[a-z0-9-]+\.)*"

_PATTERNS = [
    re.compile(rf"{_HOST}youtube\.com/watch\?(?:[^\s]*&)?v={_ID}", re.IGNORECASE),
    re.compile(rf"{_HOST}youtube\.com/(?:shorts|embed|live|v)/{_ID}", re.IGNORECASE),
    re.compile(rf"{_HOST}youtu\.be/{_ID}", re.IGNORECASE),
]

_URL_RE = re.compile(r"(?:https?://)?[^\s<>\"]+", re.IGNORECASE)


def extract_video_id(text: str) -> str | None:
    """Return the video id of the first YouTube link in `text`, if any."""
    for candidate in _URL_RE.findall(text):
        for pattern in _PATTERNS:
            match = pattern.search(candidate)
            if match:
                return match.group("id")
    return None


def find_video_ids(text: str) -> list[str]:
    """Every distinct YouTube video id in `text`, in order of appearance."""
    found: list[str] = []
    for candidate in _URL_RE.findall(text):
        for pattern in _PATTERNS:
            match = pattern.search(candidate)
            if match and match.group("id") not in found:
                found.append(match.group("id"))
                break
    return found


def is_youtube_url(text: str) -> bool:
    return extract_video_id(text) is not None


def canonical_url(video_id: str) -> str:
    """The canonical watch URL. We always hand yt-dlp this, never the user's URL,
    so tracking parameters and playlist ids never reach the network or the logs."""
    return f"https://www.youtube.com/watch?v={video_id}"
