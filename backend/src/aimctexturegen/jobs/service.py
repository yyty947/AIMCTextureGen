"""Job creation and mutation orchestration behind safe project boundaries."""

from __future__ import annotations

import os
import secrets
import stat
from collections.abc import Callable
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Protocol
from uuid import UUID, uuid4

from aimctexturegen.catalog.models import CatalogProfile
from aimctexturegen.catalog.registry import UnsupportedPackFormat
from aimctexturegen.jobs.errors import JobError
from aimctexturegen.jobs.models import (
    CreateJobCommand,
    JobRequest,
    JobSummary,
    MAX_SAFE_SEED,
)
from aimctexturegen.jobs.store import JobStore, LoadedJob
from aimctexturegen.packs.coverage import (
    CoverageValidationError,
    classify_coverage,
)
from aimctexturegen.projects._directory_guard import (
    DirectoryGuardError,
    hold_directory_identity,
    is_reparse_point,
    matches_directory_identity,
)
from aimctexturegen.projects.repository import ProjectRepository


class CatalogPort(Protocol):
    def for_pack_format(self, pack_format: int) -> CatalogProfile: ...


class JobIndexWriter(Protocol):
    def upsert_job(self, summary: JobSummary) -> None: ...


class _NoopJobIndex:
    def upsert_job(self, _summary: JobSummary) -> None:
        return None


