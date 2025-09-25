"""yt-dlp, driven as a subprocess.

Running it out of process is deliberate: YouTube extractor breakage is routine,
yt-dlp occasionally hangs or dies hard, and upgrading it in the image must
never risk API drift inside our code. A subprocess is killable; an in-process
call on the event loop is not.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chaptercut.logging import get_logger
from chaptercut.pipeline.process import ProcessError, run_checked, stream_lines
from chaptercut.providers.base import Provider
from chaptercut.util.jsonish import as_float, as_int, as_str, dict_list

log = get_logger(__name__)

INFO_TIMEOUT = 120.0

# yt-dlp writes these when YouTube demands a signed-in client.
BOT_CHECK_MARKERS = (
    "sign in to confirm you",
    "confirm you're not a bot",
    "cookies are no longer valid",
    "please sign in",
    "this content isn't available",
    "account cookies are no longer valid",
)

PROGRESS_PREFIX = "cc-progress:"
PROGRESS_TEMPLATE = (
    f"download:{PROGRESS_PREFIX}"
    "%(progress.downloaded_bytes)s/%(progress.total_bytes_estimate)s/"
    "%(progress.total_bytes)s/%(progress.speed)s/%(progress.eta)s"
)
_PROGRESS_RE = re.compile(rf"{re.escape(PROGRESS_PREFIX)}(\S+)")


class YtdlpError(RuntimeError):
    """yt-dlp failed. `bot_check` distinguishes the one failure the operator can fix."""

    def __init__(self, message: str, stderr: str = "", bot_check: bool = False) -> None:
        super().__init__(message)
        self.stderr = stderr
        self.bot_check = bot_check


@dataclass(slots=True)
class DownloadProgress:
    downloaded_bytes: int | None
    total_bytes: int | None
    speed: float | None
    eta: int | None

    @property
    def pct(self) -> float | None:
        if not self.total_bytes or self.downloaded_bytes is None:
            return None
        return min(100.0, self.downloaded_bytes / self.total_bytes * 100.0)


ProgressCallback = Callable[[DownloadProgress], None]


def _maybe(value: str) -> str | None:
    """yt-dlp prints NA for fields it does not know."""
    return None if value in ("NA", "None", "") else value


def parse_progress_line(line: str) -> DownloadProgress | None:
    """Parse one `--progress-template` line. Unrelated output returns None."""
    match = _PROGRESS_RE.search(line)
    if not match:
        return None
    parts = match.group(1).split("/")
    if len(parts) != 5:
        return None
    downloaded, estimate, total, speed, eta = (_maybe(part) for part in parts)
    return DownloadProgress(
        downloaded_bytes=as_int(downloaded),
        total_bytes=as_int(total) or as_int(estimate),
        speed=as_float(speed),
        eta=as_int(eta),
    )


def looks_like_bot_check(stderr: str) -> bool:
    lowered = stderr.lower()
    return any(marker in lowered for marker in BOT_CHECK_MARKERS)


@dataclass(slots=True)
class VideoInfo:
    """The subset of yt-dlp's JSON the pipeline actually uses."""

    raw: dict[str, Any]

    @property
    def video_id(self) -> str:
        return as_str(self.raw.get("id"))

    @property
    def title(self) -> str:
        return as_str(self.raw.get("title"), "Untitled")

    @property
    def uploader(self) -> str:
        for key in ("artist", "creator", "uploader", "channel"):
            value = self.raw.get(key)
            if value:
                return as_str(value)
        return "Unknown"

    @property
    def duration(self) -> float | None:
        return as_float(self.raw.get("duration"))

    @property
    def webpage_url(self) -> str:
        return as_str(self.raw.get("webpage_url"))

    @property
    def upload_date(self) -> str:
        """`YYYYMMDD` as yt-dlp gives it, or empty."""
        return as_str(self.raw.get("upload_date"))

    @property
    def year(self) -> str:
        date = self.upload_date
        return date[:4] if len(date) >= 4 else ""

    @property
    def iso_upload_date(self) -> str:
        date = self.upload_date
        return f"{date[0:4]}-{date[4:6]}-{date[6:8]}" if len(date) == 8 else ""

    @property
    def width(self) -> int | None:
        return as_int(self.raw.get("width"))

    @property
    def height(self) -> int | None:
        return as_int(self.raw.get("height"))

    @property
    def thumbnail_url(self) -> str | None:
        """The largest thumbnail yt-dlp lists, falling back to the default one."""
        best: tuple[int, str] | None = None
        for item in dict_list(self.raw.get("thumbnails")):
            url = item.get("url")
            if not isinstance(url, str):
                continue
            area = (as_int(item.get("width")) or 0) * (as_int(item.get("height")) or 0)
            if best is None or area > best[0]:
                best = (area, url)
        if best is not None:
            return best[1]
        fallback = self.raw.get("thumbnail")
        return fallback if isinstance(fallback, str) else None


