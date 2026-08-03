from __future__ import annotations

import hashlib
import json
import os
import shutil
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
    StoredArtifact,
)
from aimctexturegen.jobs.store import JobError, JobStore, LoadedJob
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
        _reset_temporary_directory(temporary_root)
        if final_root.exists():
            raise generation_error("OUTPUT_CONTRACT_VIOLATION")
        artifacts: list[StoredArtifact] = []
        try:
            temporary_root.mkdir()
            for position, payload in enumerate(payloads):
                candidate_index = batch.candidate_indices[position]
                name = f"candidate-{candidate_index}.png"
                _validate_output_payload(payload, canvas_size=canvas_size)
                target = temporary_root / name
                target.write_bytes(payload)
                artifacts.append(
                    _validate_stored_png(
                        target,
                        relative_path=f"raw/batch-{batch.batch_index}/{name}",
                        kind="raw",
                        canvas_size=canvas_size,
                        require_rgb=True,
                    )
                )
            try:
                temporary_root.replace(final_root)
            except OSError as error:
                raise _storage_error() from error
            reopened = tuple(
                _validate_stored_png(
                    final_root / f"candidate-{candidate_index}.png",
                    relative_path=f"raw/batch-{batch.batch_index}/candidate-{candidate_index}.png",
                    kind="raw",
                    canvas_size=canvas_size,
                    require_rgb=True,
                )
                for candidate_index in batch.candidate_indices
            )
            return reopened
        except GenerationError:
            raise
        except (OSError, ValidationError) as error:
            raise _storage_error() from error
        finally:
            _remove_tree_if_present(temporary_root)

    def process_and_publish(
        self,
        loaded: LoadedJob,
        *,
        candidate_index: int,
        resolution: int,
    ) -> CandidateArtifacts:
        request = _generation_request(loaded)
        batch = _batch_for_candidate(request, candidate_index)
        raw_artifact = _validate_stored_png(
            loaded.root / f"raw/batch-{batch.batch_index}/candidate-{candidate_index}.png",
            relative_path=f"raw/batch-{batch.batch_index}/candidate-{candidate_index}.png",
            kind="raw",
            canvas_size=1024,
            require_rgb=True,
        )
        processed_root = loaded.root / "processed"
        temporary_root = processed_root / f".candidate-{candidate_index}.tmp"
        final_root = processed_root / f"candidate-{candidate_index}"
        _reset_temporary_directory(temporary_root)
        try:
            report = process_candidate(
                loaded.root / raw_artifact.relative_path,
                temporary_root,
                stem="final",
                resolution=resolution,
            )
            (temporary_root / "final-nn.png").replace(temporary_root / "nearest.png")
            (temporary_root / "final-tile.png").replace(temporary_root / "tile.png")
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
            atomic_replace_bytes(
                temporary_root / "report.json",
                dump_report_json(updated_report),
                lambda payload: ProcessingReport.model_validate_json(payload),
            )
            _remove_file_if_present(temporary_root / "final-report.json")
            if final_root.exists():
                raise generation_error("OUTPUT_CONTRACT_VIOLATION")
            _validate_processed_directory(temporary_root, updated_report, resolution)
            try:
                temporary_root.replace(final_root)
            except OSError as error:
                raise _storage_error() from error
            return _reopen_candidate_artifacts(final_root, raw_artifact, candidate_index, resolution)
        except GenerationError:
            raise
        except Exception as error:
            raise GenerationError(
                "POSTPROCESSING_FAILED",
                "候选后处理失败",
                recommended_actions=("重新运行该候选的后处理",),
                technical_details=str(error),
            ) from error
        finally:
            _remove_tree_if_present(temporary_root)

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
            try:
                path = self._store.resolve_job_file(
                    project_id,
                    request.parent_job_id,
                    inherited_from.relative_path,
                )
            except JobError as error:
                raise generation_error("OUTPUT_CONTRACT_VIOLATION", technical_details=error.code) from error
            if hashlib.sha256(path.read_bytes()).hexdigest() != inherited_from.sha256:
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
        data = path.read_bytes()
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
) -> None:
    expected = {"final.png", "nearest.png", "tile.png", "report.json"}
    actual = {child.name for child in path.iterdir() if child.is_file()}
    if actual != expected:
        raise generation_error("OUTPUT_CONTRACT_VIOLATION")
    final = _validate_stored_png(
        path / "final.png",
        relative_path="processed/candidate-0/final.png",
        kind="final",
        canvas_size=resolution,
        require_rgb=True,
    )
    nearest = _validate_image(path / "nearest.png", "nearest")
    tile = _validate_image(path / "tile.png", "tile")
    loaded_report = ProcessingReport.model_validate_json((path / "report.json").read_bytes())
    if loaded_report != report:
        raise generation_error("OUTPUT_CONTRACT_VIOLATION")
    if loaded_report.output.sha256 != final.sha256:
        raise generation_error("OUTPUT_CONTRACT_VIOLATION")
    if loaded_report.previews.nearest_neighbor.sha256 != nearest.sha256:
        raise generation_error("OUTPUT_CONTRACT_VIOLATION")
    if loaded_report.previews.tile_3x3.sha256 != tile.sha256:
        raise generation_error("OUTPUT_CONTRACT_VIOLATION")


def _validate_image(path: Path, kind: ArtifactKind) -> StoredArtifact:
    data = path.read_bytes()
    with Image.open(BytesIO(data)) as image:
        image.load()
        if image.format != "PNG" or image.mode != "RGB":
            raise generation_error("OUTPUT_CONTRACT_VIOLATION")
        return StoredArtifact(
            kind=kind,
            relative_path=f"processed/candidate-0/{path.name}",
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
    report = ProcessingReport.model_validate_json((final_root / "report.json").read_bytes())
    _verify_processed_hashes(final_root, final_root / "report.json", "report")
    final = _validate_stored_png(
        final_root / "final.png",
        relative_path=f"processed/candidate-{candidate_index}/final.png",
        kind="final",
        canvas_size=resolution,
        require_rgb=True,
    )
    nearest = _validate_image(final_root / "nearest.png", "nearest").model_copy(
        update={"relative_path": f"processed/candidate-{candidate_index}/nearest.png"}
    )
    tile = _validate_image(final_root / "tile.png", "tile").model_copy(
        update={"relative_path": f"processed/candidate-{candidate_index}/tile.png"}
    )
    report_artifact = StoredArtifact(
        kind="report",
        relative_path=f"processed/candidate-{candidate_index}/report.json",
        sha256=hashlib.sha256((final_root / "report.json").read_bytes()).hexdigest(),
        byte_size=(final_root / "report.json").stat().st_size,
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
    report = ProcessingReport.model_validate_json(report_path.read_bytes())
    mappings = {
        "final": ("final.png", report.output.sha256),
        "nearest": ("nearest.png", report.previews.nearest_neighbor.sha256),
        "tile": ("tile.png", report.previews.tile_3x3.sha256),
    }
    for name, expected in mappings.values():
        if hashlib.sha256((candidate_root / name).read_bytes()).hexdigest() != expected:
            raise generation_error("OUTPUT_CONTRACT_VIOLATION")
    if kind == "report":
        return


def _reset_temporary_directory(path: Path) -> None:
    _remove_tree_if_present(path)
    path.parent.mkdir(parents=True, exist_ok=True)


def _remove_tree_if_present(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _remove_file_if_present(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _storage_error() -> GenerationError:
    return GenerationError(
        "JOB_STORAGE_UNAVAILABLE",
        "无法安全保存候选产物",
        recommended_actions=("稍后重试；如果持续失败，请检查项目目录权限",),
    )
