"""Canonical JSON persistence for legacy and schema-3 jobs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import ValidationError

from aimctexturegen.core.atomic_files import AtomicWriteError, atomic_replace_bytes
from aimctexturegen.jobs.codec import (
    DurableJobRequest,
    DurableJobState,
    dump_durable_request,
    dump_durable_state,
    load_job_request,
    load_job_state,
    validate_durable_pair,
)
from aimctexturegen.jobs.errors import JobError
from aimctexturegen.jobs.models import (
    CandidateRecord,
    JobRequest,
    JobStateRecord,
    dump_job_request,
    dump_job_state,
)
from aimctexturegen.jobs.models_v3 import GenerationJobRequest, GenerationJobState
from aimctexturegen.jobs.state_machine import cancel_state, recover_interrupted_state
from aimctexturegen.projects._directory_guard import (
    DirectoryGuardError,
    FileIdentity,
    capture_directory_identity,
    hold_directory_identity,
    is_reparse_point,
    matches_directory_identity,
)
from aimctexturegen.projects.repository import OpenedProject, ProjectRepository
from aimctexturegen.core.relative_paths import validate_project_relative_path


MAX_JOB_JSON_BYTES = 1024 * 1024
_ARTIFACT_DIRECTORIES = ("raw", "processed", "previews", "reports")
_LEGACY_JOB_CHILDREN = frozenset({"request.json", "state.json", *_ARTIFACT_DIRECTORIES})
_GENERATION_JOB_CHILDREN = frozenset(
    {"request.json", "state.json", "inputs", *_ARTIFACT_DIRECTORIES}
)
_GENERATION_INPUT_FILE = re.compile(r"^(style/[0-9]{2}\.png|structure\.png)$")
_JOB_WRITE_LOCK = threading.RLock()


@dataclass(frozen=True)
class LoadedJob:
    """A validated durable request/state pair and its canonical directory."""

    request: DurableJobRequest
    state: DurableJobState
    root: Path


@dataclass(frozen=True)
class JobScanIssue:
    """A path-free issue for one malformed canonical job directory."""

    job_id: UUID
    code: str
    user_message: str


@dataclass(frozen=True)
class JobScanResult:
    """Valid jobs and isolated canonical-job issues from one project scan."""

    jobs: tuple[LoadedJob, ...]
    issues: tuple[JobScanIssue, ...]


@dataclass(frozen=True)
class JobInputFile:
    relative_path: str
    payload: bytes
    sha256: str


@dataclass(frozen=True)
class JobInputSnapshot:
    references_json: bytes
    files: tuple[JobInputFile, ...]


class JobStore:
    """Persist jobs below projects opened through ``ProjectRepository``."""

    def __init__(self, repository: ProjectRepository) -> None:
        self._repository = repository

    def create(self, request: JobRequest) -> LoadedJob:
        if not isinstance(request, JobRequest):
            raise TypeError("request must be a JobRequest")
        with _JOB_WRITE_LOCK:
            with self._repository.open(request.project_id) as opened:
                with self._hold_jobs_root(opened, create=True) as jobs_identity:
                    if jobs_identity is None:
                        raise _job_error("UNSAFE_JOBS_PATH")
                    state = _initial_state(request)
                    return self._create_opened(
                        opened,
                        jobs_identity,
                        request,
                        state,
                        inputs=None,
                    )

    def create_generation(
        self,
        request: GenerationJobRequest,
        inputs: JobInputSnapshot,
    ) -> LoadedJob:
        if not isinstance(request, GenerationJobRequest):
            raise TypeError("request must be a GenerationJobRequest")
        if not isinstance(inputs, JobInputSnapshot):
            raise TypeError("inputs must be a JobInputSnapshot")
        with _JOB_WRITE_LOCK:
            with self._repository.open(request.project_id) as opened:
                with self._hold_jobs_root(opened, create=True) as jobs_identity:
                    if jobs_identity is None:
                        raise _job_error("UNSAFE_JOBS_PATH")
                    state = GenerationJobState.initial_from_request(request)
                    return self._create_opened(
                        opened,
                        jobs_identity,
                        request,
                        state,
                        inputs=inputs,
                    )

    def load(self, project_id: UUID, job_id: UUID) -> LoadedJob:
        _require_uuid(project_id, "project_id")
        _require_uuid(job_id, "job_id")
        with self._repository.open(project_id) as opened:
            with self._hold_jobs_root(opened, create=False) as jobs_identity:
                if jobs_identity is None:
                    raise _job_error("JOB_NOT_FOUND")
                return self._load_opened(opened, jobs_identity, job_id)

    def list(self, project_id: UUID) -> tuple[LoadedJob, ...]:
        result = self.scan(project_id)
        if result.issues:
            issue = result.issues[0]
            raise JobError(issue.code, issue.user_message)
        return result.jobs

    def scan(self, project_id: UUID) -> JobScanResult:
        _require_uuid(project_id, "project_id")
        with self._repository.open(project_id) as opened:
            with self._hold_jobs_root(opened, create=False) as jobs_identity:
                if jobs_identity is None:
                    return JobScanResult(jobs=(), issues=())
                try:
                    entries = tuple(os.scandir(opened.jobs_root))
                except OSError as error:
                    raise _job_error("UNSAFE_JOBS_PATH") from error
                jobs: list[LoadedJob] = []
                issues: list[JobScanIssue] = []
                for entry in sorted(entries, key=lambda item: item.name):
                    job_id = _canonical_uuid(entry.name)
                    if job_id is None:
                        continue
                    try:
                        jobs.append(self._load_opened(opened, jobs_identity, job_id))
                    except JobError as error:
                        issues.append(
                            JobScanIssue(
                                job_id=job_id,
                                code=error.code,
                                user_message=error.user_message,
                            )
                        )
                jobs.sort(key=lambda job: str(job.request.job_id))
                jobs.sort(key=lambda job: job.request.created_at, reverse=True)
                issues.sort(key=lambda issue: str(issue.job_id))
                return JobScanResult(jobs=tuple(jobs), issues=tuple(issues))

    def replace_state(
        self,
        project_id: UUID,
        job_id: UUID,
        state: DurableJobState,
        *,
        expected_revision: int,
    ) -> LoadedJob:
        _require_uuid(project_id, "project_id")
        _require_uuid(job_id, "job_id")
        if not isinstance(state, (JobStateRecord, GenerationJobState)):
            raise TypeError("state must be a durable job state")
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
                                validate_durable_pair(current.request, state)
                            except JobError as error:
                                raise _job_error("INVALID_JOB_RECORD") from error
                            payload = dump_durable_state(state)
                            _require_json_size(payload)

                            def validate_state(readback: bytes) -> None:
                                loaded = load_job_state(readback)
                                if loaded != state:
                                    raise ValueError("job state changed during write")
                                validate_durable_pair(current.request, loaded)

                            atomic_replace_bytes(
                                job_root / "state.json",
                                payload,
                                validate_state,
                            )
                            self._require_identity(opened.jobs_root, jobs_identity)
                            self._require_identity(job_root, job_identity)
                            return LoadedJob(
                                request=current.request,
                                state=state,
                                root=job_root,
                            )
                    except JobError:
                        raise
                    except (AtomicWriteError, DirectoryGuardError, OSError, ValidationError, ValueError) as error:
                        if not os.path.lexists(job_root):
                            raise _job_error("JOB_NOT_FOUND") from error
                        raise _job_error("JOB_STORAGE_UNAVAILABLE") from error

    def cancel(
        self,
        project_id: UUID,
        job_id: UUID,
        *,
        expected_revision: int,
        now: datetime,
    ) -> LoadedJob:
        with _JOB_WRITE_LOCK:
            current = self.load(project_id, job_id)
            if not isinstance(current.state, JobStateRecord):
                raise _invalid_transition()
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
        _require_uuid(new_job_id, "new_job_id")
        with _JOB_WRITE_LOCK:
            source = self.load(project_id, job_id)
            if not isinstance(source.request, JobRequest) or not isinstance(
                source.state, JobStateRecord
            ):
                raise _invalid_transition()
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
        with _JOB_WRITE_LOCK:
            current = self.load(project_id, job_id)
            if not isinstance(current.state, JobStateRecord):
                raise _invalid_transition()
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

    def resolve_job_file(
        self,
        project_id: UUID,
        job_id: UUID,
        relative_path: str,
    ) -> Path:
        _require_uuid(project_id, "project_id")
        _require_uuid(job_id, "job_id")
        validated = validate_project_relative_path(relative_path)
        loaded = self.load(project_id, job_id)
        candidate = loaded.root / Path(validated)
        try:
            resolved = candidate.resolve(strict=True)
            root = loaded.root.resolve(strict=True)
        except OSError as error:
            raise _job_error("CORRUPT_JOB_RECORD") from error
        if not resolved.is_relative_to(root):
            raise _job_error("UNSAFE_JOB_PATH")
        try:
            status = os.lstat(resolved)
        except OSError as error:
            raise _job_error("CORRUPT_JOB_RECORD") from error
        if not stat.S_ISREG(status.st_mode) or is_reparse_point(resolved, status):
            raise _job_error("UNSAFE_JOB_PATH")
        return resolved

    def _create_opened(
        self,
        opened: OpenedProject,
        jobs_identity: FileIdentity,
        request: DurableJobRequest,
        state: DurableJobState,
        *,
        inputs: JobInputSnapshot | None,
    ) -> LoadedJob:
        final_root = opened.jobs_root / str(request.job_id)
        temporary_root = opened.jobs_root / f"{request.job_id}.tmp"
        if os.path.lexists(final_root) or os.path.lexists(temporary_root):
            raise _job_error("JOB_ALREADY_EXISTS")

        request_payload = dump_durable_request(request)
        state_payload = dump_durable_state(state)
        _require_json_size(request_payload)
        _require_json_size(state_payload)
        temporary_identity: FileIdentity | None = None
        temporary_created = False
        validated_inputs = _validate_input_snapshot(inputs) if inputs else ()

        try:
            temporary_root.mkdir()
            temporary_created = True
            with hold_directory_identity(temporary_root) as created_identity:
                temporary_identity = created_identity
            if capture_directory_identity(temporary_root) != temporary_identity:
                raise DirectoryGuardError("job temporary identity changed")
            with hold_directory_identity(temporary_root) as held_identity:
                if held_identity != temporary_identity:
                    raise DirectoryGuardError("job temporary identity changed")
                for name in _ARTIFACT_DIRECTORIES:
                    (temporary_root / name).mkdir()
                if inputs is not None:
                    self._stage_inputs(temporary_root / "inputs", inputs, validated_inputs)

                def validate_request(readback: bytes) -> None:
                    if load_job_request(readback) != request:
                        raise ValueError("job request changed during write")

                def validate_state(readback: bytes) -> None:
                    loaded = load_job_state(readback)
                    if loaded != state:
                        raise ValueError("initial job state changed during write")
                    validate_durable_pair(request, loaded)

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
        except (AtomicWriteError, DirectoryGuardError, OSError, ValidationError, ValueError) as error:
            raise _job_error("JOB_STORAGE_UNAVAILABLE") from error
        finally:
            if temporary_created and temporary_identity is not None:
                _remove_owned_temporary_tree(temporary_root, temporary_identity)

    def _stage_inputs(
        self,
        inputs_root: Path,
        inputs: JobInputSnapshot,
        validated_inputs: tuple[JobInputFile, ...],
    ) -> None:
        inputs_root.mkdir()
        (inputs_root / "references.json").write_bytes(inputs.references_json)
        for item in validated_inputs:
            target = inputs_root / Path(item.relative_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            if hashlib.sha256(item.payload).hexdigest() != item.sha256:
                raise _job_error("INVALID_JOB_RECORD")
            target.write_bytes(item.payload)

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
        allow_state_temporary: bool = True,
    ) -> LoadedJob:
        expected_name = f"{job_id}.tmp" if allow_temporary_name else str(job_id)
        if job_root.parent != opened.jobs_root or job_root.name != expected_name:
            raise _job_error("UNSAFE_JOB_PATH")
        self._require_identity(opened.jobs_root, jobs_identity)
        self._require_identity(job_root, job_identity)
        layout = self._require_exact_layout(
            job_root,
            allow_state_temporary=allow_state_temporary,
        )
        if _tree_contains_reparse_point(job_root):
            raise _job_error("UNSAFE_JOB_PATH")
        try:
            request = load_job_request(
                _read_bytes(
                    job_root / "request.json",
                    opened.jobs_root,
                    jobs_identity,
                    job_root,
                    job_identity,
                )
            )
            state = load_job_state(
                _read_bytes(
                    job_root / "state.json",
                    opened.jobs_root,
                    jobs_identity,
                    job_root,
                    job_identity,
                )
            )
            if layout == "legacy" and not (
                isinstance(request, JobRequest) and isinstance(state, JobStateRecord)
            ):
                raise _job_error("CORRUPT_JOB_RECORD")
            if layout == "generation" and not (
                isinstance(request, GenerationJobRequest)
                and isinstance(state, GenerationJobState)
            ):
                raise _job_error("CORRUPT_JOB_RECORD")
            if (
                request.project_id != opened.manifest.project_id
                or request.job_id != job_id
                or state.project_id != opened.manifest.project_id
                or state.job_id != job_id
            ):
                raise _job_error("CORRUPT_JOB_RECORD")
            validate_durable_pair(request, state)
            if layout == "generation":
                _validate_generation_inputs(job_root / "inputs", request)
        except JobError as error:
            raise _job_error("CORRUPT_JOB_RECORD") from error
        except (ValueError, ValidationError) as error:
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
    ) -> Literal["legacy", "generation"]:
        try:
            children = tuple(os.scandir(job_root))
        except OSError as error:
            raise _job_error("UNSAFE_JOB_PATH") from error
        actual_children = frozenset(child.name for child in children)
        if _matches_layout(actual_children, _LEGACY_JOB_CHILDREN, allow_state_temporary):
            _require_state_temporary(job_root, actual_children)
            _require_artifact_directories(job_root)
            return "legacy"
        if _matches_layout(actual_children, _GENERATION_JOB_CHILDREN, allow_state_temporary):
            _require_state_temporary(job_root, actual_children)
            _require_artifact_directories(job_root)
            _require_generation_inputs(job_root / "inputs")
            return "generation"
        raise _job_error("CORRUPT_JOB_RECORD")


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


def _read_bytes(
    path: Path,
    jobs_root: Path,
    jobs_identity: FileIdentity,
    job_root: Path,
    job_identity: FileIdentity,
) -> bytes:
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
        return payload
    except JobError:
        raise
    except (OSError, ValueError, ValidationError) as error:
        raise _job_error("CORRUPT_JOB_RECORD") from error


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


def _matches_layout(
    actual_children: frozenset[str],
    expected_children: frozenset[str],
    allow_state_temporary: bool,
) -> bool:
    allowed = (
        expected_children | {"state.json.tmp"}
        if allow_state_temporary
        else expected_children
    )
    return actual_children in {expected_children, allowed}


def _require_state_temporary(job_root: Path, actual_children: frozenset[str]) -> None:
    if "state.json.tmp" not in actual_children:
        return
    temporary = job_root / "state.json.tmp"
    try:
        status = os.lstat(temporary)
    except OSError as error:
        raise _job_error("CORRUPT_JOB_RECORD") from error
    if is_reparse_point(temporary, status):
        raise _job_error("UNSAFE_JOB_PATH")
    if not stat.S_ISREG(status.st_mode) or status.st_size > MAX_JOB_JSON_BYTES:
        raise _job_error("CORRUPT_JOB_RECORD")


def _require_artifact_directories(job_root: Path) -> None:
    for name in _ARTIFACT_DIRECTORIES:
        path = job_root / name
        try:
            status = os.lstat(path)
        except OSError as error:
            raise _job_error("CORRUPT_JOB_RECORD") from error
        if not stat.S_ISDIR(status.st_mode) or is_reparse_point(path, status):
            raise _job_error("UNSAFE_JOB_PATH")


def _validate_input_snapshot(
    inputs: JobInputSnapshot | None,
) -> tuple[JobInputFile, ...]:
    if inputs is None:
        return ()
    if len(inputs.references_json) > MAX_JOB_JSON_BYTES:
        raise _job_error("INVALID_JOB_RECORD")
    try:
        json.loads(inputs.references_json)
    except json.JSONDecodeError as error:
        raise _job_error("INVALID_JOB_RECORD") from error
    seen: set[str] = set()
    styles = 0
    structures = 0
    for item in inputs.files:
        if _GENERATION_INPUT_FILE.fullmatch(item.relative_path) is None:
            raise _job_error("INVALID_JOB_RECORD")
        if item.relative_path in seen:
            raise _job_error("INVALID_JOB_RECORD")
        seen.add(item.relative_path)
        if hashlib.sha256(item.payload).hexdigest() != item.sha256:
            raise _job_error("INVALID_JOB_RECORD")
        if item.relative_path.startswith("style/"):
            styles += 1
        else:
            structures += 1
    if styles > 8 or structures > 1:
        raise _job_error("INVALID_JOB_RECORD")
    return inputs.files


def _require_generation_inputs(inputs_root: Path) -> None:
    try:
        status = os.lstat(inputs_root)
    except OSError as error:
        raise _job_error("CORRUPT_JOB_RECORD") from error
    if not stat.S_ISDIR(status.st_mode) or is_reparse_point(inputs_root, status):
        raise _job_error("UNSAFE_JOB_PATH")
    try:
        children = tuple(os.scandir(inputs_root))
    except OSError as error:
        raise _job_error("UNSAFE_JOB_PATH") from error
    names = frozenset(child.name for child in children)
    if "references.json" not in names:
        raise _job_error("CORRUPT_JOB_RECORD")
    references = inputs_root / "references.json"
    _require_safe_input_file(references, max_size=MAX_JOB_JSON_BYTES)
    if "style" in names:
        style_root = inputs_root / "style"
        try:
            style_status = os.lstat(style_root)
        except OSError as error:
            raise _job_error("CORRUPT_JOB_RECORD") from error
        if not stat.S_ISDIR(style_status.st_mode) or is_reparse_point(style_root, style_status):
            raise _job_error("UNSAFE_JOB_PATH")
        for path in style_root.iterdir():
            if not path.is_file() or _GENERATION_INPUT_FILE.fullmatch(f"style/{path.name}") is None:
                raise _job_error("CORRUPT_JOB_RECORD")
            _require_safe_input_file(path)
    for child in children:
        if child.name == "style":
            continue
        if child.name not in {"references.json", "structure.png"}:
            raise _job_error("CORRUPT_JOB_RECORD")
        if child.name == "structure.png":
            _require_safe_input_file(inputs_root / child.name)


def _require_safe_input_file(path: Path, *, max_size: int | None = None) -> None:
    try:
        status = os.lstat(path)
    except OSError as error:
        raise _job_error("CORRUPT_JOB_RECORD") from error
    if not stat.S_ISREG(status.st_mode) or is_reparse_point(path, status):
        raise _job_error("UNSAFE_JOB_PATH")
    if max_size is not None and status.st_size > max_size:
        raise _job_error("CORRUPT_JOB_RECORD")


def _validate_generation_inputs(inputs_root: Path, request: GenerationJobRequest) -> None:
    expected = {}
    for artifact in (*request.references.style, *request.references.structure):
        relative = artifact.relative_path.removeprefix("inputs/")
        expected[relative] = artifact
    try:
        metadata = json.loads((inputs_root / "references.json").read_bytes())
        actual_metadata = {
            item["relative_path"]: item["sha256"]
            for group in ("style", "structure")
            for item in metadata[group]
        }
    except (KeyError, TypeError, json.JSONDecodeError, OSError) as error:
        raise _job_error("CORRUPT_JOB_RECORD") from error
    if set(actual_metadata) != {artifact.relative_path for artifact in (*request.references.style, *request.references.structure)}:
        raise _job_error("CORRUPT_JOB_RECORD")
    if len(metadata.get("style", ())) != len(request.references.style) or len(metadata.get("structure", ())) != len(request.references.structure):
        raise _job_error("CORRUPT_JOB_RECORD")
    for relative, artifact in expected.items():
        path = inputs_root / relative
        _require_safe_input_file(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != artifact.sha256 or actual_metadata[artifact.relative_path] != artifact.sha256:
            raise _job_error("CORRUPT_JOB_RECORD")
    staged = {
        path.relative_to(inputs_root).as_posix()
        for path in inputs_root.rglob("*")
        if path.is_file() and path.name != "references.json"
    }
    if staged != set(expected):
        raise _job_error("CORRUPT_JOB_RECORD")


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
