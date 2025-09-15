"""URL recognition per provider, and the registry that fans out across them.

No network: a provider is pure pattern matching, which is the whole reason
intake never has to call yt-dlp just to learn an id.
"""

from __future__ import annotations

import pytest

from chaptercut.providers.base import Provider, strip_query
from chaptercut.providers.registry import (
    ALL_PROVIDERS,
    ProviderRegistry,
    UnknownProviderError,
)
from chaptercut.providers.tiktok import TikTokProvider
from chaptercut.providers.youtube import YouTubeProvider

VIDEO_ID = "dQw4w9WgXcQ"
TIKTOK_ID = "7123456789012345678"


@pytest.fixture
def youtube() -> YouTubeProvider:
    return YouTubeProvider()


@pytest.fixture
def tiktok() -> TikTokProvider:
    return TikTokProvider()


# --- YouTube -----------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "http://youtube.com/watch?v=dQw4w9WgXcQ",
        "youtube.com/watch?v=dQw4w9WgXcQ",
        "www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
        "https://www.youtube.com/watch?list=PL123&v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?t=30",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "look at this dQw4w9WgXcQ -> https://youtu.be/dQw4w9WgXcQ please",
    ],
)
def test_youtube_forms(youtube: YouTubeProvider, text: str) -> None:
    ref = youtube.match(text)
    assert ref is not None
    assert ref.provider == "youtube"
    assert ref.media_id == VIDEO_ID
    assert ref.resolved is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "hello",
        "https://vimeo.com/12345",
        "https://www.youtube.com/",
        "https://www.youtube.com/watch?v=tooshort",
        "https://notyoutube.com/watch?v=dQw4w9WgXcQ",
    ],
)
def test_youtube_rejects(youtube: YouTubeProvider, text: str) -> None:
    assert youtube.match(text) is None


def test_youtube_canonical_url_drops_playlists_and_tracking(youtube: YouTubeProvider) -> None:
    ref = youtube.match("https://youtu.be/dQw4w9WgXcQ?si=trackingtoken&t=90")
    assert ref is not None
    assert ref.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_youtube_supports_chapters(youtube: YouTubeProvider) -> None:
    assert youtube.supports_chapters is True


def test_youtube_canonical_ids(youtube: YouTubeProvider) -> None:
    assert youtube.is_canonical_id(VIDEO_ID)
    assert not youtube.is_canonical_id("tooshort")
    assert not youtube.is_canonical_id("waytoolongtobeanid")


def test_youtube_leaves_titles_alone(youtube: YouTubeProvider) -> None:
    assert youtube.clean_title("A Title #withatag") == "A Title #withatag"


# --- TikTok ------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "https://www.tiktok.com/@someuser/video/7123456789012345678",
        "https://www.tiktok.com/@some.user_1/video/7123456789012345678?is_from_webapp=1",
        "https://www.tiktok.com/@someuser/photo/7123456789012345678",
        "https://m.tiktok.com/v/7123456789012345678.html",
        "https://www.tiktok.com/embed/7123456789012345678",
        "https://www.tiktok.com/embed/v2/7123456789012345678",
        "https://www.tiktok.com/share/video/7123456789012345678",
        "tiktok.com/@u/video/7123456789012345678",
        "watch this https://www.tiktok.com/@u/video/7123456789012345678 lol",
    ],
)
def test_tiktok_forms_with_an_id(tiktok: TikTokProvider, text: str) -> None:
    ref = tiktok.match(text)
    assert ref is not None
    assert ref.provider == "tiktok"
    assert ref.media_id == TIKTOK_ID
    assert ref.resolved is True


@pytest.mark.parametrize(
    ("text", "code", "expected_url"),
    [
        ("https://vm.tiktok.com/ZMhqAbCdE/", "ZMhqAbCdE", "https://vm.tiktok.com/ZMhqAbCdE"),
        ("https://vt.tiktok.com/ZSabcdef", "ZSabcdef", "https://vt.tiktok.com/ZSabcdef"),
        (
            "https://www.tiktok.com/t/ZTabcdef/",
            "ZTabcdef",
            "https://www.tiktok.com/t/ZTabcdef",
        ),
    ],
)
def test_tiktok_short_links_defer_the_id(
    tiktok: TikTokProvider, text: str, code: str, expected_url: str
) -> None:
    # Only TikTok can turn the code into an id, so the pipeline resolves it
    # from the metadata fetch it was going to make anyway.
    ref = tiktok.match(text)
    assert ref is not None
    assert ref.media_id == code
    assert ref.resolved is False
    assert ref.url == expected_url


@pytest.mark.parametrize(
    "text",
    [
        "",
        "hello",
        "https://nottiktok.com/@u/video/7123456789012345678",
        "https://www.tiktok.com/@someuser",
        "https://www.tiktok.com/",
    ],
)
def test_tiktok_rejects(tiktok: TikTokProvider, text: str) -> None:
    assert tiktok.match(text) is None


def test_tiktok_rewrites_onto_a_url_yt_dlp_accepts(tiktok: TikTokProvider) -> None:
    # yt-dlp handles neither m.tiktok.com nor the /v/ path.
    ref = tiktok.match("https://m.tiktok.com/v/7123456789012345678.html")
    assert ref is not None
    assert ref.url == f"https://www.tiktok.com/@/video/{TIKTOK_ID}"