class JobService:
    """Validate product inputs, persist canonical JSON, then update the index."""

    def __init__(
        self,
        *,
        repository: ProjectRepository,
        catalogs: CatalogPort,
        store: JobStore,
        index: JobIndexWriter | None = None,
        seed_source: Callable[[], int] | None = None,
        job_id_source: Callable[[], UUID] = uuid4,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._catalogs = catalogs
        self._store = store
        self._index = _NoopJobIndex() if index is None else index
        self._seed_source = (
            (lambda: secrets.randbelow(MAX_SAFE_SEED + 1))
            if seed_source is None
            else seed_source
        )
        self._job_id_source = job_id_source
        self._clock = (
            (lambda: datetime.now(timezone.utc)) if clock is None else clock
        )

    def create_job(
        self,
        project_id: UUID,
        command: CreateJobCommand,
    ) -> LoadedJob:
        """Validate a missing catalog target and persist exactly four seeds."""

        if not isinstance(project_id, UUID):
            raise TypeError("project_id must be a UUID")
        if not isinstance(command, CreateJobCommand):
            raise TypeError("command must be a CreateJobCommand")

        with self._repository.open(project_id) as opened:
            try:
                profile = self._catalogs.for_pack_format(
                    opened.manifest.java_pack_format
                )
            except UnsupportedPackFormat as error:
                raise _service_error("UNSUPPORTED_PACK_FORMAT") from error
            if profile.catalog_id != opened.manifest.catalog_id:
                raise _service_error("CORRUPT_PROJECT_MANIFEST")

            target = next(
                (
                    entry
                    for entry in profile.entries
                    if entry.semantic_id == command.target_semantic_id
                ),
                None,
            )
            if target is None:
                raise _service_error("JOB_TARGET_NOT_FOUND")
            if not target.mvp_eligible:
                raise _service_error("JOB_TARGET_NOT_ELIGIBLE")

            try:
                with hold_directory_identity(opened.pack_root) as pack_identity:
                    try:
                        coverage = classify_coverage(opened.pack_root, profile)
                    except CoverageValidationError as error:
                        raise JobError(error.code, error.user_message) from error
                    item = next(
                        coverage_item
                        for coverage_item in coverage.items
                        if coverage_item.semantic_id == target.semantic_id
                    )
                    if item.status != "missing":
                        raise _service_error("JOB_TARGET_NOT_MISSING")
                    for reference in command.style_references:
                        try:
                            _verify_regular_reference(
                                opened.pack_root,
                                reference,
                            )
                        except (OSError, DirectoryGuardError) as error:
                            raise _service_error(
                                "INVALID_STYLE_REFERENCE"
                            ) from error
                    if not matches_directory_identity(
                        opened.pack_root,
                        pack_identity,
                    ):
                        raise _service_error("INVALID_STYLE_REFERENCE")
            except JobError:
                raise
            except (OSError, DirectoryGuardError) as error:
                raise _service_error("INVALID_STYLE_REFERENCE") from error

            if command.structure_reference is not None:
                parts = PurePosixPath(command.structure_reference).parts
                if parts[:2] != ("uploads", "structure-references"):
                    raise _service_error("INVALID_STRUCTURE_REFERENCE")
                try:
                    _verify_regular_reference(
                        opened.root,
                        command.structure_reference,
                    )
                except (OSError, DirectoryGuardError) as error:
                    raise _service_error(
                        "INVALID_STRUCTURE_REFERENCE"
                    ) from error

            seeds = self._draw_four_unique_seeds()
            job_id = self._job_id_source()
            created_at = self._clock()
            request = JobRequest(
                schema_version=1,
                job_id=job_id,
                project_id=opened.manifest.project_id,
                retry_of_job_id=None,
                catalog_id=profile.catalog_id,
                target_semantic_id=target.semantic_id,
                target_display_name=target.display_name,
                target_relative_path=target.relative_path,
                prompt=command.prompt,
                resolution=command.resolution,
                parallelism=command.parallelism,
                style_references=command.style_references,
                structure_reference=command.structure_reference,
                seeds=seeds,
                created_at=created_at,
            )

        loaded = self._store.create(request)
        self._upsert_after_commit(loaded)
        return loaded

    def get_job(self, project_id: UUID, job_id: UUID) -> LoadedJob:
        return self._store.load(project_id, job_id)

    def list_jobs(self, project_id: UUID) -> tuple[LoadedJob, ...]:
        return self._store.list(project_id)

    def cancel_job(
        self,
        project_id: UUID,
        job_id: UUID,
        *,
        expected_revision: int,
    ) -> LoadedJob:
        loaded = self._store.cancel(
            project_id,
            job_id,
            expected_revision=expected_revision,
            now=self._clock(),
        )
        self._upsert_after_commit(loaded)
        return loaded

    def retry_job(self, project_id: UUID, job_id: UUID) -> LoadedJob:
        loaded = self._store.retry(
            project_id,
            job_id,
            new_job_id=self._job_id_source(),
            created_at=self._clock(),
        )
        self._upsert_after_commit(loaded)
        return loaded

    def _draw_four_unique_seeds(self) -> tuple[int, int, int, int]:
        unique: list[int] = []
        try:
            for _attempt in range(1024):
                value = self._seed_source()
                if (
                    type(value) is not int
                    or value < 0
                    or value > MAX_SAFE_SEED
                ):
                    raise _service_error("INVALID_SEED_SOURCE")
                if value not in unique:
                    unique.append(value)
                    if len(unique) == 4:
                        return unique[0], unique[1], unique[2], unique[3]
        except JobError:
            raise
        except Exception as error:
            raise _service_error("INVALID_SEED_SOURCE") from error
        raise _service_error("INVALID_SEED_SOURCE")

    def _upsert_after_commit(self, loaded: LoadedJob) -> None:
        try:
            self._index.upsert_job(_job_summary(loaded))
        except Exception as error:
            raise _service_error("INDEX_UNAVAILABLE") from error


def _verify_regular_reference(base: Path, relative_path: str) -> None:
    """Hold every directory ancestor and bind the final ordinary file."""

    parts = PurePosixPath(relative_path).parts
    if not parts:
        raise OSError("reference path is empty")
    path = base.joinpath(*parts)
    if path.parent == path or not path.is_relative_to(base):
        raise OSError("reference escapes its allowed root")

    with ExitStack() as stack:
        base_identity = stack.enter_context(hold_directory_identity(base))
        current = base
        for part in parts[:-1]:
            current /= part
            stack.enter_context(hold_directory_identity(current))

        path_status = os.lstat(path)
        if (
            not stat.S_ISREG(path_status.st_mode)
            or is_reparse_point(path, path_status)
        ):
            raise OSError("reference is not an ordinary file")
        expected_identity = (path_status.st_dev, path_status.st_ino)
        with path.open("rb") as source:
            handle_status = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(handle_status.st_mode)
                or (handle_status.st_dev, handle_status.st_ino)
                != expected_identity
            ):
                raise OSError("reference changed before validation")
            final_status = os.lstat(path)
            if (
                (final_status.st_dev, final_status.st_ino) != expected_identity
                or is_reparse_point(path, final_status)
                or (
                    os.fstat(source.fileno()).st_dev,
                    os.fstat(source.fileno()).st_ino,
                )
                != expected_identity
            ):
                raise OSError("reference changed during validation")
        if not matches_directory_identity(base, base_identity):
            raise OSError("reference root changed during validation")


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


def _service_error(code: str) -> JobError:
    messages = {
        "UNSUPPORTED_PACK_FORMAT": "项目记录的资源格式当前不受支持",
        "CORRUPT_PROJECT_MANIFEST": "项目清单与目录配置不一致",
        "JOB_TARGET_NOT_FOUND": "目录中没有该生成目标",
        "JOB_TARGET_NOT_ELIGIBLE": "该目标不属于 MVP 可生成范围",
        "JOB_TARGET_NOT_MISSING": "该目标已被资源包覆盖",
        "INVALID_STYLE_REFERENCE": "风格参考必须是工作副本内的普通文件",
        "INVALID_STRUCTURE_REFERENCE": (
            "结构参考必须是结构参考上传目录内的普通文件"
        ),
        "INVALID_SEED_SOURCE": "无法生成四个唯一且安全的 seed",
        "INDEX_UNAVAILABLE": "任务已保存，但任务索引暂时不可用",
    }
    return JobError(code, messages[code])
