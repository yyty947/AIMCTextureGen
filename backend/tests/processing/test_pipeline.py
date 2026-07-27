import hashlib

import pytest
from PIL import Image

from aimctexturegen.processing.errors import ProcessingError
from aimctexturegen.processing.models import ProcessingReport
from aimctexturegen.processing import pipeline
from aimctexturegen.processing.pipeline import process_candidate


def _cell_canvas(image_from_rows, png_path, name="src.png"):
    # 32x32 canvas, resolution 16 -> 2x2 solid cells with distinct colors.
    cell_colors = [
        [((x * 13) % 256, (y * 29) % 256, (x + y) % 256) for x in range(16)]
        for y in range(16)
    ]
    rows = []
    for cell_row in cell_colors:
        expanded = []
        for color in cell_row:
            expanded.extend([color, color])
        rows.append(expanded)
        rows.append(list(expanded))
    return png_path(image_from_rows(rows), name), cell_colors


def test_pipeline_writes_all_artifacts_and_report(tmp_path, image_from_rows, png_path):
    source, cell_colors = _cell_canvas(image_from_rows, png_path)
    output = tmp_path / "out"

    report = process_candidate(source, output, stem="candidate-0", resolution=16)

    final = Image.open(output / "candidate-0.png")
    assert final.size == (16, 16)
    expected = [color for row in cell_colors for color in row]
    assert list(final.get_flattened_data()) == expected

    assert (output / "candidate-0-nn.png").exists()
    assert (output / "candidate-0-tile.png").exists()
    assert (output / "candidate-0-report.json").exists()
    assert not list(output.glob("*.tmp"))

    assert report.schema_version == 1
    assert report.resolution == 16
    assert report.input_mode == "RGB"
    assert report.input.path == "src.png"
    assert report.output.path == "candidate-0.png"
    assert report.grid_snap.cell_pixels == 2
    assert report.palette.limit is None and report.palette.method is None
    assert report.palette.unique_colors == len(set(final.get_flattened_data()))
    assert report.previews.nearest_neighbor.path == "candidate-0-nn.png"
    assert report.previews.tile_3x3.path == "candidate-0-tile.png"


def test_report_hashes_match_written_files(tmp_path, image_from_rows, png_path):
    source, _cells = _cell_canvas(image_from_rows, png_path)
    output = tmp_path / "out"
    report = process_candidate(source, output, stem="c", resolution=16)
    for ref in (report.output, report.previews.nearest_neighbor, report.previews.tile_3x3):
        digest = hashlib.sha256((output / ref.path).read_bytes()).hexdigest()
        assert digest == ref.sha256
    assert report.input.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()


def test_report_json_round_trips_to_equal_report(tmp_path, image_from_rows, png_path):
    source, _cells = _cell_canvas(image_from_rows, png_path)
    output = tmp_path / "out"
    report = process_candidate(source, output, stem="c", resolution=16)
    loaded = ProcessingReport.model_validate_json(
        (output / "c-report.json").read_bytes()
    )
    assert loaded == report


def test_double_run_is_byte_identical(tmp_path, image_from_rows, png_path):
    source, _cells = _cell_canvas(image_from_rows, png_path)
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    first = process_candidate(source, first_dir, stem="c", resolution=16, palette_limit=8)
    second = process_candidate(source, second_dir, stem="c", resolution=16, palette_limit=8)

    assert first == second
    for name in ("c.png", "c-nn.png", "c-tile.png", "c-report.json"):
        assert (first_dir / name).read_bytes() == (second_dir / name).read_bytes()


def test_palette_limit_is_applied_and_reported(tmp_path, image_from_rows, png_path):
    source, _cells = _cell_canvas(image_from_rows, png_path)
    output = tmp_path / "out"
    report = process_candidate(source, output, stem="c", resolution=16, palette_limit=8)
    final = Image.open(output / "c.png")
    assert len(set(final.get_flattened_data())) <= 8
    assert report.palette.limit == 8
    assert report.palette.method == "median-cut"
    assert report.palette.unique_colors == len(set(final.get_flattened_data()))


def test_seam_scores_in_report_match_final_texture(tmp_path, image_from_rows, png_path):
    from aimctexturegen.processing.seam import seam_scores

    source, _cells = _cell_canvas(image_from_rows, png_path)
    output = tmp_path / "out"
    report = process_candidate(source, output, stem="c", resolution=16)
    final = Image.open(output / "c.png").convert("RGB")
    horizontal, vertical, average = seam_scores(final)
    assert (report.seam_score.horizontal, report.seam_score.vertical, report.seam_score.average) == (
        horizontal,
        vertical,
        average,
    )


@pytest.mark.parametrize(
    ("resolution", "stem", "expected_code"),
    [
        (48, "candidate", "INVALID_RESOLUTION"),
        (16, "", "INVALID_OUTPUT_STEM"),
        (16, "../candidate", "INVALID_OUTPUT_STEM"),
        (16, "folder/candidate", "INVALID_OUTPUT_STEM"),
        (16, "folder\\candidate", "INVALID_OUTPUT_STEM"),
    ],
)
def test_pipeline_rejects_invalid_output_values_before_creating_directory(
    tmp_path, resolution, stem, expected_code
):
    output = tmp_path / "out"

    with pytest.raises(ProcessingError) as error:
        process_candidate(
            tmp_path / "not-decoded.png", output, stem=stem, resolution=resolution
        )

    assert error.value.code == expected_code
    assert not output.exists()


def test_pipeline_accepts_opaque_rgba_and_records_original_mode(tmp_path, png_path):
    source = png_path(Image.new("RGBA", (32, 32), (10, 20, 30, 255)))

    report = process_candidate(source, tmp_path / "out", stem="c", resolution=16)

    assert report.input_mode == "RGBA"
    with Image.open(tmp_path / "out" / "c.png") as final:
        assert final.mode == "RGB"


def test_pipeline_removes_temporary_file_when_replacement_fails(
    tmp_path, image_from_rows, png_path, monkeypatch
):
    source, _cells = _cell_canvas(image_from_rows, png_path)
    output = tmp_path / "out"
    original_replace = pipeline.os.replace
    replacements = 0

    def fail_second_replacement(source_path, destination_path):
        nonlocal replacements
        replacements += 1
        if replacements == 2:
            raise OSError("replace blocked")
        original_replace(source_path, destination_path)

    monkeypatch.setattr(pipeline.os, "replace", fail_second_replacement)

    with pytest.raises(OSError, match="replace blocked"):
        process_candidate(source, output, stem="c", resolution=16)

    assert not list(output.glob("*.tmp"))
