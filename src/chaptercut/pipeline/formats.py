"""Pick the handful of video qualities worth showing as buttons."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chaptercut.util.jsonish import as_int, dict_list

MAX_OPTIONS = 6


@dataclass(frozen=True, slots=True)
class FormatOption:
    format_id: str
    height: int
    ext: str
    size_bytes: int | None
    needs_audio: bool

    @property
    def label_height(self) -> str:
        return f"{self.height}p"


def _size_of(fmt: dict[str, Any]) -> int | None:
    return as_int(fmt.get("filesize")) or as_int(fmt.get("filesize_approx"))


def _best_audio_size(formats: list[dict[str, Any]]) -> int:
    """Size of the audio stream that yt-dlp would merge in, for the size estimate."""
    sizes = [
        size
        for fmt in formats
        if fmt.get("acodec") not in (None, "none") and fmt.get("vcodec") in (None, "none")
        if (size := _size_of(fmt)) is not None
    ]
    return min(sizes) if sizes else 0


def select_video_formats(info: dict[str, Any]) -> list[FormatOption]:
    """One option per distinct height, mp4 preferred, largest height first."""
    formats = dict_list(info.get("formats"))
    audio_size = _best_audio_size(formats)

    best_per_height: dict[int, dict[str, Any]] = {}
    for fmt in formats:
        if fmt.get("vcodec") in (None, "none"):
            continue
        height = as_int(fmt.get("height"))
        format_id = fmt.get("format_id")
        if not height or height <= 0 or not isinstance(format_id, str):
            continue
        current = best_per_height.get(height)
        if current is None or _rank(fmt) > _rank(current):
            best_per_height[height] = fmt

    options: list[FormatOption] = []
    for height in sorted(best_per_height, reverse=True)[:MAX_OPTIONS]:
        fmt = best_per_height[height]
        needs_audio = fmt.get("acodec") in (None, "none")
        size = _size_of(fmt)
        if size is not None and needs_audio:
            size += audio_size
        options.append(
            FormatOption(
                format_id=str(fmt["format_id"]),
                height=height,
                ext=str(fmt.get("ext") or "mp4"),
                size_bytes=size,
                needs_audio=needs_audio,
            )
        )
    return options


def _rank(fmt: dict[str, Any]) -> tuple[int, int, int]:
    """Prefer mp4, then a known size, then the higher bitrate."""
    is_mp4 = 1 if fmt.get("ext") == "mp4" else 0
    has_size = 1 if _size_of(fmt) is not None else 0
    tbr = as_int(fmt.get("tbr")) or 0
    return (is_mp4, has_size, tbr)


def find_option(options: list[FormatOption], format_id: str) -> FormatOption | None:
    return next((o for o in options if o.format_id == format_id), None)


def options_to_json(options: list[FormatOption]) -> list[dict[str, Any]]:
    return [
        {
            "format_id": o.format_id,
            "height": o.height,
            "ext": o.ext,
            "size_bytes": o.size_bytes,
            "needs_audio": o.needs_audio,
        }
        for o in options
    ]


def options_from_json(raw: list[dict[str, Any]]) -> list[FormatOption]:
    return [
        FormatOption(
            format_id=str(item["format_id"]),
            height=int(item["height"]),
            ext=str(item.get("ext") or "mp4"),
            size_bytes=as_int(item.get("size_bytes")),
            needs_audio=bool(item.get("needs_audio")),
        )
        for item in raw
    ]
