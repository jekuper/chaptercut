from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from chaptercut.pipeline.package import make_zip_sync
from chaptercut.pipeline.tagging import apply_mtimes

DOWNLOADED_AT = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def tracks(tmp_path: Path) -> list[Path]:
    paths = []
    for index in range(1, 4):
        path = tmp_path / f"{index:02d} - Track {index}.mp3"
        path.write_bytes(b"audio" * 100)
        paths.append(path)
    (tmp_path / "cover.jpg").write_bytes(b"jpeg")
    paths.append(tmp_path / "cover.jpg")
    apply_mtimes(paths, DOWNLOADED_AT)
    return paths


def test_files_land_under_the_inner_folder(tracks: list[Path], tmp_path: Path) -> None:
    archive_path = make_zip_sync(tracks, tmp_path / "out.zip", "Test Album")
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
    assert names == [
        "Test Album/01 - Track 1.mp3",
        "Test Album/02 - Track 2.mp3",
        "Test Album/03 - Track 3.mp3",
        "Test Album/cover.jpg",
    ]


def test_entries_are_stored_not_deflated(tracks: list[Path], tmp_path: Path) -> None:
    archive_path = make_zip_sync(tracks, tmp_path / "out.zip", "Album")
    with zipfile.ZipFile(archive_path) as archive:
        assert all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist())


def test_contents_survive_the_round_trip(tracks: list[Path], tmp_path: Path) -> None:
    archive_path = make_zip_sync(tracks, tmp_path / "out.zip", "Album")
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.read("Album/01 - Track 1.mp3") == tracks[0].read_bytes()
        assert archive.testzip() is None


def test_mtimes_are_preserved_in_order(tracks: list[Path], tmp_path: Path) -> None:
    archive_path = make_zip_sync(tracks, tmp_path / "out.zip", "Album")
    with zipfile.ZipFile(archive_path) as archive:
        stamps = [info.date_time for info in archive.infolist()]
    assert stamps == sorted(stamps)
    assert stamps[0][:3] == (2026, 8, 23)


def test_missing_files_are_skipped(tracks: list[Path], tmp_path: Path) -> None:
    archive_path = make_zip_sync([*tracks, tmp_path / "gone.mp3"], tmp_path / "out.zip", "Album")
    with zipfile.ZipFile(archive_path) as archive:
        assert len(archive.namelist()) == len(tracks)


def test_an_empty_zip_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty zip"):
        make_zip_sync([], tmp_path / "out.zip", "Album")


def test_the_destination_directory_is_created(tracks: list[Path], tmp_path: Path) -> None:
    destination = tmp_path / "nested" / "deeper" / "out.zip"
    assert make_zip_sync(tracks, destination, "Album").is_file()
