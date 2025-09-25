from __future__ import annotations

import json
from pathlib import Path

from chaptercut.cache.manifest import MANIFEST_NAME, SCHEMA_VERSION, read_manifest
from tests.conftest import make_manifest, populate_cache_dir


def test_round_trip(tmp_path: Path) -> None:
    manifest = make_manifest()
    populate_cache_dir(tmp_path, manifest)
    loaded = read_manifest(tmp_path)
    assert loaded is not None
    assert loaded.provider == "youtube"
    assert loaded.video_id == manifest.video_id
    assert loaded.downloaded_at == "2026-01-02T03:04:05Z"
    assert [track.file for track in loaded.tracks] == [t.file for t in manifest.tracks]


def test_schema_key_is_serialized_by_its_alias(tmp_path: Path) -> None:
    manifest = make_manifest()
    populate_cache_dir(tmp_path, manifest)
    raw = json.loads((tmp_path / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert raw["schema"] == SCHEMA_VERSION
    assert "schema_version" not in raw


def test_missing_file_is_not_cached(tmp_path: Path) -> None:
    assert read_manifest(tmp_path) is None


def test_unparseable_json_is_not_cached(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_NAME).write_text("{not json", encoding="utf-8")
    assert read_manifest(tmp_path) is None


def test_wrong_shape_is_not_cached(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_NAME).write_text('{"schema": 2}', encoding="utf-8")
    assert read_manifest(tmp_path) is None


def test_wrong_schema_version_is_not_cached(tmp_path: Path) -> None:
    manifest = make_manifest()
    populate_cache_dir(tmp_path, manifest)
    raw = json.loads((tmp_path / MANIFEST_NAME).read_text(encoding="utf-8"))
    raw["schema"] = 99
    (tmp_path / MANIFEST_NAME).write_text(json.dumps(raw), encoding="utf-8")
    assert read_manifest(tmp_path) is None


def test_manifest_without_tracks_is_not_cached(tmp_path: Path) -> None:
    manifest = make_manifest(tracks=0)
    manifest.write(tmp_path)
    assert read_manifest(tmp_path) is None


def test_manifest_pointing_at_a_missing_track_is_not_cached(tmp_path: Path) -> None:
    manifest = make_manifest()
    populate_cache_dir(tmp_path, manifest)
    (tmp_path / manifest.tracks[0].file).unlink()
    assert read_manifest(tmp_path) is None


def test_a_v1_manifest_without_a_provider_is_not_cached(tmp_path: Path) -> None:
    # Upgrade path: entries written before providers existed fail validation
    # and get swept, which only costs a re-download.
    manifest = make_manifest()
    populate_cache_dir(tmp_path, manifest)
    raw = json.loads((tmp_path / MANIFEST_NAME).read_text(encoding="utf-8"))
    raw["schema"] = 1
    del raw["provider"]
    (tmp_path / MANIFEST_NAME).write_text(json.dumps(raw), encoding="utf-8")
    assert read_manifest(tmp_path) is None
