"""On-disk cache of processed audio, keyed by video id.

The invariant that the predecessor broke: a directory counts as cached only if
it holds a valid manifest, and it only ever appears under its final name via an
atomic rename. A crash mid-write leaves a `.tmp` directory, which the startup
sweep removes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from chaptercut.cache.manifest import MANIFEST_NAME, Manifest, read_manifest
from chaptercut.logging import get_logger
from chaptercut.util.paths import dir_size, rmtree_quiet

log = get_logger(__name__)

TMP_SUFFIX = ".tmp"


@dataclass(frozen=True, slots=True)
class CachedResult:
    video_id: str
    directory: Path
    manifest: Manifest

    @property
    def track_paths(self) -> list[Path]:
        return [self.directory / track.file for track in self.manifest.tracks]

    @property
    def cover_path(self) -> Path | None:
        if not self.manifest.cover:
            return None
        path = self.directory / self.manifest.cover
        return path if path.is_file() else None

    @property
    def size_bytes(self) -> int:
        return dir_size(self.directory)


class CacheStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, video_id: str) -> Path:
        return self.root / video_id

    def tmp_path_for(self, video_id: str) -> Path:
        return self.root / f"{video_id}{TMP_SUFFIX}"

    def get(self, video_id: str) -> CachedResult | None:
        directory = self.path_for(video_id)
        if not directory.is_dir():
            return None
        manifest = read_manifest(directory)
        if manifest is None:
            log.warning("cache.invalid_entry", video_id=video_id)
            rmtree_quiet(directory)
            return None
        return CachedResult(video_id=video_id, directory=directory, manifest=manifest)

    def commit(self, video_id: str, source_dir: Path, manifest: Manifest) -> CachedResult:
        """Publish `source_dir` as the cache entry for `video_id`, atomically."""
        self.root.mkdir(parents=True, exist_ok=True)
        staging = self.tmp_path_for(video_id)
        rmtree_quiet(staging)
        # The work dir and the cache live on the same volume, so this is a rename.
        os.replace(source_dir, staging)
        manifest.write(staging)

        final = self.path_for(video_id)
        rmtree_quiet(final)
        os.replace(staging, final)
        log.info("cache.committed", video_id=video_id, tracks=len(manifest.tracks))

        result = self.get(video_id)
        if result is None:  # pragma: no cover - would mean the manifest we just wrote is bad
            raise RuntimeError(f"cache entry for {video_id} is invalid right after commit")
        return result

    def delete(self, video_id: str) -> bool:
        directory = self.path_for(video_id)
        existed = directory.exists()
        rmtree_quiet(directory)
        rmtree_quiet(self.tmp_path_for(video_id))
        return existed

    def sweep(self) -> int:
        """Remove staging leftovers and any directory without a valid manifest."""
        if not self.root.is_dir():
            return 0
        removed = 0
        for entry in self.root.iterdir():
            if entry.name.endswith(TMP_SUFFIX) or not entry.is_dir():
                rmtree_quiet(entry) if entry.is_dir() else entry.unlink(missing_ok=True)
                removed += 1
                continue
            if read_manifest(entry) is None:
                log.warning("cache.sweep_invalid", entry=entry.name)
                rmtree_quiet(entry)
                removed += 1
        return removed

    def entries(self) -> list[tuple[str, Manifest]]:
        if not self.root.is_dir():
            return []
        found: list[tuple[str, Manifest]] = []
        for entry in sorted(self.root.iterdir()):
            if not entry.is_dir() or not (entry / MANIFEST_NAME).is_file():
                continue
            manifest = read_manifest(entry)
            if manifest is not None:
                found.append((entry.name, manifest))
        return found

    def usage_bytes(self) -> int:
        return dir_size(self.root)

    def clear(self) -> int:
        count = 0
        for video_id, _ in self.entries():
            self.delete(video_id)
            count += 1
        self.sweep()
        return count
