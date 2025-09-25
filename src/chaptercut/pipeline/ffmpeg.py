"""ffmpeg and ffprobe, as async subprocesses."""

from __future__ import annotations

import json
from pathlib import Path

from chaptercut.pipeline.process import ProcessError, run_checked
from chaptercut.util.jsonish import as_dict, as_float

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

PROBE_TIMEOUT = 60.0
CUT_TIMEOUT = 300.0


class FfmpegError(RuntimeError):
    pass


async def probe_duration(path: Path, timeout: float = PROBE_TIMEOUT) -> float:
    """Duration in seconds. This is what the last chapter's end time comes from."""
    argv = [
        FFPROBE,
        "-hide_banner",
        "-loglevel",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = await run_checked(argv, timeout=timeout)
    except ProcessError as exc:
        raise FfmpegError(f"ffprobe failed on {path.name}") from exc
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise FfmpegError("ffprobe returned output that is not JSON") from exc
    fmt = as_dict(payload).get("format")
    duration = as_float(as_dict(fmt).get("duration"))
    if duration is None or duration <= 0:
        raise FfmpegError(f"ffprobe reported no duration for {path.name}")
    return duration


async def cut(
    source: Path,
    destination: Path,
    start: float,
    end: float,
    timeout: float = CUT_TIMEOUT,
) -> Path:
    """Extract [start, end) into `destination` by stream copy.

    No re-encode: it is fast and lossless. `-ss` before `-i` seeks to the
    nearest frame, which for MP3 is accurate to about 26 ms.
    """
    if end <= start:
        raise FfmpegError(f"refusing to cut an empty range {start}..{end}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-to",
        f"{end:.3f}",
        "-i",
        str(source),
        "-c",
        "copy",
        "-map_metadata",
        "-1",
        "-map",
        "0:a",
        str(destination),
    ]
    try:
        await run_checked(argv, timeout=timeout)
    except ProcessError as exc:
        raise FfmpegError(f"ffmpeg could not cut {destination.name}: {exc.stderr[-400:]}") from exc
    if not destination.is_file() or destination.stat().st_size == 0:
        raise FfmpegError(f"ffmpeg produced an empty file for {destination.name}")
    return destination


async def available() -> bool:
    """Whether ffmpeg and ffprobe are on PATH. Used by /status."""
    for binary in (FFMPEG, FFPROBE):
        try:
            await run_checked([binary, "-version"], timeout=15)
        except (ProcessError, OSError):
            return False
    return True
