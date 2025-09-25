"""Turn yt-dlp chapter data into the track list we cut and tag."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chaptercut.pipeline.sanitize import safe_title
from chaptercut.util.jsonish import as_float, dict_list

# Chapters shorter than this are almost always artifacts of a mis-parsed
# description ("0:00 intro, 0:01 song"). Merging them would be surprising, so
# we keep them but never let a zero-length cut reach ffmpeg.
MIN_CHAPTER_SECONDS = 0.5


@dataclass(frozen=True, slots=True)
class Track:
    index: int
    title: str
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def chapters_from_info(info: dict[str, Any], duration: float | None = None) -> list[Track]:
    """Track list for a video. No chapters means a single whole-video track.

    `duration` overrides the duration reported by yt-dlp; pass the ffprobe value
    of the downloaded file, which is authoritative for the last chapter's end.
    """
    total = duration if duration is not None else as_float(info.get("duration"))
    video_title = safe_title(str(info.get("title") or "audio"))

    chapters = dict_list(info.get("chapters"))

    if not chapters:
        if total is None or total <= 0:
            raise ValueError("cannot build a track list without a duration")
        return [Track(index=1, title=video_title, start=0.0, end=total)]

    parsed: list[tuple[float, float | None, str]] = []
    for position, chapter in enumerate(chapters, start=1):
        start = as_float(chapter.get("start_time")) or 0.0
        end = as_float(chapter.get("end_time"))
        title = safe_title(str(chapter.get("title") or "")) or f"Chapter {position}"
        parsed.append((start, end, title))

    parsed.sort(key=lambda item: item[0])

    tracks: list[Track] = []
    for position, (start, end, title) in enumerate(parsed):
        if end is None or end <= start:
            # Missing end time: run to the next chapter, or to the end of the file.
            end = parsed[position + 1][0] if position + 1 < len(parsed) else total  # pyright: ignore[reportAssignmentType]
        if end is None:
            raise ValueError("last chapter has no end time and the duration is unknown")
        if total is not None:
            end = min(end, total)
        if end - start < MIN_CHAPTER_SECONDS:
            continue
        tracks.append(Track(index=len(tracks) + 1, title=title, start=start, end=end))

    if not tracks:
        if total is None or total <= 0:
            raise ValueError("cannot build a track list without a duration")
        return [Track(index=1, title=video_title, start=0.0, end=total)]
    return tracks
