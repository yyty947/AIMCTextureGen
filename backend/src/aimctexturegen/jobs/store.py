"""Canonical JSON persistence for four-candidate jobs."""

from __future__ import annotations

import os
import shutil
import stat
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel, ValidationError

from aimctexturegen.core.atomic_files import AtomicWriteError, atomic_replace_bytes
from aimctexturegen.jobs.errors import JobError
from aimctexturegen.jobs.models import (
    CandidateRecord,
    JobRequest,
    JobStateRecord,
    dump_job_request,
    dump_job_state,
    validate_job_pair,
)
from aimctexturegen.jobs.state_machine import (
    cancel_state,
    recover_interrupted_state,
)
from aimctexturegen.projects._directory_guard import (
    DirectoryGuardError,
    FileIdentity,
    capture_directory_identity,
    hold_directory_identity,
    is_reparse_point,
    matches_directory_identity,
)
from aimctexturegen.projects.repository import OpenedProject, ProjectRepository


MAX_JOB_JSON_BYTES = 1024 * 1024
_ARTIFACT_DIRECTORIES = ("raw", "processed", "previews", "reports")
_JOB_CHILDREN = frozenset(
    {"request.json", "state.json", *_ARTIFACT_DIRECTORIES}
)
_JOB_WRITE_LOCK = threading.RLock()
_ModelT = TypeVar("_ModelT", bound=BaseModel)


@dataclass(frozen=True)
class LoadedJob:
    """A validated request/state pair and its canonical directory."""

    request: JobRequest
    state: JobStateRecord
    root: Path


