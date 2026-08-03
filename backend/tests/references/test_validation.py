import hashlib
import io

import pytest
from PIL import Image

from aimctexturegen.references.validation import (
    MAX_REFERENCE_BYTES,
    MAX_REFERENCE_PIXELS,
    MAX_REFERENCE_SIDE,
    MIN_REFERENCE_SIDE,
    ReferenceValidationError,
    validate_reference_png,
)


def png(mode: str, width: int, height: int) -> bytes:
    payload = io.BytesIO()
    Image.new(mode, (width, height), 128).save(payload, format="PNG")
    return payload.getvalue()


def animated_png() -> bytes:
    payload = io.BytesIO()
    frames = [Image.new("RGBA", (16, 16), color) for color in ((255, 0, 0, 255), (0, 0, 255, 255))]
    frames[0].save(
        payload,
        format="PNG",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    return payload.getvalue()


def truncated_png() -> bytes:
    return png("RGB", 16, 16)[:-12]


def test_accepts_static_square_rgb_and_rgba_png() -> None:
    rgb = validate_reference_png(png("RGB", MIN_REFERENCE_SIDE, MIN_REFERENCE_SIDE))
    rgba = validate_reference_png(png("RGBA", MAX_REFERENCE_SIDE, MAX_REFERENCE_SIDE))

    assert rgb.mode == "RGB"
    assert rgb.width == MIN_REFERENCE_SIDE
    assert rgb.height == MIN_REFERENCE_SIDE
    assert rgb.byte_size == len(rgb.payload)
    assert rgb.sha256 == hashlib.sha256(rgb.payload).hexdigest()
    assert rgba.mode == "RGBA"
    assert rgba.width == MAX_REFERENCE_SIDE
    assert rgba.height == MAX_REFERENCE_SIDE


@pytest.mark.parametrize(
    ("payload", "label"),
    [
        (animated_png(), "animated"),
        (truncated_png(), "truncated"),
        (png("L", 16, 16), "grayscale"),
        (png("RGB", MIN_REFERENCE_SIDE - 1, MIN_REFERENCE_SIDE - 1), "too-small"),
        (png("RGB", 16, 17), "non-square"),
        (b"\x89PNG\r\n\x1a\n", "signature-only"),
        (b"x" * (MAX_REFERENCE_BYTES + 1), "oversized"),
    ],
    ids=[
        "animated",
        "truncated",
        "grayscale",
        "too-small",
        "non-square",
        "signature-only",
        "oversized",
    ],
)
def test_rejects_out_of_contract_reference(payload: bytes, label: str) -> None:
    with pytest.raises(ReferenceValidationError) as captured:
        validate_reference_png(payload)

    assert captured.value.code == "REFERENCE_INVALID"


def test_rejects_decompression_bomb_sized_pixels_even_inside_byte_limit() -> None:
    huge = png("RGB", MAX_REFERENCE_SIDE, MAX_REFERENCE_PIXELS // MAX_REFERENCE_SIDE + 1)

    with pytest.raises(ReferenceValidationError) as captured:
        validate_reference_png(huge)

    assert captured.value.code == "REFERENCE_INVALID"
