"""Cover normalization. Pillow only; the fetch path is not exercised here."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from chaptercut.pipeline.cover import MAX_EDGE, normalize


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
