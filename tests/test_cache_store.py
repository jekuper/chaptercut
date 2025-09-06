from __future__ import annotations

from pathlib import Path

from chaptercut.cache.manifest import MANIFEST_NAME
from chaptercut.cache.store import CacheStore
from tests.conftest import make_manifest, populate_cache_dir

VIDEO_ID = "dQw4w9WgXcQ"


def staged(tmp_path: Path, tracks: int = 2) -> tuple[Path, object]:
    manifest = make_manifest(VIDEO_ID, tracks=tracks)
    source = tmp_path / "work" / "out"
    source.mkdir(parents=True)
    for track in manifest.tracks:
        (source / track.file).write_bytes(b"audio")
    (source / "cover.jpg").write_bytes(b"jpeg")
    return source, manifest


def test_commit_then_get(cache: CacheStore, tmp_path: Path) -> None:
    source, manifest = staged(tmp_path)
    result = cache.commit(VIDEO_ID, source, manifest)  # pyright: ignore[reportArgumentType]

    assert result.directory == cache.path_for(VIDEO_ID)
    assert not source.exists()
    assert len(result.track_paths) == 2
    assert all(path.is_file() for path in result.track_paths)
    assert result.cover_path is not None

    again = cache.get(VIDEO_ID)
    assert again is not None
    assert again.manifest.video_id == VIDEO_ID


def test_commit_leaves_no_staging_directory(cache: CacheStore, tmp_path: Path) -> None:
    source, manifest = staged(tmp_path)
    cache.commit(VIDEO_ID, source, manifest)  # pyright: ignore[reportArgumentType]
    assert not cache.tmp_path_for(VIDEO_ID).exists()


def test_directory_without_a_manifest_is_not_cached(cache: CacheStore) -> None:
    # Exactly the predecessor's failure: a directory created early, manifest
    # never written, and every later request believing it was cached.
    poisoned = cache.path_for(VIDEO_ID)
    poisoned.mkdir(parents=True)
    (poisoned / "cover.jpg").write_bytes(b"jpeg")

    assert cache.get(VIDEO_ID) is None
    assert not poisoned.exists()


def test_get_deletes_an_entry_with_an_invalid_manifest(cache: CacheStore) -> None:
    directory = cache.path_for(VIDEO_ID)
    directory.mkdir(parents=True)
    (directory / MANIFEST_NAME).write_text("{broken", encoding="utf-8")
    assert cache.get(VIDEO_ID) is None
    assert not directory.exists()


def test_sweep_removes_staging_leftovers(cache: CacheStore) -> None:
    leftover = cache.tmp_path_for(VIDEO_ID)
    leftover.mkdir(parents=True)
    (leftover / "half.mp3").write_bytes(b"partial")

    assert cache.sweep() == 1
    assert not leftover.exists()


def test_sweep_keeps_valid_entries(cache: CacheStore) -> None:
    manifest = make_manifest(VIDEO_ID)
    populate_cache_dir(cache.path_for(VIDEO_ID), manifest)
    assert cache.sweep() == 0
    assert cache.get(VIDEO_ID) is not None


def test_sweep_removes_stray_files(cache: CacheStore) -> None:
    (cache.root / "stray.txt").write_text("junk", encoding="utf-8")
    assert cache.sweep() == 1
    assert not (cache.root / "stray.txt").exists()


def test_commit_replaces_an_existing_entry(cache: CacheStore, tmp_path: Path) -> None:
    populate_cache_dir(cache.path_for(VIDEO_ID), make_manifest(VIDEO_ID, tracks=5))
    source, manifest = staged(tmp_path, tracks=2)
    result = cache.commit(VIDEO_ID, source, manifest)  # pyright: ignore[reportArgumentType]
    assert len(result.track_paths) == 2
    assert len(list(cache.path_for(VIDEO_ID).glob("*.mp3"))) == 2


def test_delete(cache: CacheStore) -> None:
    populate_cache_dir(cache.path_for(VIDEO_ID), make_manifest(VIDEO_ID))
    assert cache.delete(VIDEO_ID) is True
    assert cache.delete(VIDEO_ID) is False
    assert cache.get(VIDEO_ID) is None


def test_entries_and_usage(cache: CacheStore) -> None:
    populate_cache_dir(cache.path_for("aaaaaaaaaaa"), make_manifest("aaaaaaaaaaa"))
    populate_cache_dir(cache.path_for("bbbbbbbbbbb"), make_manifest("bbbbbbbbbbb"))
    entries = cache.entries()
    assert [video_id for video_id, _ in entries] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert cache.usage_bytes() > 0


def test_clear(cache: CacheStore) -> None:
    populate_cache_dir(cache.path_for("aaaaaaaaaaa"), make_manifest("aaaaaaaaaaa"))
    populate_cache_dir(cache.path_for("bbbbbbbbbbb"), make_manifest("bbbbbbbbbbb"))
    assert cache.clear() == 2
    assert cache.entries() == []


def test_usage_of_a_missing_root_is_zero(tmp_path: Path) -> None:
    assert CacheStore(tmp_path / "nope").usage_bytes() == 0
    assert CacheStore(tmp_path / "nope").entries() == []
    assert CacheStore(tmp_path / "nope").sweep() == 0
