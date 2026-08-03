"""Strict schema-3 generation request/state contracts."""

from __future__ import annotations

import json
import re
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from aimctexturegen.core.relative_paths import validate_project_relative_path
from aimctexturegen.jobs.errors import JobError
from aimctexturegen.jobs.models import CandidateIndex, JobStatus, MAX_SAFE_SEED, Seed


GenerationCandidateStatus = Literal[
    "pending",
    "generating",
    "raw_ready",
    "postprocessing",
    "completed",
    "failed",
    "canceled",
    "inherited",
]
GenerationBatchStatus = Literal[
    "pending",
    "generating",
    "raw_ready",
    "completed",
    "failed",
    "canceled",
]
WorkflowVariant = Literal[
    "text2img-no-style",
    "text2img-style",
    "img2img-no-style",
    "img2img-style",
]
ArtifactKind = Literal["raw", "final", "nearest", "tile", "report"]
FourGenerationCandidates = tuple[
    "GenerationCandidateRecord",
    "GenerationCandidateRecord",
    "GenerationCandidateRecord",
    "GenerationCandidateRecord",
]

_STYLE_PATH = re.compile(r"^inputs/style/[0-9]{2}\.png$")
_STRUCTURE_PATH = "inputs/structure.png"
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_ACTIVE_JOB_STATUSES = frozenset({"generating", "postprocessing"})
_TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "canceled"})
_TERMINAL_CANDIDATE_STATUSES = frozenset(
    {"completed", "failed", "canceled", "inherited"}
)
_NONTERMINAL_CANDIDATE_STATUSES = frozenset(
    {"pending", "generating", "raw_ready", "postprocessing"}
)
_SUMMARY_FAILURE_CODE = "CANCEL_CONFIRMATION_FAILED"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class StoredArtifact(_StrictModel):
    kind: ArtifactKind
    relative_path: str
    sha256: str = Field(min_length=64, max_length=64)
    byte_size: int = Field(ge=0)
    media_type: str = Field(min_length=1)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return validate_project_relative_path(value)

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if _HEX_64.fullmatch(value) is None:
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value

    @model_validator(mode="after")
    def validate_dimensions(self) -> Self:
        if (self.width is None) != (self.height is None):
            raise ValueError("width and height must both be present or absent")
        return self

class CandidateArtifacts(_StrictModel):
    raw: StoredArtifact | None = None
    final: StoredArtifact | None = None
    nearest: StoredArtifact | None = None
    tile: StoredArtifact | None = None
    report: StoredArtifact | None = None

    @model_validator(mode="after")
    def validate_kinds(self) -> Self:
        expected = {
            "raw": self.raw,
            "final": self.final,
            "nearest": self.nearest,
            "tile": self.tile,
            "report": self.report,
        }
        for kind, artifact in expected.items():
            if artifact is not None and artifact.kind != kind:
                raise ValueError(f"{kind} artifact kind mismatch")
            if artifact is not None:
                root = {"raw": "raw/", "final": "processed/", "nearest": "previews/", "tile": "previews/", "report": "reports/"}[kind]
                if not artifact.relative_path.startswith(root):
                    raise ValueError("artifact kind does not match schema-3 layout directory")
        return self

    def is_complete(self) -> bool:
        return all(
            getattr(self, field) is not None
            for field in ("raw", "final", "nearest", "tile", "report")
        )


class CandidateLineage(_StrictModel):
    parent_job_id: UUID
    parent_candidate_index: CandidateIndex


class ExecutionBatch(_StrictModel):
    batch_index: int = Field(ge=0, le=3)
    candidate_indices: tuple[CandidateIndex, ...]
    seed: Seed

    @model_validator(mode="after")
    def validate_indices(self) -> Self:
        if not self.candidate_indices:
            raise ValueError("execution batch requires candidates")
        if tuple(sorted(self.candidate_indices)) != self.candidate_indices:
            raise ValueError("execution batch candidates must be ordered")
        if len(set(self.candidate_indices)) != len(self.candidate_indices):
            raise ValueError("execution batch candidates must be unique")
        if len(self.candidate_indices) not in {1, 2, 4}:
            raise ValueError("execution batch size must be 1, 2, or 4")
        return self


class GenerationFailure(_StrictModel):
    error_code: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    user_message: str = Field(min_length=1)
    recommended_actions: tuple[str, ...]
    technical_details: str | None
    retryable: bool
    occurred_at: AwareDatetime