class JobStore:
    """Persist jobs below projects opened through ``ProjectRepository``."""

    def __init__(self, repository: ProjectRepository) -> None:
        self._repository = repository

    def create(self, request: JobRequest) -> LoadedJob:
        """Publish one new queued job directory atomically."""

        if not isinstance(request, JobRequest):
            raise TypeError("request must be a JobRequest")
        with _JOB_WRITE_LOCK:
            with self._repository.open(request.project_id) as opened:
                with self._hold_jobs_root(opened, create=True) as jobs_identity:
                    if jobs_identity is None:
                        raise _job_error("UNSAFE_JOBS_PATH")
                    return self._create_opened(opened, jobs_identity, request)

    def load(self, project_id: UUID, job_id: UUID) -> LoadedJob:
        """Load and cross-validate one canonical job record."""

        _require_uuid(project_id, "project_id")
        _require_uuid(job_id, "job_id")
        with self._repository.open(project_id) as opened:
            with self._hold_jobs_root(opened, create=False) as jobs_identity:
                if jobs_identity is None:
                    raise _job_error("JOB_NOT_FOUND")
                return self._load_opened(opened, jobs_identity, job_id)

    def list(self, project_id: UUID) -> tuple[LoadedJob, ...]:
        """List valid canonical jobs by creation time then job ID."""

        _require_uuid(project_id, "project_id")
        with self._repository.open(project_id) as opened:
            with self._hold_jobs_root(opened, create=False) as jobs_identity:
                if jobs_identity is None:
                    return ()
                try:
                    entries = tuple(os.scandir(opened.jobs_root))
                except OSError as error:
                    raise _job_error("UNSAFE_JOBS_PATH") from error
                jobs: list[LoadedJob] = []
                for entry in sorted(entries, key=lambda item: item.name):
                    job_id = _canonical_uuid(entry.name)
                    if job_id is None:
                        continue
                    jobs.append(self._load_opened(opened, jobs_identity, job_id))
                jobs.sort(key=lambda job: str(job.request.job_id))
                jobs.sort(key=lambda job: job.request.created_at, reverse=True)
                return tuple(jobs)

    def replace_state(
        self,
        project_id: UUID,
        job_id: UUID,
        state: JobStateRecord,
        *,
        expected_revision: int,
    ) -> LoadedJob:
        """Conditionally replace only ``state.json`` with the next revision."""

        _require_uuid(project_id, "project_id")
        _require_uuid(job_id, "job_id")
        if not isinstance(state, JobStateRecord):
            raise TypeError("state must be a JobStateRecord")
        if type(expected_revision) is not int or expected_revision < 0:
            raise TypeError("expected_revision must be a nonnegative integer")

        with _JOB_WRITE_LOCK:
            with self._repository.open(project_id) as opened:
                with self._hold_jobs_root(opened, create=False) as jobs_identity:
                    if jobs_identity is None:
                        raise _job_error("JOB_NOT_FOUND")
                    job_root = opened.jobs_root / str(job_id)
                    try:
                        with hold_directory_identity(job_root) as job_identity:
                            current = self._load_guarded_job(
                                opened,
                                jobs_identity,
                                job_root,
                                job_identity,
                                job_id,
                                allow_state_temporary=True,
                            )
                            if current.state.revision != expected_revision:
                                raise _job_error("JOB_REVISION_CONFLICT")
                            if state.revision != expected_revision + 1:
                                raise _job_error("INVALID_JOB_RECORD")
                            try:
                                validate_job_pair(current.request, state)
                            except JobError as error:
                                raise _job_error("INVALID_JOB_RECORD") from error
                            payload = dump_job_state(state)
                            _require_json_size(payload)

                            def validate_state(readback: bytes) -> None:
                                loaded = _parse_model(
                                    readback,
                                    JobStateRecord,
                                )
                                if loaded != state:
                                    raise ValueError(
                                        "job state changed during write"
                                    )
                                validate_job_pair(current.request, loaded)

                            try:
                                atomic_replace_bytes(
                                    job_root / "state.json",
                                    payload,
                                    validate_state,
                                )
                            except AtomicWriteError as error:
                                raise _job_error(
                                    "JOB_STORAGE_UNAVAILABLE"
                                ) from error
                            except OSError as error:
                                raise _job_error(
                                    "JOB_STORAGE_UNAVAILABLE"
                                ) from error
                            self._require_identity(
                                opened.jobs_root,
                                jobs_identity,
                            )
                            self._require_identity(job_root, job_identity)
                            return LoadedJob(
                                request=current.request,
                                state=state,
                                root=job_root,
                            )
                    except JobError:
                        raise
                    except (DirectoryGuardError, OSError) as error:
                        if not os.path.lexists(job_root):
                            raise _job_error("JOB_NOT_FOUND") from error
                        raise _job_error("UNSAFE_JOB_PATH") from error

    def cancel(
        self,
        project_id: UUID,
        job_id: UUID,
        *,
        expected_revision: int,
        now: datetime,
    ) -> LoadedJob:
        """Persist a legal cancellation in one revision."""

        with _JOB_WRITE_LOCK:
            current = self.load(project_id, job_id)
            if current.state.revision != expected_revision:
                raise _job_error("JOB_REVISION_CONFLICT")
            replacement = cancel_state(current.state, now=now)
            return self.replace_state(
                project_id,
                job_id,
                replacement,
                expected_revision=expected_revision,
            )

    def retry(
        self,
        project_id: UUID,
        job_id: UUID,
        *,
        new_job_id: UUID,
        created_at: datetime,
    ) -> LoadedJob:
        """Create a new queued job preserving a terminal source request."""

        _require_uuid(new_job_id, "new_job_id")
        with _JOB_WRITE_LOCK:
            source = self.load(project_id, job_id)
            if source.state.status not in {"failed", "canceled"}:
                raise _invalid_transition()
            request = JobRequest.model_validate(
                {
                    **source.request.model_dump(),
                    "job_id": new_job_id,
                    "retry_of_job_id": source.request.job_id,
                    "created_at": created_at,
                }
            )
            return self.create(request)

    def recover_interrupted(
        self,
        project_id: UUID,
        job_id: UUID,
        *,
        expected_revision: int,
        now: datetime,
    ) -> LoadedJob:
        """Persist restart recovery only when the pure state machine changes."""

        with _JOB_WRITE_LOCK:
            current = self.load(project_id, job_id)
            if current.state.revision != expected_revision:
                raise _job_error("JOB_REVISION_CONFLICT")
            replacement = recover_interrupted_state(current.state, now=now)
            if replacement is current.state:
                return current
            return self.replace_state(
                project_id,
                job_id,
                replacement,
                expected_revision=expected_revision,
            )

    def _create_opened(
        self,
        opened: OpenedProject,
        jobs_identity: FileIdentity,
        request: JobRequest,
    ) -> LoadedJob:
        final_root = opened.jobs_root / str(request.job_id)
        temporary_root = opened.jobs_root / f"{request.job_id}.tmp"
        if os.path.lexists(final_root) or os.path.lexists(temporary_root):
            raise _job_error("JOB_ALREADY_EXISTS")

        state = _initial_state(request)
        request_payload = dump_job_request(request)
        state_payload = dump_job_state(state)
        _require_json_size(request_payload)
        _require_json_size(state_payload)
        temporary_identity: FileIdentity | None = None
        temporary_created = False

        try:
            temporary_root.mkdir()
            temporary_created = True
            temporary_identity = capture_directory_identity(temporary_root)
            with hold_directory_identity(temporary_root) as held_identity:
                if held_identity != temporary_identity:
                    raise DirectoryGuardError("job temporary identity changed")
                for name in _ARTIFACT_DIRECTORIES:
                    (temporary_root / name).mkdir()

                def validate_request(readback: bytes) -> None:
                    if _parse_model(readback, JobRequest) != request:
                        raise ValueError("job request changed during write")

                def validate_state(readback: bytes) -> None:
                    loaded = _parse_model(readback, JobStateRecord)
                    if loaded != state:
                        raise ValueError("initial job state changed during write")
                    validate_job_pair(request, loaded)

                atomic_replace_bytes(
                    temporary_root / "request.json",
                    request_payload,
                    validate_request,
                )
                atomic_replace_bytes(
                    temporary_root / "state.json",
                    state_payload,
                    validate_state,
                )
                loaded = self._load_guarded_job(
                    opened,
                    jobs_identity,
                    temporary_root,
                    temporary_identity,
                    request.job_id,
                    allow_temporary_name=True,
                )
                if loaded.request != request or loaded.state != state:
                    raise ValueError("published job records changed")
                self._require_exact_layout(temporary_root)
                if _tree_contains_reparse_point(temporary_root):
                    raise DirectoryGuardError("job tree contains a reparse point")

            self._require_identity(opened.jobs_root, jobs_identity)
            self._require_identity(temporary_root, temporary_identity)
            if os.path.lexists(final_root):
                raise _job_error("JOB_ALREADY_EXISTS")
            temporary_root.rename(final_root)
            return LoadedJob(request=request, state=state, root=final_root)
        except JobError:
            raise
        except (AtomicWriteError, ValidationError, ValueError, OSError) as error:
            raise _job_error("JOB_STORAGE_UNAVAILABLE") from error
        finally:
            if temporary_created and temporary_identity is not None:
                _remove_owned_temporary_tree(
                    temporary_root,
                    temporary_identity,
                )

    def _load_opened(
        self,
        opened: OpenedProject,
        jobs_identity: FileIdentity,
        job_id: UUID,
    ) -> LoadedJob:
        job_root = opened.jobs_root / str(job_id)
        try:
            try:
                os.lstat(job_root)
            except FileNotFoundError as error:
                raise _job_error("JOB_NOT_FOUND") from error
            with hold_directory_identity(job_root) as job_identity:
                return self._load_guarded_job(
                    opened,
                    jobs_identity,
                    job_root,
                    job_identity,
                    job_id,
                )
        except JobError:
            raise
        except (DirectoryGuardError, OSError) as error:
            if not os.path.lexists(job_root):
                raise _job_error("JOB_NOT_FOUND") from error
            raise _job_error("UNSAFE_JOB_PATH") from error

    def _load_guarded_job(
        self,
        opened: OpenedProject,
        jobs_identity: FileIdentity,
        job_root: Path,
        job_identity: FileIdentity,
        job_id: UUID,
        *,
        allow_temporary_name: bool = False,
        allow_state_temporary: bool = False,
    ) -> LoadedJob:
        expected_name = f"{job_id}.tmp" if allow_temporary_name else str(job_id)
        if job_root.parent != opened.jobs_root or job_root.name != expected_name:
            raise _job_error("UNSAFE_JOB_PATH")
        self._require_identity(opened.jobs_root, jobs_identity)
        self._require_identity(job_root, job_identity)
        self._require_exact_layout(
            job_root,
            allow_state_temporary=allow_state_temporary,
        )
        if _tree_contains_reparse_point(job_root):
            raise _job_error("UNSAFE_JOB_PATH")
        request = _read_model(
            job_root / "request.json",
            JobRequest,
            opened.jobs_root,
            jobs_identity,
            job_root,
            job_identity,
        )
        state = _read_model(
            job_root / "state.json",
            JobStateRecord,
            opened.jobs_root,
            jobs_identity,
            job_root,
            job_identity,
        )
        if (
            request.project_id != opened.manifest.project_id
            or request.job_id != job_id
            or state.project_id != opened.manifest.project_id
            or state.job_id != job_id
        ):
            raise _job_error("CORRUPT_JOB_RECORD")
        try:
            validate_job_pair(request, state)
        except JobError as error:
            raise _job_error("CORRUPT_JOB_RECORD") from error
        self._require_identity(opened.jobs_root, jobs_identity)
        self._require_identity(job_root, job_identity)
        return LoadedJob(request=request, state=state, root=job_root)

    @contextmanager
    def _hold_jobs_root(
        self,
        opened: OpenedProject,
        *,
        create: bool,
    ):
        jobs_root = opened.jobs_root
        if jobs_root.parent != opened.root or jobs_root.name != "jobs":
            raise _job_error("UNSAFE_JOBS_PATH")
        if not os.path.lexists(jobs_root):
            if not create:
                yield None
                return
            try:
                jobs_root.mkdir()
            except FileExistsError:
                pass
            except OSError as error:
                raise _job_error("JOB_STORAGE_UNAVAILABLE") from error
        try:
            with hold_directory_identity(jobs_root) as jobs_identity:
                try:
                    yield jobs_identity
                finally:
                    self._require_identity(jobs_root, jobs_identity)
        except JobError:
            raise
        except (DirectoryGuardError, OSError) as error:
            raise _job_error("UNSAFE_JOBS_PATH") from error

    @staticmethod
    def _require_identity(path: Path, expected: FileIdentity) -> None:
        if not matches_directory_identity(path, expected):
            raise _job_error("UNSAFE_JOB_PATH")

    @staticmethod
    def _require_exact_layout(
        job_root: Path,
        *,
        allow_state_temporary: bool = False,
    ) -> None:
        try:
            children = tuple(os.scandir(job_root))
        except OSError as error:
            raise _job_error("UNSAFE_JOB_PATH") from error
        expected_children = (
            _JOB_CHILDREN | {"state.json.tmp"}
            if allow_state_temporary
            else _JOB_CHILDREN
        )
        actual_children = frozenset(child.name for child in children)
        if actual_children not in {_JOB_CHILDREN, expected_children}:
            raise _job_error("CORRUPT_JOB_RECORD")
        for name in _ARTIFACT_DIRECTORIES:
            path = job_root / name
            try:
                status = os.lstat(path)
            except OSError as error:
                raise _job_error("CORRUPT_JOB_RECORD") from error
            if not stat.S_ISDIR(status.st_mode) or is_reparse_point(path, status):
                raise _job_error("UNSAFE_JOB_PATH")


