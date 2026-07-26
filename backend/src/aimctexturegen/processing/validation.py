from __future__ import annotations

from pathlib import Path
from typing import Literal

from PIL import Image, UnidentifiedImageError

from .errors import ProcessingError

_OPAQUE = 255


def load_rgb_canvas(
    source: Path, resolution: int
) -> tuple[Image.Image, Literal["RGB", "RGBA"]]:
    """Decode a candidate PNG into a validated RGB canvas.

    Accepts RGB, and RGBA only when every alpha value is fully opaque
    (the alpha channel is then dropped deterministically). The canvas must
    be square with a side divisible by the target resolution.
    """
    try:
        with Image.open(source) as decoded:
            image = decoded.copy()
    except (UnidentifiedImageError, OSError) as error:
        raise ProcessingError("UNDECODABLE_IMAGE", "候选图像无法解码，文件可能已损坏") from error

    mode = image.mode
    if mode == "RGBA":
        if image.getchannel("A").getextrema() != (_OPAQUE, _OPAQUE):
            raise ProcessingError(
                "UNSUPPORTED_IMAGE_MODE",
                "RGBA 图像包含非完全不透明像素，普通方块 MVP 不处理透明度",
            )
        image = image.convert("RGB")
    elif mode != "RGB":
        raise ProcessingError("UNSUPPORTED_IMAGE_MODE", f"不支持的图像模式：{mode}")

    width, height = image.size
    if width != height:
        raise ProcessingError("INVALID_CANVAS", f"画布必须是正方形，实际为 {width}x{height}")
    if width % resolution != 0:
        raise ProcessingError(
            "INVALID_CANVAS", f"画布边长 {width} 无法被目标网格 {resolution} 整除"
        )
    return image, mode
