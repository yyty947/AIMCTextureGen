from __future__ import annotations

import hashlib
import io
import warnings

from PIL import Image, UnidentifiedImageError

from .models import ValidatedReference


MAX_REFERENCE_BYTES = 16 * 1024 * 1024
MAX_REFERENCE_PIXELS = 16_777_216
MIN_REFERENCE_SIDE = 16
MAX_REFERENCE_SIDE = 4096
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ReferenceValidationError(ValueError):
    def __init__(self, code: str, user_message: str) -> None:
        self.code = code
        self.user_message = user_message
        super().__init__(user_message)


def validate_reference_png(payload: bytes) -> ValidatedReference:
    try:
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")
        if len(payload) > MAX_REFERENCE_BYTES:
            raise ValueError("payload exceeds maximum size")
        if not payload.startswith(_PNG_SIGNATURE):
            raise ValueError("payload does not have a PNG signature")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as image:
                if image.format != "PNG":
                    raise ValueError("reference must be a PNG")
                if getattr(image, "n_frames", 1) != 1 or getattr(image, "is_animated", False):
                    raise ValueError("reference must be a static PNG")
                width, height = image.size
                if width != height:
                    raise ValueError("reference must be square")
                if not (MIN_REFERENCE_SIDE <= width <= MAX_REFERENCE_SIDE):
                    raise ValueError("reference side is out of range")
                if width * height > MAX_REFERENCE_PIXELS:
                    raise ValueError("reference pixel count exceeds the maximum")
                image.verify()
            with Image.open(io.BytesIO(payload)) as image:
                image.load()
                if image.mode not in {"RGB", "RGBA"}:
                    raise ValueError("reference must decode to RGB or RGBA")
                return ValidatedReference(
                    payload=payload,
                    sha256=hashlib.sha256(payload).hexdigest(),
                    byte_size=len(payload),
                    width=width,
                    height=height,
                    mode=image.mode,
                )
    except (
        OSError,
        SyntaxError,
        TypeError,
        ValueError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as error:
        raise ReferenceValidationError(
            "REFERENCE_INVALID",
            "参考 PNG 不符合格式要求",
        ) from error
