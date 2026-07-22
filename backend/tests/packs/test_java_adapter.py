import json
import os
import struct
import subprocess
import warnings
import zipfile
from pathlib import Path

import pytest

from aimctexturegen.packs.java_adapter import JavaPackAdapter, PackValidationError
import aimctexturegen.packs.java_adapter as adapter_module


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
    "supported_formats",
    [
        [34, 48],
        {"min_inclusive": 34},
        {"min_inclusive": 34, "max_inclusive": 48, "unexpected": 49},
        {"min_inclusive": True, "max_inclusive": 48},
        {"min_inclusive": "34", "max_inclusive": 48},
        {"min_inclusive": 48, "max_inclusive": 34},
    ],
    ids=[
        "list-shape",
        "missing-bound",
        "extra-bound",
        "boolean-bound",
        "string-bound",
        "reversed-range",
    ],
)
def test_rejects_invalid_supported_formats(
    tmp_path: Path, supported_formats: object
) -> None:
    source = tmp_path / "invalid-range.zip"
    metadata = {
        "pack": {
            "pack_format": 34,
            "supported_formats": supported_formats,
            "description": "synthetic",
        }
    }
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("pack.mcmeta", json.dumps(metadata))

    with pytest.raises(PackValidationError) as raised:
        JavaPackAdapter().inspect(source)

    assert raised.value.code == "INVALID_PACK_METADATA"


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


def test_rejects_root_plus_wrapper_pack_roots(tmp_path: Path) -> None:
    source = tmp_path / "root-and-wrapper.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("pack.mcmeta", _metadata())
        archive.writestr("wrapper/pack.mcmeta", _metadata())

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


def test_rejects_exact_duplicate_file_names(tmp_path: Path) -> None:
    source = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("pack.mcmeta", _metadata())
        archive.writestr("assets/example.txt", b"first")
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"Duplicate name: 'assets/example\.txt'",
                category=UserWarning,
                module="zipfile",
            )
            archive.writestr("assets/example.txt", b"second")

    with pytest.raises(PackValidationError) as raised:
        JavaPackAdapter().inspect(source)

    assert raised.value.code == "CASE_CONFLICT"


def test_rejects_file_directory_topology_collision(tmp_path: Path) -> None:
    source = tmp_path / "topology-conflict.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("pack.mcmeta", _metadata())
        archive.writestr("assets", b"file")
        archive.writestr("assets/example.txt", b"child")

    with pytest.raises(PackValidationError) as raised:
        JavaPackAdapter().inspect(source)

    assert raised.value.code == "CASE_CONFLICT"


def test_rejects_parent_directory_case_conflict(tmp_path: Path) -> None:
    source = tmp_path / "parent-case-conflict.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("pack.mcmeta", _metadata())
        archive.writestr("Assets/first.txt", b"first")
        archive.writestr("assets/second.txt", b"second")

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


def _create_directory_link_or_junction(link: Path, target: Path) -> str:
    try:
        link.symlink_to(target, target_is_directory=True)
        return "symlink"
    except OSError as symlink_error:
        if os.name != "nt":
            pytest.skip(
                "Directory-link fixture requires symlink privilege: "
                f"{symlink_error}"
            )
        junction = subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if junction.returncode != 0:
            detail = junction.stderr.strip() or junction.stdout.strip()
            pytest.skip(
                "Directory-link containment test requires symlink privilege "
                f"or junction creation support: {symlink_error}; {detail}"
            )
        return "junction"


