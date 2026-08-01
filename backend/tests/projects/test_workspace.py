import io
import json
import os
import shutil
import subprocess
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image
from pydantic import ValidationError

import aimctexturegen.projects.workspace as workspace_module
from aimctexturegen.catalog.registry import CatalogRegistry
from aimctexturegen.packs.java_adapter import JavaPackAdapter, PackValidationError
from aimctexturegen.projects.models import (
    MAX_PROJECT_MANIFEST_BYTES,
    ProjectManifest,
    load_project_manifest,
)
from aimctexturegen.projects.workspace import ProjectWorkspace


CATALOG_ROOT = Path(__file__).parents[3] / "catalogs" / "java"


@pytest.fixture
def pack_zip_factory(tmp_path: Path) -> Callable[[str, dict[str, bytes], int], Path]:
    def create(name: str, members: dict[str, bytes], pack_format: int = 34) -> Path:
        path = tmp_path / name
        payload = {
            "pack": {
                "pack_format": pack_format,
                "description": "AIMCTextureGen synthetic test pack",
            }
        }
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("pack.mcmeta", json.dumps(payload))
            for member_name, data in members.items():
                archive.writestr(member_name, data)
        return path

    return create


@pytest.fixture
def one_pixel_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1), (64, 64, 64)).save(buffer, format="PNG")
    return buffer.getvalue()


def build_workspace(root: Path, adapter: JavaPackAdapter | None = None) -> ProjectWorkspace:
    return ProjectWorkspace(
        root,
        adapter or JavaPackAdapter(),
        CatalogRegistry(CATALOG_ROOT),
    )


def _create_junction(link: Path, target: Path) -> None:
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            f"Unable to create test junction: {result.stdout}{result.stderr}"
        )
    assert link.is_junction()


def _remove_junction(link: Path) -> None:
    if os.path.lexists(link):
        os.rmdir(link)


def test_import_creates_snapshot_and_working_copy(
    tmp_path: Path,
    pack_zip_factory: Callable[[str, dict[str, bytes], int], Path],
    one_pixel_png: bytes,
) -> None:
    source = pack_zip_factory(
        "source.zip",
        {"assets/minecraft/textures/block/stone.png": one_pixel_png},
    )
    source_hash_before = sha256(source.read_bytes()).hexdigest()
    workspace = build_workspace(tmp_path / "projects")

    manifest = workspace.import_pack(source, "Synthetic Pack")

    assert manifest is not None
    project_root = tmp_path / "projects" / str(manifest.project_id)
    assert manifest.project_name == "Synthetic Pack"
    assert manifest.java_pack_format == 34
    assert manifest.catalog_id == "java-dev-format-34"
    assert manifest.source_sha256 == source_hash_before
    assert (project_root / "source" / "imported-pack.zip").is_file()
    assert (project_root / "source" / "imported-pack.zip").read_bytes() == source.read_bytes()
    assert (project_root / "pack" / "pack.mcmeta").is_file()
    assert (
        project_root / "pack" / "assets/minecraft/textures/block/stone.png"
    ).read_bytes() == one_pixel_png
    assert source_hash_before == sha256(source.read_bytes()).hexdigest()
    assert not list((tmp_path / "projects").glob("*.tmp"))
    manifest_document = json.loads((project_root / "project.json").read_text("utf-8"))
    assert (project_root / "project.json").stat().st_size < MAX_PROJECT_MANIFEST_BYTES
    assert set(manifest_document) == {
        "schema_version",
        "project_id",
        "project_name",
        "edition",
        "java_pack_format",
        "supported_formats",
        "catalog_id",
        "source_sha256",
        "created_at",
        "updated_at",
        "default_resolution",
        "default_parallelism",
        "style_references",
    }
    persisted, migrated = load_project_manifest(
        (project_root / "project.json").read_bytes()
    )
    assert persisted == manifest
    assert migrated is False
    assert persisted.schema_version == 2
    assert persisted.default_resolution == 16
    assert persisted.default_parallelism == 1
    assert persisted.style_references == ()
    assert persisted.edition == "java"
    assert persisted.created_at.utcoffset() is not None
    assert persisted.updated_at.utcoffset() is not None


