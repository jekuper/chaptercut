from __future__ import annotations

from typing import Any

from chaptercut.pipeline.formats import (
    MAX_OPTIONS,
    find_option,
    options_from_json,
    options_to_json,
    select_video_formats,
)


def video_format(
    format_id: str,
    height: int,
    ext: str = "mp4",
    filesize: int | None = None,
    acodec: str = "none",
    tbr: int = 1000,
) -> dict[str, Any]:
    return {
        "format_id": format_id,
        "height": height,
        "ext": ext,
        "vcodec": "avc1",
        "acodec": acodec,
        "filesize": filesize,
        "tbr": tbr,
    }


def audio_format(format_id: str, filesize: int) -> dict[str, Any]:
    return {
        "format_id": format_id,
        "ext": "m4a",
        "vcodec": "none",
        "acodec": "mp4a",
        "filesize": filesize,
    }


def test_audio_only_formats_are_excluded() -> None:
    options = select_video_formats({"formats": [audio_format("140", 1000)]})
    assert options == []


def test_one_option_per_height_sorted_descending() -> None:
    info = {
        "formats": [
            video_format("a", 720),
            video_format("b", 1080),
            video_format("c", 360),
            video_format("d", 1080, ext="webm"),
        ]
    }
    options = select_video_formats(info)
    assert [option.height for option in options] == [1080, 720, 360]


def test_mp4_wins_over_webm_at_the_same_height() -> None:
    info = {
        "formats": [
            video_format("webm-one", 1080, ext="webm", tbr=5000),
            video_format("mp4-one", 1080, ext="mp4", tbr=1000),
        ]
    }
    assert select_video_formats(info)[0].format_id == "mp4-one"


def test_size_estimate_adds_the_audio_stream_for_video_only_formats() -> None:
    info = {
        "formats": [
            video_format("v", 1080, filesize=100),
            audio_format("a", 25),
        ]
    }
    option = select_video_formats(info)[0]
    assert option.needs_audio is True
    assert option.size_bytes == 125


def test_size_estimate_left_alone_when_the_format_has_audio() -> None:
    info = {
        "formats": [
            video_format("v", 1080, filesize=100, acodec="mp4a"),
            audio_format("a", 25),
        ]
    }
    option = select_video_formats(info)[0]
    assert option.needs_audio is False
    assert option.size_bytes == 100


def test_filesize_approx_is_used_as_a_fallback() -> None:
    fmt = video_format("v", 720)
    fmt["filesize_approx"] = 4242
    assert select_video_formats({"formats": [fmt]})[0].size_bytes == 4242


def test_unknown_size_stays_none() -> None:
    assert select_video_formats({"formats": [video_format("v", 720)]})[0].size_bytes is None


def test_at_most_six_options_keeping_the_tallest() -> None:
    heights = [144, 240, 360, 480, 720, 1080, 1440, 2160]
    info = {"formats": [video_format(str(h), h) for h in heights]}
    options = select_video_formats(info)
    assert len(options) == MAX_OPTIONS
    assert [option.height for option in options] == [2160, 1440, 1080, 720, 480, 360]


def test_formats_without_height_or_id_are_skipped() -> None:
    info = {
        "formats": [
            {"format_id": "no-height", "vcodec": "avc1"},
            {"height": 720, "vcodec": "avc1"},
            video_format("good", 480),
        ]
    }
    assert [option.format_id for option in select_video_formats(info)] == ["good"]


def test_missing_or_malformed_formats_key() -> None:
    assert select_video_formats({}) == []
    assert select_video_formats({"formats": "nonsense"}) == []
    assert select_video_formats({"formats": [None, 5, "x"]}) == []


def test_json_round_trip_preserves_options() -> None:
    info = {"formats": [video_format("v", 1080, filesize=100), audio_format("a", 25)]}
    options = select_video_formats(info)
    assert options_from_json(options_to_json(options)) == options


def test_find_option() -> None:
    options = select_video_formats({"formats": [video_format("v", 720)]})
    assert find_option(options, "v") is options[0]
    assert find_option(options, "missing") is None
