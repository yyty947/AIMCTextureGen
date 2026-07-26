from __future__ import annotations

from PIL import Image

PREVIEW_TARGET_SIDE = 512


def _scale_for(side: int) -> int:
    return PREVIEW_TARGET_SIDE // side


def nearest_neighbor_preview(texture: Image.Image) -> Image.Image:
    scale = _scale_for(texture.width)
    return texture.resize(
        (texture.width * scale, texture.height * scale), Image.Resampling.NEAREST
    )


def tile_3x3(texture: Image.Image) -> Image.Image:
    side = texture.width
    tiled = Image.new("RGB", (side * 3, side * 3))
    for tile_y in range(3):
        for tile_x in range(3):
            tiled.paste(texture, (tile_x * side, tile_y * side))
    scale = _scale_for(side)
    return tiled.resize((tiled.width * scale, tiled.height * scale), Image.Resampling.NEAREST)
