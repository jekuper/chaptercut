"""TikTok.

Two things differ from YouTube. There are never chapter markers, so audio is
always a single track. And the share links (`vm.tiktok.com/ZMxxxx`) hide the id
behind a redirect, so intake records the short code and the pipeline learns the
real id from the metadata fetch it was going to make anyway.

Canonical URLs are rebuilt rather than passed through, because yt-dlp's TikTok
extractor is picky: it accepts `www.tiktok.com/@<user>/video/<id>` (the user
part may be empty) but not `m.tiktok.com`, not `/v/<id>`, and not `/photo/`.
"""

from __future__ import annotations

import re

from chaptercut.providers.base import HOST_PREFIX, MediaRef, Provider

_ID = r"(?P<id>\d{6,25})"
_USER = r"(?P<user>[\w.-]{1,40})"

# A share-link code, e.g. vm.tiktok.com/ZMhqAbCdE
_CODE = r"(?P<code>[A-Za-z0-9]{5,32})"

# Photo posts are slideshows. yt-dlp has no /photo/ extractor, but the id
# namespace is shared with videos, so they are rewritten onto the video path:
# that at least gets the backing audio, which is what an audio request wants.
_FULL_PATTERNS = [
    re.compile(rf"{HOST_PREFIX}tiktok\.com/@{_USER}/(?:video|photo)/{_ID}", re.IGNORECASE),
]

_ID_ONLY_PATTERNS = [
    re.compile(rf"{HOST_PREFIX}tiktok\.com/(?:@|share)/(?:video|photo)/{_ID}", re.IGNORECASE),
    re.compile(rf"{HOST_PREFIX}tiktok\.com/v/{_ID}", re.IGNORECASE),
    re.compile(rf"{HOST_PREFIX}tiktok\.com/embed/(?:v2/)?{_ID}", re.IGNORECASE),
]

# Checked last: these paths carry a redirect code, not an id.
_SHORT_PATTERNS = [
    re.compile(rf"{HOST_PREFIX}(?:vm|vt)\.tiktok\.com/{_CODE}", re.IGNORECASE),
    re.compile(rf"{HOST_PREFIX}tiktok\.com/t/{_CODE}", re.IGNORECASE),
]

_ID_RE = re.compile(r"^\d{6,25}$")

# A trailing run of hashtags, which is how most TikTok captions end.
_TRAILING_TAGS_RE = re.compile(r"(?:\s*#[^\s#]+)+\s*$")
_WHITESPACE_RE = re.compile(r"\s+")


class TikTokProvider(Provider):
    name = "tiktok"
    label = "TikTok"
    supports_chapters = False

    def match(self, candidate: str) -> MediaRef | None:
        for pattern in _FULL_PATTERNS:
            found = pattern.search(candidate)
            if found:
                return MediaRef(
                    provider=self.name,
                    media_id=found.group("id"),
                    url=canonical_url(found.group("id"), user=found.group("user")),
                )

        for pattern in _ID_ONLY_PATTERNS:
            found = pattern.search(candidate)
            if found:
                return MediaRef(
                    provider=self.name,
                    media_id=found.group("id"),
                    url=canonical_url(found.group("id")),
                )

        for pattern in _SHORT_PATTERNS:
            found = pattern.search(candidate)
            if found:
                # The code is a placeholder id; the fetch stage replaces it.
                code = found.group("code")
                return MediaRef(
                    provider=self.name,
                    media_id=code,
                    url=short_url(found.group(0), code),
                    resolved=False,
                )
        return None

    def is_canonical_id(self, media_id: str) -> bool:
        return bool(_ID_RE.match(media_id))

    def clean_title(self, title: str) -> str:
        """Drop the trailing hashtag pile from a caption.

        "sunset timelapse #fyp #viral" becomes "sunset timelapse". A caption
        that is nothing but hashtags is left alone, since the alternative is an
        empty title.
        """
        stripped = _WHITESPACE_RE.sub(" ", _TRAILING_TAGS_RE.sub("", title)).strip()
        return stripped or _WHITESPACE_RE.sub(" ", title).strip()


def canonical_url(media_id: str, user: str = "") -> str:
    """The watch URL for a known id. An empty user is valid for the extractor."""
    return f"https://www.tiktok.com/@{user}/video/{media_id}"


def short_url(matched: str, code: str) -> str:
    """A share link, normalised to https and stripped of any trailing path."""
    host = "www.tiktok.com/t" if "/t/" in matched else _short_host(matched)
    return f"https://{host}/{code}"


def _short_host(matched: str) -> str:
    return "vt.tiktok.com" if "vt." in matched.lower() else "vm.tiktok.com"
