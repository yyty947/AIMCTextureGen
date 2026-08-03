from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aimctexturegen.catalog.models import CatalogEntry, CatalogProfile
from aimctexturegen.catalog.registry import CatalogRegistry, UnsupportedPackFormat
from aimctexturegen.comfy.client import ComfyClient
from aimctexturegen.comfy.errors import ComfyCanceledError, ManifestNotFoundError
from aimctexturegen.comfy.manifests import (
    ModelProfileManifestV2,
    WorkflowVariantRecord,
    manifest_sha256,
)
from aimctexturegen.comfy.registry import ManifestRegistry
from aimctexturegen.generation.artifacts import CandidateArtifactStore
from aimctexturegen.jobs.generation_state import (
    complete_candidate,
    complete_generation,
    confirm_canceled,
    fail_generation,
    mark_batch_raw_ready,
    record_progress,
    request_cancel,
    start_batch,
    start_generation,
)
from aimctexturegen.jobs.models import MAX_SAFE_SEED
from aimctexturegen.jobs.models_v3 import (
    CompiledPrompt as StoredCompiledPrompt,
    ExecutionBatch,
    FrozenReferences,
    GenerationAdvanced,
    GenerationBatchRecord,
    GenerationCandidateRecord,
    GenerationJobRequest,
    GenerationJobState,
    GenerationModelBinding,
    GenerationTarget,
    StoredArtifact,
)
from aimctexturegen.jobs.store import JobInputFile, JobInputSnapshot, JobStore, LoadedJob
from aimctexturegen.model_profiles.sdxl_v2 import SDXLV2Binding
from aimctexturegen.model_profiles.workflows import GenericWorkflowInputs
from aimctexturegen.packs.coverage import CoverageValidationError, classify_coverage
from aimctexturegen.processing.errors import ProcessingError
from aimctexturegen.projects.repository import ProjectRepository
from aimctexturegen.references.models import ReferenceSelections
from aimctexturegen.references.service import ReferenceService, ReferenceServiceError

from .errors import (
    GenerationError,
    generation_error,
    generation_failure_from_error,
)
from .prompts import compile_block_prompt


PROFILE_KEY = ("sdxl-mapchip-ipadapter", "2")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CreateGenerationCommand(_StrictModel):
    target_semantic_id: str = Field(min_length=1)
    user_description: str = Field(max_length=4000)
    user_negative_prompt: str = Field(default="", max_length=4000)
    resolution: Literal[16, 32, 64]
    parallelism: Literal[1, 2, 4]
    references: ReferenceSelections
    denoise: float | None = Field(default=None)
    style_weight: float | None = Field(default=None)


@dataclass(frozen=True)
class ExecutionContext:
    client: ComfyClient | Any
    cancel_requested: Callable[[], bool]
    shutdown_requested: Callable[[], bool] = lambda: False
    prompt_registered: Callable[[str], None] | None = None
    state_committed: Callable[[LoadedJob], None] | None = None
    progress_interval_seconds: float = 0.25
    completion_timeout_seconds: float = 60.0


class _LoadedExecutionError(Exception):
    def __init__(self, loaded: LoadedJob, cause: Exception) -> None:
        super().__init__(str(cause))
        self.loaded = loaded
        self.cause = cause


class _GenerationShutdown(Exception):
    """Stop the worker without translating application shutdown into cancel."""


