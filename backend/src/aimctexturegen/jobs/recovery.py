"""Startup migration, interrupted-job recovery, and index rebuilding."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

from aimctexturegen.index.models import IndexSnapshot
from aimctexturegen.index.service import IndexService
from aimctexturegen.jobs.errors import JobError
from aimctexturegen.jobs.models import JobStateRecord
from aimctexturegen.jobs.state_machine import recover_interrupted_state
from aimctexturegen.jobs.store import (
    JobScanResult,
    JobStore,
    LoadedJob,
)
from aimctexturegen.projects.repository import (
    ProjectRepository,
    ProjectScanResult,
)


_ACTIVE_JOB_STATUSES = frozenset({"generating", "postprocessing"})


@dataclass(frozen=True)
class RecoveryIssue:
    """A path-free project or job issue observed during startup recovery."""

    project_id: UUID
    job_id: UUID | None
    code: str
    user_message: str


@dataclass(frozen=True)
class RecoveryReport:
    """Immutable counts, issues, and completion time for one startup run."""

    project_count: int
    job_count: int
    recovered_job_count: int
    issues: tuple[RecoveryIssue, ...]
    completed_at: datetime


class ProjectRepositoryPort(Protocol):
    def list_manifests(self) -> ProjectScanResult: ...


class JobStorePort(Protocol):
    def scan(self, project_id: UUID) -> JobScanResult: ...

    def replace_state(
        self,
        project_id: UUID,
        job_id: UUID,
        state: JobStateRecord,
        *,
        expected_revision: int,
    ) -> LoadedJob: ...


class IndexServicePort(Protocol):
    def rebuild(self) -> IndexSnapshot: ...


class RecoveryService:
    """Recover canonical project/job JSON, then rebuild its disposable index."""

    def __init__(
        self,
        *,
        repository: ProjectRepository | ProjectRepositoryPort,
        store: JobStore | JobStorePort,
        index: IndexService | IndexServicePort,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._store = store
        self._index = index
        self._clock = _utc_now if clock is None else clock

    def run(self) -> RecoveryReport:
        """Migrate projects, recover active jobs, and index final disk state."""

        recovery_time = self._clock()
        latest_recovery_time = recovery_time
        project_scan = self._repository.list_manifests()
        issues = [
            RecoveryIssue(
                project_id=issue.project_id,
                job_id=None,
                code=issue.code,
                user_message=issue.user_message,
            )
            for issue in project_scan.issues
        ]
        job_count = 0
        recovered_job_count = 0

        for manifest in project_scan.manifests:
            job_scan = self._store.scan(manifest.project_id)
            job_count += len(job_scan.jobs)
            issues.extend(
                RecoveryIssue(
                    project_id=manifest.project_id,
                    job_id=issue.job_id,
                    code=issue.code,
                    user_message=issue.user_message,
                )
                for issue in job_scan.issues
            )
            for loaded in job_scan.jobs:
                if loaded.state.status not in _ACTIVE_JOB_STATUSES:
                    continue
                effective_recovery_time = max(
                    recovery_time,
                    loaded.state.updated_at,
                )
                latest_recovery_time = max(
                    latest_recovery_time,
                    effective_recovery_time,
                )
                replacement = recover_interrupted_state(
                    loaded.state,
                    now=effective_recovery_time,
                )
                try:
                    self._store.replace_state(
                        manifest.project_id,
                        loaded.request.job_id,
                        replacement,
                        expected_revision=loaded.state.revision,
                    )
                except JobError as error:
                    issues.append(
                        RecoveryIssue(
                            project_id=manifest.project_id,
                            job_id=loaded.request.job_id,
                            code=error.code,
                            user_message=error.user_message,
                        )
                    )
                else:
                    recovered_job_count += 1

        self._index.rebuild()
        issues.sort(
            key=lambda issue: (
                str(issue.project_id),
                "" if issue.job_id is None else str(issue.job_id),
                issue.code,
            )
        )
        return RecoveryReport(
            project_count=len(project_scan.manifests),
            job_count=job_count,
            recovered_job_count=recovered_job_count,
            issues=tuple(issues),
            completed_at=max(self._clock(), latest_recovery_time),
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
