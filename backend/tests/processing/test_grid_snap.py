from aimctexturegen.processing.grid_snap import snap_to_grid


def test_solid_cells_survive_snapping_exactly(image_from_rows):
    colors = [
        [(0, 0, 0), (255, 0, 0)],
        [(0, 255, 0), (0, 0, 255)],
    ]
    rows = []
    for cell_row in colors:
        expanded = []
        for color in cell_row:
            expanded.extend([color, color])
        rows.append(expanded)
        rows.append(list(expanded))
    canvas = image_from_rows(rows)  # 4x4 canvas, 2x2 cells

    snapped = snap_to_grid(canvas, 2)

    assert snapped.size == (2, 2)
    pixels = snapped.load()
    assert pixels[0, 0] == (0, 0, 0)
    assert pixels[1, 0] == (255, 0, 0)
    assert pixels[0, 1] == (0, 255, 0)
    assert pixels[1, 1] == (0, 0, 255)


def test_even_count_uses_lower_median_per_channel(image_from_rows):
    # One 2x2 cell with sorted red values [10, 20, 30, 40] -> lower median 20.
    rows = [
        [(10, 0, 0), (20, 0, 0)],
        [(30, 0, 0), (40, 0, 0)],
    ]
    snapped = snap_to_grid(image_from_rows(rows), 1)
    assert snapped.load()[0, 0] == (20, 0, 0)


def test_channels_are_snapped_independently(image_from_rows):
    rows = [
        [(10, 200, 3), (20, 100, 1)],
        [(30, 50, 4), (40, 150, 2)],
    ]
    # Lower medians: R [10,20,30,40]->20, G [50,100,150,200]->100, B [1,2,3,4]->2.
    snapped = snap_to_grid(image_from_rows(rows), 1)
    assert snapped.load()[0, 0] == (20, 100, 2)


def test_identity_when_canvas_side_equals_resolution(image_from_rows):
    rows = [[(1, 2, 3), (4, 5, 6)], [(7, 8, 9), (10, 11, 12)]]
    canvas = image_from_rows(rows)
    snapped = snap_to_grid(canvas, 2)
    assert list(snapped.getdata()) == list(canvas.getdata())


def test_input_canvas_is_not_mutated(image_from_rows):
    rows = [[(10, 0, 0), (20, 0, 0)], [(30, 0, 0), (40, 0, 0)]]
    canvas = image_from_rows(rows)
    before = list(canvas.getdata())
    snap_to_grid(canvas, 1)
    assert list(canvas.getdata()) == before