def test_failed_validation_leaves_no_project_directory(tmp_path: Path) -> None:
    source = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(
            "pack.mcmeta",
            '{"pack":{"pack_format":34,"description":"synthetic"}}',
        )
        archive.writestr("../escape.txt", b"unsafe")
    projects_root = tmp_path / "projects"

    with pytest.raises(PackValidationError, match="资源包包含不安全的路径"):
        build_workspace(projects_root).import_pack(source, "Unsafe Pack")

    assert not projects_root.exists()


def test_directory_import_produces_deterministic_snapshot(
    tmp_path: Path,
    one_pixel_png: bytes,
) -> None:
    source = tmp_path / "directory-pack"
    texture = source / "assets" / "minecraft" / "textures" / "block" / "stone.png"
    texture.parent.mkdir(parents=True)
    metadata = b'{"pack":{"pack_format":34,"description":"synthetic"}}'
    (source / "pack.mcmeta").write_bytes(metadata)
    texture.write_bytes(one_pixel_png)
    source_hashes_before = {
        path.relative_to(source).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(source.rglob("*"))
        if path.is_file()
    }
    workspace = build_workspace(tmp_path / "projects")

    first = workspace.import_pack(source, "Directory One")
    second = workspace.import_pack(source, "Directory Two")

    first_snapshot = (
        tmp_path / "projects" / str(first.project_id) / "source" / "imported-pack.zip"
    )
    second_snapshot = (
        tmp_path / "projects" / str(second.project_id) / "source" / "imported-pack.zip"
    )
    assert first_snapshot.read_bytes() == second_snapshot.read_bytes()
    assert first.source_sha256 == sha256(first_snapshot.read_bytes()).hexdigest()
    assert second.source_sha256 == first.source_sha256
    with zipfile.ZipFile(first_snapshot) as archive:
        assert archive.namelist() == [
            "assets/minecraft/textures/block/stone.png",
            "pack.mcmeta",
        ]
        assert archive.read("pack.mcmeta") == metadata
        assert archive.read("assets/minecraft/textures/block/stone.png") == one_pixel_png
    assert source_hashes_before == {
        path.relative_to(source).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(source.rglob("*"))
        if path.is_file()
    }


def test_project_name_separators_do_not_affect_generated_paths(
    tmp_path: Path,
    pack_zip_factory: Callable[[str, dict[str, bytes], int], Path],
) -> None:
    source = pack_zip_factory("source.zip", {})
    projects_root = tmp_path / "projects"
    project_name = r"..\outside/child"

    manifest = build_workspace(projects_root).import_pack(source, project_name)

    assert manifest is not None
    assert manifest.project_name == project_name
    assert (projects_root / str(manifest.project_id) / "project.json").is_file()
    assert not (tmp_path / "outside").exists()
    assert {path.name for path in projects_root.iterdir()} == {str(manifest.project_id)}


def test_empty_trimmed_project_name_is_rejected_before_creating_root(
    tmp_path: Path,
    pack_zip_factory: Callable[[str, dict[str, bytes], int], Path],
) -> None:
    source = pack_zip_factory("source.zip", {})
    projects_root = tmp_path / "projects"

    with pytest.raises(PackValidationError) as raised:
        build_workspace(projects_root).import_pack(source, " \t\r\n ")

    assert raised.value.code == "INVALID_PROJECT_NAME"
    assert raised.value.user_message == "项目名称不能为空"
    assert not projects_root.exists()


def test_project_name_length_boundary_and_oversize_rejection(
    tmp_path: Path,
    pack_zip_factory: Callable[[str, dict[str, bytes], int], Path],
) -> None:
    source = pack_zip_factory("source.zip", {})
    projects_root = tmp_path / "projects"
    workspace = build_workspace(projects_root)

    accepted = workspace.import_pack(source, "x" * 128)
    assert accepted.project_name == "x" * 128

    with pytest.raises(PackValidationError) as raised:
        workspace.import_pack(source, "x" * 129)

    assert raised.value.code == "INVALID_PROJECT_NAME"
    assert [path.name for path in projects_root.iterdir()] == [str(accepted.project_id)]


