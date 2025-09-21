"""Filename sanitising, atomic publish, and retention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from filexchange.storage import Storage, new_token, safe_name


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    root = tmp_path / "uploads"
    root.mkdir()
    return Storage(root, timedelta(hours=24))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Some Album.zip", "Some Album.zip"),
        ("../../etc/passwd", "passwd"),
        (r"..\..\evil.exe", "evil.exe"),
        ("/absolute/path.mp3", "path.mp3"),
        ("weird:|name?.mp3", "weird name.mp3"),
        ("", "download"),
        ("...", "download"),
        ("CON.mp3", "download"),
        ("no-extension", "no-extension"),
    ],
)
def test_safe_name(raw: str, expected: str) -> None:
    assert safe_name(raw) == expected


def test_safe_name_keeps_the_extension_when_truncating() -> None:
    name = safe_name("x" * 400 + ".mp3")
    assert name.endswith(".mp3")
    assert len(name) <= 120


def test_tokens_are_long_and_distinct() -> None:
    tokens = {new_token() for _ in range(100)}
    assert len(tokens) == 100
    assert all(len(token) >= 40 for token in tokens)


def test_a_staged_upload_is_not_downloadable(storage: Storage) -> None:
    # Half-written files must never resolve, which is what commit-by-rename buys.
    token, destination = storage.begin("track.mp3")
    destination.write_bytes(b"partial")

    assert storage.resolve(token, "track.mp3") is None

    stored = storage.commit(token)
    assert storage.resolve(token, "track.mp3") == stored.path


def test_commit_moves_the_whole_entry(storage: Storage) -> None:
    token, destination = storage.begin("track.mp3")
    destination.write_bytes(b"data")
    stored = storage.commit(token)

    assert stored.size == 4
    assert not storage.staging_dir(token).exists()
    assert stored.path.parent == storage.entry_dir(token)


def test_committing_nothing_is_an_error(storage: Storage) -> None:
    token, _destination = storage.begin("track.mp3")
    with pytest.raises(FileNotFoundError):
        storage.commit(token)


def test_abort_clears_the_staging_directory(storage: Storage) -> None:
    token, destination = storage.begin("track.mp3")
    destination.write_bytes(b"partial")
    storage.abort(token)
    assert not storage.staging_dir(token).exists()


def test_resolve_rejects_a_mismatched_filename(storage: Storage) -> None:
    token, destination = storage.begin("track.mp3")
    destination.write_bytes(b"data")
    storage.commit(token)
    assert storage.resolve(token, "../track.mp3") is None
    assert storage.resolve("..", "track.mp3") is None


def test_sweep_removes_expired_entries(storage: Storage) -> None:
    token, destination = storage.begin("old.mp3")
    destination.write_bytes(b"data")
    storage.commit(token)

    later = datetime.now(UTC) + timedelta(hours=25)
    assert storage.sweep(now=later) == 1
    assert storage.resolve(token, "old.mp3") is None


def test_sweep_keeps_fresh_entries(storage: Storage) -> None:
    token, destination = storage.begin("new.mp3")
    destination.write_bytes(b"data")
    storage.commit(token)
    assert storage.sweep() == 0
    assert storage.resolve(token, "new.mp3") is not None


def test_sweep_clears_abandoned_staging(storage: Storage) -> None:
    token, destination = storage.begin("interrupted.mp3")
    destination.write_bytes(b"partial")
    assert storage.sweep() == 1
    assert not storage.staging_dir(token).exists()


def test_listing_and_purging(storage: Storage) -> None:
    for name in ("a.mp3", "b.mp3"):
        token, destination = storage.begin(name)
        destination.write_bytes(b"data")
        storage.commit(token)

    assert {entry.filename for entry in storage.list_entries()} == {"a.mp3", "b.mp3"}
    assert storage.count() == 2
    assert storage.usage_bytes() == 8
    assert storage.purge_all() == 2
    assert storage.list_entries() == []


def test_listing_skips_staging(storage: Storage) -> None:
    token, destination = storage.begin("staged.mp3")
    destination.write_bytes(b"partial")
    assert storage.list_entries() == []
    assert storage.count() == 0


def test_delete_rejects_a_malformed_token(storage: Storage) -> None:
    assert storage.delete("..") is False
    assert storage.delete("short") is False


def test_a_missing_root_is_handled(tmp_path: Path) -> None:
    empty = Storage(tmp_path / "nope", timedelta(hours=1))
    assert empty.sweep() == 0
    assert empty.count() == 0
    assert empty.usage_bytes() == 0
    assert empty.list_entries() == []
    assert empty.purge_all() == 0
