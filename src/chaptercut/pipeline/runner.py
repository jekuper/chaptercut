"""The processing pipeline: fetch, download, split, tag, cache, package.

Knows nothing about Telegram. Progress goes out through a `ProgressSink`, and
the finished artefacts come back as a result object the worker delivers.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from chaptercut.cache.manifest import Manifest, ManifestTrack
from chaptercut.cache.store import CachedResult, CacheStore
from chaptercut.logging import get_logger
from chaptercut.pipeline import cover as cover_art
from chaptercut.pipeline import ffmpeg, package
from chaptercut.pipeline.chapters import Track, chapters_from_info
from chaptercut.pipeline.sanitize import safe_filename, safe_title, track_filename
from chaptercut.pipeline.sink import ProgressSink
from chaptercut.pipeline.tagging import TrackMeta, apply_mtimes, write_tags
from chaptercut.pipeline.ytdlp import DownloadProgress, VideoInfo, Ytdlp
from chaptercut.queue.models import ExtractType, Job, Phase
from chaptercut.settings import Settings
from chaptercut.util.timefmt import format_bytes, iso, parse_iso, utcnow
from chaptercut.util.youtube import canonical_url

log = get_logger(__name__)

SOURCE_STEM = "source"
VIDEO_STEM = "video"
OUT_DIRNAME = "out"


class PipelineError(RuntimeError):
    """A job failed for a reason worth showing the user verbatim."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


@dataclass(slots=True)
class AudioResult:
    video_id: str
    title: str
    uploader: str
    duration: float
    manifest: Manifest
    directory: Path
    tracks: list[Path]
    cover: Path | None
    zip_path: Path | None
    from_cache: bool

    @property
    def track_count(self) -> int:
        return len(self.tracks)

    @property
    def total_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.tracks if path.is_file())


@dataclass(slots=True)
class VideoResult:
    video_id: str
    title: str
    uploader: str
    duration: float
    path: Path
    width: int | None
    height: int | None
    thumbnail: Path | None

    @property
    def total_bytes(self) -> int:
        return self.path.stat().st_size if self.path.is_file() else 0


@dataclass(slots=True)
class JobPaths:
    """The scratch layout for one job. The worker owns its lifetime."""

    root: Path
    out: Path = field(init=False)

    def __post_init__(self) -> None:
        self.out = self.root / OUT_DIRNAME

    def prepare(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)