def test_workspace_counts_non_bmp_project_name_as_code_points(
    tmp_path: Path,
    pack_zip_factory: Callable[[str, dict[str, bytes], int], Path],
) -> None:
    source = pack_zip_factory("unicode-name.zip", {})
    projects_root = tmp_path / "projects"
    workspace = build_workspace(projects_root)

    accepted = workspace.import_pack(source, "😀" * 128)
    assert accepted.project_name == "😀" * 128

    with pytest.raises(PackValidationError) as raised:
        workspace.import_pack(source, "😀" * 129)
    assert raised.value.code == "INVALID_PROJECT_NAME"


class _DeletingAdapter(JavaPackAdapter):
    def inspect(self, source: Path):
        inspected = super().inspect(source)
        source.unlink()
        return inspected


def test_copy_failure_cleans_only_staged_project_directory(
    tmp_path: Path,
    pack_zip_factory: Callable[[str, dict[str, bytes], int], Path],
) -> None:
    source = pack_zip_factory("source.zip", {})
    projects_root = tmp_path / "projects"
    unrelated = projects_root / "keep-me"
    unrelated.mkdir(parents=True)
    (unrelated / "sentinel.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        build_workspace(projects_root, _DeletingAdapter()).import_pack(
            source, "Copy failure"
        )

    assert (unrelated / "sentinel.txt").read_text(encoding="utf-8") == "keep"
    assert {path.name for path in projects_root.iterdir()} == {"keep-me"}


def test_cleanup_never_follows_replaced_temp_directory_junction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pack_zip_factory: Callable[[str, dict[str, bytes], int], Path],
) -> None:
    source = pack_zip_factory("source.zip", {})
    projects_root = tmp_path / "projects"
    unrelated = projects_root / "unrelated"
    unrelated.mkdir(parents=True)
    sentinel = unrelated / "sentinel.txt"
    sentinel.write_text("must survive", encoding="utf-8")
    workspace = build_workspace(projects_root)

    def replace_temp_with_junction(_inspected, destination: Path) -> str:
        temporary_root = destination.parents[1]
        shutil.rmtree(temporary_root)
        _create_junction(temporary_root, unrelated)
        raise OSError("forced failure after temp junction substitution")

    monkeypatch.setattr(workspace, "_create_snapshot", replace_temp_with_junction)

    with pytest.raises(OSError, match="forced failure"):
        workspace.import_pack(source, "Junction cleanup attack")

    temporary_roots = [
        path for path in projects_root.iterdir() if path.name.endswith(".tmp")
    ]
    try:
        assert sentinel.is_file()
        assert sentinel.read_text(encoding="utf-8") == "must survive"
        assert len(temporary_roots) == 1
        assert temporary_roots[0].is_junction()
    finally:
        for temporary_root in temporary_roots:
            _remove_junction(temporary_root)


def test_pack_junction_substitution_cannot_write_outside_temp_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pack_zip_factory: Callable[[str, dict[str, bytes], int], Path],
    one_pixel_png: bytes,
) -> None:
    source = pack_zip_factory(
        "source.zip",
        {"assets/minecraft/textures/block/stone.png": one_pixel_png},
    )
    projects_root = tmp_path / "projects"
    outside = tmp_path / "outside-pack"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("must survive", encoding="utf-8")
    workspace = build_workspace(projects_root)
    create_snapshot = workspace._create_snapshot

    def substitute_pack_junction(inspected, destination: Path) -> str:
        source_hash = create_snapshot(inspected, destination)
        pack_directory = destination.parents[1] / "pack"
        pack_directory.rmdir()
        _create_junction(pack_directory, outside)
        return source_hash

    monkeypatch.setattr(workspace, "_create_snapshot", substitute_pack_junction)

    with pytest.raises(PackValidationError) as raised:
        workspace.import_pack(source, "Pack junction attack")

    project_directories = list(projects_root.iterdir())
    try:
        assert raised.value.code == "UNSAFE_PROJECT_PATH"
        assert sentinel.read_text(encoding="utf-8") == "must survive"
        assert not (outside / "pack.mcmeta").exists()
        assert not (outside / "assets").exists()
        assert all(path.name.endswith(".tmp") for path in project_directories)
    finally:
        for project_directory in project_directories:
            pack_directory = project_directory / "pack"
            if pack_directory.is_junction():
                _remove_junction(pack_directory)
            if project_directory.exists() and not project_directory.is_junction():
                shutil.rmtree(project_directory)