def _initial_state(request: JobRequest) -> JobStateRecord:
    candidates = tuple(
        CandidateRecord(
            candidate_index=index,
            seed=seed,
            status="pending",
            failure=None,
            started_at=None,
            finished_at=None,
        )
        for index, seed in enumerate(request.seeds)
    )
    return JobStateRecord(
        schema_version=1,
        job_id=request.job_id,
        project_id=request.project_id,
        revision=0,
        status="queued",
        candidates=candidates,
        failure=None,
        created_at=request.created_at,
        updated_at=request.created_at,
        started_at=None,
        finished_at=None,
    )


def _read_model(
    path: Path,
    model_type: type[_ModelT],
    jobs_root: Path,
    jobs_identity: FileIdentity,
    job_root: Path,
    job_identity: FileIdentity,
) -> _ModelT:
    try:
        path_status = os.lstat(path)
        if (
            not stat.S_ISREG(path_status.st_mode)
            or is_reparse_point(path, path_status)
            or path_status.st_size > MAX_JOB_JSON_BYTES
        ):
            raise OSError("job JSON is not a bounded regular file")
        expected_identity = _file_identity(path_status)
        with path.open("rb") as source:
            handle_status = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(handle_status.st_mode)
                or _file_identity(handle_status) != expected_identity
            ):
                raise OSError("job JSON changed before reading")
            payload = source.read(MAX_JOB_JSON_BYTES + 1)
            if len(payload) > MAX_JOB_JSON_BYTES:
                raise OSError("job JSON exceeds its size limit")
            if not matches_directory_identity(jobs_root, jobs_identity):
                raise OSError("jobs directory identity changed")
            if not matches_directory_identity(job_root, job_identity):
                raise OSError("job directory identity changed")
            final_status = os.lstat(path)
            if (
                _file_identity(final_status) != expected_identity
                or is_reparse_point(path, final_status)
                or _file_identity(os.fstat(source.fileno())) != expected_identity
            ):
                raise OSError("job JSON changed while reading")
        return _parse_model(payload, model_type)
    except JobError:
        raise
    except (OSError, ValueError, ValidationError) as error:
        raise _job_error("CORRUPT_JOB_RECORD") from error


