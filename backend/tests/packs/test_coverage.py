import io
import os
import subprocess
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from aimctexturegen.catalog.registry import CatalogRegistry
from aimctexturegen.packs.coverage import (
    CoverageItem,
    CoverageReport,
    CoverageValidationError,
    classify_coverage,
)


CATALOG_ROOT = Path(__file__).parents[3] / "catalogs" / "java"


def _profile():
    return CatalogRegistry(CATALOG_ROOT).for_pack_format(34)


def _write_png(path: Path, size: tuple[int, int] = (1, 1)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = io.BytesIO()
    Image.new("RGB", size, (64, 64, 64)).save(payload, format="PNG")
    path.write_bytes(payload.getvalue())


def _create_junction(link: Path, target: Path) -> None:
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"Unable to create test junction: {result.stdout}{result.stderr}")
    assert link.is_junction()


def _remove_junction(link: Path) -> None:
    if os.path.lexists(link):
        os.rmdir(link)


def test_classifies_exact_catalog_paths_and_square_unknown_candidates(
    tmp_path: Path,
) -> None:
    pack_root = tmp_path / "pack"
    _write_png(pack_root / "assets/minecraft/textures/block/stone.png")
    _write_png(pack_root / "assets/minecraft/textures/block/custom_test.png")

    report = classify_coverage(pack_root, _profile())

    assert report.covered_count == 1
    assert report.missing_count == 1
    assert report.catalog_status == "development_fixture"
    assert [(item.semantic_id, item.status) for item in report.items] == [
        ("minecraft:stone", "covered"),
        ("minecraft:deepslate", "missing"),
    ]
    assert report.unknown_paths == (
        "assets/minecraft/textures/block/custom_test.png",
    )


def test_wrongly_cased_catalog_path_does_not_cover_canonical_path(tmp_path: Path) -> None:
    pack_root = tmp_path / "pack"
    _write_png(pack_root / "assets/minecraft/textures/block/Stone.png")

    report = classify_coverage(pack_root, _profile())

    assert [(item.semantic_id, item.status) for item in report.items] == [
        ("minecraft:stone", "missing"),
        ("minecraft:deepslate", "missing"),
    ]
    assert report.unknown_paths == ("assets/minecraft/textures/block/Stone.png",)


def test_rejects_corrupt_png_at_catalog_path(tmp_path: Path) -> None:
    pack_root = tmp_path / "pack"
    corrupt = pack_root / "assets/minecraft/textures/block/stone.png"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_bytes(b"not a PNG")

    with pytest.raises(CoverageValidationError) as raised:
        classify_coverage(pack_root, _profile())

    assert raised.value.code == "INVALID_TEXTURE_PNG"
    assert raised.value.user_message == "目录材质 PNG 无法解码"


def test_excludes_invalid_and_non_square_unknown_pngs(tmp_path: Path) -> None:
    pack_root = tmp_path / "pack"
    textures = pack_root / "assets/example/textures/block"
    _write_png(textures / "rectangular.png", size=(1, 2))
    (textures / "invalid.png").write_bytes(b"not a PNG")

    report = classify_coverage(pack_root, _profile())

    assert report.unknown_paths == ()


def test_sorts_unknown_paths_deterministically(tmp_path: Path) -> None:
    pack_root = tmp_path / "pack"
    _write_png(pack_root / "assets/minecraft/textures/block/zeta.png")
    _write_png(pack_root / "assets/minecraft/textures/block/alpha.png")

    first = classify_coverage(pack_root, _profile())
    second = classify_coverage(pack_root, _profile())

    assert first == second
    assert first.unknown_paths == (
        "assets/minecraft/textures/block/alpha.png",
        "assets/minecraft/textures/block/zeta.png",
    )


def test_does_not_follow_directory_junctions(tmp_path: Path) -> None:
    pack_root = tmp_path / "pack"
    outside = tmp_path / "outside"
    _write_png(outside / "minecraft/textures/block/stone.png")
    pack_root.mkdir()
    junction = pack_root / "assets"
    _create_junction(junction, outside)

    try:
        report = classify_coverage(pack_root, _profile())
    finally:
        _remove_junction(junction)

    assert report.covered_count == 0
    assert report.unknown_paths == ()


def test_rejects_a_root_junction_before_classifying_outside_pack(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    corrupt = outside / "assets/minecraft/textures/block/stone.png"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_bytes(b"not a PNG")
    pack_root = tmp_path / "pack"
    _create_junction(pack_root, outside)

    try:
        with pytest.raises(CoverageValidationError) as raised:
            classify_coverage(pack_root, _profile())
    finally:
        _remove_junction(pack_root)

    assert raised.value.code == "UNSAFE_PACK_ROOT"
    assert raised.value.user_message == "资源包工作目录不安全"


def test_coverage_item_rejects_coercion_and_extra_fields() -> None:
    payload = {
        "semantic_id": "minecraft:stone",
        "display_name": "Stone",
        "relative_path": "assets/minecraft/textures/block/stone.png",
        "mvp_eligible": "true",
        "status": "covered",
    }

    with pytest.raises(ValidationError):
        CoverageItem.model_validate(payload)

    payload["mvp_eligible"] = True
    payload["unexpected"] = "value"
    with pytest.raises(ValidationError):
        CoverageItem.model_validate(payload)


def test_coverage_report_rejects_coercion_and_extra_fields() -> None:
    item = CoverageItem(
        semantic_id="minecraft:stone",
        display_name="Stone",
        relative_path="assets/minecraft/textures/block/stone.png",
        mvp_eligible=True,
        status="covered",
    )
    payload = {
        "catalog_id": "java-dev-format-34",
        "catalog_status": "development_fixture",
        "covered_count": "1",
        "missing_count": 0,
        "unknown_paths": (),
        "items": (item,),
    }

    with pytest.raises(ValidationError):
        CoverageReport.model_validate(payload)

    payload["covered_count"] = 1
    payload["unexpected"] = "value"
    with pytest.raises(ValidationError):
        CoverageReport.model_validate(payload)


def test_coverage_models_are_frozen() -> None:
    item = CoverageItem(
        semantic_id="minecraft:stone",
        display_name="Stone",
        relative_path="assets/minecraft/textures/block/stone.png",
        mvp_eligible=True,
        status="covered",
    )
    report = CoverageReport(
        catalog_id="java-dev-format-34",
        catalog_status="development_fixture",
        covered_count=1,
        missing_count=0,
        unknown_paths=(),
        items=(item,),
    )

    with pytest.raises(ValidationError):
        item.status = "missing"
    with pytest.raises(ValidationError):
        report.covered_count = 0
