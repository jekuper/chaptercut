"""Cover art: fetch the thumbnail, square it, and write a JPEG.

Two sizes come out of this. The big one is embedded in the ID3 tags, where
more pixels are better. The small one is handed to Telegram, which rejects
anything over 320x320 or 200 kB.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncIterator
from pathlib import Path

import aiohttp
from PIL import Image

from chaptercut.logging import get_logger

log = get_logger(__name__)

COVER_NAME = "cover.jpg"
THUMB_NAME = "thumb.jpg"

MAX_EDGE = 1000
JPEG_QUALITY = 90

# Telegram's limits for a thumbnail attached to audio, video or a document.
THUMB_MAX_EDGE = 320
THUMB_QUALITY = 85

FETCH_TIMEOUT = 30.0
MAX_DOWNLOAD_BYTES = 16 * 1024 * 1024
CHUNK_BYTES = 64 * 1024

# YouTube thumbnails are 16:9; anything within this of square is left alone.
SQUARE_TOLERANCE = 0.05


async def collect_capped(chunks: AsyncIterator[bytes], limit: int) -> bytes | None:
    """Read every chunk, giving up if the total passes `limit`.

    A plain `content.read(limit)` looks equivalent but is not: it returns only
    what is already buffered, which silently truncates anything bigger than the
    first chunk off the wire.
    """
    parts: list[bytes] = []
    total = 0
    async for chunk in chunks:
        total += len(chunk)
        if total > limit:
            log.warning("cover.too_large", limit=limit)
            return None
        parts.append(chunk)
    return b"".join(parts)


async def fetch_bytes(url: str, timeout: float = FETCH_TIMEOUT) -> bytes | None:
    """Download the thumbnail. A missing cover is not a job failure."""
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session,
            session.get(url) as response,
        ):
            if response.status != 200:
                log.warning("cover.http_error", status=response.status)
                return None
            return await collect_capped(
                response.content.iter_chunked(CHUNK_BYTES), MAX_DOWNLOAD_BYTES
            )
    except (TimeoutError, aiohttp.ClientError, OSError) as exc:
        log.warning("cover.fetch_failed", error=type(exc).__name__)
        return None


def normalize(
    data: bytes,
    square: bool = True,
    max_edge: int = MAX_EDGE,
    quality: int = JPEG_QUALITY,
) -> bytes:
    """RGB JPEG, optionally center-cropped to a square, at most `max_edge` a side."""
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        rgb = image.convert("RGB")

        if square:
            width, height = rgb.size
            if abs(width - height) / max(width, height) > SQUARE_TOLERANCE:
                edge = min(width, height)
                left = (width - edge) // 2
                top = (height - edge) // 2
                rgb = rgb.crop((left, top, left + edge, top + edge))

        if max(rgb.size) > max_edge:
            rgb.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=quality, optimize=True)
        return buffer.getvalue()


async def fetch_and_normalize(
    url: str | None,
    destination: Path,
    square: bool = True,
    max_edge: int = MAX_EDGE,
    quality: int = JPEG_QUALITY,
) -> Path | None:
    """Write `destination` and return it, or None if no usable cover was found."""
    if not url:
        return None
    data = await fetch_bytes(url)
    if data is None:
        return None
    try:
        jpeg = await asyncio.to_thread(normalize, data, square, max_edge, quality)
    except (OSError, ValueError) as exc:
        log.warning("cover.decode_failed", error=type(exc).__name__)
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(jpeg)
    return destination


async def make_thumbnail(
    source: Path | None, destination: Path, square: bool = True
) -> Path | None:
    """Shrink an existing cover to something Telegram will accept.

    Cheap enough to redo per job, so it lives in the scratch directory and the
    cache keeps only the full-size cover.
    """
    if source is None or not source.is_file():
        return None
    try:
        jpeg = await asyncio.to_thread(
            normalize, source.read_bytes(), square, THUMB_MAX_EDGE, THUMB_QUALITY
        )
    except (OSError, ValueError) as exc:
        log.warning("thumbnail.failed", error=type(exc).__name__)
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(jpeg)
    return destination
