"""SoundCloud.

Audio only: there is no video to offer, so intake skips the Audio/Video
question. There are no chapter markers either, so a track is always one MP3.

A permalink names the uploader and a slug (`soundcloud.com/<user>/<slug>`),
not the numeric track id that yt-dlp reports. So every permalink is an
unresolved reference, the same way a TikTok share link is: the slug pair
stands in as the id until the metadata fetch supplies the real one, and the
cache is keyed by that. Only `api.soundcloud.com/tracks/<id>` carries the id.

`on.soundcloud.com/<code>` share links are plain redirects. yt-dlp's generic
extractor follows them and hands off to the SoundCloud one, so the short URL
goes through as is.
"""

from __future__ import annotations

import re

from chaptercut.providers.base import MediaRef, Provider

# Only the main site hosts carry permalinks; `on.` and `api.` are different
# shapes and are matched separately below.
_SITE = r"(?<![\w.-])(?:https?://)?(?:(?:www|m)\.)?soundcloud\.com/"
_SEGMENT = r"[A-Za-z0-9_-]+"

# First path segments that are site pages, not uploaders.
_RESERVED_USERS = frozenset(
    {
        "charts",
        "dashboard",
        "discover",
        "explore",
        "feed",
        "imprint",
        "jobs",
        "login",
        "logout",
        "messages",
        "mobile",
        "notifications",
        "pages",
        "people",
        "popular",
        "search",
        "settings",
        "signin",
        "stations",
        "stream",
        "tags",
        "terms-of-use",
        "upload",
        "you",
    }
)

# Second path segments that are profile tabs or playlists, not tracks.
_RESERVED_SLUGS = frozenset(
    {
        "albums",
        "comments",
        "followers",
        "following",
        "likes",
        "popular-tracks",
        "reposts",
        "sets",
        "spotlight",
        "tracks",
    }
)

# Private tracks carry a secret token as a third segment: /<user>/<slug>/s-XXXX
_TRACK_RE = re.compile(
    rf"{_SITE}(?P<user>{_SEGMENT})/(?P<slug>{_SEGMENT})(?:/(?P<token>s-[A-Za-z0-9]+))?",
    re.IGNORECASE,
)
_API_RE = re.compile(
    r"(?<![\w.-])(?:https?://)?api(?:-v2)?\.soundcloud\.com/tracks/(?P<id>\d{1,20})",
    re.IGNORECASE,
)
_SHORT_RE = re.compile(
    r"(?<![\w.-])(?:https?://)?on\.soundcloud\.com/(?P<code>[A-Za-z0-9]{5,32})",
    re.IGNORECASE,
)

_ID_RE = re.compile(r"^\d{1,20}$")


class SoundCloudProvider(Provider):
    name = "soundcloud"
    label = "SoundCloud"
    supports_chapters = False
    supports_video = False

    def match(self, candidate: str) -> MediaRef | None:
        found = _API_RE.search(candidate)
        if found:
            track_id = found.group("id")
            return MediaRef(
                provider=self.name,
                media_id=track_id,
                url=f"https://api.soundcloud.com/tracks/{track_id}",
            )

        found = _SHORT_RE.search(candidate)
        if found:
            code = found.group("code")
            return MediaRef(
                provider=self.name,
                media_id=code,
                url=f"https://on.soundcloud.com/{code}",
                resolved=False,
            )

        found = _TRACK_RE.search(candidate)
        if found:
            user, slug = found.group("user"), found.group("slug")
            if user.lower() in _RESERVED_USERS or slug.lower() in _RESERVED_SLUGS:
                return None
            return MediaRef(
                provider=self.name,
                media_id=f"{user}/{slug}",
                url=canonical_url(user, slug, found.group("token")),
                resolved=False,
            )
        return None

    def is_canonical_id(self, media_id: str) -> bool:
        return bool(_ID_RE.match(media_id))


def canonical_url(user: str, slug: str, token: str | None = None) -> str:
    """The permalink, stripped of the `?in=<set>` and tracking parameters."""
    url = f"https://soundcloud.com/{user}/{slug}"
    return f"{url}/{token}" if token else url