class Pipeline:
    def __init__(self, settings: Settings, ytdlp: Ytdlp, cache: CacheStore) -> None:
        self.settings = settings
        self.ytdlp = ytdlp
        self.cache = cache

    async def run(self, job: Job, work_root: Path, sink: ProgressSink) -> AudioResult | VideoResult:
        paths = JobPaths(work_root)
        paths.prepare()
        if job.kind is ExtractType.AUDIO:
            return await self._run_audio(job, paths, sink)
        return await self._run_video(job, paths, sink)

    # --- audio ------------------------------------------------------------

    async def _run_audio(self, job: Job, paths: JobPaths, sink: ProgressSink) -> AudioResult:
        cached = self.cache.get(job.video_id)
        if cached is not None:
            log.info("pipeline.cache_hit", video_id=job.video_id)
            return await self._from_cache(cached, paths, sink)

        await sink.update(Phase.FETCH, detail="reading metadata", force=True)
        info = await self._fetch_info(job)

        downloaded_at = utcnow()
        await sink.update(Phase.DOWNLOAD, pct=0.0, force=True)
        source = await self._download_audio(job, paths, sink)

        duration = await ffmpeg.probe_duration(source)
        tracks = chapters_from_info(info.raw, duration=duration)
        log.info("pipeline.tracks", video_id=job.video_id, count=len(tracks))

        cover = await cover_art.fetch_and_normalize(
            info.thumbnail_url,
            paths.out / cover_art.COVER_NAME,
            square=self.settings.cover_square,
        )

        written = await self._cut_tracks(source, tracks, paths, sink)
        await self._tag_tracks(written, tracks, info, downloaded_at, cover, sink)
        apply_mtimes([path for _, path in written], downloaded_at)

        manifest = _build_manifest(info, tracks, written, cover, downloaded_at, duration)
        entry = self.cache.commit(job.video_id, paths.out, manifest)

        # The out dir moved into the cache; work from the committed copy now.
        paths.out.mkdir(parents=True, exist_ok=True)
        apply_mtimes(entry.track_paths, downloaded_at)

        zip_path = await self._package(entry, paths, sink)
        return AudioResult(
            video_id=job.video_id,
            title=manifest.title,
            uploader=manifest.uploader,
            duration=duration,
            manifest=manifest,
            directory=entry.directory,
            tracks=entry.track_paths,
            cover=entry.cover_path,
            zip_path=zip_path,
            from_cache=False,
        )

    async def _from_cache(
        self, cached: CachedResult, paths: JobPaths, sink: ProgressSink
    ) -> AudioResult:
        manifest = cached.manifest
        # Copy and zip operations reset mtimes; restore the original ordering.
        apply_mtimes(cached.track_paths, parse_iso(manifest.downloaded_at))
        zip_path = await self._package(cached, paths, sink)
        return AudioResult(
            video_id=cached.video_id,
            title=manifest.title,
            uploader=manifest.uploader,
            duration=manifest.duration_ms / 1000,
            manifest=manifest,
            directory=cached.directory,
            tracks=cached.track_paths,
            cover=cached.cover_path,
            zip_path=zip_path,
            from_cache=True,
        )

    async def _package(
        self, entry: CachedResult, paths: JobPaths, sink: ProgressSink
    ) -> Path | None:
        """Build the ZIP, unless a single track is going out on its own."""
        if len(entry.manifest.tracks) < 2:
            return None
        if self.settings.audio_multi_delivery not in ("zip", "both"):
            return None
        await sink.update(Phase.PACKAGE, detail="building archive", force=True)
        folder = safe_filename(entry.manifest.title, fallback=entry.video_id)
        members = [*entry.track_paths]
        if entry.cover_path is not None:
            members.append(entry.cover_path)
        return await package.make_zip(members, paths.root / f"{folder}.zip", folder)

    async def _cut_tracks(
        self, source: Path, tracks: list[Track], paths: JobPaths, sink: ProgressSink
    ) -> list[tuple[Track, Path]]:
        written: list[tuple[Track, Path]] = []
        total = len(tracks)
        for track in tracks:
            await sink.update(
                Phase.SPLIT,
                pct=(track.index - 1) / total * 100,
                detail=f"{track.index}/{total} {track.title}",
            )
            destination = paths.out / track_filename(track.index, total, track.title)
            await ffmpeg.cut(source, destination, track.start, track.end)
            written.append((track, destination))
        return written

    async def _tag_tracks(
        self,
        written: list[tuple[Track, Path]],
        tracks: list[Track],
        info: VideoInfo,
        downloaded_at: datetime,
        cover: Path | None,
        sink: ProgressSink,
    ) -> None:
        await sink.update(Phase.TAG, detail=f"{len(tracks)} tracks", force=True)
        meta = TrackMeta(
            album=safe_title(info.title),
            artist=safe_title(info.uploader),
            video_id=info.video_id,
            url=info.webpage_url or canonical_url(info.video_id),
            year=info.year,
            downloaded_at=downloaded_at,
            total_tracks=len(tracks),
        )
        for index, (track, path) in enumerate(written, start=1):
            await sink.update(Phase.TAG, pct=index / len(written) * 100, detail=track.title)
            await write_tags(path, track, meta, cover)

    async def _download_audio(self, job: Job, paths: JobPaths, sink: ProgressSink) -> Path:
        target = paths.root / SOURCE_STEM
        progress = _ProgressBridge(sink, Phase.DOWNLOAD)
        return await self.ytdlp.download_audio(
            canonical_url(job.video_id),
            target,
            timeout=self.settings.download_timeout_seconds,
            bitrate=self.settings.audio_bitrate,
            on_progress=progress.handle,
        )

    # --- video ------------------------------------------------------------

    async def _run_video(self, job: Job, paths: JobPaths, sink: ProgressSink) -> VideoResult:
        await sink.update(Phase.FETCH, detail="reading metadata", force=True)
        info = await self._fetch_info(job)

        await sink.update(Phase.DOWNLOAD, pct=0.0, force=True)
        progress = _ProgressBridge(sink, Phase.DOWNLOAD)
        path = await self.ytdlp.download_video(
            canonical_url(job.video_id),
            paths.root / VIDEO_STEM,
            timeout=self.settings.download_timeout_seconds,
            format_id=job.format_id,
            on_progress=progress.handle,
        )

        thumbnail = await cover_art.fetch_and_normalize(
            info.thumbnail_url, paths.root / "thumb.jpg", square=False
        )
        duration = info.duration or 0.0
        return VideoResult(
            video_id=job.video_id,
            title=safe_title(info.title),
            uploader=safe_title(info.uploader),
            duration=duration,
            path=path,
            width=info.width,
            height=info.height,
            thumbnail=thumbnail,
        )

    async def _fetch_info(self, job: Job) -> VideoInfo:
        info = await self.ytdlp.info(canonical_url(job.video_id))
        if info.duration is None and not info.raw.get("chapters"):
            log.warning("pipeline.no_duration", video_id=job.video_id)
        return info

    # --- cache maintenance ------------------------------------------------

    def evict_to_fit(self, order: list[str]) -> list[str]:
        """Delete least-recently-served entries until the cache fits the budget.

        `order` is the eviction order; returns the video ids actually removed.
        """
        budget = self.settings.cache_max_bytes
        usage = self.cache.usage_bytes()
        removed: list[str] = []
        for video_id in order:
            if usage <= budget:
                break
            entry = self.cache.get(video_id)
            size = entry.size_bytes if entry is not None else 0
            if self.cache.delete(video_id):
                usage -= size
                removed.append(video_id)
                log.info("cache.evicted", video_id=video_id, freed=format_bytes(size))
        return removed