class GenerationCandidateRecord(_StrictModel):
    candidate_index: CandidateIndex
    batch_index: int = Field(ge=0, le=3)
    position_in_batch: int = Field(ge=0, le=3)
    batch_seed: Seed
    status: GenerationCandidateStatus
    artifacts: CandidateArtifacts
    lineage: CandidateLineage | None
    failure: GenerationFailure | None
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None

    @model_validator(mode="after")
    def validate_candidate(self) -> Self:
        if self.candidate_index < self.position_in_batch:
            raise ValueError("position_in_batch is inconsistent")
        if self.status == "failed":
            if self.failure is None:
                raise ValueError("failed candidate requires failure")
        elif self.failure is not None:
            raise ValueError("candidate failure is only valid for failed status")

        if self.status == "pending":
            if self.started_at is not None or self.finished_at is not None:
                raise ValueError("pending candidate cannot have timestamps")
        elif self.status in {"generating", "raw_ready", "postprocessing"}:
            if self.started_at is None or self.finished_at is not None:
                raise ValueError("active candidate requires only started_at")
        elif self.status in {"completed", "inherited"}:
            if self.started_at is None or self.finished_at is None:
                raise ValueError("terminal candidate requires both timestamps")
        elif self.status == "failed":
            if self.finished_at is None:
                raise ValueError("failed candidate requires finished_at")
        elif self.finished_at is None:
            raise ValueError("canceled candidate requires finished_at")
        if (
            self.started_at is not None
            and self.finished_at is not None
            and self.finished_at < self.started_at
        ):
            raise ValueError("candidate finished before it started")

        if self.status == "raw_ready" and self.artifacts.raw is None:
            raise ValueError("raw_ready candidate requires raw artifact")
        if self.status in {"completed", "inherited"} and not self.artifacts.is_complete():
            raise ValueError("completed or inherited candidate requires complete artifacts")
        if self.status == "inherited" and self.lineage is None:
            raise ValueError("inherited candidate requires lineage")
        if self.status != "inherited" and self.lineage is not None:
            raise ValueError("lineage is only valid for inherited candidate")
        return self


class GenerationBatchRecord(_StrictModel):
    batch_index: int = Field(ge=0, le=3)
    candidate_indices: tuple[CandidateIndex, ...]
    seed: Seed
    status: GenerationBatchStatus
    prompt_id: str | None = None
    sampling_step: int | None = Field(default=None, ge=0)
    sampling_maximum: int | None = Field(default=None, ge=1)
    raw_artifacts: tuple[StoredArtifact, ...] = ()
    failure: GenerationFailure | None = None
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_batch(self) -> Self:
        if tuple(sorted(self.candidate_indices)) != self.candidate_indices:
            raise ValueError("batch candidates must be ordered")
        if len(set(self.candidate_indices)) != len(self.candidate_indices):
            raise ValueError("batch candidates must be unique")
        if self.status == "failed":
            if self.failure is None:
                raise ValueError("failed batch requires failure")
        elif self.failure is not None:
            raise ValueError("batch failure is only valid for failed status")
        if (self.sampling_step is None) != (self.sampling_maximum is None):
            raise ValueError("sampling progress must be complete or absent")
        if (
            self.sampling_step is not None
            and self.sampling_maximum is not None
            and self.sampling_step > self.sampling_maximum
        ):
            raise ValueError("sampling progress exceeds maximum")
        if self.status == "pending":
            if self.started_at is not None or self.finished_at is not None:
                raise ValueError("pending batch cannot have timestamps")
        elif self.status == "generating":
            if self.started_at is None or self.finished_at is not None:
                raise ValueError("generating batch requires only started_at")
        else:
            if self.started_at is None or self.finished_at is None:
                raise ValueError("terminal/raw-ready batch requires both timestamps")
        if (
            self.started_at is not None
            and self.finished_at is not None
            and self.finished_at < self.started_at
        ):
            raise ValueError("batch finished before it started")
        if self.status in {"raw_ready", "completed"} and len(self.raw_artifacts) != len(
            self.candidate_indices
        ):
            raise ValueError("raw-ready/completed batch requires one raw artifact per candidate")
        return self


class GenerationTarget(_StrictModel):
    catalog_id: str = Field(min_length=1)
    target_semantic_id: str = Field(min_length=1)
    target_display_name: str = Field(min_length=1)
    target_relative_path: str

    @field_validator("target_relative_path")
    @classmethod
    def validate_target_relative_path(cls, value: str) -> str:
        return validate_project_relative_path(value)


class CompiledPrompt(_StrictModel):
    prompt_version: str = Field(min_length=1)
    positive_prompt: str = Field(min_length=1)
    negative_prompt: str = Field(min_length=1)
    user_prompt: str