def test_tiktok_photo_posts_are_rewritten_onto_the_video_path(tiktok: TikTokProvider) -> None:
    # There is no /photo/ extractor, but the id namespace is shared, so this
    # at least reaches the backing audio.
    ref = tiktok.match(f"https://www.tiktok.com/@u/photo/{TIKTOK_ID}")
    assert ref is not None
    assert ref.url == f"https://www.tiktok.com/@u/video/{TIKTOK_ID}"


def test_tiktok_keeps_the_username_in_the_canonical_url(tiktok: TikTokProvider) -> None:
    ref = tiktok.match(f"https://www.tiktok.com/@some.user_1/video/{TIKTOK_ID}?x=1")
    assert ref is not None
    assert ref.url == f"https://www.tiktok.com/@some.user_1/video/{TIKTOK_ID}"


def test_tiktok_has_no_chapters(tiktok: TikTokProvider) -> None:
    assert tiktok.supports_chapters is False


def test_tiktok_canonical_ids(tiktok: TikTokProvider) -> None:
    assert tiktok.is_canonical_id(TIKTOK_ID)
    assert not tiktok.is_canonical_id("ZMhqAbCdE")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("sunset timelapse #fyp #foryou #viral", "sunset timelapse"),
        ("no tags here", "no tags here"),
        ("  spaced   out  #tag ", "spaced out"),
        ("mid #tag sentence", "mid #tag sentence"),
        ("#fyp #viral", "#fyp #viral"),
        ("", ""),
    ],
)
def test_tiktok_strips_the_trailing_hashtag_pile(
    tiktok: TikTokProvider, raw: str, expected: str
) -> None:
    assert tiktok.clean_title(raw) == expected


# --- registry ----------------------------------------------------------------


def test_the_registry_routes_each_link_to_its_owner() -> None:
    registry = ProviderRegistry()
    assert registry.match(f"https://youtu.be/{VIDEO_ID}").provider == "youtube"  # pyright: ignore[reportOptionalMemberAccess]
    assert registry.match(f"https://vm.tiktok.com/{'Z' * 9}").provider == "tiktok"  # pyright: ignore[reportOptionalMemberAccess]
    assert registry.match("https://vimeo.com/12345") is None


def test_find_refs_keeps_order_of_appearance() -> None:
    registry = ProviderRegistry()
    refs = registry.find_refs(
        f"https://www.tiktok.com/@u/video/{TIKTOK_ID} and https://youtu.be/{VIDEO_ID}"
    )
    assert [(ref.provider, ref.media_id) for ref in refs] == [
        ("tiktok", TIKTOK_ID),
        ("youtube", VIDEO_ID),
    ]


def test_find_refs_dedupes_within_a_provider() -> None:
    registry = ProviderRegistry()
    refs = registry.find_refs(
        f"https://youtu.be/{VIDEO_ID} https://www.youtube.com/watch?v={VIDEO_ID}"
    )
    assert len(refs) == 1


def test_the_same_id_on_two_providers_is_not_deduped() -> None:
    # Deduping is by (provider, id), not id alone.
    registry = ProviderRegistry()
    refs = registry.find_refs("https://youtu.be/1234567890a https://vm.tiktok.com/1234567890a")
    assert len(refs) == 2


def test_enabled_restricts_the_registry() -> None:
    registry = ProviderRegistry.enabled(["youtube"])
    assert registry.names == ["youtube"]
    assert registry.match(f"https://www.tiktok.com/@u/video/{TIKTOK_ID}") is None


def test_enabled_is_case_and_space_insensitive() -> None:
    assert ProviderRegistry.enabled([" TikTok "]).names == ["tiktok"]


def test_an_empty_enabled_list_means_everything() -> None:
    assert ProviderRegistry.enabled([]).names == [p.name for p in ALL_PROVIDERS]


def test_an_unknown_provider_name_is_rejected() -> None:
    with pytest.raises(UnknownProviderError, match="vimeo"):
        ProviderRegistry.enabled(["vimeo"])


def test_get_and_find() -> None:
    registry = ProviderRegistry()
    assert registry.get("tiktok").label == "TikTok"
    assert registry.find("nope") is None
    with pytest.raises(UnknownProviderError):
        registry.get("nope")


def test_labels_are_human_readable() -> None:
    assert ProviderRegistry().labels == ["YouTube", "TikTok"]


def test_every_registered_provider_has_a_usable_name_and_label() -> None:
    for provider in ALL_PROVIDERS:
        assert provider.name and provider.name.islower()
        assert provider.label
    assert len({p.name for p in ALL_PROVIDERS}) == len(ALL_PROVIDERS)


def test_a_provider_name_with_the_key_separator_is_refused() -> None:
    # The cache key splits provider from id on the first hyphen.
    with pytest.raises(ValueError, match="lowercase alphanumeric"):

        class Bad(Provider):
            name = "not-ok"
            label = "Bad"

            def match(self, candidate: str):  # pragma: no cover - never constructed
                return None

            def is_canonical_id(self, media_id: str) -> bool:  # pragma: no cover
                return False


# --- shared helpers ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://example.com/a/b?x=1#frag", "https://example.com/a/b"),
        ("http://example.com/a/", "https://example.com/a"),
        ("example.com/a", "https://example.com/a"),
    ],
)
def test_strip_query(raw: str, expected: str) -> None:
    assert strip_query(raw) == expected
