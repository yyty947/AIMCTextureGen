from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Literal
from uuid import UUID

from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from aimctexturegen.core.atomic_files import AtomicWriteError, atomic_replace_bytes
from aimctexturegen.jobs.models_v3 import (
    CandidateArtifacts,
    ExecutionBatch,
    GenerationJobRequest,
    GenerationJobState,
    StoredArtifact,
)
from aimctexturegen.jobs.store import JobError, JobStore, LoadedJob
from aimctexturegen.projects._directory_guard import (
    DirectoryGuardError,
    FileIdentity,
    capture_directory_identity,
    hold_directory_identity,
    is_reparse_point,
    matches_directory_identity,
)
from aimctexturegen.processing.models import ProcessingReport, dump_report_json
from aimctexturegen.processing.pipeline import process_candidate

from .errors import GenerationError, generation_error


ArtifactKind = Literal["raw", "final", "nearest", "tile", "report"]


class CandidateArtifactStore:
    def __init__(self, store: JobStore) -> None:
        self._store = store

    def publish_raw_batch(
        self,
        loaded: LoadedJob,
        batch: ExecutionBatch,
        payloads: tuple[bytes, ...],
        *,
        canvas_size: int,
    ) -> tuple[StoredArtifact, ...]:
        request = _generation_request(loaded)
        _require_batch(request, batch)
        if len(payloads) != len(batch.candidate_indices):
            raise generation_error("OUTPUT_CONTRACT_VIOLATION")
        raw_root = loaded.root / "raw"
        temporary_root = raw_root / f".batch-{batch.batch_index}.tmp"
        final_root = raw_root / f"batch-{batch.batch_index}"
        try:
            with _hold_publication_roots(loaded.root, raw_root) as guard:
                guard.require()
                if os.path.lexists(final_root):
                    raise generation_error("OUTPUT_CONTRACT_VIOLATION")
                temporary_identity: FileIdentity | None = None
                temporary_identity = _reset_temporary_directory(temporary_root, guard)
                assert temporary_identity is not None
                temporary_guard = guard.with_directory(temporary_root, temporary_identity)
                published_identity: FileIdentity | None = None
                try:
                    for position, payload in enumerate(payloads):
                        candidate_index = batch.candidate_indices[position]
                        name = f"candidate-{candidate_index}.png"
                        _validate_output_payload(payload, canvas_size=canvas_size)
                        target = temporary_root / name
                        _write_atomic_bytes(
                            target,
                            payload,
                            lambda readback: _validate_output_payload(
                                readback,
                                canvas_size=canvas_size,
                            ),
                            temporary_guard,
                        )
                        temporary_guard.require()
                        _validate_stored_png(
                            target,
                            relative_path=f"raw/batch-{batch.batch_index}/{name}",
                            kind="raw",
                            canvas_size=canvas_size,
                            require_rgb=True,
                        )
                    _validate_raw_directory(
                        temporary_root,
                        batch,
                        canvas_size=canvas_size,
                    )
                    temporary_guard.require()
                    published_identity = _publish_directory(
                        temporary_root,
                        final_root,
                        temporary_identity,
                        guard,
                    )
                    try:
                        return _validate_raw_directory(
                            final_root,
                            batch,
                            canvas_size=canvas_size,
                        )
                    except BaseException:
                        _remove_owned_directory(
                            final_root,
                            guard,
                            expected_identity=published_identity,
                        )
                        published_identity = None
                        raise
                finally:
                    if temporary_identity is not None:
                        _cleanup_temporary_directory(
                            temporary_root,
                            temporary_identity,
                            guard,
                        )
        except GenerationError:
            raise
        except (AtomicWriteError, DirectoryGuardError, OSError, ValidationError) as error:
            raise _storage_error() from error

    def process_and_publish(
        self,
        loaded: LoadedJob,
        *,
        candidate_index: int,
        resolution: int,
    ) -> CandidateArtifacts:
        request = _generation_request(loaded)
        batch = _batch_for_candidate(request, candidate_index)
        raw_relative_path = (
            f"raw/batch-{batch.batch_index}/candidate-{candidate_index}.png"
        )
        processed_root = loaded.root / "processed"
        temporary_root = processed_root / f".candidate-{candidate_index}.tmp"
        final_root = processed_root / f"candidate-{candidate_index}"
        try:
            raw_root = loaded.root / "raw"
            with _hold_publication_roots(
                loaded.root,
                raw_root,
                processed_root,
            ) as guard:
                guard.require()
                try:
                    raw_path = self._store.resolve_job_file(
                        loaded.request.project_id,
                        loaded.request.job_id,
                        raw_relative_path,
                    )
                except JobError as error:
                    raise generation_error(
                        "OUTPUT_CONTRACT_VIOLATION",
                        technical_details=error.code,
                    ) from error
                raw_artifact = _validate_stored_png(
                    raw_path,
                    relative_path=raw_relative_path,
                    kind="raw",
                    canvas_size=1024,
                    require_rgb=True,
                )
                temporary_identity: FileIdentity | None = None
                temporary_identity = _reset_temporary_directory(temporary_root, guard)
                assert temporary_identity is not None
                temporary_guard = guard.with_directory(temporary_root, temporary_identity)
                published_identity: FileIdentity | None = None
                try:
                    temporary_guard.require()
                    report = process_candidate(
                        raw_path,
                        temporary_root,
                        stem="final",
                        resolution=resolution,
                    )
                    temporary_guard.require()
                    _replace_owned_file(
                        temporary_root / "final-nn.png",
                        temporary_root / "nearest.png",
                        temporary_guard,
                    )
                    _replace_owned_file(
                        temporary_root / "final-tile.png",
                        temporary_root / "tile.png",
                        temporary_guard,
                    )
                    updated_report = report.model_copy(
                        update={
                            "previews": report.previews.model_copy(
                                update={
                                    "nearest_neighbor": report.previews.nearest_neighbor.model_copy(
                                        update={"path": "nearest.png"}
                                    ),
                                    "tile_3x3": report.previews.tile_3x3.model_copy(
                                        update={"path": "tile.png"}
                                    ),
                                }
                            )
                        }
                    )
                    _write_atomic_bytes(
                        temporary_root / "report.json",
                        dump_report_json(updated_report),
                        lambda payload: ProcessingReport.model_validate_json(payload),
                        temporary_guard,
                    )
                    _remove_file_if_present(
                        temporary_root / "final-report.json",
                        temporary_guard,
                    )
                    temporary_guard.require()
                    _validate_processed_directory(
                        temporary_root,
                        updated_report,
                        resolution,
                        candidate_index,
                    )
                    temporary_guard.require()
                    if os.path.lexists(final_root):
                        raise generation_error("OUTPUT_CONTRACT_VIOLATION")
                    published_identity = _publish_directory(
                        temporary_root,
                        final_root,
                        temporary_identity,
                        guard,
                    )
                    try:
                        return _reopen_candidate_artifacts(
                            final_root,
                            raw_artifact,
                            candidate_index,
                            resolution,
                        )
                    except BaseException:
                        _remove_owned_directory(
                            final_root,
                            guard,
                            expected_identity=published_identity,
                        )
                        published_identity = None
                        raise
                finally:
                    if temporary_identity is not None:
                        _cleanup_temporary_directory(
                            temporary_root,
                            temporary_identity,
                            guard,
                        )
        except GenerationError:
            raise
        except (AtomicWriteError, DirectoryGuardError, OSError, ValidationError) as error:
            raise _storage_error() from error
        except Exception as error:
            raise GenerationError(
                "POSTPROCESSING_FAILED",
                "候选后处理失败",
                recommended_actions=("重新运行该候选的后处理",),
                technical_details=str(error),
            ) from error

    def resolve(
        self,
        project_id: UUID,
        job_id: UUID,
        candidate_index: int,
        kind: ArtifactKind,
        *,
        inherited_from: StoredArtifact | None = None,
    ) -> Path:
        if kind not in {"raw", "final", "nearest", "tile", "report"}:
            raise generation_error("OUTPUT_CONTRACT_VIOLATION")
        loaded = self._store.load(project_id, job_id)
        if inherited_from is not None:
            request = _generation_request(loaded)
            if request.parent_job_id is None:
                raise generation_error("OUTPUT_CONTRACT_VIOLATION")
            child_candidate = _candidate_record(loaded, candidate_index)
            if (
                child_candidate.status != "inherited"
                or child_candidate.lineage is None
                or child_candidate.lineage.parent_job_id != request.parent_job_id
                or inherited_from.kind != kind
            ):
                raise generation_error("OUTPUT_CONTRACT_VIOLATION")
            try:
                parent = self._store.load(project_id, request.parent_job_id)
            except JobError as error:
                raise generation_error(
                    "OUTPUT_CONTRACT_VIOLATION",
                    technical_details=error.code,
                ) from error
            parent_candidate_index = child_candidate.lineage.parent_candidate_index
            parent_candidate = _candidate_record(parent, parent_candidate_index)
            if parent_candidate.status not in {"completed", "inherited"}:
                raise generation_error("OUTPUT_CONTRACT_VIOLATION")
            expected_artifact = getattr(parent_candidate.artifacts, kind, None)
            if expected_artifact is None:
                raise generation_error("OUTPUT_CONTRACT_VIOLATION")
            expected_relative_path = _canonical_relative_path(
                _generation_request(parent),
                parent_candidate_index,
                kind,
            )
            if (
                expected_artifact != inherited_from
                or inherited_from.relative_path != expected_relative_path
            ):
                raise generation_error("OUTPUT_CONTRACT_VIOLATION")
            try:
                path = self._store.resolve_job_file(
                    project_id,
                    request.parent_job_id,
                    expected_relative_path,
                )
            except JobError as error:
                raise generation_error("OUTPUT_CONTRACT_VIOLATION", technical_details=error.code) from error
            path_bytes = _read_safe_bytes(path)
            if (
                hashlib.sha256(path_bytes).hexdigest() != inherited_from.sha256
                or len(path_bytes) != inherited_from.byte_size
            ):
                raise generation_error("OUTPUT_CONTRACT_VIOLATION")
            return path
        relative_path = _canonical_relative_path(_generation_request(loaded), candidate_index, kind)
        try:
            path = self._store.resolve_job_file(project_id, job_id, relative_path)
        except JobError as error:
            raise generation_error("OUTPUT_CONTRACT_VIOLATION", technical_details=error.code) from error
        if kind != "raw":
            report_path = self._store.resolve_job_file(
                project_id,
                job_id,
                _canonical_relative_path(_generation_request(loaded), candidate_index, "report"),
            )
            _verify_processed_hashes(path.parent, report_path, kind)
        return path


