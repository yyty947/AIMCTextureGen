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
from aimctexturegen.jobs.generation_state import recover_generation_interruption
from aimctexturegen.jobs.models import JobStateRecord
from aimctexturegen.jobs.models_v3 import GenerationJobState
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

    def load(self, project_id: UUID, job_id: UUID) -> LoadedJob: ...

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
        """Recover active jobs and rebuild the disposable index."""

        report = self.recover()
        self._index.rebuild()
        return report

    def recover(self) -> RecoveryReport:
        """Migrate projects and recover active jobs without rebuilding the index."""

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
                recovered, effective_recovery_time = self._recover_active_job(
                    loaded,
                    recovery_time=recovery_time,
                )
                latest_recovery_time = max(
                    latest_recovery_time,
                    effective_recovery_time,
                )
                if recovered:
                    recovered_job_count += 1

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

    def _recover_active_job(
        self,
        loaded: LoadedJob,
        *,
        recovery_time: datetime,
    ) -> tuple[bool, datetime]:
        """Retry one revision race, but never publish a zombie active job."""

        current = loaded
        for attempt in range(2):
            effective_recovery_time = max(
                recovery_time,
                current.state.updated_at,
            )
            if current.state.status not in _ACTIVE_JOB_STATUSES:
                return False, effective_recovery_time
            replacement = self._recover_state(
                current.state,
                now=effective_recovery_time,
            )
            try:
                self._store.replace_state(
                    current.request.project_id,
                    current.request.job_id,
                    replacement,
                    expected_revision=current.state.revision,
                )
            except JobError as error:
                if error.code != "JOB_REVISION_CONFLICT" or attempt == 1:
                    raise
                current = self._store.load(
                    current.request.project_id,
                    current.request.job_id,
                )
                continue
            return True, effective_recovery_time
        raise AssertionError("recovery retry loop exhausted")

    @staticmethod
    def _recover_state(
        state: JobStateRecord | GenerationJobState,
        *,
        now: datetime,
    ) -> JobStateRecord | GenerationJobState:
        if isinstance(state, GenerationJobState):
            return recover_generation_interruption(state, now=now)
        return recover_interrupted_state(state, now=now)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