class _ProgressBridge:
    """Turns yt-dlp progress records into sink updates.

    The subprocess reader must not block, so each update is dispatched as a
    task. Tasks are held in a set until they finish; a bare create_task can be
    garbage-collected mid-flight.
    """

    def __init__(self, sink: ProgressSink, phase: Phase) -> None:
        self.sink = sink
        self.phase = phase
        self._tasks: set[asyncio.Task[None]] = set()

    def handle(self, progress: DownloadProgress) -> None:
        task = asyncio.get_running_loop().create_task(self._emit(progress))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _emit(self, progress: DownloadProgress) -> None:
        detail = f"{format_bytes(progress.speed)}/s" if progress.speed else None
        await self.sink.update(self.phase, pct=progress.pct, detail=detail)


def _build_manifest(
    info: VideoInfo,
    tracks: list[Track],
    written: list[tuple[Track, Path]],
    cover: Path | None,
    downloaded_at: datetime,
    duration: float,
) -> Manifest:
    return Manifest(
        schema=1,
        video_id=info.video_id,
        url=info.webpage_url or canonical_url(info.video_id),
        title=safe_title(info.title),
        uploader=safe_title(info.uploader),
        upload_date=info.iso_upload_date,
        duration_ms=int(duration * 1000),
        cover=cover.name if cover is not None else None,
        tracks=[
            ManifestTrack(
                n=track.index,
                file=path.name,
                title=track.title,
                start_ms=int(track.start * 1000),
                end_ms=int(track.end * 1000),
            )
            for track, path in written
        ],
        downloaded_at=iso(downloaded_at),
    )