class FrozenReferences(_StrictModel):
    style: tuple[StoredArtifact, ...] = Field(default=(), max_length=8)
    structure: tuple[StoredArtifact, ...] = Field(default=(), max_length=1)

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        for artifact in self.style:
            if _STYLE_PATH.fullmatch(artifact.relative_path) is None:
                raise ValueError("style references must use inputs/style/NN.png")
        for artifact in self.structure:
            if artifact.relative_path != _STRUCTURE_PATH:
                raise ValueError("structure reference must use inputs/structure.png")
        return self


class GenerationAdvanced(_StrictModel):
    style_strength: float | None = Field(default=None, ge=0.0, le=2.0)
    denoise_strength: float | None = Field(default=None, ge=0.0, le=1.0)
    lora_weight: float | None = Field(default=None, ge=0.0, le=2.0)


class GenerationModelBinding(_StrictModel):
    profile_id: str = Field(min_length=1)
    profile_version: str = Field(min_length=1)
    profile_manifest_sha256: str = Field(min_length=64, max_length=64)
    runtime_id: str = Field(min_length=1)
    runtime_version: str = Field(min_length=1)
    runtime_manifest_sha256: str = Field(min_length=64, max_length=64)
    workflow_variant: WorkflowVariant
    workflow_sha256: str = Field(min_length=64, max_length=64)
    output_node_id: str = Field(min_length=1)

    @field_validator(
        "profile_manifest_sha256",
        "runtime_manifest_sha256",
        "workflow_sha256",
    )
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if _HEX_64.fullmatch(value) is None:
            raise ValueError("digest must be 64 lowercase hex characters")
        return value


class GenerationJobRequest(_StrictModel):
    schema_version: Literal[3]
    job_id: UUID
    project_id: UUID
    parent_job_id: UUID | None
    target: GenerationTarget
    prompt: CompiledPrompt
    resolution: Literal[16, 32, 64]
    parallelism: Literal[1, 2, 4]
    execution_batches: tuple[ExecutionBatch, ...]
    references: FrozenReferences
    advanced: GenerationAdvanced
    model_profile: GenerationModelBinding
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if self.parent_job_id == self.job_id:
            raise ValueError("a job cannot parent itself")
        expected = {
            1: ((0,), (1,), (2,), (3,)),
            2: ((0, 1), (2, 3)),
            4: ((0, 1, 2, 3),),
        }[self.parallelism]
        actual = tuple(batch.candidate_indices for batch in self.execution_batches)
        indices = tuple(batch.batch_index for batch in self.execution_batches)
        if actual != expected or indices != tuple(range(len(expected))):
            raise ValueError("execution batches must match the exact native partition")
        style_count = len(self.references.style)
        structure_present = len(self.references.structure) == 1
        variant = self.model_profile.workflow_variant
        expected_variant = (
            ("img2img" if structure_present else "text2img")
            + "-"
            + ("style" if style_count else "no-style")
        )
        if variant != expected_variant:
            raise ValueError("workflow variant does not match frozen references")
        return self


