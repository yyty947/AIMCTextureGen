from __future__ import annotations

from PIL import Image


def _median_low(values: list[int]) -> int:
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


def snap_to_grid(canvas: Image.Image, resolution: int) -> Image.Image:
    """Collapse each grid cell to its per-channel lower-median color."""
    cell = canvas.width // resolution
    source = canvas.load()
    snapped = Image.new("RGB", (resolution, resolution))
    target = snapped.load()
    for grid_y in range(resolution):
        for grid_x in range(resolution):
            reds: list[int] = []
            greens: list[int] = []
            blues: list[int] = []
            for y in range(grid_y * cell, (grid_y + 1) * cell):
                for x in range(grid_x * cell, (grid_x + 1) * cell):
                    red, green, blue = source[x, y]
                    reds.append(red)
                    greens.append(green)
                    blues.append(blue)
            target[grid_x, grid_y] = (
                _median_low(reds),
                _median_low(greens),
                _median_low(blues),
            )
    return snapped
