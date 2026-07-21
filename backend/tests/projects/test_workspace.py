import io
import json
import zipfile
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path

import pytest
from PIL import Image

from aimctexturegen.catalog.registry import CatalogRegistry
from aimctexturegen.packs.java_adapter import JavaPackAdapter, PackValidationError
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