class GenerationJobState(_StrictModel):
    schema_version: Literal[2]
    job_id: UUID
    project_id: UUID
    revision: int = Field(ge=0)
    status: JobStatus
    batches: tuple[GenerationBatchRecord, ...]
    candidates: FourGenerationCandidates
    failure: GenerationFailure | None
    cancel_requested_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None

    @classmethod
    def initial_from_request(cls, request: GenerationJobRequest) -> "GenerationJobState":
        return cls(
            schema_version=2,
            job_id=request.job_id,
            project_id=request.project_id,
            revision=0,
            status="queued",
            batches=tuple(
                GenerationBatchRecord(
                    batch_index=batch.batch_index,
                    candidate_indices=batch.candidate_indices,
                    seed=batch.seed,
                    status="pending",
                    prompt_id=None,
                    sampling_step=None,
                    sampling_maximum=None,
                    raw_artifacts=(),
                    failure=None,
                    started_at=None,
                    finished_at=None,
                )
                for batch in request.execution_batches
            ),
            candidates=tuple(
                GenerationCandidateRecord(
                    candidate_index=candidate_index,
                    batch_index=batch.batch_index,
                    position_in_batch=position,
                    batch_seed=batch.seed,
                    status="pending",
                    artifacts=CandidateArtifacts(),
                    lineage=None,
                    failure=None,
                    started_at=None,
                    finished_at=None,
                )
                for batch in request.execution_batches
                for position, candidate_index in enumerate(batch.candidate_indices)
            ),
            failure=None,
            cancel_requested_at=None,
            created_at=request.created_at,
            updated_at=request.created_at,
            started_at=None,
            finished_at=None,
        )

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        batch_indices = tuple(batch.batch_index for batch in self.batches)
        if batch_indices != tuple(range(len(self.batches))):
            raise ValueError("batch records must be ordered and contiguous")
        candidate_indices = tuple(candidate.candidate_index for candidate in self.candidates)
        if candidate_indices != (0, 1, 2, 3):
            raise ValueError("candidate records must be ordered from 0 through 3")
        batch_map = {batch.batch_index: batch for batch in self.batches}
        covered: list[int] = []
        for candidate in self.candidates:
            batch = batch_map.get(candidate.batch_index)
            if batch is None:
                raise ValueError("candidate batch is missing")
            if candidate.batch_seed != batch.seed:
                raise ValueError("candidate batch seed mismatch")
            if candidate.candidate_index not in batch.candidate_indices:
                raise ValueError("candidate is not present in its batch")
            if batch.candidate_indices[candidate.position_in_batch] != candidate.candidate_index:
                raise ValueError("candidate position_in_batch mismatch")
            covered.append(candidate.candidate_index)
        if tuple(sorted(covered)) != (0, 1, 2, 3):
            raise ValueError("batches must cover four candidates exactly once")

        if self.updated_at < self.created_at:
            raise ValueError("job updated before it was created")
        for timestamp in (self.started_at, self.finished_at, self.cancel_requested_at):
            if timestamp is not None and not (self.created_at <= timestamp <= self.updated_at):
                raise ValueError("job timestamp is outside lifecycle bounds")
        if (
            self.started_at is not None
            and self.finished_at is not None
            and self.finished_at < self.started_at
        ):
            raise ValueError("job finished before it started")

        if self.failure is not None:
            if self.status != "failed":
                if not (
                    self.status in _ACTIVE_JOB_STATUSES
                    and self.cancel_requested_at is not None
                    and self.failure.error_code == _SUMMARY_FAILURE_CODE
                ):
                    raise ValueError("nonterminal failures are only valid for cancel confirmation failure")
            elif self.finished_at is None:
                raise ValueError("failed job requires finished_at")
        elif self.status == "failed":
            raise ValueError("failed job requires failure")

        if self.status == "queued":
            if self.started_at is not None or self.finished_at is not None:
                raise ValueError("queued job cannot have lifecycle timestamps")
        elif self.status in _ACTIVE_JOB_STATUSES:
            if self.started_at is None or self.finished_at is not None:
                raise ValueError("active job requires only started_at")
        else:
            if self.finished_at is None:
                raise ValueError("terminal job requires finished_at")

        candidate_statuses = tuple(candidate.status for candidate in self.candidates)
        if self.status == "queued" and candidate_statuses != ("pending",) * 4:
            raise ValueError("queued job requires pending candidates")
        if self.status == "completed" and any(
            status not in {"completed", "inherited"} for status in candidate_statuses
        ):
            raise ValueError("completed job requires completed or inherited candidates")
        if self.status in _TERMINAL_JOB_STATUSES and any(
            status in _NONTERMINAL_CANDIDATE_STATUSES for status in candidate_statuses
        ):
            raise ValueError("terminal job cannot contain nonterminal candidates")
        return self


def validate_generation_pair(
    request: GenerationJobRequest,
    state: GenerationJobState,
) -> None:
    if request.job_id != state.job_id or request.project_id != state.project_id:
        raise JobError("INVALID_JOB_RECORD", "任务请求与状态记录不一致")
    batch_by_index = {batch.batch_index: batch for batch in request.execution_batches}
    if len(batch_by_index) != len(state.batches):
        raise JobError("INVALID_JOB_RECORD", "任务请求与状态记录不一致")
    for batch in state.batches:
        source = batch_by_index.get(batch.batch_index)
        if source is None or source.seed != batch.seed or source.candidate_indices != batch.candidate_indices:
            raise JobError("INVALID_JOB_RECORD", "任务请求与状态记录不一致")
    for candidate in state.candidates:
        source = batch_by_index[candidate.batch_index]
        if source.seed != candidate.batch_seed:
            raise JobError("INVALID_JOB_RECORD", "任务请求与状态记录不一致")
        if source.candidate_indices[candidate.position_in_batch] != candidate.candidate_index:
            raise JobError("INVALID_JOB_RECORD", "任务请求与状态记录不一致")


def dump_generation_request(request: GenerationJobRequest) -> bytes:
    return _dump_model(request)


def dump_generation_state(state: GenerationJobState) -> bytes:
    return _dump_model(state)


def _dump_model(model: _StrictModel) -> bytes:
    text = json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (text + "\n").encode("utf-8")
