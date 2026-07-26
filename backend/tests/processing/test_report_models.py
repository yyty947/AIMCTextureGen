import pydantic
import pytest

from aimctexturegen.processing.models import (
    ALGORITHM_VERSION,
    SCHEMA_VERSION,
    SCORE_DECIMALS,
    GridSnapInfo,
    ImageRef,
    PaletteInfo,
    PreviewRefs,
    ProcessingReport,
    ProcessorInfo,
    SeamScoreInfo,
    dump_report_json,
)


def _image_ref(name: str) -> ImageRef:
    return ImageRef(path=name, sha256="ab" * 32, width=16, height=16)


def _report() -> ProcessingReport:
    return ProcessingReport(
        schema_version=1,
        processor=ProcessorInfo(algorithm_version=ALGORITHM_VERSION, pillow_version="12.3.0"),
        input=ImageRef(path="raw.png", sha256="cd" * 32, width=1024, height=1024),
        input_mode="RGB",
        output=_image_ref("final.png"),
        resolution=16,
        grid_snap=GridSnapInfo(method="per-channel-median-low", cell_pixels=64),
        palette=PaletteInfo(unique_colors=7, limit=None, method=None),
        seam_score=SeamScoreInfo(horizontal=0.25, vertical=0.5, average=0.375),
        previews=PreviewRefs(
            nearest_neighbor=_image_ref("final-nn.png"),
            tile_3x3=_image_ref("final-tile.png"),
        ),
    )


def test_constants_match_phase_2_contract():
    assert SCHEMA_VERSION == 1
    assert ALGORITHM_VERSION == 1
    assert SCORE_DECIMALS == 6


def test_report_round_trips_through_deterministic_json():
    report = _report()
    payload = dump_report_json(report)
    assert ProcessingReport.model_validate_json(payload) == report


def test_dump_is_sorted_compact_utf8_with_trailing_newline():
    payload = dump_report_json(_report())
    text = payload.decode("utf-8")
    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    body = text[:-1]
    assert "\n" not in body
    assert ": " not in body and ", " not in body
    assert body.index('"grid_snap"') < body.index('"input"') < body.index('"palette"')


def test_dump_is_byte_stable_across_calls():
    assert dump_report_json(_report()) == dump_report_json(_report())


def test_report_is_frozen_and_rejects_unknown_fields():
    report = _report()
    with pytest.raises(pydantic.ValidationError):
        report.schema_version = 2  # type: ignore[misc]
    with pytest.raises(pydantic.ValidationError):
        ProcessingReport.model_validate({**report.model_dump(), "extra_field": True})


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 2),
        ("resolution", 48),
        ("input_mode", "P"),
    ],
)
def test_report_rejects_out_of_contract_values(field, value):
    data = _report().model_dump()
    data[field] = value
    with pytest.raises(pydantic.ValidationError):
        ProcessingReport.model_validate(data)


def test_image_ref_rejects_malformed_sha256():
    with pytest.raises(pydantic.ValidationError):
        ImageRef(path="x.png", sha256="ZZ" * 32, width=1, height=1)


def test_seam_scores_must_stay_normalized():
    with pytest.raises(pydantic.ValidationError):
        SeamScoreInfo(horizontal=1.5, vertical=0.0, average=0.75)


def test_palette_limit_must_be_at_least_two_when_present():
    with pytest.raises(pydantic.ValidationError):
        PaletteInfo(unique_colors=3, limit=1, method="median-cut")