def _parse_model(payload: bytes, model_type: type[_ModelT]) -> _ModelT:
    if len(payload) > MAX_JOB_JSON_BYTES:
        raise ValueError("job JSON exceeds its size limit")
    return model_type.model_validate_json(payload, strict=True)


def _require_json_size(payload: bytes) -> None:
    if len(payload) > MAX_JOB_JSON_BYTES:
        raise _job_error("INVALID_JOB_RECORD")


def _canonical_uuid(name: str) -> UUID | None:
    try:
        value = UUID(name)
    except (ValueError, AttributeError):
        return None
    return value if str(value) == name else None


def _require_uuid(value: UUID, name: str) -> None:
    if not isinstance(value, UUID):
        raise TypeError(f"{name} must be a UUID")


def _file_identity(status: os.stat_result) -> tuple[int, int]:
    return status.st_dev, status.st_ino


def _tree_contains_reparse_point(root: Path) -> bool:
    try:
        for current_root, directory_names, file_names in os.walk(
            root,
            topdown=True,
            followlinks=False,
        ):
            current = Path(current_root)
            for name in (*directory_names, *file_names):
                path = current / name
                status = os.lstat(path)
                if is_reparse_point(path, status):
                    return True
    except OSError:
        return True
    return False


def _remove_owned_temporary_tree(
    temporary_root: Path,
    expected_identity: FileIdentity,
) -> None:
    if (
        temporary_root.name.endswith(".tmp")
        and matches_directory_identity(temporary_root, expected_identity)
        and not _tree_contains_reparse_point(temporary_root)
        and matches_directory_identity(temporary_root, expected_identity)
    ):
        shutil.rmtree(temporary_root)


def _job_error(code: str) -> JobError:
    messages = {
        "JOB_NOT_FOUND": "未找到该任务",
        "JOB_ALREADY_EXISTS": "任务已存在",
        "INVALID_JOB_RECORD": "任务记录无效",
        "CORRUPT_JOB_RECORD": "任务记录损坏或不一致",
        "JOB_REVISION_CONFLICT": "任务状态已被其他操作更新",
        "JOB_STORAGE_UNAVAILABLE": "无法安全保存任务",
        "UNSAFE_JOBS_PATH": "任务存储目录不安全",
        "UNSAFE_JOB_PATH": "任务目录不安全",
    }
    return JobError(code, messages[code])


def _invalid_transition() -> JobError:
    return JobError(
        "INVALID_JOB_TRANSITION",
        "当前任务状态不允许此操作",
    )