def test_pack_junction_inserted_after_containment_creates_no_outside_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pack_zip_factory: Callable[[str, dict[str, bytes], int], Path],
) -> None:
    source = pack_zip_factory("source.zip", {})
    projects_root = tmp_path / "projects"
    outside = tmp_path / "outside-after-check"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("must survive", encoding="utf-8")
    workspace = build_workspace(projects_root)
    contained_destination = workspace_module._contained_destination
    substituted = False

    def substitute_after_containment(*args, **kwargs) -> Path:
        nonlocal substituted
        destination = contained_destination(*args, **kwargs)
        if not substituted:
            pack_directory = args[2]
            pack_directory.rmdir()
            _create_junction(pack_directory, outside)
            substituted = True
        return destination

    monkeypatch.setattr(
        workspace_module,
        "_contained_destination",
        substitute_after_containment,
    )

    with pytest.raises(PackValidationError) as raised:
        workspace.import_pack(source, "Check-to-open junction attack")

    project_directories = list(projects_root.iterdir())
    try:
        assert raised.value.code == "UNSAFE_PROJECT_PATH"
        assert sentinel.read_text(encoding="utf-8") == "must survive"
        assert not (outside / "pack.mcmeta").exists()
        assert {path.name for path in outside.iterdir()} == {"sentinel.txt"}
        assert all(path.name.endswith(".tmp") for path in project_directories)
    finally:
        for project_directory in project_directories:
            pack_directory = project_directory / "pack"
            if pack_directory.is_junction():
                _remove_junction(pack_directory)
            if project_directory.exists() and not project_directory.is_junction():
                shutil.rmtree(project_directory)


def test_snapshot_replacement_before_copy_prevents_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pack_zip_factory: Callable[[str, dict[str, bytes], int], Path],
    one_pixel_png: bytes,
) -> None:
    source = pack_zip_factory(
        "source.zip",
        {"assets/minecraft/textures/block/stone.png": one_pixel_png},
    )
    replacement = pack_zip_factory(
        "replacement.zip",
        {"assets/minecraft/textures/block/stone.png": b"replacement"},
    )
    projects_root = tmp_path / "projects"
    workspace = build_workspace(projects_root)
    validate_snapshot = workspace._validate_snapshot

    def replace_after_validation(original, snapshot) -> None:
        validate_snapshot(original, snapshot)
        shutil.copyfile(replacement, snapshot.source)

    monkeypatch.setattr(workspace, "_validate_snapshot", replace_after_validation)

    with pytest.raises(PackValidationError) as raised:
        workspace.import_pack(source, "Snapshot replacement attack")

    assert raised.value.code == "SOURCE_CHANGED"
    assert not list(projects_root.iterdir())


def _corrupt_stored_member_crc(path: Path, member_name: str) -> None:
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo(member_name)
    payload = bytearray(path.read_bytes())
    filename_length = int.from_bytes(
        payload[info.header_offset + 26 : info.header_offset + 28], "little"
    )
    extra_length = int.from_bytes(
        payload[info.header_offset + 28 : info.header_offset + 30], "little"
    )
    data_offset = info.header_offset + 30 + filename_length + extra_length
    payload[data_offset] ^= 0x01
    path.write_bytes(payload)


