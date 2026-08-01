import io
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image

from aimctexturegen.catalog.registry import CatalogRegistry
from aimctexturegen.index.service import IndexUnavailableError
from aimctexturegen.projects.models import (
    ProjectManifest,
    ProjectSummary,
    dump_project_manifest,
)
from aimctexturegen.projects.repository import ProjectRepository
from aimctexturegen.projects.service import ProjectService, ProjectServiceError


CATALOG_ROOT = Path(__file__).parents[3] / "catalogs" / "java"


def _manifest(
    project_id: UUID,
    *,
    catalog_id: str = "java-dev-format-34",
) -> ProjectManifest:
    timestamp = datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc)
    return ProjectManifest(
        schema_version=2,
        project_id=project_id,
        project_name="Service project",
        edition="java",
        java_pack_format=34,
        supported_formats=None,
        catalog_id=catalog_id,
        source_sha256="cd" * 32,
        created_at=timestamp,
        updated_at=timestamp,
        default_resolution=16,
        default_parallelism=1,
        style_references=(),
    )


def _write_project(
    projects_root: Path,
    manifest: ProjectManifest,
    *,
    stone_png: bytes | None = None,
) -> Path:
    project_root = projects_root / str(manifest.project_id)
    pack_root = project_root / "pack"
    pack_root.mkdir(parents=True)
    if stone_png is not None:
        stone = (
            pack_root
            / "assets"
            / "minecraft"
            / "textures"
            / "block"
            / "stone.png"
        )
        stone.parent.mkdir(parents=True)
        stone.write_bytes(stone_png)
    (project_root / "project.json").write_bytes(dump_project_manifest(manifest))
    return project_root


def _png() -> bytes:
    payload = io.BytesIO()
    Image.new("RGB", (1, 1), (32, 32, 32)).save(payload, format="PNG")
    return payload.getvalue()


def _summary(manifest: ProjectManifest) -> ProjectSummary:
    return ProjectSummary(
        project_id=manifest.project_id,
        project_name=manifest.project_name,
        edition=manifest.edition,
        java_pack_format=manifest.java_pack_format,
        catalog_id=manifest.catalog_id,
        created_at=manifest.created_at,
        updated_at=manifest.updated_at,
    )


class RecordingWorkspace:
    def __init__(
        self,
        projects_root: Path,
        manifest: ProjectManifest,
    ) -> None:
        self.projects_root = projects_root
        self.manifest = manifest
        self.calls: list[tuple[Path, str]] = []

    def import_pack(self, source: Path, project_name: str) -> ProjectManifest:
        self.calls.append((source, project_name))
        project_root = self.projects_root / str(self.manifest.project_id)
        project_root.mkdir(parents=True)
        (project_root / "project.json").write_bytes(
            dump_project_manifest(self.manifest)
        )
        return self.manifest


class RecordingIndex:
    def __init__(
        self,
        *,
        projects: tuple[ProjectSummary, ...] = (),
        fail_upsert: bool = False,
        fail_rebuild: bool = False,
        fail_list: bool = False,
    ) -> None:
        self.projects = projects
        self.fail_upsert = fail_upsert
        self.fail_rebuild = fail_rebuild
        self.fail_list = fail_list
        self.upserts: list[ProjectManifest] = []
        self.rebuild_calls = 0
        self.list_calls = 0

    def upsert_project(self, manifest: ProjectManifest) -> None:
        self.upserts.append(manifest)
        if self.fail_upsert:
            raise IndexUnavailableError()

    def list_projects(self) -> tuple[ProjectSummary, ...]:
        self.list_calls += 1
        if self.fail_list:
            raise IndexUnavailableError()
        return self.projects

    def rebuild(self) -> None:
        self.rebuild_calls += 1
        if self.fail_rebuild:
            raise OSError("injected index rebuild failure")


def _service(
    projects_root: Path,
    workspace: RecordingWorkspace,
    index: RecordingIndex,
) -> ProjectService:
    return ProjectService(
        workspace=workspace,
        repository=ProjectRepository(projects_root),
        catalogs=CatalogRegistry(CATALOG_ROOT),
        index=index,
    )


def test_import_is_disk_authoritative_when_index_upsert_succeeds(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    manifest = _manifest(UUID("11111111-1111-4111-8111-111111111111"))
    workspace = RecordingWorkspace(projects_root, manifest)
    index = RecordingIndex()
    service = _service(projects_root, workspace, index)
    source = tmp_path / "source.zip"
    source.write_bytes(b"synthetic")

    imported = service.import_pack(source, "Imported")

    assert imported == manifest
    assert workspace.calls == [(source, "Imported")]
    assert index.upserts == [manifest]
    assert index.rebuild_calls == 0
    assert (projects_root / str(manifest.project_id) / "project.json").is_file()


def test_import_maps_central_index_failure_without_a_second_repair(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    manifest = _manifest(UUID("22222222-2222-4222-8222-222222222222"))
    workspace = RecordingWorkspace(projects_root, manifest)
    index = RecordingIndex(fail_upsert=True)
    service = _service(projects_root, workspace, index)
    source = tmp_path / "source.zip"
    source.write_bytes(b"synthetic")

    with pytest.raises(ProjectServiceError) as captured:
        service.import_pack(source, "Imported")

    assert captured.value.code == "INDEX_UNAVAILABLE"
    assert index.rebuild_calls == 0
    assert (projects_root / str(manifest.project_id) / "project.json").is_file()


def test_get_and_list_projects_return_service_contracts(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    manifest = _manifest(UUID("33333333-3333-4333-8333-333333333333"))
    _write_project(projects_root, manifest)
    workspace = RecordingWorkspace(projects_root, manifest)
    index = RecordingIndex(projects=(_summary(manifest),))
    service = _service(projects_root, workspace, index)

    assert service.get_project(manifest.project_id) == manifest
    assert service.list_projects() == (_summary(manifest),)
    assert index.list_calls == 1


def test_list_projects_maps_central_index_failure_without_a_second_repair(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    manifest = _manifest(UUID("44444444-4444-4444-8444-444444444444"))
    workspace = RecordingWorkspace(projects_root, manifest)
    index = RecordingIndex(projects=(_summary(manifest),), fail_list=True)
    service = _service(projects_root, workspace, index)

    with pytest.raises(ProjectServiceError) as captured:
        service.list_projects()

    assert captured.value.code == "INDEX_UNAVAILABLE"
    assert index.list_calls == 1
    assert index.rebuild_calls == 0


def test_coverage_uses_opened_working_copy_and_matching_catalog(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    manifest = _manifest(UUID("55555555-5555-4555-8555-555555555555"))
    _write_project(projects_root, manifest, stone_png=_png())
    service = _service(
        projects_root,
        RecordingWorkspace(projects_root, manifest),
        RecordingIndex(),
    )

    report = service.get_coverage(manifest.project_id)

    assert report.catalog_id == manifest.catalog_id
    assert report.covered_count == 1
    assert report.missing_count == 1


def test_coverage_rejects_manifest_catalog_mismatch(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    manifest = _manifest(
        UUID("66666666-6666-4666-8666-666666666666"),
        catalog_id="wrong-catalog",
    )
    _write_project(projects_root, manifest)
    service = _service(
        projects_root,
        RecordingWorkspace(projects_root, manifest),
        RecordingIndex(),
    )

    with pytest.raises(ProjectServiceError) as captured:
        service.get_coverage(manifest.project_id)

    assert captured.value.code == "CORRUPT_PROJECT_MANIFEST"
