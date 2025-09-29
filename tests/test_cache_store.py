from __future__ import annotations

from pathlib import Path

from chaptercut.cache.manifest import MANIFEST_NAME
from chaptercut.cache.store import CacheKey, CacheStore
from tests.conftest import make_manifest, populate_cache_dir

VIDEO_ID = "dQw4w9WgXcQ"
KEY = CacheKey("youtube", VIDEO_ID)


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
    result = cache.commit(KEY, source, manifest)  # pyright: ignore[reportArgumentType]

    assert result.directory == cache.path_for(KEY)
    assert not source.exists()
    assert len(result.track_paths) == 2
    assert all(path.is_file() for path in result.track_paths)
    assert result.cover_path is not None

    again = cache.get(KEY)
    assert again is not None
    assert again.manifest.video_id == VIDEO_ID


def test_commit_leaves_no_staging_directory(cache: CacheStore, tmp_path: Path) -> None:
    source, manifest = staged(tmp_path)
    cache.commit(KEY, source, manifest)  # pyright: ignore[reportArgumentType]
    assert not cache.tmp_path_for(KEY).exists()


def test_directory_without_a_manifest_is_not_cached(cache: CacheStore) -> None:
    # A directory created early with the manifest never written: every later
    # request would otherwise believe the video was cached.
    poisoned = cache.path_for(KEY)
    poisoned.mkdir(parents=True)
    (poisoned / "cover.jpg").write_bytes(b"jpeg")

    assert cache.get(KEY) is None
    assert not poisoned.exists()


def test_get_deletes_an_entry_with_an_invalid_manifest(cache: CacheStore) -> None:
    directory = cache.path_for(KEY)
    directory.mkdir(parents=True)
    (directory / MANIFEST_NAME).write_text("{broken", encoding="utf-8")
    assert cache.get(KEY) is None
    assert not directory.exists()


def test_sweep_removes_staging_leftovers(cache: CacheStore) -> None:
    leftover = cache.tmp_path_for(KEY)
    leftover.mkdir(parents=True)
    (leftover / "half.mp3").write_bytes(b"partial")

    assert cache.sweep() == 1
    assert not leftover.exists()


def test_sweep_keeps_valid_entries(cache: CacheStore) -> None:
    manifest = make_manifest(VIDEO_ID)
    populate_cache_dir(cache.path_for(KEY), manifest)
    assert cache.sweep() == 0
    assert cache.get(KEY) is not None


def test_sweep_removes_stray_files(cache: CacheStore) -> None:
    (cache.root / "stray.txt").write_text("junk", encoding="utf-8")
    assert cache.sweep() == 1
    assert not (cache.root / "stray.txt").exists()


def test_commit_replaces_an_existing_entry(cache: CacheStore, tmp_path: Path) -> None:
    populate_cache_dir(cache.path_for(KEY), make_manifest(VIDEO_ID, tracks=5))
    source, manifest = staged(tmp_path, tracks=2)
    result = cache.commit(KEY, source, manifest)  # pyright: ignore[reportArgumentType]
    assert len(result.track_paths) == 2
    assert len(list(cache.path_for(KEY).glob("*.mp3"))) == 2


def test_delete(cache: CacheStore) -> None:
    populate_cache_dir(cache.path_for(KEY), make_manifest(VIDEO_ID))
    assert cache.delete(KEY) is True
    assert cache.delete(KEY) is False
    assert cache.get(KEY) is None


def test_entries_and_usage(cache: CacheStore) -> None:
    for video_id in ("aaaaaaaaaaa", "bbbbbbbbbbb"):
        key = CacheKey("youtube", video_id)
        populate_cache_dir(cache.path_for(key), make_manifest(video_id))
    entries = cache.entries()
    assert [entry.key.media_id for entry in entries] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert cache.usage_bytes() > 0


def test_clear(cache: CacheStore) -> None:
    for video_id in ("aaaaaaaaaaa", "bbbbbbbbbbb"):
        key = CacheKey("youtube", video_id)
        populate_cache_dir(cache.path_for(key), make_manifest(video_id))
    assert cache.clear() == 2
    assert cache.entries() == []


def test_usage_of_a_missing_root_is_zero(tmp_path: Path) -> None:
    assert CacheStore(tmp_path / "nope").usage_bytes() == 0
    assert CacheStore(tmp_path / "nope").entries() == []
    assert CacheStore(tmp_path / "nope").sweep() == 0


def test_the_same_id_on_two_providers_does_not_collide(cache: CacheStore) -> None:
    shared = "abc123"
    youtube = CacheKey("youtube", shared)
    tiktok = CacheKey("tiktok", shared)
    populate_cache_dir(cache.path_for(youtube), make_manifest(shared, tracks=2))
    populate_cache_dir(cache.path_for(tiktok), make_manifest(shared, tracks=5, provider="tiktok"))

    assert cache.path_for(youtube) != cache.path_for(tiktok)
    yt = cache.get(youtube)
    tt = cache.get(tiktok)
    assert yt is not None and len(yt.manifest.tracks) == 2
    assert tt is not None and len(tt.manifest.tracks) == 5

    cache.delete(youtube)
    assert cache.get(youtube) is None
    assert cache.get(tiktok) is not None


def test_keys_round_trip_through_directory_names() -> None:
    # YouTube ids contain hyphens, so the split has to be from the left only.
    for key in (
        CacheKey("youtube", "dQw4-9WgXcQ"),
        CacheKey("youtube", "a-b-c-d"),
        CacheKey("tiktok", "7123456789012345678"),
    ):
        assert CacheKey.parse(key.dirname) == key


def test_unparseable_directory_names_are_rejected() -> None:
    assert CacheKey.parse("nodash") is None
    assert CacheKey.parse("-noprovider") is None
    assert CacheKey.parse("YouTube-x") is None
    assert CacheKey.parse("youtube-") is None


def test_an_unsafe_id_never_escapes_the_cache_directory(cache: CacheStore) -> None:
    # Ids can come from yt-dlp, not just from our own regexes.
    key = CacheKey("youtube", "../../etc/passwd")
    path = cache.path_for(key)
    assert path.parent == cache.root
    assert ".." not in path.name


def test_a_directory_that_is_not_a_key_is_swept(cache: CacheStore) -> None:
    stray = cache.root / "no-key-here-just-junk"
    stray.mkdir(parents=True)
    (stray / "x.mp3").write_bytes(b"x")
    assert cache.sweep() == 1
    assert not stray.exists()


def test_find_by_media_id_spans_providers(cache: CacheStore) -> None:
    shared = "abc123"
    populate_cache_dir(cache.path_for(CacheKey("youtube", shared)), make_manifest(shared))
    populate_cache_dir(
        cache.path_for(CacheKey("tiktok", shared)), make_manifest(shared, provider="tiktok")
    )
    found = cache.find_by_media_id(shared)
    assert {entry.key.provider for entry in found} == {"youtube", "tiktok"}
    assert cache.find_by_media_id("nothing") == []