class GenerationService:
    def __init__(
        self,
        *,
        repository: ProjectRepository,
        catalogs: CatalogRegistry,
        references: ReferenceService,
        store: JobStore,
        manifests: ManifestRegistry,
        artifacts: CandidateArtifactStore | None = None,
        seed_source: Callable[[], int] | None = None,
        job_id_source: Callable[[], UUID] = uuid4,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._repository = repository
        self._catalogs = catalogs
        self._references = references
        self._store = store
        self._manifests = manifests
        self._artifacts = artifacts or CandidateArtifactStore(store)
        self._seed_source = (
            (lambda: secrets.randbelow(MAX_SAFE_SEED + 1))
            if seed_source is None
            else seed_source
        )
        self._job_id_source = job_id_source
        self._clock = (
            (lambda: datetime.now(timezone.utc)) if clock is None else clock
        )
        self._monotonic = monotonic

    def create_job(self, project_id: UUID, command: CreateGenerationCommand) -> LoadedJob:
        if not isinstance(project_id, UUID):
            raise TypeError("project_id must be a UUID")
        if not isinstance(command, CreateGenerationCommand):
            raise TypeError("command must be a CreateGenerationCommand")
        advanced = _validated_advanced(command)

        with self._repository.open(project_id) as opened:
            profile = _catalog_profile(self._catalogs, opened.manifest.java_pack_format)
            if profile.catalog_id != opened.manifest.catalog_id:
                raise generation_error("JOB_TARGET_NOT_FOUND")
            target = _resolve_target(profile, command.target_semantic_id)
            if target is None:
                raise generation_error("JOB_TARGET_NOT_FOUND")
            if not target.mvp_eligible:
                raise generation_error("JOB_TARGET_NOT_ELIGIBLE")
            coverage = _classify_missing(opened.pack_root, profile, target.semantic_id)
            if coverage != "missing":
                raise generation_error("JOB_TARGET_NOT_MISSING")

            binding = build_generation_profile_binding(
                self._manifests,
                profile_id=PROFILE_KEY[0],
                profile_version=PROFILE_KEY[1],
                style_reference_count=len(command.references.style),
                structure_reference_present=command.references.structure is not None,
            )
            try:
                frozen_snapshot = self._references.freeze(project_id, command.references)
            except ReferenceServiceError as error:
                raise generation_error(
                    "REFERENCE_INVALID",
                    technical_details=f"{error.code}: {error.user_message}",
                ) from error

            try:
                compiled = compile_block_prompt(
                    resolution=command.resolution,
                    display_name=target.display_name,
                    prompt_terms=target.prompt_terms,
                    user_description=command.user_description,
                    user_negative_prompt=command.user_negative_prompt,
                )
                stored_prompt = StoredCompiledPrompt(
                    prompt_version=compiled.prompt_version,
                    positive_prompt=compiled.compiled_positive,
                    negative_prompt=compiled.compiled_negative,
                    user_prompt=compiled.user_prompt,
                )
                target_record = GenerationTarget(
                    catalog_id=profile.catalog_id,
                    target_semantic_id=target.semantic_id,
                    target_display_name=target.display_name,
                    target_relative_path=target.relative_path,
                )
            except (TypeError, ValueError, ValidationError) as error:
                raise generation_error("INVALID_GENERATION_COMMAND") from error
            frozen_references = _freeze_references(frozen_snapshot)
            batches = build_execution_batches(
                parallelism=command.parallelism,
                seed_source=self._seed_source,
            )
            request = GenerationJobRequest(
                schema_version=3,
                job_id=self._job_id_source(),
                project_id=opened.manifest.project_id,
                parent_job_id=None,
                target=target_record,
                prompt=stored_prompt,
                resolution=command.resolution,
                parallelism=command.parallelism,
                execution_batches=batches,
                references=frozen_references,
                advanced=advanced,
                model_profile=binding,
                created_at=self._clock(),
            )
        return self._store.create_generation(request, frozen_snapshot)

    def run_job(
        self,
        project_id: UUID,
        job_id: UUID,
        context: ExecutionContext,
    ) -> LoadedJob:
        loaded = self._store.load(project_id, job_id)
        _require_generation_request(loaded)
        _require_generation_state(loaded)
        try:
            if context.shutdown_requested():
                return loaded
            if loaded.state.status == "queued":
                loaded = self._commit_state(
                    loaded,
                    start_generation(_require_generation_state(loaded), now=self._clock()),
                    context=context,
                )
            loaded = self._cancel_if_requested(loaded, context)
            for batch in loaded.request.execution_batches:
                loaded = self._continue_batch(loaded, batch, context)
                if loaded.state.status in {"failed", "canceled"}:
                    return loaded
            if any(
                candidate.status not in {"completed", "inherited"}
                for candidate in _require_generation_state(loaded).candidates
            ):
                raise generation_error("COMFY_EXECUTION_FAILED")
            if context.shutdown_requested():
                raise _GenerationShutdown()
            loaded = self._commit_state(
                loaded,
                complete_generation(_require_generation_state(loaded), now=self._clock()),
                context=context,
            )
            return loaded
        except _GenerationShutdown:
            return self._store.load(project_id, job_id)
        except _LoadedExecutionError as error:
            loaded = error.loaded
            if isinstance(error.cause, ComfyCanceledError):
                if context.shutdown_requested():
                    return self._store.load(project_id, job_id)
                return self._confirm_comfy_cancellation(loaded, context)
            mapped = generation_failure_from_error(
                error.cause,
                stage=_require_generation_state(loaded).status,
                occurred_at=self._clock(),
            )
            failed = fail_generation(
                _require_generation_state(loaded),
                mapped,
                now=self._clock(),
            )
            return self._commit_state(loaded, failed, context=context)
        except Exception as error:
            mapped = generation_failure_from_error(
                error,
                stage=_require_generation_state(loaded).status,
                occurred_at=self._clock(),
            )
            failed = fail_generation(
                _require_generation_state(loaded),
                mapped,
                now=self._clock(),
            )
            return self._commit_state(loaded, failed, context=context)

    def retry_job(self, project_id: UUID, parent_job_id: UUID) -> LoadedJob:
        parent = self._store.load(project_id, parent_job_id)
        request = _require_generation_request(parent)
        state = _require_generation_state(parent)
        if state.status not in {"failed", "canceled"}:
            raise generation_error("COMFY_EXECUTION_FAILED")

        child_request = GenerationJobRequest.model_validate(
            {
                **request.model_dump(),
                "job_id": self._job_id_source(),
                "parent_job_id": request.job_id,
                "created_at": self._clock(),
            }
        )
        child = self._store.create_generation(
            child_request,
            self._load_input_snapshot(parent),
        )
        child_state = _require_generation_state(child)
        candidates = list(child_state.candidates)
        batches = list(child_state.batches)

        for batch in request.execution_batches:
            if not self._batch_raw_is_complete(parent, batch):
                continue
            raw_payloads = tuple(
                self._read_candidate_artifact_bytes(parent, index, "raw")
                for index in batch.candidate_indices
            )
            raw_artifacts = self._artifacts.publish_raw_batch(
                child,
                batch,
                raw_payloads,
                canvas_size=1024,
            )
            all_inherited = True
            for position, candidate_index in enumerate(batch.candidate_indices):
                if self._candidate_is_complete(parent, candidate_index):
                    parent_candidate = state.candidates[candidate_index]
                    candidates[candidate_index] = GenerationCandidateRecord.model_validate(
                        {
                            **candidates[candidate_index].model_dump(),
                            "status": "inherited",
                            "artifacts": parent_candidate.artifacts.model_dump(),
                            "lineage": {
                                "parent_job_id": request.job_id,
                                "parent_candidate_index": candidate_index,
                            },
                            "started_at": self._clock(),
                            "finished_at": self._clock(),
                        }
                    )
                    continue
                all_inherited = False
                candidates[candidate_index] = GenerationCandidateRecord.model_validate(
                    {
                        **candidates[candidate_index].model_dump(),
                        "status": "raw_ready",
                        "artifacts": {"raw": raw_artifacts[position].model_dump()},
                        "started_at": self._clock(),
                    }
                )
            batches[batch.batch_index] = GenerationBatchRecord.model_validate(
                {
                    **batches[batch.batch_index].model_dump(),
                    "status": "completed" if all_inherited else "raw_ready",
                    "raw_artifacts": tuple(
                        artifact.model_dump() for artifact in raw_artifacts
                    ),
                    "started_at": self._clock(),
                    "finished_at": self._clock(),
                }
            )

        replacement = GenerationJobState.model_validate(
            {
                **child_state.model_dump(),
                "revision": child_state.revision + 1,
                "batches": tuple(batch.model_dump() for batch in batches),
                "candidates": tuple(
                    candidate.model_dump() for candidate in candidates
                ),
                "updated_at": self._clock(),
            }
        )
        return self._store.replace_state(
            project_id,
            child_request.job_id,
            replacement,
            expected_revision=child_state.revision,
        )

    def _continue_batch(
        self,
        loaded: LoadedJob,
        batch: ExecutionBatch,
        context: ExecutionContext,
    ) -> LoadedJob:
        current = loaded
        try:
            current = self._cancel_if_requested(current, context)
            state = _require_generation_state(current)
            batch_state = state.batches[batch.batch_index]
            if all(
                state.candidates[index].status in {"completed", "inherited"}
                for index in batch.candidate_indices
            ):
                return current
            if batch_state.status == "raw_ready":
                return self._process_raw_ready_candidates(current, batch, context)

            current = self._commit_state(
                current,
                start_batch(state, batch.batch_index, now=self._clock()),
                context=context,
            )
            uploaded = self._upload_frozen_references(current, context.client)
            workflow = self._compile_batch_workflow(current.request, batch, uploaded)
            prompt_id = context.client.submit_prompt(workflow)
            if context.prompt_registered is not None:
                context.prompt_registered(prompt_id)
            current = self._commit_state(
                current,
                _state_with_prompt_id(
                    _require_generation_state(current),
                    batch.batch_index,
                    prompt_id,
                ),
                context=context,
            )
            progress = _ProgressRecorder(
                interval_seconds=context.progress_interval_seconds,
                monotonic=self._monotonic,
                commit=lambda value, maximum: self._commit_progress(
                    project_id=current.request.project_id,
                    job_id=current.request.job_id,
                    batch_index=batch.batch_index,
                    value=value,
                    maximum=maximum,
                    context=context,
                ),
            )
            history = context.client.wait_completion(
                prompt_id,
                timeout=context.completion_timeout_seconds,
                progress=progress.record,
                cancel_requested=lambda: (
                    context.cancel_requested() or context.shutdown_requested()
                ),
            )
            progress.flush()
            declared = context.client.declared_output_images(
                history,
                output_node_id=current.request.model_profile.output_node_id,
            )
            raw_payloads = tuple(
                context.client.get_output_image(image)
                for image in declared
            )
            raw_artifacts = self._artifacts.publish_raw_batch(
                current,
                batch,
                raw_payloads,
                canvas_size=1024,
            )
            current = self._commit_state(
                current,
                mark_batch_raw_ready(
                    _require_generation_state(current),
                    batch.batch_index,
                    raw_artifacts,
                    now=self._clock(),
                ),
                context=context,
            )
            current = self._cancel_if_requested(current, context)
            return self._process_raw_ready_candidates(current, batch, context)
        except _LoadedExecutionError:
            raise
        except _GenerationShutdown:
            raise
        except Exception as error:
            raise _LoadedExecutionError(current, error) from error

    def _process_raw_ready_candidates(
        self,
        loaded: LoadedJob,
        batch: ExecutionBatch,
        context: ExecutionContext,
    ) -> LoadedJob:
        current = loaded
        try:
            for candidate_index in batch.candidate_indices:
                current = self._cancel_if_requested(current, context)
                candidate = _require_generation_state(current).candidates[candidate_index]
                if candidate.status in {"completed", "inherited"}:
                    continue
                artifacts = self._artifacts.process_and_publish(
                    current,
                    candidate_index=candidate_index,
                    resolution=current.request.resolution,
                )
                current = self._commit_state(
                    current,
                    complete_candidate(
                        _require_generation_state(current),
                        candidate_index,
                        artifacts,
                        now=self._clock(),
                    ),
                    context=context,
                )
            return current
        except _LoadedExecutionError:
            raise
        except _GenerationShutdown:
            raise
        except Exception as error:
            raise _LoadedExecutionError(current, error) from error

    def _commit_progress(
        self,
        *,
        project_id: UUID,
        job_id: UUID,
        batch_index: int,
        value: int,
        maximum: int,
        context: ExecutionContext,
    ) -> None:
        current = self._store.load(project_id, job_id)
        if current.state.status not in {"generating", "postprocessing"}:
            return
        replacement = record_progress(
            _require_generation_state(current),
            batch_index,
            value,
            maximum,
            now=self._clock(),
        )
        self._commit_state(current, replacement, context=context)

    def _upload_frozen_references(
        self,
        loaded: LoadedJob,
        client: Any,
    ) -> dict[str, str]:
        uploaded: dict[str, str] = {}
        for artifact in loaded.request.references.style:
            reference_id = Path(artifact.relative_path).stem.replace(".", "-")
            name = f"{loaded.request.job_id}-style-{reference_id}.png"
            payload = (loaded.root / artifact.relative_path).read_bytes()
            response = client.upload_image(payload, name)
            uploaded[artifact.relative_path] = str(response.get("name", name))
        for artifact in loaded.request.references.structure:
            name = f"{loaded.request.job_id}-structure-00.png"
            payload = (loaded.root / artifact.relative_path).read_bytes()
            response = client.upload_image(payload, name)
            uploaded[artifact.relative_path] = str(response.get("name", name))
        return uploaded

    def _compile_batch_workflow(
        self,
        request: GenerationJobRequest,
        batch: ExecutionBatch,
        uploaded: dict[str, str],
    ) -> dict:
        profile = self._manifests.profile(
            request.model_profile.profile_id,
            request.model_profile.profile_version,
        )
        if not isinstance(profile, ModelProfileManifestV2):
            raise generation_error("PROFILE_NOT_READY")
        workflow = _workflow_for_variant(
            profile.workflows,
            request.model_profile.workflow_variant,
        )
        if workflow is None or workflow.sha256 != request.model_profile.workflow_sha256:
            raise generation_error("PROFILE_NOT_READY")
        workflow_path = (
            Path(getattr(self._manifests, "_root"))
            / "workflows"
            / workflow.relative_path
        )
        try:
            actual_workflow_sha256 = hashlib.sha256(
                workflow_path.read_bytes()
            ).hexdigest()
        except OSError as error:
            raise generation_error(
                "PROFILE_NOT_READY",
                technical_details=str(error),
            ) from error
        if actual_workflow_sha256 != request.model_profile.workflow_sha256:
            raise generation_error("PROFILE_NOT_READY")
        binding = SDXLV2Binding(
            variant=request.model_profile.workflow_variant,
            workflow_path=workflow_path,
        )
        return binding.compile(
            GenericWorkflowInputs(
                prompt=request.prompt.positive_prompt,
                negative_prompt=request.prompt.negative_prompt,
                seed=batch.seed,
                inference_canvas=request.resolution,
                batch_size=len(batch.candidate_indices),
                style_reference_names=tuple(
                    uploaded[artifact.relative_path]
                    for artifact in request.references.style
                ),
                structure_reference_name=(
                    uploaded[request.references.structure[0].relative_path]
                    if request.references.structure
                    else None
                ),
                advanced={
                    key: value
                    for key, value in {
                        "denoise": request.advanced.denoise_strength,
                        "style_weight": request.advanced.style_strength,
                    }.items()
                    if value is not None
                },
            )
        )

    def _commit_state(
        self,
        loaded: LoadedJob,
        replacement: GenerationJobState,
        *,
        context: ExecutionContext,
    ) -> LoadedJob:
        committed = self._store.replace_state(
            loaded.request.project_id,
            loaded.request.job_id,
            replacement,
            expected_revision=loaded.state.revision,
        )
        if context.state_committed is not None:
            context.state_committed(committed)
        return committed

    def _cancel_if_requested(
        self,
        loaded: LoadedJob,
        context: ExecutionContext,
    ) -> LoadedJob:
        if context.shutdown_requested():
            raise _GenerationShutdown()
        if not context.cancel_requested():
            return loaded
        state = _require_generation_state(loaded)
        if state.cancel_requested_at is None:
            loaded = self._commit_state(
                loaded,
                request_cancel(state, now=self._clock()),
                context=context,
            )
            state = _require_generation_state(loaded)
        if state.status != "canceled":
            loaded = self._commit_state(
                loaded,
                confirm_canceled(state, now=self._clock()),
                context=context,
            )
        return loaded

    def _confirm_comfy_cancellation(
        self,
        loaded: LoadedJob,
        context: ExecutionContext,
    ) -> LoadedJob:
        current = loaded
        state = _require_generation_state(current)
        if state.status in {"canceled", "completed", "failed"}:
            return current
        if state.cancel_requested_at is None:
            current = self._commit_state(
                current,
                request_cancel(state, now=self._clock()),
                context=context,
            )
            state = _require_generation_state(current)
        if state.status != "canceled":
            current = self._commit_state(
                current,
                confirm_canceled(state, now=self._clock()),
                context=context,
            )
        return current

    def _batch_raw_is_complete(
        self,
        loaded: LoadedJob,
        batch: ExecutionBatch,
    ) -> bool:
        batch_state = _require_generation_state(loaded).batches[batch.batch_index]
        if len(batch_state.raw_artifacts) != len(batch.candidate_indices):
            return False
        try:
            for candidate_index in batch.candidate_indices:
                self._read_candidate_artifact_bytes(loaded, candidate_index, "raw")
        except GenerationError:
            return False
        return True

    def _candidate_is_complete(
        self,
        loaded: LoadedJob,
        candidate_index: int,
    ) -> bool:
        candidate = _require_generation_state(loaded).candidates[candidate_index]
        if candidate.status not in {"completed", "inherited"}:
            return False
        try:
            for kind in ("raw", "final", "nearest", "tile", "report"):
                self._read_candidate_artifact_bytes(loaded, candidate_index, kind)
        except GenerationError:
            return False
        return True

    def _read_candidate_artifact_bytes(
        self,
        loaded: LoadedJob,
        candidate_index: int,
        kind: str,
    ) -> bytes:
        candidate = _require_generation_state(loaded).candidates[candidate_index]
        inherited = getattr(candidate.artifacts, kind) if candidate.status == "inherited" else None
        path = self._artifacts.resolve(
            loaded.request.project_id,
            loaded.request.job_id,
            candidate_index,
            kind,  # type: ignore[arg-type]
            inherited_from=inherited,
        )
        return path.read_bytes()

    def _load_input_snapshot(self, loaded: LoadedJob) -> JobInputSnapshot:
        request = _require_generation_request(loaded)
        return JobInputSnapshot(
            references_json=(loaded.root / "inputs" / "references.json").read_bytes(),
            files=tuple(
                JobInputFile(
                    relative_path=artifact.relative_path.removeprefix("inputs/"),
                    payload=(loaded.root / artifact.relative_path).read_bytes(),
                    sha256=artifact.sha256,
                )
                for artifact in (*request.references.style, *request.references.structure)
            ),
        )


class _ProgressRecorder:
    def __init__(
        self,
        *,
        interval_seconds: float,
        monotonic: Callable[[], float],
        commit: Callable[[int, int], None],
    ) -> None:
        self._interval_seconds = max(0.0, interval_seconds)
        self._monotonic = monotonic
        self._commit = commit
        self._last_commit: float | None = None
        self._pending: tuple[int, int] | None = None

    def record(self, value: int, maximum: int) -> None:
        self._pending = (value, maximum)
        now = self._monotonic()
        if self._last_commit is None or now - self._last_commit >= self._interval_seconds:
            self._commit(value, maximum)
            self._last_commit = now

    def flush(self) -> None:
        if self._pending is None:
            return
        self._commit(*self._pending)
        self._last_commit = self._monotonic()


def _state_with_prompt_id(
    state: GenerationJobState,
    batch_index: int,
    prompt_id: str,
) -> GenerationJobState:
    batches = list(state.batches)
    batch = batches[batch_index]
    batches[batch_index] = GenerationBatchRecord.model_validate(
        {
            **batch.model_dump(),
            "prompt_id": prompt_id,
        }
    )
    return GenerationJobState.model_validate(
        {
            **state.model_dump(),
            "revision": state.revision + 1,
            "batches": tuple(item.model_dump() for item in batches),
            "updated_at": state.updated_at,
        }
    )


def _require_generation_request(loaded: LoadedJob) -> GenerationJobRequest:
    if not isinstance(loaded.request, GenerationJobRequest):
        raise generation_error("COMFY_EXECUTION_FAILED")
    return loaded.request


def _require_generation_state(loaded: LoadedJob) -> GenerationJobState:
    if not isinstance(loaded.state, GenerationJobState):
        raise generation_error("COMFY_EXECUTION_FAILED")
    return loaded.state


def _validated_advanced(command: CreateGenerationCommand) -> GenerationAdvanced:
    if command.denoise is not None and command.references.structure is None:
        raise generation_error("INVALID_GENERATION_COMMAND")
    if command.style_weight is not None and not command.references.style:
        raise generation_error("REFERENCE_INVALID")
    try:
        return GenerationAdvanced(
            style_strength=command.style_weight,
            denoise_strength=command.denoise,
            lora_weight=None,
        )
    except ValidationError as error:
        raise generation_error("INVALID_GENERATION_COMMAND") from error


def build_execution_batches(
    parallelism: Literal[1, 2, 4],
    seed_source: Callable[[], int],
) -> tuple[ExecutionBatch, ...]:
    partitions = {
        1: ((0,), (1,), (2,), (3,)),
        2: ((0, 1), (2, 3)),
        4: ((0, 1, 2, 3),),
    }[parallelism]
    batches: list[ExecutionBatch] = []
    for batch_index, candidate_indices in enumerate(partitions):
        seed = seed_source()
        if type(seed) is not int or seed < 0 or seed > MAX_SAFE_SEED:
            raise generation_error("INVALID_SEED_SOURCE")
        batches.append(
            ExecutionBatch(
                batch_index=batch_index,
                candidate_indices=candidate_indices,
                seed=seed,
            )
        )
    return tuple(batches)


def build_generation_profile_binding(
    registry: ManifestRegistry,
    *,
    profile_id: str,
    profile_version: str,
    style_reference_count: int,
    structure_reference_present: bool,
    require_verified: bool = True,
) -> GenerationModelBinding:
    try:
        profile = registry.profile(profile_id, profile_version)
    except ManifestNotFoundError as error:
        raise generation_error("PROFILE_NOT_READY", technical_details=str(error)) from error
    if not isinstance(profile, ModelProfileManifestV2):
        raise generation_error("PROFILE_WORKFLOW_MISMATCH")
    if require_verified and profile.support_state != "verified":
        raise generation_error("PROFILE_NOT_READY")
    capabilities = profile.capabilities
    if structure_reference_present and not capabilities.structure_reference:
        raise generation_error("PROFILE_WORKFLOW_MISMATCH")
    if not structure_reference_present and not capabilities.text_to_image:
        raise generation_error("PROFILE_WORKFLOW_MISMATCH")
    if not (
        capabilities.style_reference_min
        <= style_reference_count
        <= capabilities.style_reference_max
    ):
        raise generation_error("PROFILE_WORKFLOW_MISMATCH")

    variant = (
        ("img2img" if structure_reference_present else "text2img")
        + "-"
        + ("style" if style_reference_count else "no-style")
    )
    workflow = _workflow_for_variant(profile.workflows, variant)
    if workflow is None or workflow.sha256 is None:
        raise generation_error("PROFILE_WORKFLOW_MISMATCH")
    runtime = registry.runtime(profile.compatible_runtime_ids[0])
    return GenerationModelBinding(
        profile_id=profile.profile_id,
        profile_version=profile.profile_version,
        profile_manifest_sha256=manifest_sha256(profile),
        runtime_id=runtime.runtime_id,
        runtime_version=runtime.runtime_version,
        runtime_manifest_sha256=manifest_sha256(runtime),
        workflow_variant=variant,
        workflow_sha256=workflow.sha256,
        output_node_id=workflow.output_node_id,
    )


def _freeze_references(snapshot: JobInputSnapshot) -> FrozenReferences:
    try:
        metadata = json.loads(snapshot.references_json)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise generation_error("REFERENCE_INVALID", technical_details=str(error)) from error
    payloads = {
        item.relative_path: item.payload
        for item in snapshot.files
    }
    style = tuple(
        _stored_reference(item, payloads)
        for item in metadata.get("style", ())
    )
    structure = tuple(
        _stored_reference(item, payloads)
        for item in metadata.get("structure", ())
    )
    return FrozenReferences(style=style, structure=structure)


def _stored_reference(item: dict, payloads: dict[str, bytes]) -> StoredArtifact:
    relative_path = item["relative_path"]
    source_relative = relative_path.removeprefix("inputs/")
    payload = payloads[source_relative]
    if hashlib.sha256(payload).hexdigest() != item["sha256"]:
        raise generation_error("REFERENCE_INVALID")
    return StoredArtifact(
        kind="raw",
        relative_path=relative_path,
        sha256=item["sha256"],
        byte_size=item["byte_size"],
        media_type="image/png",
        width=item["width"],
        height=item["height"],
    )


def _workflow_for_variant(
    workflows: tuple[WorkflowVariantRecord, ...],
    variant: str,
) -> WorkflowVariantRecord | None:
    return next((workflow for workflow in workflows if workflow.variant == variant), None)


def _resolve_target(profile: CatalogProfile, semantic_id: str) -> CatalogEntry | None:
    return next((entry for entry in profile.entries if entry.semantic_id == semantic_id), None)


def _classify_missing(pack_root, profile: CatalogProfile, semantic_id: str) -> str:
    try:
        coverage = classify_coverage(pack_root, profile)
    except CoverageValidationError as error:
        raise generation_error("REFERENCE_INVALID", technical_details=error.user_message) from error
    item = next((entry for entry in coverage.items if entry.semantic_id == semantic_id), None)
    if item is None:
        raise generation_error("JOB_TARGET_NOT_FOUND")
    return item.status


def _catalog_profile(catalogs: CatalogRegistry, pack_format: int) -> CatalogProfile:
    try:
        return catalogs.for_pack_format(pack_format)
    except UnsupportedPackFormat as error:
        raise generation_error("JOB_TARGET_NOT_FOUND", technical_details=str(error)) from error