class YtdlpFactory:
    """Builds a configured `Ytdlp` per provider.

    Cookie jars are per-site: `cookies-<provider>.txt` in the data directory
    wins, falling back to the shared `CC_COOKIES_FILE`. That way adding a
    provider that needs its own login is a file drop, not a config change.
    """

    def __init__(
        self,
        data_dir: Path,
        default_cookies: Path | None = None,
        extra_args: Sequence[str] = (),
        binary: str = "yt-dlp",
    ) -> None:
        self.data_dir = data_dir
        self.default_cookies = default_cookies
        self.extra_args = list(extra_args)
        self.binary = binary

    def cookies_for(self, provider: str) -> Path | None:
        specific = self.data_dir / f"cookies-{provider}.txt"
        if specific.is_file():
            return specific
        if self.default_cookies is not None and self.default_cookies.is_file():
            return self.default_cookies
        return None

    def for_provider(self, provider: Provider) -> Ytdlp:
        return Ytdlp(
            binary=self.binary,
            cookies_file=self.cookies_for(provider.name),
            extra_args=[*self.extra_args, *provider.ytdlp_args],
        )


class Ytdlp:
    """A configured yt-dlp invocation: binary, cookies, and operator extra args."""

    def __init__(
        self,
        binary: str = "yt-dlp",
        cookies_file: Path | None = None,
        extra_args: Sequence[str] = (),
    ) -> None:
        self.binary = binary
        self.cookies_file = cookies_file
        self.extra_args = list(extra_args)

    def _base_argv(self) -> list[str]:
        argv = [self.binary, "--no-playlist", "--no-warnings", "--ignore-config"]
        if self.cookies_file is not None:
            argv += ["--cookies", str(self.cookies_file)]
        argv += self.extra_args
        return argv

    async def info(self, url: str, timeout: float = INFO_TIMEOUT) -> VideoInfo:
        argv = [*self._base_argv(), "--dump-single-json", "--skip-download", url]
        try:
            result = await run_checked(argv, timeout=timeout)
        except ProcessError as exc:
            raise _wrap(exc, "could not read video metadata") from exc
        try:
            raw = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise YtdlpError("yt-dlp returned output that is not JSON") from exc
        if not isinstance(raw, dict):
            raise YtdlpError("yt-dlp returned unexpected metadata")
        return VideoInfo(raw)  # pyright: ignore[reportUnknownArgumentType]

    async def download_audio(
        self,
        url: str,
        destination: Path,
        timeout: float,
        bitrate: str = "",
        on_progress: ProgressCallback | None = None,
    ) -> Path:
        """Extract to a single MP3 at `destination`. Returns the written path."""
        argv = [
            *self._base_argv(),
            "-f",
            "bestaudio/best",
            "--extract-audio",
            "--audio-format",
            "mp3",
            "--audio-quality",
            bitrate or "0",
            "--no-part",
            "--newline",
            "--progress",
            "--progress-template",
            PROGRESS_TEMPLATE,
            "-o",
            str(destination.with_suffix(".%(ext)s")),
            url,
        ]
        await self._download(argv, timeout, on_progress)
        return _resolve_output(destination, ("mp3",))

    async def download_video(
        self,
        url: str,
        destination: Path,
        timeout: float,
        format_id: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> Path:
        selector = f"{format_id}+bestaudio/{format_id}/best" if format_id else "best"
        argv = [
            *self._base_argv(),
            "-f",
            selector,
            "--merge-output-format",
            "mp4",
            "--no-part",
            "--newline",
            "--progress",
            "--progress-template",
            PROGRESS_TEMPLATE,
            "-o",
            str(destination.with_suffix(".%(ext)s")),
            url,
        ]
        await self._download(argv, timeout, on_progress)
        return _resolve_output(destination, ("mp4", "mkv", "webm"))

    async def _download(
        self, argv: list[str], timeout: float, on_progress: ProgressCallback | None
    ) -> None:
        def handle(line: str) -> None:
            if on_progress is None:
                return
            progress = parse_progress_line(line)
            if progress is not None:
                on_progress(progress)

        try:
            await stream_lines(argv, timeout=timeout, on_line=handle)
        except ProcessError as exc:
            raise _wrap(exc, "download failed") from exc

    async def version(self) -> str:
        try:
            result = await run_checked([self.binary, "--version"], timeout=30)
        except (ProcessError, OSError):
            return "unknown"
        return result.stdout.strip()


def _wrap(exc: ProcessError, message: str) -> YtdlpError:
    bot_check = looks_like_bot_check(exc.stderr)
    if bot_check:
        log.warning("ytdlp.bot_check")
    return YtdlpError(message, stderr=exc.stderr, bot_check=bot_check)


def _resolve_output(destination: Path, extensions: Sequence[str]) -> Path:
    """yt-dlp picks the real extension; find what it actually wrote."""
    if destination.is_file():
        return destination
    for extension in extensions:
        candidate = destination.with_suffix(f".{extension}")
        if candidate.is_file():
            return candidate
    matches = sorted(destination.parent.glob(f"{destination.stem}.*"))
    if matches:
        return matches[0]
    raise YtdlpError("yt-dlp reported success but wrote no file")