def _generation_request(loaded: LoadedJob) -> GenerationJobRequest:
    if not isinstance(loaded.request, GenerationJobRequest):
        raise TypeError("loaded job must be schema-3 generation job")
    return loaded.request


def _candidate_record(loaded: LoadedJob, candidate_index: int):
    if not isinstance(loaded.state, GenerationJobState):
        raise generation_error("OUTPUT_CONTRACT_VIOLATION")
    if candidate_index not in range(4):
        raise generation_error("OUTPUT_CONTRACT_VIOLATION")
    return loaded.state.candidates[candidate_index]


def _require_batch(request: GenerationJobRequest, batch: ExecutionBatch) -> None:
    if batch not in request.execution_batches:
        raise generation_error("OUTPUT_CONTRACT_VIOLATION")


def _batch_for_candidate(request: GenerationJobRequest, candidate_index: int) -> ExecutionBatch:
    if candidate_index not in range(4):
        raise generation_error("OUTPUT_CONTRACT_VIOLATION")
    for batch in request.execution_batches:
        if candidate_index in batch.candidate_indices:
            return batch
    raise generation_error("OUTPUT_CONTRACT_VIOLATION")


def _canonical_relative_path(
    request: GenerationJobRequest,
    candidate_index: int,
    kind: ArtifactKind,
) -> str:
    batch = _batch_for_candidate(request, candidate_index)
    if kind == "raw":
        return f"raw/batch-{batch.batch_index}/candidate-{candidate_index}.png"
    names = {
        "final": "final.png",
        "nearest": "nearest.png",
        "tile": "tile.png",
        "report": "report.json",
    }
    return f"processed/candidate-{candidate_index}/{names[kind]}"