def test_rejects_directory_link_or_junction_escaping_selected_root(
    tmp_path: Path,
) -> None:
    source = tmp_path / "directory-pack"
    source.mkdir()
    source.joinpath("pack.mcmeta").write_text(_metadata(), encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.joinpath("external.txt").write_text("synthetic", encoding="utf-8")
    link_kind = _create_directory_link_or_junction(source / "escape", outside)

    with pytest.raises(PackValidationError) as raised:
        JavaPackAdapter().inspect(source)

    assert link_kind in {"symlink", "junction"}
    assert raised.value.code == "UNSAFE_PACK_PATH"


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


def test_inspects_nested_wrapper_directory_source(
    tmp_path: Path, one_pixel_png: bytes
) -> None:
    source = tmp_path / "wrapped-directory"
    wrapper = source / "synthetic-pack"
    texture = wrapper / "assets" / "minecraft" / "textures" / "block" / "stone.png"
    texture.parent.mkdir(parents=True)
    wrapper.joinpath("pack.mcmeta").write_text(_metadata(), encoding="utf-8")
    texture.write_bytes(one_pixel_png)

    inspected = JavaPackAdapter().inspect(source)

    assert inspected.pack_root.as_posix() == "synthetic-pack"
    assert inspected.normalized_files == frozenset(
        {"pack.mcmeta", "assets/minecraft/textures/block/stone.png"}
    )


def test_translates_unsupported_zip_compression(tmp_path: Path) -> None:
    source = tmp_path / "unsupported-compression.zip"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("pack.mcmeta", _metadata())

    payload = bytearray(source.read_bytes())
    local_header = payload.index(b"PK\x03\x04")
    central_header = payload.index(b"PK\x01\x02")
    struct.pack_into("<H", payload, local_header + 8, 99)
    struct.pack_into("<H", payload, central_header + 10, 99)
    source.write_bytes(payload)

    with pytest.raises(PackValidationError) as raised:
        JavaPackAdapter().inspect(source)

    assert raised.value.code == "UNSUPPORTED_ZIP_COMPRESSION"


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


def _central_header_offset(payload: bytearray, member_name: str) -> int:
    offset = 0
    encoded_name = member_name.encode()
    while True:
        offset = payload.find(b"PK\x01\x02", offset)
        assert offset >= 0
        filename_length = struct.unpack_from("<H", payload, offset + 28)[0]
        name = bytes(payload[offset + 46 : offset + 46 + filename_length])
        if name == encoded_name:
            return offset
        offset += 46 + filename_length


def _patch_central_sizes(
    path: Path,
    member_name: str,
    *,
    compressed: int | None = None,
    uncompressed: int | None = None,
) -> None:
    payload = bytearray(path.read_bytes())
    offset = _central_header_offset(payload, member_name)
    if compressed is not None:
        struct.pack_into("<I", payload, offset + 20, compressed)
    if uncompressed is not None:
        struct.pack_into("<I", payload, offset + 24, uncompressed)
    path.write_bytes(payload)


def test_rejects_archive_member_count_before_reading_payloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter_module, "MAX_ZIP_MEMBERS", 3, raising=False)
    source = tmp_path / "too-many.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("pack.mcmeta", _metadata())
        archive.writestr("one.bin", b"1")
        archive.writestr("two.bin", b"2")
        archive.writestr("three.bin", b"3")

    with pytest.raises(PackValidationError) as raised:
        JavaPackAdapter().inspect(source)

    assert raised.value.code == "ZIP_MEMBER_COUNT_EXCEEDED"


def test_rejects_oversize_metadata_from_central_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter_module, "MAX_PACK_METADATA_BYTES", 64, raising=False)
    source = tmp_path / "metadata-too-large.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("pack.mcmeta", _metadata())
    _patch_central_sizes(source, "pack.mcmeta", uncompressed=65)

    with pytest.raises(PackValidationError) as raised:
        JavaPackAdapter().inspect(source)

    assert raised.value.code == "PACK_METADATA_TOO_LARGE"


def test_rejects_oversize_member_and_total_expansion_from_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "expansion.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("pack.mcmeta", _metadata())
        archive.writestr("one.bin", b"1")
        archive.writestr("two.bin", b"2")

    monkeypatch.setattr(adapter_module, "MAX_ZIP_MEMBER_BYTES", 64, raising=False)
    _patch_central_sizes(source, "one.bin", compressed=65, uncompressed=65)
    with pytest.raises(PackValidationError) as member_error:
        JavaPackAdapter().inspect(source)
    assert member_error.value.code == "ZIP_MEMBER_TOO_LARGE"

    monkeypatch.setattr(adapter_module, "MAX_ZIP_MEMBER_BYTES", 128, raising=False)
    monkeypatch.setattr(
        adapter_module,
        "MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES",
        100,
        raising=False,
    )
    _patch_central_sizes(source, "one.bin", compressed=51, uncompressed=51)
    _patch_central_sizes(source, "two.bin", compressed=51, uncompressed=51)
    with pytest.raises(PackValidationError) as total_error:
        JavaPackAdapter().inspect(source)
    assert total_error.value.code == "ZIP_TOTAL_SIZE_EXCEEDED"


def test_rejects_high_compression_ratio(tmp_path: Path) -> None:
    source = tmp_path / "ratio.zip"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("pack.mcmeta", _metadata())
        archive.writestr("highly-compressible.bin", b"0" * (2 * 1024 * 1024))

    with pytest.raises(PackValidationError) as raised:
        JavaPackAdapter().inspect(source)

    assert raised.value.code == "ZIP_COMPRESSION_RATIO_EXCEEDED"


def test_rejects_encrypted_member_flag_with_stable_error(tmp_path: Path) -> None:
    source = tmp_path / "encrypted-flag.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("pack.mcmeta", _metadata())
        archive.writestr("encrypted.bin", b"not really encrypted")
    payload = bytearray(source.read_bytes())
    with zipfile.ZipFile(source) as archive:
        info = archive.getinfo("encrypted.bin")
    local_flags = struct.unpack_from("<H", payload, info.header_offset + 6)[0]
    struct.pack_into("<H", payload, info.header_offset + 6, local_flags | 1)
    central = _central_header_offset(payload, "encrypted.bin")
    central_flags = struct.unpack_from("<H", payload, central + 8)[0]
    struct.pack_into("<H", payload, central + 8, central_flags | 1)
    source.write_bytes(payload)

    with pytest.raises(PackValidationError) as raised:
        JavaPackAdapter().inspect(source)

    assert raised.value.code == "ENCRYPTED_ZIP_MEMBER"
