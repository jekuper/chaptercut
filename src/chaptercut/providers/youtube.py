"""YouTube."""

from __future__ import annotations

import re

from chaptercut.providers.base import HOST_PREFIX, MediaRef, Provider

# 11-character base64url video id.
_ID = r"(?P<id>[A-Za-z0-9_-]{11})"

_PATTERNS = [
    re.compile(rf"{HOST_PREFIX}youtube\.com/watch\?(?:[^\s]*&)?v={_ID}", re.IGNORECASE),
    re.compile(rf"{HOST_PREFIX}youtube\.com/(?:shorts|embed|live|v)/{_ID}", re.IGNORECASE),
    re.compile(rf"{HOST_PREFIX}youtu\.be/{_ID}", re.IGNORECASE),
]

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


class YouTubeProvider(Provider):
    name = "youtube"
    label = "YouTube"
    supports_chapters = True

    def match(self, candidate: str) -> MediaRef | None:
        for pattern in _PATTERNS:
            found = pattern.search(candidate)
            if found:
                video_id = found.group("id")
                return MediaRef(
                    provider=self.name,
                    media_id=video_id,
                    url=canonical_url(video_id),
                )
        return None

    def is_canonical_id(self, media_id: str) -> bool:
        return bool(_ID_RE.match(media_id))


def canonical_url(video_id: str) -> str:
    """The canonical watch URL.

    Rebuilt from the id rather than kept as typed, so playlist ids and tracking
    parameters never reach the network, the logs, or the tags.
    """
    return f"https://www.youtube.com/watch?v={video_id}"