def _validate_output_payload(payload: bytes, *, canvas_size: int) -> None:
    try:
        with Image.open(BytesIO(payload)) as image:
            image.load()
            if image.format != "PNG":
                raise generation_error("OUTPUT_CONTRACT_VIOLATION")
            if image.mode != "RGB":
                raise generation_error("OUTPUT_CONTRACT_VIOLATION")
            if image.size != (canvas_size, canvas_size):
                raise generation_error("OUTPUT_CONTRACT_VIOLATION")
    except GenerationError:
        raise
    except (OSError, UnidentifiedImageError) as error:
        raise generation_error("OUTPUT_CONTRACT_VIOLATION", technical_details=str(error)) from error


def _validate_stored_png(
    path: Path,
    *,
    relative_path: str,
    kind: ArtifactKind,
    canvas_size: int,
    require_rgb: bool,
) -> StoredArtifact:
    try:
        data = _read_safe_bytes(path)
        with Image.open(BytesIO(data)) as image:
            image.load()
            if image.format != "PNG":
                raise generation_error("OUTPUT_CONTRACT_VIOLATION")
            if require_rgb and image.mode != "RGB":
                raise generation_error("OUTPUT_CONTRACT_VIOLATION")
            if image.size != (canvas_size, canvas_size):
                raise generation_error("OUTPUT_CONTRACT_VIOLATION")
            return StoredArtifact(
                kind=kind,
                relative_path=relative_path,
                sha256=hashlib.sha256(data).hexdigest(),
                byte_size=len(data),
                media_type="image/png",
                width=image.width,
                height=image.height,
            )
    except GenerationError:
        raise
    except (OSError, UnidentifiedImageError, ValidationError) as error:
        raise generation_error("OUTPUT_CONTRACT_VIOLATION", technical_details=str(error)) from error


