"""On-disk cache of processed audio, keyed by provider and media id.

The invariant that the predecessor broke: a directory counts as cached only if
it holds a valid manifest, and it only ever appears under its final name via an
atomic rename. A crash mid-write leaves a `.tmp` directory, which the startup
sweep removes.

Keys are namespaced by provider because ids are only unique within a site: an
11-character YouTube id and a 19-digit TikTok id will not collide today, but
nothing guarantees that for the next provider added.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from chaptercut.cache.manifest import MANIFEST_NAME, Manifest, read_manifest
from chaptercut.logging import get_logger
from chaptercut.providers.base import PROVIDER_NAME_RE, SAFE_ID_RE
from chaptercut.util.paths import dir_size, rmtree_quiet

log = get_logger(__name__)

TMP_SUFFIX = ".tmp"
SEPARATOR = "-"


def _safe_id(media_id: str) -> str:
    """A directory-safe form of `media_id`.

    Ids reaching here can come from yt-dlp, not just from our own regexes, so
    anything outside the safe set is replaced by a digest rather than trusted
    as a path component.
    """
    if SAFE_ID_RE.match(media_id):
        return media_id
    return hashlib.sha1(media_id.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class CacheKey:
    provider: str
    media_id: str

    @property
    def dirname(self) -> str:
        return f"{self.provider}{SEPARATOR}{_safe_id(self.media_id)}"

    def __str__(self) -> str:
        return f"{self.provider}:{self.media_id}"

    @classmethod
    def parse(cls, dirname: str) -> CacheKey | None:
        """Read a key back from a directory name, or None if it is not one.

        The provider slug never contains the separator, so a single split from
        the left is unambiguous even though media ids may contain hyphens.
        """
        provider, separator, media_id = dirname.partition(SEPARATOR)
        if not separator or not media_id or not PROVIDER_NAME_RE.match(provider):
            return None
        return cls(provider=provider, media_id=media_id)


@dataclass(frozen=True, slots=True)
class CachedResult:
    key: CacheKey
    directory: Path
    manifest: Manifest

    @property
    def provider(self) -> str:
        return self.manifest.provider

    @property
    def video_id(self) -> str:
        return self.manifest.video_id

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

    def path_for(self, key: CacheKey) -> Path:
        return self.root / key.dirname

    def tmp_path_for(self, key: CacheKey) -> Path:
        return self.root / f"{key.dirname}{TMP_SUFFIX}"

    def get(self, key: CacheKey) -> CachedResult | None:
        directory = self.path_for(key)
        if not directory.is_dir():
            return None
        manifest = read_manifest(directory)
        if manifest is None:
            log.warning("cache.invalid_entry", key=str(key))
            rmtree_quiet(directory)
            return None
        return CachedResult(key=key, directory=directory, manifest=manifest)

    def commit(self, key: CacheKey, source_dir: Path, manifest: Manifest) -> CachedResult:
        """Publish `source_dir` as the cache entry for `key`, atomically."""
        self.root.mkdir(parents=True, exist_ok=True)
        staging = self.tmp_path_for(key)
        rmtree_quiet(staging)
        # The work dir and the cache live on the same volume, so this is a rename.
        os.replace(source_dir, staging)
        manifest.write(staging)

        final = self.path_for(key)
        rmtree_quiet(final)
        os.replace(staging, final)
        log.info("cache.committed", key=str(key), tracks=len(manifest.tracks))

        result = self.get(key)
        if result is None:  # pragma: no cover - would mean the manifest we just wrote is bad
            raise RuntimeError(f"cache entry for {key} is invalid right after commit")
        return result

    def delete(self, key: CacheKey) -> bool:
        directory = self.path_for(key)
        existed = directory.exists()
        rmtree_quiet(directory)
        rmtree_quiet(self.tmp_path_for(key))
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
            if CacheKey.parse(entry.name) is None or read_manifest(entry) is None:
                log.warning("cache.sweep_invalid", entry=entry.name)
                rmtree_quiet(entry)
                removed += 1
        return removed

    def entries(self) -> list[CachedResult]:
        if not self.root.is_dir():
            return []
        found: list[CachedResult] = []
        for entry in sorted(self.root.iterdir()):
            if not entry.is_dir() or not (entry / MANIFEST_NAME).is_file():
                continue
            key = CacheKey.parse(entry.name)
            manifest = read_manifest(entry) if key is not None else None
            if key is not None and manifest is not None:
                found.append(CachedResult(key=key, directory=entry, manifest=manifest))
        return found

    def find_by_media_id(self, media_id: str) -> list[CachedResult]:
        """Every cached entry with this id, across providers.

        Used by `/cache <id>` when the operator pastes a bare id and there is
        no URL to say which site it came from.
        """
        return [entry for entry in self.entries() if entry.manifest.video_id == media_id]

    def usage_bytes(self) -> int:
        return dir_size(self.root)

    def clear(self) -> int:
        count = 0
        for entry in self.entries():
            self.delete(entry.key)
            count += 1
        self.sweep()
        return count
