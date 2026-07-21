import json
import zipfile
from pathlib import Path

import pytest

from aimctexturegen.packs.java_adapter import JavaPackAdapter, PackValidationError


def _metadata(pack_format: object = 34) -> str:
    return json.dumps(
        {
            "pack": {
                "pack_format": pack_format,
                "description": "AIMCTextureGen synthetic test pack",
            }
        }
    )


def test_inspects_root_pack_and_preserves_primary_format(
    pack_zip_factory, one_pixel_png
) -> None:
    source = pack_zip_factory(
        "valid.zip",
        {"assets/minecraft/textures/block/stone.png": one_pixel_png},
    )

    inspected = JavaPackAdapter().inspect(source)

    assert inspected.source_kind == "zip"
    assert inspected.pack_root.as_posix() == "."
    assert inspected.metadata.pack_format == 34
    assert inspected.normalized_files == frozenset(
        {"pack.mcmeta", "assets/minecraft/textures/block/stone.png"}
    )


def test_preserves_supported_formats_without_replacing_primary(tmp_path: Path) -> None:
    source = tmp_path / "range.zip"
    metadata = {
        "pack": {
            "pack_format": 34,
            "supported_formats": {"min_inclusive": 34, "max_inclusive": 48},
            "description": "synthetic",
        }
    }
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("pack.mcmeta", json.dumps(metadata))

    inspected = JavaPackAdapter().inspect(source)

    assert inspected.metadata.pack_format == 34
    assert inspected.metadata.supported_formats == (34, 48)


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "../escape.txt",
        "..\\escape.txt",
        "/absolute.txt",
        "C:/drive.txt",
        "C:\\drive.txt",
        "\\\\server\\share\\file.txt",
        "\\\\?\\C:\\device.txt",
        "assets/CON/file.txt",
        "assets/nul.txt",
        "assets/COM9.bin",
        "assets/lpt1/file.txt",
        ".",
        "assets/./file.txt",
        "assets//file.txt",
        "assets/file.txt:stream",
    ],
)
def test_rejects_unsafe_zip_member(tmp_path: Path, unsafe_name: str) -> None:
    source = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("pack.mcmeta", _metadata())
        archive.writestr(unsafe_name, b"unsafe")

    with pytest.raises(PackValidationError) as raised:
        JavaPackAdapter().inspect(source)

    assert raised.value.code == "UNSAFE_PACK_PATH"


def test_rejects_missing_pack_metadata(tmp_path: Path) -> None:
    source = tmp_path / "missing.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("assets/example.txt", b"synthetic")

    with pytest.raises(PackValidationError) as raised:
        JavaPackAdapter().inspect(source)

    assert raised.value.code == "PACK_METADATA_NOT_FOUND"


def test_rejects_malformed_pack_metadata(tmp_path: Path) -> None:
    source = tmp_path / "malformed.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("pack.mcmeta", "{not-json")

    with pytest.raises(PackValidationError) as raised:
        JavaPackAdapter().inspect(source)

    assert raised.value.code == "INVALID_PACK_METADATA"


def test_rejects_missing_pack_format(tmp_path: Path) -> None:
    source = tmp_path / "missing-format.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(
            "pack.mcmeta",
            json.dumps({"pack": {"description": "synthetic"}}),
        )

    with pytest.raises(PackValidationError) as raised:
        JavaPackAdapter().inspect(source)

    assert raised.value.code == "INVALID_PACK_METADATA"


@pytest.mark.parametrize("pack_format", [None, "34", True])
def test_rejects_missing_or_non_integer_pack_format(
    tmp_path: Path, pack_format: object
) -> None:
    source = tmp_path / "invalid-format.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("pack.mcmeta", _metadata(pack_format))

    with pytest.raises(PackValidationError) as raised:
        JavaPackAdapter().inspect(source)

    assert raised.value.code == "INVALID_PACK_METADATA"


def test_rejects_two_possible_pack_roots(tmp_path: Path) -> None:
    source = tmp_path / "ambiguous.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("first/pack.mcmeta", _metadata())
        archive.writestr("second/pack.mcmeta", _metadata())

    with pytest.raises(PackValidationError) as raised:
        JavaPackAdapter().inspect(source)

    assert raised.value.code == "AMBIGUOUS_PACK_ROOT"


def test_rejects_case_folding_duplicate_paths(tmp_path: Path) -> None:
    source = tmp_path / "case-conflict.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("pack.mcmeta", _metadata())
        archive.writestr("assets/example.txt", b"lower")
        archive.writestr("Assets/EXAMPLE.txt", b"upper")

    with pytest.raises(PackValidationError) as raised:
        JavaPackAdapter().inspect(source)

    assert raised.value.code == "CASE_CONFLICT"


def test_inspects_valid_directory_source(tmp_path: Path, one_pixel_png: bytes) -> None:
    source = tmp_path / "directory-pack"
    texture = source / "assets" / "minecraft" / "textures" / "block" / "stone.png"
    texture.parent.mkdir(parents=True)
    source.joinpath("pack.mcmeta").write_text(_metadata(), encoding="utf-8")
    texture.write_bytes(one_pixel_png)

    inspected = JavaPackAdapter().inspect(source)

    assert inspected.source_kind == "directory"
    assert inspected.pack_root.as_posix() == "."
    assert inspected.metadata.pack_format == 34
    assert inspected.normalized_files == frozenset(
        {"pack.mcmeta", "assets/minecraft/textures/block/stone.png"}
    )


def test_inspects_exactly_one_nested_wrapper_directory(
    tmp_path: Path, one_pixel_png: bytes
) -> None:
    source = tmp_path / "wrapped.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("synthetic-pack/pack.mcmeta", _metadata())
        archive.writestr(
            "synthetic-pack/assets/minecraft/textures/block/stone.png",
            one_pixel_png,
        )

    inspected = JavaPackAdapter().inspect(source)

    assert inspected.pack_root.as_posix() == "synthetic-pack"
    assert inspected.normalized_files == frozenset(
        {"pack.mcmeta", "assets/minecraft/textures/block/stone.png"}
    )


def test_rejects_invalid_zip_file(tmp_path: Path) -> None:
    source = tmp_path / "invalid.zip"
    source.write_bytes(b"not a zip archive")

    with pytest.raises(PackValidationError) as raised:
        JavaPackAdapter().inspect(source)

    assert raised.value.code == "INVALID_ZIP"


def test_rejects_unsupported_source(tmp_path: Path) -> None:
    source = tmp_path / "pack.txt"
    source.write_text("synthetic", encoding="utf-8")

    with pytest.raises(PackValidationError) as raised:
        JavaPackAdapter().inspect(source)

    assert raised.value.code == "UNSUPPORTED_SOURCE"