def _validate_processed_directory(
    path: Path,
    report: ProcessingReport,
    resolution: int,
    candidate_index: int,
) -> None:
    expected = {"final.png", "nearest.png", "tile.png", "report.json"}
    try:
        entries = tuple(os.scandir(path))
    except OSError as error:
        raise generation_error("OUTPUT_CONTRACT_VIOLATION", technical_details=str(error)) from error
    actual = {entry.name for entry in entries}
    if (
        actual != expected
        or any(
            not entry.is_file(follow_symlinks=False)
            or is_reparse_point(Path(entry.path), os.lstat(entry.path))
            for entry in entries
        )
    ):
        raise generation_error("OUTPUT_CONTRACT_VIOLATION")
    final = _validate_stored_png(
        path / "final.png",
        relative_path=f"processed/candidate-{candidate_index}/final.png",
        kind="final",
        canvas_size=resolution,
        require_rgb=True,
    )
    nearest = _validate_image(path / "nearest.png", "nearest", candidate_index)
    tile = _validate_image(path / "tile.png", "tile", candidate_index)
    loaded_report = ProcessingReport.model_validate_json(
        _read_safe_bytes(path / "report.json")
    )
    if loaded_report != report:
        raise generation_error("OUTPUT_CONTRACT_VIOLATION")
    if loaded_report.output.sha256 != final.sha256:
        raise generation_error("OUTPUT_CONTRACT_VIOLATION")
    if loaded_report.previews.nearest_neighbor.sha256 != nearest.sha256:
        raise generation_error("OUTPUT_CONTRACT_VIOLATION")
    if loaded_report.previews.tile_3x3.sha256 != tile.sha256:
        raise generation_error("OUTPUT_CONTRACT_VIOLATION")


def _validate_image(path: Path, kind: ArtifactKind, candidate_index: int) -> StoredArtifact:
    data = _read_safe_bytes(path)
    with Image.open(BytesIO(data)) as image:
        image.load()
        if image.format != "PNG" or image.mode != "RGB":
            raise generation_error("OUTPUT_CONTRACT_VIOLATION")
        return StoredArtifact(
            kind=kind,
            relative_path=f"processed/candidate-{candidate_index}/{path.name}",
            sha256=hashlib.sha256(data).hexdigest(),
            byte_size=len(data),
            media_type="image/png",
            width=image.width,
            height=image.height,
        )


