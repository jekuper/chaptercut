from __future__ import annotations

import pytest

from chaptercut.pipeline.chapters import chapters_from_info
from tests.conftest import ytdlp_info


def test_no_chapters_yields_one_whole_video_track() -> None:
    tracks = chapters_from_info(ytdlp_info(duration=212.0))
    assert len(tracks) == 1
    assert tracks[0].index == 1
    assert tracks[0].title == "Test Album"
    assert (tracks[0].start, tracks[0].end) == (0.0, 212.0)


def test_chapters_become_tracks_in_order() -> None:
    info = ytdlp_info(
        chapters=[
            {"start_time": 0, "end_time": 60, "title": "Intro"},
            {"start_time": 60, "end_time": 120, "title": "Middle"},
        ]
    )
    tracks = chapters_from_info(info)
    assert [track.title for track in tracks] == ["Intro", "Middle"]
    assert [track.index for track in tracks] == [1, 2]


def test_unsorted_chapters_are_sorted_by_start() -> None:
    info = ytdlp_info(
        chapters=[
            {"start_time": 60, "end_time": 120, "title": "Second"},
            {"start_time": 0, "end_time": 60, "title": "First"},
        ]
    )
    assert [track.title for track in chapters_from_info(info)] == ["First", "Second"]


def test_missing_last_end_time_uses_duration() -> None:
    info = ytdlp_info(
        chapters=[
            {"start_time": 0, "end_time": 60, "title": "Intro"},
            {"start_time": 60, "title": "Outro"},
        ],
        duration=200.0,
    )
    tracks = chapters_from_info(info)
    assert tracks[-1].end == 200.0


def test_missing_middle_end_time_uses_next_start() -> None:
    info = ytdlp_info(
        chapters=[
            {"start_time": 0, "title": "One"},
            {"start_time": 45, "end_time": 120, "title": "Two"},
        ]
    )
    tracks = chapters_from_info(info)
    assert tracks[0].end == 45


def test_probe_duration_overrides_reported_duration() -> None:
    info = ytdlp_info(
        chapters=[{"start_time": 0, "title": "Only"}],
        duration=999.0,
    )
    tracks = chapters_from_info(info, duration=100.0)
    assert tracks[0].end == 100.0


def test_chapter_beyond_duration_is_clamped() -> None:
    info = ytdlp_info(chapters=[{"start_time": 0, "end_time": 500, "title": "Long"}])
    tracks = chapters_from_info(info, duration=120.0)
    assert tracks[0].end == 120.0


def test_zero_length_chapters_are_dropped_and_reindexed() -> None:
    info = ytdlp_info(
        chapters=[
            {"start_time": 0, "end_time": 60, "title": "Real"},
            {"start_time": 60, "end_time": 60, "title": "Empty"},
            {"start_time": 60, "end_time": 120, "title": "Also Real"},
        ]
    )
    tracks = chapters_from_info(info)
    assert [track.title for track in tracks] == ["Real", "Also Real"]
    assert [track.index for track in tracks] == [1, 2]


def test_untitled_chapter_gets_a_position_label() -> None:
    info = ytdlp_info(chapters=[{"start_time": 0, "end_time": 60, "title": ""}])
    assert chapters_from_info(info)[0].title == "Chapter 1"


def test_single_chapter_with_bogus_end_spans_the_video() -> None:
    # One marker at 0:00 means "the whole video, called this".
    info = ytdlp_info(chapters=[{"start_time": 0, "end_time": 0, "title": "Whole Thing"}])
    tracks = chapters_from_info(info, duration=120.0)
    assert len(tracks) == 1
    assert tracks[0].title == "Whole Thing"
    assert (tracks[0].start, tracks[0].end) == (0.0, 120.0)


def test_all_chapters_too_short_falls_back_to_whole_video() -> None:
    info = ytdlp_info(
        chapters=[
            {"start_time": 0, "end_time": 0.2, "title": "A"},
            {"start_time": 0.2, "end_time": 0.3, "title": "B"},
        ]
    )
    tracks = chapters_from_info(info, duration=0.3)
    assert len(tracks) == 1
    assert tracks[0].title == "Test Album"


def test_no_duration_and_no_chapters_is_an_error() -> None:
    info = ytdlp_info()
    info["duration"] = None
    with pytest.raises(ValueError, match="duration"):
        chapters_from_info(info)


def test_track_duration_property() -> None:
    info = ytdlp_info(chapters=[{"start_time": 10, "end_time": 70, "title": "One"}])
    assert chapters_from_info(info)[0].duration == 60.0
