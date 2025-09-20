"""Sending finished files back to Telegram.

This lives on the bot side, not in the pipeline: delivery is a Telegram
concern, and `pipeline/` must stay importable without aiogram.

The Bot API server runs with --local and shares the data volume, so every file
goes out as an FSInputFile path the server reads directly. There is exactly one
delivery path and no size-based branching: over the limit is an error, not a
fallback to some other transport.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile, Message

from chaptercut.bot import texts
from chaptercut.logging import get_logger
from chaptercut.pipeline.runner import AudioResult, VideoResult
from chaptercut.pipeline.sanitize import safe_filename
from chaptercut.util.timefmt import format_bytes

log = get_logger(__name__)


class TooLargeError(RuntimeError):
    def __init__(self, size: int, limit: int) -> None:
        self.size = size
        self.limit = limit
        super().__init__(
            texts.FAILED_TOO_LARGE.format(size=format_bytes(size), limit=format_bytes(limit))
        )


@dataclass(slots=True)
class Delivery:
    """How a finished job is sent."""

    bot: Bot
    chat_id: int
    max_send_bytes: int
    multi_mode: str = "zip"

    def _check_size(self, path: Path) -> int:
        size = path.stat().st_size
        if size > self.max_send_bytes:
            raise TooLargeError(size, self.max_send_bytes)
        return size

    async def send_audio_result(self, result: AudioResult) -> list[Message]:
        if result.track_count == 1:
            return [await self._send_single_track(result)]

        sent: list[Message] = []
        if result.zip_path is not None:
            sent.append(await self._send_zip(result, result.zip_path))
        if self.multi_mode in ("individual", "both"):
            sent.extend(await self._send_tracks(result))
        return sent

    async def _send_single_track(self, result: AudioResult) -> Message:
        path = result.tracks[0]
        self._check_size(path)
        track = result.manifest.tracks[0]
        return await self.bot.send_audio(
            chat_id=self.chat_id,
            audio=FSInputFile(path, filename=path.name),
            title=track.title,
            performer=result.uploader,
            duration=int((track.end_ms - track.start_ms) / 1000),
            thumbnail=FSInputFile(result.thumbnail) if result.thumbnail else None,
            caption=texts.audio_caption(
                result.title, result.uploader, result.track_count, result.total_bytes
            ),
            parse_mode="HTML",
        )

    async def _send_zip(self, result: AudioResult, zip_path: Path) -> Message:
        self._check_size(zip_path)
        return await self.bot.send_document(
            chat_id=self.chat_id,
            document=FSInputFile(zip_path, filename=zip_path.name),
            caption=texts.audio_caption(
                result.title, result.uploader, result.track_count, zip_path.stat().st_size
            ),
            parse_mode="HTML",
            thumbnail=FSInputFile(result.thumbnail) if result.thumbnail else None,
        )

    async def _send_tracks(self, result: AudioResult) -> list[Message]:
        sent: list[Message] = []
        for path, track in zip(result.tracks, result.manifest.tracks, strict=True):
            self._check_size(path)
            sent.append(
                await self.bot.send_audio(
                    chat_id=self.chat_id,
                    audio=FSInputFile(path, filename=path.name),
                    title=track.title,
                    performer=result.uploader,
                    duration=int((track.end_ms - track.start_ms) / 1000),
                    thumbnail=FSInputFile(result.thumbnail) if result.thumbnail else None,
                )
            )
        return sent

    async def send_video_result(self, result: VideoResult) -> Message:
        size = self._check_size(result.path)
        filename = f"{safe_filename(result.title, fallback=result.video_id)}.mp4"
        return await self.bot.send_video(
            chat_id=self.chat_id,
            video=FSInputFile(result.path, filename=filename),
            caption=texts.video_caption(result.title, result.uploader, size),
            parse_mode="HTML",
            duration=int(result.duration),
            width=result.width,
            height=result.height,
            thumbnail=FSInputFile(result.thumbnail) if result.thumbnail else None,
            supports_streaming=True,
        )
