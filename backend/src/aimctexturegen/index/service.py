"""Coordinate canonical project/job JSON with the disposable query index."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from typing import Protocol, TypeVar
from uuid import UUID

from aimctexturegen.index.models import IndexSnapshot
from aimctexturegen.jobs.models import JobSummary
from aimctexturegen.jobs.store import JobScanResult, JobStore, LoadedJob
from aimctexturegen.projects.models import (
    ProjectManifest,
    ProjectSummary,
)
from aimctexturegen.projects.repository import (
    ProjectRepository,
    ProjectScanResult,
)


_ResultT = TypeVar("_ResultT")


class ProjectIndexPort(Protocol):
    def upsert_project(self, manifest: ProjectManifest) -> None: ...

    def upsert_job(self, summary: JobSummary) -> None: ...

    def list_projects(self) -> tuple[ProjectSummary, ...]: ...

    def list_jobs(self, project_id: UUID) -> tuple[JobSummary, ...]: ...

    def replace_snapshot(self, snapshot: IndexSnapshot) -> None: ...


class ProjectRepositoryPort(Protocol):
    def list_manifests(self) -> ProjectScanResult: ...


class JobStorePort(Protocol):
    def scan(self, project_id: UUID) -> JobScanResult: ...


class IndexUnavailableError(Exception):
    """A stable, path-free failure after the one allowed repair attempt."""

    code = "INDEX_UNAVAILABLE"

    def __init__(self) -> None:
        self.user_message = "项目与任务索引暂时不可用"
        super().__init__(self.user_message)


class IndexService:
    """Rebuild summaries from disk and retry one failed SQLite operation."""

    def __init__(
        self,
        *,
        repository: ProjectRepository | ProjectRepositoryPort,
        store: JobStore | JobStorePort,
        index: ProjectIndexPort,
    ) -> None:
        self._repository = repository
        self._store = store
        self._index = index
        self._coordination_lock = getattr(
            index,
            "coordination_lock",
            threading.RLock(),
        )

    def rebuild(self) -> IndexSnapshot:
        """Replace the index from validated canonical project and job models."""

        try:
            return self._rebuild_once()
        except (sqlite3.DatabaseError, OSError) as error:
            raise IndexUnavailableError() from error

    def upsert_project(self, manifest: ProjectManifest) -> None:
        self._guarded(lambda: self._index.upsert_project(manifest))

    def upsert_job(self, summary: JobSummary) -> None:
        self._guarded(lambda: self._index.upsert_job(summary))

    def list_projects(self) -> tuple[ProjectSummary, ...]:
        return self._guarded(self._index.list_projects)

    def list_jobs(self, project_id: UUID) -> tuple[JobSummary, ...]:
        if not isinstance(project_id, UUID):
            raise TypeError("project_id must be a UUID")
        return self._guarded(lambda: self._index.list_jobs(project_id))

    def _rebuild_once(self) -> IndexSnapshot:
        with self._coordination_lock:
            projects: list[ProjectSummary] = []
            jobs: list[JobSummary] = []
            scan = self._repository.list_manifests()
            for manifest in scan.manifests:
                projects.append(_project_summary(manifest))
                job_scan = self._store.scan(manifest.project_id)
                jobs.extend(
                    _job_summary(loaded)
                    for loaded in job_scan.jobs
                )
            snapshot = IndexSnapshot(projects=tuple(projects), jobs=tuple(jobs))
            self._index.replace_snapshot(snapshot)
            return snapshot

    def _guarded(self, operation: Callable[[], _ResultT]) -> _ResultT:
        with self._coordination_lock:
            try:
                return operation()
            except (sqlite3.DatabaseError, OSError):
                try:
                    self._rebuild_once()
                    return operation()
                except (sqlite3.DatabaseError, OSError) as error:
                    raise IndexUnavailableError() from error


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


def _job_summary(loaded: LoadedJob) -> JobSummary:
    return JobSummary(
        job_id=loaded.request.job_id,
        project_id=loaded.request.project_id,
        retry_of_job_id=loaded.request.retry_of_job_id,
        target_semantic_id=loaded.request.target_semantic_id,
        target_display_name=loaded.request.target_display_name,
        resolution=loaded.request.resolution,
        parallelism=loaded.request.parallelism,
        status=loaded.state.status,
        revision=loaded.state.revision,
        candidate_statuses=tuple(
            candidate.status for candidate in loaded.state.candidates
        ),
        created_at=loaded.request.created_at,
        updated_at=loaded.state.updated_at,
    )
