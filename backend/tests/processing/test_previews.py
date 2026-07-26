import pytest

from aimctexturegen.processing.previews import (
    PREVIEW_TARGET_SIDE,
    nearest_neighbor_preview,
    tile_3x3,
)


def _two_tone(image_from_rows, side: int):
    rows = [
        [(0, 0, 0) if x < side // 2 else (255, 255, 255) for x in range(side)]
        for _ in range(side)
    ]
    return image_from_rows(rows)


@pytest.mark.parametrize("resolution,scale", [(16, 32), (32, 16), (64, 8)])
def test_preview_upscales_to_512(image_from_rows, resolution, scale):
    preview = nearest_neighbor_preview(_two_tone(image_from_rows, resolution))
    assert preview.size == (PREVIEW_TARGET_SIDE, PREVIEW_TARGET_SIDE)
    assert preview.load()[0, 0] == (0, 0, 0)
    assert preview.load()[scale, 0] == (0, 0, 0)


def test_nearest_neighbor_introduces_no_new_colors(image_from_rows):
    texture = _two_tone(image_from_rows, 16)
    preview = nearest_neighbor_preview(texture)
    assert set(preview.getdata()) == set(texture.getdata())


def test_preview_blocks_are_exact_pixel_replication(image_from_rows):
    texture = image_from_rows([[(9, 8, 7), (1, 2, 3)], [(4, 5, 6), (10, 11, 12)]])
    # 2x2 test texture is below product resolutions but exercises replication:
    preview = nearest_neighbor_preview(texture)
    scale = PREVIEW_TARGET_SIDE // 2
    pixels = preview.load()
    assert pixels[0, 0] == (9, 8, 7)
    assert pixels[scale - 1, scale - 1] == (9, 8, 7)
    assert pixels[scale, 0] == (1, 2, 3)
    assert pixels[0, scale] == (4, 5, 6)


def test_tile_3x3_repeats_texture_and_upscales(image_from_rows):
    texture = _two_tone(image_from_rows, 16)
    tiled = tile_3x3(texture)
    assert tiled.size == (3 * PREVIEW_TARGET_SIDE, 3 * PREVIEW_TARGET_SIDE)
    pixels = tiled.load()
    # Same texel sampled from all nine tiles must be identical.
    for tile_x in range(3):
        for tile_y in range(3):
            assert pixels[tile_x * PREVIEW_TARGET_SIDE, tile_y * PREVIEW_TARGET_SIDE] == (0, 0, 0)
    assert set(tiled.getdata()) == set(texture.getdata())