def test_corrupt_crc_in_normal_member_is_stable_and_cleans_stage(
    tmp_path: Path,
    pack_zip_factory: Callable[[str, dict[str, bytes], int], Path],
) -> None:
    source = pack_zip_factory("corrupt-member.zip", {"assets/data.bin": b"payload"})
    _corrupt_stored_member_crc(source, "assets/data.bin")
    projects_root = tmp_path / "projects"

    with pytest.raises(PackValidationError) as raised:
        build_workspace(projects_root).import_pack(source, "Corrupt member")

    assert raised.value.code == "CORRUPT_ZIP_MEMBER"
    assert list(projects_root.iterdir()) == []


def _manifest_values() -> dict[str, object]:
    timestamp = datetime.now(timezone.utc)
    return {
        "schema_version": 2,
        "project_id": uuid4(),
        "project_name": "Strict manifest",
        "edition": "java",
        "java_pack_format": 34,
        "supported_formats": (34, 48),
        "catalog_id": "java-dev-format-34",
        "source_sha256": "0" * 64,
        "created_at": timestamp,
        "updated_at": timestamp,
        "default_resolution": 16,
        "default_parallelism": 1,
        "style_references": (),
    }


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("schema_version", "1"),
        ("schema_version", 1),
        ("edition", "bedrock"),
        ("project_id", str(uuid4())),
        ("java_pack_format", "34"),
        ("default_resolution", "16"),
        ("default_parallelism", "1"),
        ("created_at", datetime.now()),
        ("updated_at", datetime.now()),
    ],
)
def test_project_manifest_rejects_coercion_constants_and_naive_timestamps(
    field: str,
    invalid_value: object,
) -> None:
    values = _manifest_values()
    values[field] = invalid_value

    with pytest.raises(ValidationError):
        ProjectManifest.model_validate(values)


def test_project_manifest_rejects_extra_fields_and_is_frozen() -> None:
    values = _manifest_values()
    values["unexpected"] = "rejected"
    with pytest.raises(ValidationError):
        ProjectManifest.model_validate(values)

    manifest = ProjectManifest.model_validate(_manifest_values())
    with pytest.raises(ValidationError):
        manifest.project_name = "mutation rejected"


def test_project_manifest_rejects_oversize_project_name() -> None:
    accepted = _manifest_values()
    accepted["project_name"] = "x" * 128
    assert ProjectManifest.model_validate(accepted).project_name == "x" * 128

    values = _manifest_values()
    values["project_name"] = "x" * 129

    with pytest.raises(ValidationError):
        ProjectManifest.model_validate(values)


def test_project_manifest_counts_non_bmp_project_name_as_code_points() -> None:
    accepted = _manifest_values()
    accepted["project_name"] = "😀" * 128
    assert ProjectManifest.model_validate(accepted).project_name == "😀" * 128

    rejected = _manifest_values()
    rejected["project_name"] = "😀" * 129
    with pytest.raises(ValidationError):
        ProjectManifest.model_validate(rejected)


class _LyingMemberStream:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = iter(chunks)

    def read(self, _size: int = -1) -> bytes:
        return next(self._chunks, b"")


def test_bounded_copy_enforces_actual_member_bytes_not_declared_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_module, "MAX_ZIP_MEMBER_BYTES", 8)
    info = zipfile.ZipInfo("lying.bin")
    info.file_size = 1

    with pytest.raises(PackValidationError) as raised:
        workspace_module._copy_zip_member_bounded(
            _LyingMemberStream([b"x" * 9]),
            io.BytesIO(),
            info,
            0,
        )

    assert raised.value.code == "ZIP_MEMBER_TOO_LARGE"


def test_bounded_copy_enforces_actual_total_bytes_not_declared_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_module, "MAX_ZIP_MEMBER_BYTES", 100)
    monkeypatch.setattr(workspace_module, "MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES", 10)
    info = zipfile.ZipInfo("lying-total.bin")
    info.file_size = 1

    with pytest.raises(PackValidationError) as raised:
        workspace_module._copy_zip_member_bounded(
            _LyingMemberStream([b"xyz"]),
            io.BytesIO(),
            info,
            8,
        )

    assert raised.value.code == "ZIP_TOTAL_SIZE_EXCEEDED"
