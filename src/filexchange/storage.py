"""Where uploads live on disk, and the rules that keep them there.

Two properties matter. Nothing a caller sends may ever be used to build a path
outside the uploads root, and a download link must never resolve to a file that
is still being written.
"""

from __future__ import annotations

import re
import secrets
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

# 32 bytes of urlsafe randomness: the link is the capability, so it has to be
# unguessable on its own.
TOKEN_BYTES = 32
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")

TMP_SUFFIX = ".tmp"
MAX_FILENAME_LEN = 120

# Kept deliberately narrow. The name ends up in a URL and in a
# Content-Disposition header, and neither is worth getting clever about.
_ALLOWED = re.compile(r"[^A-Za-z0-9 ._-]")
_WHITESPACE = re.compile(r"\s+")
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def safe_name(filename: str, fallback: str = "download") -> str:
    """A filename that is safe as a single path segment and as a URL segment.

    Any directory component is discarded rather than sanitised, so `../../x`
    becomes `x` instead of something that still resembles a traversal.
    """
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    name = _ALLOWED.sub(" ", name)
    name = _WHITESPACE.sub(" ", name).strip(" .")

    stem, dot, suffix = name.rpartition(".")
    if dot and 0 < len(suffix) <= 8:
        stem = stem[: MAX_FILENAME_LEN - len(suffix) - 1].strip(" .")
        name = f"{stem}.{suffix}" if stem else ""
    else:
        name = name[:MAX_FILENAME_LEN].strip(" .")

    if not name or name.rpartition(".")[0].upper() in _RESERVED or name.upper() in _RESERVED:
        return fallback
    return name


@dataclass(frozen=True, slots=True)
class StoredFile:
    token: str
    filename: str
    path: Path
    size: int
    expires_at: datetime


class Storage:
    def __init__(self, root: Path, retention: timedelta) -> None:
        self.root = root
        self.retention = retention

    # --- paths ------------------------------------------------------------

    def entry_dir(self, token: str) -> Path:
        return self.root / token

    def staging_dir(self, token: str) -> Path:
        return self.root / f"{token}{TMP_SUFFIX}"

    def resolve(self, token: str, filename: str) -> Path | None:
        """The path a download refers to, or None if it is not inside the root.

        The token and filename are validated by shape first, then the resolved
        path is checked against the root. Either check alone would probably do;
        both together mean a mistake in one is not exploitable.
        """
        if not TOKEN_RE.match(token):
            return None
        if filename != safe_name(filename):
            return None

        root = self.root.resolve()
        candidate = (root / token / filename).resolve()
        if not candidate.is_relative_to(root):
            return None
        if not candidate.is_file():
            return None
        return candidate

    # --- lifecycle --------------------------------------------------------

    def begin(self, filename: str) -> tuple[str, Path]:
        """Reserve a token and return the staging path to write into."""
        token = new_token()
        staging = self.staging_dir(token)
        staging.mkdir(parents=True, exist_ok=True)
        return token, staging / safe_name(filename)

    def commit(self, token: str) -> StoredFile:
        """Publish the staged upload. Atomic: the entry appears whole."""
        staging = self.staging_dir(token)
        files = sorted(p for p in staging.iterdir() if p.is_file())
        if not files:
            raise FileNotFoundError(f"nothing staged for {token}")

        final = self.entry_dir(token)
        staging.rename(final)

        published = final / files[0].name
        return StoredFile(
            token=token,
            filename=published.name,
            path=published,
            size=published.stat().st_size,
            expires_at=datetime.now(UTC) + self.retention,
        )

    def abort(self, token: str) -> None:
        _rmtree(self.staging_dir(token))

    def delete(self, token: str) -> bool:
        if not TOKEN_RE.match(token):
            return False
        directory = self.entry_dir(token)
        existed = directory.is_dir()
        _rmtree(directory)
        _rmtree(self.staging_dir(token))
        return existed

    # --- maintenance ------------------------------------------------------

    def sweep(self, now: datetime | None = None) -> int:
        """Delete expired entries and any staging left by an interrupted upload."""
        if not self.root.is_dir():
            return 0
        moment = now or datetime.now(UTC)
        cutoff = moment - self.retention
        removed = 0
        for entry in self.root.iterdir():
            if not entry.is_dir():
                entry.unlink(missing_ok=True)
                removed += 1
                continue
            if entry.name.endswith(TMP_SUFFIX) or _modified_at(entry) < cutoff:
                _rmtree(entry)
                removed += 1
        return removed

    def list_entries(self) -> list[StoredFile]:
        """Every published entry, newest first."""
        if not self.root.is_dir():
            return []
        found: list[StoredFile] = []
        for entry in self.root.iterdir():
            if not entry.is_dir() or entry.name.endswith(TMP_SUFFIX):
                continue
            files = sorted(p for p in entry.iterdir() if p.is_file())
            if not files:
                continue
            published = files[0]
            found.append(
                StoredFile(
                    token=entry.name,
                    filename=published.name,
                    path=published,
                    size=published.stat().st_size,
                    expires_at=_modified_at(entry) + self.retention,
                )
            )
        found.sort(key=lambda item: item.expires_at, reverse=True)
        return found

    def purge_all(self) -> int:
        """Delete everything, expired or not."""
        if not self.root.is_dir():
            return 0
        removed = 0
        for entry in self.root.iterdir():
            if entry.is_dir():
                _rmtree(entry)
            else:
                entry.unlink(missing_ok=True)
            removed += 1
        return removed

    def usage_bytes(self) -> int:
        if not self.root.is_dir():
            return 0
        return sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())

    def count(self) -> int:
        if not self.root.is_dir():
            return 0
        return sum(1 for p in self.root.iterdir() if p.is_dir() and not p.name.endswith(TMP_SUFFIX))


def _modified_at(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def _rmtree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
