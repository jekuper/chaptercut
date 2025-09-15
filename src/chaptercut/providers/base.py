"""What a source site has to tell the rest of the system.

A provider is pure URL knowledge: how to recognise its links, what the id is,
and what a canonical URL looks like. It performs no I/O, which is what keeps
intake free of network calls and makes providers trivial to test.

Everything downstream (downloading, splitting, tagging) is already generic,
because yt-dlp and ffmpeg do not care which site a URL points at.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

# A bare token that could be a URL. Providers match their own patterns inside
# each candidate, so a link glued to surrounding punctuation still resolves.
URL_CANDIDATE_RE = re.compile(r"(?:https?://)?[^\s<>\"]+", re.IGNORECASE)

# Any number of subdomain labels, with a lookbehind so that "notyoutube.com"
# cannot match on its "youtube.com" tail.
HOST_PREFIX = r"(?<![\w.-])(?:https?://)?(?:[a-z0-9-]+\.)*"

# Ids that are safe to use as a directory name without further encoding.
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# A provider name becomes part of a cache directory name, so it must not
# contain the separator or anything path-like.
PROVIDER_NAME_RE = re.compile(r"^[a-z0-9]+$")


@dataclass(frozen=True, slots=True)
class MediaRef:
    """One recognised link.

    `resolved` is False when only the site itself can turn the URL into an id,
    as with TikTok's `vm.tiktok.com` redirects. In that case `media_id` holds
    the short code, which is a perfectly good placeholder until the metadata
    fetch reports the real one.
    """

    provider: str
    media_id: str
    url: str
    resolved: bool = True


class Provider(ABC):
    """URL knowledge for one source site."""

    name: str = ""
    label: str = ""

    # Whether the site exposes chapter markers at all. TikTok never does, so
    # its audio is always a single track.
    supports_chapters: bool = False

    # Whether processed audio for this site is worth keeping on disk.
    cache_audio: bool = True

    # Extra yt-dlp flags for every call against this site.
    ytdlp_args: tuple[str, ...] = ()

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if cls.name and not PROVIDER_NAME_RE.match(cls.name):
            raise ValueError(f"provider name {cls.name!r} must be lowercase alphanumeric")

    @abstractmethod
    def match(self, candidate: str) -> MediaRef | None:
        """A reference for `candidate`, or None if this site does not own it."""

    @abstractmethod
    def is_canonical_id(self, media_id: str) -> bool:
        """Whether `media_id` is the site's real id rather than a placeholder."""

    def clean_title(self, title: str) -> str:
        """Tidy a title before it becomes a tag or a filename."""
        return title

    def find(self, text: str) -> list[MediaRef]:
        """Every distinct reference to this site in `text`, in order."""
        found: list[MediaRef] = []
        seen: set[str] = set()
        for candidate in URL_CANDIDATE_RE.findall(text):
            ref = self.match(candidate)
            if ref is not None and ref.media_id not in seen:
                seen.add(ref.media_id)
                found.append(ref)
        return found


def strip_query(url: str) -> str:
    """Drop the query and fragment, and force https.

    This is the default canonicalisation: it removes tracking parameters
    without inventing a URL shape that the extractor might not accept.
    """
    if "//" not in url:
        url = f"https://{url}"
    parts = urlsplit(url)
    return urlunsplit(("https", parts.netloc, parts.path.rstrip("/"), "", ""))