def _reopen_candidate_artifacts(
    final_root: Path,
    raw_artifact: StoredArtifact,
    candidate_index: int,
    resolution: int,
) -> CandidateArtifacts:
    report = ProcessingReport.model_validate_json(
        _read_safe_bytes(final_root / "report.json")
    )
    _verify_processed_hashes(final_root, final_root / "report.json", "report")
    final = _validate_stored_png(
        final_root / "final.png",
        relative_path=f"processed/candidate-{candidate_index}/final.png",
        kind="final",
        canvas_size=resolution,
        require_rgb=True,
    )
    nearest = _validate_image(final_root / "nearest.png", "nearest", candidate_index).model_copy(
        update={"relative_path": f"processed/candidate-{candidate_index}/nearest.png"}
    )
    tile = _validate_image(final_root / "tile.png", "tile", candidate_index).model_copy(
        update={"relative_path": f"processed/candidate-{candidate_index}/tile.png"}
    )
    report_bytes = _read_safe_bytes(final_root / "report.json")
    report_artifact = StoredArtifact(
        kind="report",
        relative_path=f"processed/candidate-{candidate_index}/report.json",
        sha256=hashlib.sha256(report_bytes).hexdigest(),
        byte_size=len(report_bytes),
        media_type="application/json",
        width=None,
        height=None,
    )
    if report.output.sha256 != final.sha256:
        raise generation_error("OUTPUT_CONTRACT_VIOLATION")
    return CandidateArtifacts(
        raw=raw_artifact,
        final=final,
        nearest=nearest,
        tile=tile,
        report=report_artifact,
    )


def _verify_processed_hashes(candidate_root: Path, report_path: Path, kind: ArtifactKind) -> None:
    report = ProcessingReport.model_validate_json(_read_safe_bytes(report_path))
    mappings = {
        "final": ("final.png", report.output.sha256),
        "nearest": ("nearest.png", report.previews.nearest_neighbor.sha256),
        "tile": ("tile.png", report.previews.tile_3x3.sha256),
    }
    for name, expected in mappings.values():
        if hashlib.sha256(_read_safe_bytes(candidate_root / name)).hexdigest() != expected:
            raise generation_error("OUTPUT_CONTRACT_VIOLATION")
    if kind == "report":
        return


@dataclass(frozen=True)
class _HeldDirectory:
    path: Path
    identity: FileIdentity


@dataclass(frozen=True)
class _PublicationGuard:
    directories: tuple[_HeldDirectory, ...]

    def with_directory(self, path: Path, identity: FileIdentity) -> "_PublicationGuard":
        return _PublicationGuard((*self.directories, _HeldDirectory(path, identity)))

    def require(self) -> None:
        for directory in self.directories:
            if not matches_directory_identity(directory.path, directory.identity):
                raise DirectoryGuardError("publication directory identity changed")

    def require_parent(self, path: Path) -> None:
        if not any(path.parent == directory.path for directory in self.directories):
            raise DirectoryGuardError("publication path has an unguarded parent")
        self.require()


@contextmanager
def _hold_publication_roots(
    job_root: Path,
    *artifact_roots: Path,
) -> Iterator[_PublicationGuard]:
    with ExitStack() as stack:
        paths = (job_root, *artifact_roots)
        if any(
            path != job_root and not path.is_relative_to(job_root)
            for path in artifact_roots
        ):
            raise DirectoryGuardError("publication root escapes job root")
        held = tuple(
            _HeldDirectory(path, stack.enter_context(hold_directory_identity(path)))
            for path in paths
        )
        guard = _PublicationGuard(held)
        guard.require()
        yield guard
        guard.require()


