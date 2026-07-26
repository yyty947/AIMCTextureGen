import pytest

from aimctexturegen.processing.errors import ProcessingError
from aimctexturegen.processing.palette import limit_palette


def test_limit_below_two_is_rejected(image_from_rows):
    texture = image_from_rows([[(0, 0, 0)]])
    with pytest.raises(ProcessingError) as raised:
        limit_palette(texture, 1)
    assert raised.value.code == "INVALID_PALETTE_LIMIT"


def test_texture_already_within_limit_is_returned_unchanged(image_from_rows):
    rows = [[(0, 0, 0), (255, 255, 255)], [(0, 0, 0), (255, 255, 255)]]
    texture = image_from_rows(rows)
    limited = limit_palette(texture, 4)
    assert list(limited.get_flattened_data()) == list(texture.get_flattened_data())
    assert limited is not texture


def test_median_cut_two_clusters_is_exact(image_from_rows):
    # Dark cluster {0, 10} and light cluster {240, 250} on the red-dominant axis.
    rows = [
        [(0, 0, 0), (10, 10, 10)],
        [(250, 250, 250), (240, 240, 240)],
    ]
    limited = limit_palette(image_from_rows(rows), 2)
    # Box split at the pixel-count midpoint; representatives are weighted
    # lower medians: dark -> (0, 0, 0), light -> (240, 240, 240).
    assert list(limited.get_flattened_data()) == [
        (0, 0, 0),
        (0, 0, 0),
        (240, 240, 240),
        (240, 240, 240),
    ]


def test_result_never_exceeds_limit(image_from_rows):
    rows = [
        [(r * 16, g * 16, (r + g) * 8) for r in range(8)]
        for g in range(8)
    ]
    limited = limit_palette(image_from_rows(rows), 5)
    assert len(set(limited.get_flattened_data())) <= 5


def test_limit_palette_is_deterministic(image_from_rows):
    rows = [
        [((x * 37) % 256, (y * 91) % 256, (x * y) % 256) for x in range(16)]
        for y in range(16)
    ]
    first = limit_palette(image_from_rows(rows), 8)
    second = limit_palette(image_from_rows(rows), 8)
    assert list(first.get_flattened_data()) == list(second.get_flattened_data())


def test_input_texture_is_not_mutated(image_from_rows):
    rows = [[(0, 0, 0), (10, 10, 10)], [(250, 250, 250), (240, 240, 240)]]
    texture = image_from_rows(rows)
    before = list(texture.get_flattened_data())
    limit_palette(texture, 2)
    assert list(texture.get_flattened_data()) == before
