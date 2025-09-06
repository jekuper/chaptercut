from __future__ import annotations

import pytest

from chaptercut.util.youtube import (
    canonical_url,
    extract_video_id,
    find_video_ids,
    is_youtube_url,
)

VIDEO_ID = "dQw4w9WgXcQ"


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
def test_recognised_forms(text: str) -> None:
    assert extract_video_id(text) == VIDEO_ID
    assert is_youtube_url(text)


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
def test_rejected_forms(text: str) -> None:
    assert extract_video_id(text) is None
    assert not is_youtube_url(text)


def test_ids_are_deduped_and_ordered() -> None:
    text = (
        "https://youtu.be/aaaaaaaaaaa and https://youtu.be/bbbbbbbbbbb "
        "and https://www.youtube.com/watch?v=aaaaaaaaaaa"
    )
    assert find_video_ids(text) == ["aaaaaaaaaaa", "bbbbbbbbbbb"]


def test_canonical_url_drops_tracking_parameters() -> None:
    video_id = extract_video_id("https://youtu.be/dQw4w9WgXcQ?si=trackingtoken&t=90")
    assert video_id is not None
    assert canonical_url(video_id) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