def _reset_temporary_directory(
    path: Path,
    guard: _PublicationGuard,
) -> FileIdentity:
    guard.require_parent(path)
    if os.path.lexists(path):
        _remove_owned_directory(path, guard)
    guard.require_parent(path)
    if os.path.lexists(path):
        raise DirectoryGuardError("temporary publication directory still exists")
    try:
        path.mkdir()
    except OSError as error:
        raise DirectoryGuardError("temporary publication directory cannot be created") from error
    temporary_identity: FileIdentity | None = None
    try:
        with hold_directory_identity(path) as created_identity:
            temporary_identity = created_identity
        guard.require_parent(path)
        identity = capture_directory_identity(path)
        if identity != temporary_identity:
            raise DirectoryGuardError("temporary publication directory identity changed")
        if not matches_directory_identity(path, temporary_identity):
            raise DirectoryGuardError("temporary publication directory identity changed")
    except (DirectoryGuardError, OSError) as error:
        if temporary_identity is None:
            raise
        try:
            _remove_owned_directory(
                path,
                guard,
                expected_identity=temporary_identity,
            )
        except (DirectoryGuardError, OSError) as cleanup_error:
            raise error from cleanup_error
        raise
    assert temporary_identity is not None
    return temporary_identity


def _cleanup_temporary_directory(
    path: Path,
    expected_identity: FileIdentity,
    guard: _PublicationGuard,
) -> None:
    try:
        _remove_owned_directory(path, guard, expected_identity=expected_identity)
    except (DirectoryGuardError, OSError):
        # Never remove a path whose parent or identity no longer matches the
        # directory held for this publication. A stale safe temp can be
        # diagnosed and reclaimed by the next guarded attempt.
        return


def _remove_owned_directory(
    path: Path,
    guard: _PublicationGuard,
    *,
    expected_identity: FileIdentity | None = None,
) -> None:
    guard.require_parent(path)
    if not os.path.lexists(path):
        return
    try:
        status = os.lstat(path)
    except OSError as error:
        raise DirectoryGuardError("publication directory cannot be inspected") from error
    if (
        not stat.S_ISDIR(status.st_mode)
        or is_reparse_point(path, status)
        or (
            expected_identity is not None
            and not matches_directory_identity(path, expected_identity)
        )
        or _tree_contains_reparse_point(path)
    ):
        raise DirectoryGuardError("publication directory is unsafe to remove")
    guard.require_parent(path)
    shutil.rmtree(path)
    guard.require_parent(path)


def _publish_directory(
    temporary_root: Path,
    final_root: Path,
    temporary_identity: FileIdentity,
    guard: _PublicationGuard,
) -> FileIdentity:
    guard.require_parent(temporary_root)
    guard.require_parent(final_root)
    if temporary_root.parent != final_root.parent:
        raise DirectoryGuardError("publication directories have different parents")
    if os.path.lexists(final_root):
        raise generation_error("OUTPUT_CONTRACT_VIOLATION")
    try:
        status = os.lstat(temporary_root)
    except OSError as error:
        raise DirectoryGuardError("temporary publication directory disappeared") from error
    if (
        not stat.S_ISDIR(status.st_mode)
        or is_reparse_point(temporary_root, status)
        or not matches_directory_identity(temporary_root, temporary_identity)
        or _tree_contains_reparse_point(temporary_root)
    ):
        raise DirectoryGuardError("temporary publication directory changed")
    guard.require_parent(temporary_root)
    try:
        os.replace(temporary_root, final_root)
    except OSError as error:
        try:
            _remove_owned_directory(
                final_root,
                guard,
                expected_identity=temporary_identity,
            )
        except (DirectoryGuardError, OSError) as cleanup_error:
            raise _storage_error() from cleanup_error
        raise _storage_error() from error
    try:
        _verify_published_directory(final_root, temporary_identity, guard)
    except BaseException:
        try:
            _remove_owned_directory(
                final_root,
                guard,
                expected_identity=temporary_identity,
            )
        except (DirectoryGuardError, OSError) as cleanup_error:
            raise _storage_error() from cleanup_error
        raise
    return temporary_identity


def _verify_published_directory(
    final_root: Path,
    expected_identity: FileIdentity,
    guard: _PublicationGuard,
) -> None:
    guard.require_parent(final_root)
    try:
        final_status = os.lstat(final_root)
    except OSError as error:
        raise DirectoryGuardError("published directory cannot be reopened") from error
    if (
        not stat.S_ISDIR(final_status.st_mode)
        or is_reparse_point(final_root, final_status)
        or not matches_directory_identity(final_root, expected_identity)
    ):
        raise DirectoryGuardError("published directory identity changed")


