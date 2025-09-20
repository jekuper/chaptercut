"""Cover normalization. Pillow only; the fetch path is not exercised here."""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from PIL import Image

from chaptercut.pipeline.cover import (
    MAX_DOWNLOAD_BYTES,
    MAX_EDGE,
    THUMB_MAX_EDGE,
    collect_capped,
    make_thumbnail,
    normalize,
)


def image_bytes(width: int, height: int, mode: str = "RGB", fmt: str = "JPEG") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, (width, height), (200, 100, 50)).save(buffer, format=fmt)
    return buffer.getvalue()


def size_of(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as image:
        return image.size


def test_widescreen_is_centre_cropped_to_a_square() -> None:
    assert size_of(normalize(image_bytes(1280, 720), square=True)) == (720, 720)


def test_tall_images_are_cropped_too() -> None:
    assert size_of(normalize(image_bytes(400, 900), square=True)) == (400, 400)


def test_square_is_off_by_request() -> None:
    assert size_of(normalize(image_bytes(1280, 720), square=False)) == (1000, 563)


def test_nearly_square_images_are_left_alone() -> None:
    assert size_of(normalize(image_bytes(500, 490), square=True)) == (500, 490)


def test_large_images_are_capped() -> None:
    width, height = size_of(normalize(image_bytes(4000, 4000), square=True))
    assert max(width, height) == MAX_EDGE


def test_small_images_are_not_upscaled() -> None:
    assert size_of(normalize(image_bytes(200, 200), square=True)) == (200, 200)


def test_output_is_always_a_jpeg() -> None:
    data = normalize(image_bytes(300, 300, fmt="PNG"), square=True)
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"


def test_transparency_is_flattened() -> None:
    data = normalize(image_bytes(300, 300, mode="RGBA", fmt="PNG"), square=True)
    with Image.open(io.BytesIO(data)) as image:
        assert image.mode == "RGB"


def test_garbage_input_raises() -> None:
    with pytest.raises((OSError, ValueError)):
        normalize(b"not an image", square=True)


# --- reading the body --------------------------------------------------------


async def fake_chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


async def test_every_chunk_is_collected() -> None:
    # The bug this replaces: aiohttp's content.read(n) returns only what is
    # already buffered, so a multi-chunk image arrived truncated and Pillow
    # rejected it with "image file is truncated".
    data = await collect_capped(fake_chunks(b"a" * 100, b"b" * 100, b"c" * 50), limit=10_000)
    assert data == b"a" * 100 + b"b" * 100 + b"c" * 50


async def test_an_oversized_body_is_abandoned() -> None:
    assert await collect_capped(fake_chunks(b"x" * 60, b"x" * 60), limit=100) is None


async def test_an_empty_body_collects_to_empty() -> None:
    assert await collect_capped(fake_chunks(), limit=100) == b""


async def test_a_multi_chunk_jpeg_survives_the_round_trip() -> None:
    payload = image_bytes(1280, 720)
    chunks = [payload[i : i + 4096] for i in range(0, len(payload), 4096)]
    assert len(chunks) > 1, "fixture must span several chunks to be meaningful"
    collected = await collect_capped(fake_chunks(*chunks), limit=MAX_DOWNLOAD_BYTES)
    assert collected == payload
    assert size_of(normalize(collected, square=True)) == (720, 720)


# --- telegram-sized thumbnails -----------------------------------------------


async def test_a_thumbnail_fits_telegrams_limits(tmp_path: Path) -> None:
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(normalize(image_bytes(2000, 2000), square=True))
    assert max(size_of(cover.read_bytes())) == MAX_EDGE

    thumb = await make_thumbnail(cover, tmp_path / "thumb.jpg")

    assert thumb is not None
    assert max(size_of(thumb.read_bytes())) <= THUMB_MAX_EDGE
    assert thumb.stat().st_size < 200 * 1024


async def test_a_wide_thumbnail_keeps_its_shape(tmp_path: Path) -> None:
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(image_bytes(1280, 720))
    thumb = await make_thumbnail(cover, tmp_path / "thumb.jpg", square=False)
    assert thumb is not None
    width, height = size_of(thumb.read_bytes())
    assert width == THUMB_MAX_EDGE
    assert height < width


async def test_no_cover_means_no_thumbnail(tmp_path: Path) -> None:
    assert await make_thumbnail(None, tmp_path / "thumb.jpg") is None
    assert await make_thumbnail(tmp_path / "gone.jpg", tmp_path / "thumb.jpg") is None


async def test_an_unreadable_cover_does_not_raise(tmp_path: Path) -> None:
    broken = tmp_path / "cover.jpg"
    broken.write_bytes(b"not an image")
    assert await make_thumbnail(broken, tmp_path / "thumb.jpg") is None
