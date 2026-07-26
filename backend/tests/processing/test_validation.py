import pytest
from PIL import Image

from aimctexturegen.processing.errors import ProcessingError
from aimctexturegen.processing.validation import load_rgb_canvas


def test_rgb_canvas_loads_and_reports_original_mode(png_path, solid_canvas):
    source = png_path(solid_canvas(32))
    canvas, mode = load_rgb_canvas(source, 16)
    assert mode == "RGB"
    assert canvas.mode == "RGB"
    assert canvas.size == (32, 32)
    assert canvas.load()[0, 0] == (120, 130, 140)


def test_fully_opaque_rgba_is_accepted_and_flattened(png_path):
    source = png_path(Image.new("RGBA", (32, 32), (10, 20, 30, 255)))
    canvas, mode = load_rgb_canvas(source, 16)
    assert mode == "RGBA"
    assert canvas.mode == "RGB"
    assert canvas.load()[5, 5] == (10, 20, 30)


def test_rgba_with_any_transparency_is_rejected(png_path):
    image = Image.new("RGBA", (32, 32), (10, 20, 30, 255))
    image.load()[0, 0] = (10, 20, 30, 254)
    with pytest.raises(ProcessingError) as raised:
        load_rgb_canvas(png_path(image), 16)
    assert raised.value.code == "UNSUPPORTED_IMAGE_MODE"


@pytest.mark.parametrize("mode,color", [("L", 128), ("P", 3)])
def test_other_modes_are_rejected(png_path, mode, color):
    with pytest.raises(ProcessingError) as raised:
        load_rgb_canvas(png_path(Image.new(mode, (32, 32), color)), 16)
    assert raised.value.code == "UNSUPPORTED_IMAGE_MODE"


def test_non_square_canvas_is_rejected(png_path):
    with pytest.raises(ProcessingError) as raised:
        load_rgb_canvas(png_path(Image.new("RGB", (32, 48))), 16)
    assert raised.value.code == "INVALID_CANVAS"


@pytest.mark.parametrize("side,resolution", [(24, 16), (16, 32), (100, 64)])
def test_non_divisible_side_is_rejected(png_path, side, resolution):
    with pytest.raises(ProcessingError) as raised:
        load_rgb_canvas(png_path(Image.new("RGB", (side, side))), resolution)
    assert raised.value.code == "INVALID_CANVAS"


def test_side_equal_to_resolution_is_accepted(png_path):
    canvas, _mode = load_rgb_canvas(png_path(Image.new("RGB", (16, 16))), 16)
    assert canvas.size == (16, 16)


def test_undecodable_bytes_are_rejected(tmp_path):
    source = tmp_path / "broken.png"
    source.write_bytes(b"not a png at all")
    with pytest.raises(ProcessingError) as raised:
        load_rgb_canvas(source, 16)
    assert raised.value.code == "UNDECODABLE_IMAGE"


def test_error_messages_are_chinese(png_path):
    with pytest.raises(ProcessingError) as raised:
        load_rgb_canvas(png_path(Image.new("RGB", (24, 24))), 16)
    assert any("一" <= ch <= "鿿" for ch in raised.value.message)