def _write_atomic_bytes(
    destination: Path,
    payload: bytes,
    validator,
    guard: _PublicationGuard,
) -> None:
    guard.require_parent(destination)
    atomic_replace_bytes(destination, payload, validator)
    guard.require_parent(destination)


def _replace_owned_file(
    source: Path,
    destination: Path,
    guard: _PublicationGuard,
) -> None:
    if source.parent != destination.parent:
        raise DirectoryGuardError("renamed files must share a guarded parent")
    guard.require_parent(source)
    if os.path.lexists(destination):
        raise DirectoryGuardError("destination file already exists")
    try:
        status = os.lstat(source)
    except OSError as error:
        raise DirectoryGuardError("source file cannot be inspected") from error
    if not stat.S_ISREG(status.st_mode) or is_reparse_point(source, status):
        raise DirectoryGuardError("source file is unsafe")
    expected_identity = _file_identity(status)
    try:
        os.replace(source, destination)
    except OSError as error:
        raise _storage_error() from error
    guard.require_parent(destination)
    try:
        final_status = os.lstat(destination)
    except OSError as error:
        raise DirectoryGuardError("renamed file cannot be reopened") from error
    if (
        not stat.S_ISREG(final_status.st_mode)
        or is_reparse_point(destination, final_status)
        or _file_identity(final_status) != expected_identity
    ):
        raise DirectoryGuardError("renamed file identity changed")


def _remove_file_if_present(path: Path, guard: _PublicationGuard) -> None:
    guard.require_parent(path)
    if not os.path.lexists(path):
        return
    try:
        status = os.lstat(path)
    except OSError as error:
        raise DirectoryGuardError("temporary file cannot be inspected") from error
    if not stat.S_ISREG(status.st_mode) or is_reparse_point(path, status):
        raise DirectoryGuardError("temporary file is unsafe to remove")
    try:
        os.unlink(path)
    except OSError as error:
        raise _storage_error() from error
    guard.require_parent(path)
    if os.path.lexists(path):
        raise DirectoryGuardError("temporary file changed during removal")


def _read_safe_bytes(path: Path) -> bytes:
    status = os.lstat(path)
    if not stat.S_ISREG(status.st_mode) or is_reparse_point(path, status):
        raise OSError("artifact is not a safe regular file")
    expected_identity = _file_identity(status)
    payload = path.read_bytes()
    final_status = os.lstat(path)
    if (
        not stat.S_ISREG(final_status.st_mode)
        or is_reparse_point(path, final_status)
        or _file_identity(final_status) != expected_identity
    ):
        raise OSError("artifact changed while reading")
    return payload


def _validate_raw_directory(
    path: Path,
    batch: ExecutionBatch,
    *,
    canvas_size: int,
) -> tuple[StoredArtifact, ...]:
    expected_names = {
        f"candidate-{candidate_index}.png" for candidate_index in batch.candidate_indices
    }
    try:
        entries = tuple(os.scandir(path))
    except OSError as error:
        raise generation_error("OUTPUT_CONTRACT_VIOLATION", technical_details=str(error)) from error
    actual_names = {entry.name for entry in entries}
    if (
        actual_names != expected_names
        or any(
            not entry.is_file(follow_symlinks=False)
            or is_reparse_point(Path(entry.path), os.lstat(entry.path))
            for entry in entries
        )
    ):
        raise generation_error("OUTPUT_CONTRACT_VIOLATION")
    return tuple(
        _validate_stored_png(
            path / f"candidate-{candidate_index}.png",
            relative_path=(
                f"raw/batch-{batch.batch_index}/candidate-{candidate_index}.png"
            ),
            kind="raw",
            canvas_size=canvas_size,
            require_rgb=True,
        )
        for candidate_index in batch.candidate_indices
    )


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


def _file_identity(status: os.stat_result) -> FileIdentity:
    return FileIdentity(device=status.st_dev, file_id=status.st_ino)


def _storage_error() -> GenerationError:
    return GenerationError(
        "JOB_STORAGE_UNAVAILABLE",
        "无法安全保存候选产物",
        recommended_actions=("稍后重试；如果持续失败，请检查项目目录权限",),
    )
