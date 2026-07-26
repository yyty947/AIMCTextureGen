from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture
def image_from_rows():
    def _build(rows):
        height = len(rows)
        width = len(rows[0])
        image = Image.new("RGB", (width, height))
        pixels = image.load()
        for y, row in enumerate(rows):
            assert len(row) == width
            for x, color in enumerate(row):
                pixels[x, y] = color
        return image

    return _build


@pytest.fixture
def png_path(tmp_path):
    def _write(image: Image.Image, name: str = "src.png") -> Path:
        target = tmp_path / name
        image.save(target, format="PNG")
        return target

    return _write


@pytest.fixture
def solid_canvas(image_from_rows):
    def _build(side: int, color=(120, 130, 140)) -> Image.Image:
        return image_from_rows([[color] * side for _ in range(side)])

    return _build
