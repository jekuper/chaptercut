from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from chaptercut.settings import Settings, load_settings
from tests.conftest import FAKE_TOKEN


def build(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "bot_token": FAKE_TOKEN,
        "allowed_user_ids": "111,222",
        "admin_user_ids": "111",
    }
    values.update(overrides)
    return Settings(**values)  # pyright: ignore[reportArgumentType]


def test_user_ids_parse_from_a_comma_separated_string() -> None:
    settings = build(allowed_user_ids=" 111, 222 ,333 ")
    assert settings.allowed_user_ids == [111, 222, 333]


def test_token_shape_is_validated() -> None:
    with pytest.raises(ValidationError, match="bot token"):
        build(bot_token="not-a-token")


def test_at_least_one_allowed_user_is_required() -> None:
    with pytest.raises(ValidationError):
        build(allowed_user_ids="", admin_user_ids="")


def test_admins_must_be_a_subset_of_allowed_users() -> None:
    with pytest.raises(ValidationError, match="admin user ids"):
        build(allowed_user_ids="111", admin_user_ids="999")


def test_local_mode_is_rejected_against_the_cloud_server() -> None:
    with pytest.raises(ValidationError, match="cloud"):
        build(bot_api_url="https://api.telegram.org", bot_api_local=True)


def test_cloud_server_is_allowed_when_local_is_off() -> None:
    settings = build(bot_api_url="https://api.telegram.org", bot_api_local=False)
    assert settings.bot_api_local is False


def test_bitrate_shape_is_validated() -> None:
    assert build(audio_bitrate="192k").audio_bitrate == "192K"
    assert build(audio_bitrate="").audio_bitrate == ""
    with pytest.raises(ValidationError, match="bitrate"):
        build(audio_bitrate="loud")


def test_unknown_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError, match="log level"):
        build(log_level="chatty")


def test_worker_concurrency_must_be_positive() -> None:
    with pytest.raises(ValidationError, match="concurrency"):
        build(worker_concurrency=0)


def test_extra_ytdlp_args_are_shell_split() -> None:
    settings = build(ytdlp_extra_args='--extractor-args "youtube:player_client=mweb"')
    assert settings.ytdlp_extra_arg_list == [
        "--extractor-args",
        "youtube:player_client=mweb",
    ]


def test_path_layout(tmp_path: Path) -> None:
    settings = build(data_dir=tmp_path)
    assert settings.cache_dir == tmp_path / "cache"
    assert settings.work_dir == tmp_path / "work"
    assert settings.db_path == tmp_path / "chaptercut.db"
    assert settings.heartbeat_path == tmp_path / "heartbeat"


def test_ensure_dirs_creates_the_layout(tmp_path: Path) -> None:
    settings = build(data_dir=tmp_path / "fresh")
    settings.ensure_dirs()
    assert settings.cache_dir.is_dir()
    assert settings.work_dir.is_dir()


def test_allow_and_admin_checks() -> None:
    settings = build()
    assert settings.is_allowed(111) and settings.is_allowed(222)
    assert not settings.is_allowed(333)
    assert settings.is_admin(111)
    assert not settings.is_admin(222)


def test_missing_cookie_file_reads_as_absent(tmp_path: Path) -> None:
    assert build(cookies_file=tmp_path / "nope.txt").active_cookies_file() is None
    assert build().active_cookies_file() is None


def test_present_cookie_file_is_returned(tmp_path: Path) -> None:
    path = tmp_path / "cookies.txt"
    path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    assert build(cookies_file=path).active_cookies_file() == path


def test_token_is_not_in_the_repr() -> None:
    assert FAKE_TOKEN not in repr(build())


def test_ids_load_from_the_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # pydantic-settings json-decodes list fields by default, which turned a
    # single id like CC_ALLOWED_USER_IDS=111 into an int and failed validation.
    monkeypatch.setenv("CC_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("CC_ALLOWED_USER_IDS", "111")
    monkeypatch.setenv("CC_ADMIN_USER_IDS", "111")
    monkeypatch.setenv("CC_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    settings = load_settings()

    assert settings.allowed_user_ids == [111]
    assert settings.admin_user_ids == [111]


def test_multiple_ids_load_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CC_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("CC_ALLOWED_USER_IDS", "111,222,333")
    monkeypatch.setenv("CC_ADMIN_USER_IDS", "111")
    monkeypatch.setenv("CC_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    assert load_settings().allowed_user_ids == [111, 222, 333]


def test_an_empty_admin_list_loads_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CC_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("CC_ALLOWED_USER_IDS", "111")
    monkeypatch.setenv("CC_ADMIN_USER_IDS", "")
    monkeypatch.setenv("CC_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    assert load_settings().admin_user_ids == []


def test_a_single_int_is_accepted_directly() -> None:
    assert build(allowed_user_ids=111, admin_user_ids=111).allowed_user_ids == [111]
