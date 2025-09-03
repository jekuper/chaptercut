"""Cover art: fetch the thumbnail, square it, and write a JPEG."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import aiohttp
from PIL import Image

from chaptercut.logging import get_logger

log = get_logger(__name__)

COVER_NAME = "cover.jpg"
MAX_EDGE = 1000
JPEG_QUALITY = 90
FETCH_TIMEOUT = 30.0
MAX_DOWNLOAD_BYTES = 16 * 1024 * 1024

# YouTube thumbnails are 16:9; anything within this of square is left alone.
SQUARE_TOLERANCE = 0.05


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
            return await response.content.read(MAX_DOWNLOAD_BYTES)
    except (TimeoutError, aiohttp.ClientError, OSError) as exc:
        log.warning("cover.fetch_failed", error=type(exc).__name__)
        return None


def normalize(data: bytes, square: bool = True) -> bytes:
    """RGB JPEG, optionally center-cropped to a square, at most MAX_EDGE on a side."""
    with Image.open(io.BytesIO(data)) as image:
        rgb = image.convert("RGB")

        if square:
            width, height = rgb.size
            if abs(width - height) / max(width, height) > SQUARE_TOLERANCE:
                edge = min(width, height)
                left = (width - edge) // 2
                top = (height - edge) // 2
                rgb = rgb.crop((left, top, left + edge, top + edge))

        if max(rgb.size) > MAX_EDGE:
            rgb.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)

        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return buffer.getvalue()


async def fetch_and_normalize(
    url: str | None, destination: Path, square: bool = True
) -> Path | None:
    """Write `destination` and return it, or None if no usable cover was found."""
    if not url:
        return None
    data = await fetch_bytes(url)
    if data is None:
        return None
    try:
        jpeg = await asyncio.to_thread(normalize, data, square)
    except (OSError, ValueError) as exc:
        log.warning("cover.decode_failed", error=type(exc).__name__)
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(jpeg)
    return destination
