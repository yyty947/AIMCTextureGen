import math

from aimctexturegen.processing.seam import seam_scores


def test_solid_texture_scores_zero(image_from_rows):
    texture = image_from_rows([[(90, 90, 90)] * 4 for _ in range(4)])
    assert seam_scores(texture) == (0.0, 0.0, 0.0)


def test_black_left_white_right_maximizes_horizontal_only(image_from_rows):
    rows = [[(0, 0, 0), (0, 0, 0), (255, 255, 255), (255, 255, 255)] for _ in range(4)]
    horizontal, vertical, average = seam_scores(image_from_rows(rows))
    assert horizontal == 1.0
    assert vertical == 0.0
    assert average == 0.5


def test_single_channel_edge_difference_matches_formula(image_from_rows):
    # Left edge (0,0,0) vs right edge (51,0,0): distance 51 / (sqrt(3)*255).
    rows = [[(0, 0, 0), (51, 0, 0)] for _ in range(2)]
    horizontal, vertical, _average = seam_scores(image_from_rows(rows))
    expected = round(51 / (math.sqrt(3) * 255), 6)
    assert horizontal == expected
    assert vertical == 0.0


def test_seamed_texture_scores_higher_than_seamless(image_from_rows):
    seamless = image_from_rows([[(40, 40, 40)] * 4 for _ in range(4)])
    seamed = image_from_rows(
        [[(0, 0, 0)] * 4, [(0, 0, 0)] * 4, [(0, 0, 0)] * 4, [(200, 200, 200)] * 4]
    )
    assert seam_scores(seamed)[1] > seam_scores(seamless)[1]


def test_scores_are_rounded_to_six_decimals(image_from_rows):
    rows = [[(0, 0, 0), (1, 2, 3)] for _ in range(2)]
    horizontal, _vertical, average = seam_scores(image_from_rows(rows))
    assert horizontal == round(horizontal, 6)
    assert average == round(average, 6)
