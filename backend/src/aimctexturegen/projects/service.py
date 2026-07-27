"""Project business orchestration behind repository and index ports."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import UUID

from aimctexturegen.catalog.models import CatalogProfile
from aimctexturegen.catalog.registry import UnsupportedPackFormat
from aimctexturegen.packs.coverage import (
    CoverageReport,
    CoverageValidationError,
    classify_coverage,
)
from aimctexturegen.projects._directory_guard import (
    DirectoryGuardError,
    hold_directory_identity,
    matches_directory_identity,
)
from aimctexturegen.projects.models import ProjectManifest, ProjectSummary
from aimctexturegen.projects.repository import ProjectRepository


class WorkspacePort(Protocol):
    def import_pack(self, source: Path, project_name: str) -> ProjectManifest: ...


class CatalogPort(Protocol):
    def for_pack_format(self, pack_format: int) -> CatalogProfile: ...


class ProjectIndexPort(Protocol):
    def upsert_project(self, manifest: ProjectManifest) -> None: ...

    def list_projects(self) -> tuple[ProjectSummary, ...]: ...

    def rebuild(self) -> None: ...


class ProjectServiceError(Exception):
    """A stable project-domain failure independent of FastAPI."""

    def __init__(self, code: str, user_message: str) -> None:
        self.code = code
        self.user_message = user_message
        super().__init__(user_message)


class RepositoryProjectIndex:
    """Temporary disk-backed query port until the disposable index lands."""

    def __init__(self, repository: ProjectRepository) -> None:
        self._repository = repository

    def upsert_project(self, _manifest: ProjectManifest) -> None:
        return None

    def list_projects(self) -> tuple[ProjectSummary, ...]:
        return tuple(
            _project_summary(manifest)
            for manifest in self._repository.list_manifests().manifests
        )

    def rebuild(self) -> None:
        self._repository.list_manifests()


class ProjectService:
    """Compose project import, open, listing, and coverage behavior."""

    def __init__(
        self,
        *,
        workspace: WorkspacePort,
        repository: ProjectRepository,
        catalogs: CatalogPort,
        index: ProjectIndexPort | None = None,
    ) -> None:
        self._workspace = workspace
        self._repository = repository
        self._catalogs = catalogs
        self._index = (
            RepositoryProjectIndex(repository) if index is None else index
        )

    def import_pack(self, source: Path, project_name: str) -> ProjectManifest:
        """Import to canonical disk first, then repair the disposable index."""

        manifest = self._workspace.import_pack(source, project_name)
        try:
            self._index.upsert_project(manifest)
        except Exception:
            try:
                self._index.rebuild()
            except Exception as error:
                raise _service_error("INDEX_UNAVAILABLE") from error
        return manifest

    def get_project(self, project_id: UUID) -> ProjectManifest:
        with self._repository.open(project_id) as opened:
            return opened.manifest

    def list_projects(self) -> tuple[ProjectSummary, ...]:
        try:
            return self._index.list_projects()
        except Exception:
            try:
                self._index.rebuild()
                return self._index.list_projects()
            except Exception as error:
                raise _service_error("INDEX_UNAVAILABLE") from error

    def get_coverage(self, project_id: UUID) -> CoverageReport:
        with self._repository.open(project_id) as opened:
            try:
                with hold_directory_identity(opened.pack_root) as pack_identity:
                    try:
                        profile = self._catalogs.for_pack_format(
                            opened.manifest.java_pack_format
                        )
                    except UnsupportedPackFormat as error:
                        raise _service_error("UNSUPPORTED_PACK_FORMAT") from error
                    if profile.catalog_id != opened.manifest.catalog_id:
                        raise _service_error("CORRUPT_PROJECT_MANIFEST")
                    try:
                        report = classify_coverage(opened.pack_root, profile)
                    except CoverageValidationError as error:
                        raise ProjectServiceError(
                            error.code,
                            error.user_message,
                        ) from error
                    if not matches_directory_identity(
                        opened.pack_root,
                        pack_identity,
                    ):
                        raise _service_error("UNSAFE_PACK_ROOT")
                    return report
            except ProjectServiceError:
                raise
            except DirectoryGuardError as error:
                raise _service_error("UNSAFE_PACK_ROOT") from error


def _project_summary(manifest: ProjectManifest) -> ProjectSummary:
    return ProjectSummary(
        project_id=manifest.project_id,
        project_name=manifest.project_name,
        edition=manifest.edition,
        java_pack_format=manifest.java_pack_format,
        catalog_id=manifest.catalog_id,
        created_at=manifest.created_at,
        updated_at=manifest.updated_at,
    )


def _service_error(code: str) -> ProjectServiceError:
    messages = {
        "INDEX_UNAVAILABLE": "项目索引暂时不可用",
        "UNSUPPORTED_PACK_FORMAT": "项目记录的资源格式当前不受支持",
        "CORRUPT_PROJECT_MANIFEST": "项目清单与目录配置不一致",
        "UNSAFE_PACK_ROOT": "资源包工作目录不安全",
    }
    return ProjectServiceError(code, messages[code])
