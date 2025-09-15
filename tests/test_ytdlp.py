"""Progress parsing, error classification, and metadata reading. No network."""

from __future__ import annotations

from pathlib import Path

import pytest

from chaptercut.pipeline.ytdlp import (
    PROGRESS_PREFIX,
    VideoInfo,
    Ytdlp,
    YtdlpError,
    YtdlpFactory,
    looks_like_bot_check,
    parse_progress_line,
)
from chaptercut.providers.tiktok import TikTokProvider
from chaptercut.providers.youtube import YouTubeProvider
from tests.conftest import ytdlp_info


def progress_line(downloaded: str, estimate: str, total: str, speed: str, eta: str) -> str:
    return f"{PROGRESS_PREFIX}{downloaded}/{estimate}/{total}/{speed}/{eta}"


def test_progress_line_is_parsed() -> None:
    progress = parse_progress_line(progress_line("500", "1000", "1000", "125000.5", "4"))
    assert progress is not None
    assert progress.downloaded_bytes == 500
    assert progress.total_bytes == 1000
    assert progress.speed == pytest.approx(125000.5)
    assert progress.eta == 4
    assert progress.pct == pytest.approx(50.0)


def test_estimate_is_used_when_the_total_is_unknown() -> None:
    progress = parse_progress_line(progress_line("250", "1000", "NA", "NA", "NA"))
    assert progress is not None
    assert progress.total_bytes == 1000
    assert progress.speed is None
    assert progress.pct == pytest.approx(25.0)


def test_percentage_is_none_without_a_total() -> None:
    progress = parse_progress_line(progress_line("250", "NA", "NA", "NA", "NA"))
    assert progress is not None
    assert progress.pct is None


def test_percentage_is_capped_at_one_hundred() -> None:
    progress = parse_progress_line(progress_line("2000", "NA", "1000", "NA", "NA"))
    assert progress is not None
    assert progress.pct == 100.0


@pytest.mark.parametrize(
    "line",
    [
        "",
        "[download] Destination: source.mp3",
        "[ExtractAudio] Destination: source.mp3",
        f"{PROGRESS_PREFIX}too/few/fields",
    ],
)
def test_unrelated_output_is_ignored(line: str) -> None:
    assert parse_progress_line(line) is None


@pytest.mark.parametrize(
    "stderr",
    [
        "ERROR: Sign in to confirm you're not a bot",
        "ERROR: [youtube] xyz: Please sign in",
        "The provided YouTube account cookies are no longer valid",
    ],
)
def test_bot_check_is_recognised(stderr: str) -> None:
    assert looks_like_bot_check(stderr)


def test_ordinary_errors_are_not_bot_checks() -> None:
    assert not looks_like_bot_check("ERROR: Video unavailable")


def test_video_info_reads_the_fields_the_pipeline_uses() -> None:
    info = VideoInfo(ytdlp_info())
    assert info.video_id == "dQw4w9WgXcQ"
    assert info.title == "Test Album"
    assert info.uploader == "Test Channel"
    assert info.duration == 120.0
    assert info.year == "2026"
    assert info.iso_upload_date == "2026-01-02"


def test_video_info_prefers_the_music_artist_over_the_channel() -> None:
    raw = ytdlp_info()
    raw["artist"] = "Real Artist"
    assert VideoInfo(raw).uploader == "Real Artist"


def test_video_info_falls_back_when_fields_are_missing() -> None:
    info = VideoInfo({})
    assert info.title == "Untitled"
    assert info.uploader == "Unknown"
    assert info.duration is None
    assert info.year == ""
    assert info.iso_upload_date == ""
    assert info.thumbnail_url is None


def test_largest_thumbnail_wins() -> None:
    assert VideoInfo(ytdlp_info()).thumbnail_url == "https://example.invalid/big.jpg"


def test_thumbnail_falls_back_to_the_scalar_field() -> None:
    raw = ytdlp_info()
    raw["thumbnails"] = []
    raw["thumbnail"] = "https://example.invalid/only.jpg"
    assert VideoInfo(raw).thumbnail_url == "https://example.invalid/only.jpg"


def test_cookies_and_extra_args_reach_the_command_line(tmp_path: Path) -> None:
    cookies = tmp_path / "cookies.txt"
    ytdlp = Ytdlp(cookies_file=cookies, extra_args=["--extractor-args", "youtube:player=mweb"])
    argv = ytdlp._base_argv()
    assert "--cookies" in argv
    assert str(cookies) in argv
    assert argv[-2:] == ["--extractor-args", "youtube:player=mweb"]
    assert "--no-playlist" in argv


def test_no_cookies_means_no_cookie_flag() -> None:
    assert "--cookies" not in Ytdlp()._base_argv()


async def test_a_missing_output_file_is_an_error(tmp_path: Path) -> None:
    from chaptercut.pipeline.ytdlp import _resolve_output

    with pytest.raises(YtdlpError, match="wrote no file"):
        _resolve_output(tmp_path / "source", ("mp3",))


async def test_the_written_extension_is_discovered(tmp_path: Path) -> None:
    from chaptercut.pipeline.ytdlp import _resolve_output

    (tmp_path / "source.mp3").write_bytes(b"audio")
    assert _resolve_output(tmp_path / "source", ("mp3",)) == tmp_path / "source.mp3"


async def test_version_of_a_missing_binary_is_unknown() -> None:
    assert await Ytdlp(binary="definitely-not-installed-xyz").version() == "unknown"


# --- per-provider configuration ----------------------------------------------


def test_the_factory_uses_a_provider_specific_cookie_jar(tmp_path: Path) -> None:
    shared = tmp_path / "cookies.txt"
    shared.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    tiktok_jar = tmp_path / "cookies-tiktok.txt"
    tiktok_jar.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    factory = YtdlpFactory(data_dir=tmp_path, default_cookies=shared)

    assert factory.cookies_for("tiktok") == tiktok_jar
    assert factory.cookies_for("youtube") == shared


def test_the_factory_falls_back_to_the_shared_jar(tmp_path: Path) -> None:
    shared = tmp_path / "cookies.txt"
    shared.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    factory = YtdlpFactory(data_dir=tmp_path, default_cookies=shared)
    assert factory.cookies_for("tiktok") == shared


def test_no_jar_anywhere_means_no_cookies(tmp_path: Path) -> None:
    factory = YtdlpFactory(data_dir=tmp_path, default_cookies=tmp_path / "missing.txt")
    assert factory.cookies_for("youtube") is None
    assert "--cookies" not in factory.for_provider(YouTubeProvider())._base_argv()


def test_provider_args_are_appended_after_the_operator_args(tmp_path: Path) -> None:
    class Fussy(YouTubeProvider):
        name = "fussy"
        ytdlp_args = ("--extractor-args", "site:mode=x")

    factory = YtdlpFactory(data_dir=tmp_path, extra_args=["--geo-bypass"])
    argv = factory.for_provider(Fussy())._base_argv()

    assert argv[-3:] == ["--geo-bypass", "--extractor-args", "site:mode=x"]


def test_each_provider_gets_its_own_client(tmp_path: Path) -> None:
    jar = tmp_path / "cookies-tiktok.txt"
    jar.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    factory = YtdlpFactory(data_dir=tmp_path)

    youtube = factory.for_provider(YouTubeProvider())
    tiktok = factory.for_provider(TikTokProvider())

    assert youtube.cookies_file is None
    assert tiktok.cookies_file == jar
