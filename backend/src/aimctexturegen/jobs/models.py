"""Strict, versioned durable job contracts and deterministic JSON dumps."""

from __future__ import annotations

import json
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


MAX_PROMPT_CODE_POINTS = 4000
MAX_SAFE_SEED = 9_007_199_254_740_991

JobStatus = Literal[
    "queued",
    "generating",
    "postprocessing",
    "completed",
    "failed",
    "canceled",
]
CandidateStatus = Literal[
    "pending",
    "generating",
    "postprocessing",
    "completed",
    "failed",
    "canceled",
]
CandidateIndex = Literal[0, 1, 2, 3]
Seed = Annotated[int, Field(ge=0, le=MAX_SAFE_SEED)]
FourSeeds = tuple[Seed, Seed, Seed, Seed]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _GenerationInputs(_StrictModel):
    target_semantic_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CODE_POINTS)
    resolution: Literal[16, 32, 64]
    parallelism: Literal[1, 2, 4]
    style_references: tuple[str, ...] = Field(min_length=1, max_length=8)
    structure_reference: str | None

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must contain a non-whitespace character")
        return value

    @field_validator("style_references")
    @classmethod
    def validate_style_references(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(validate_project_relative_path(value) for value in values)

    @field_validator("structure_reference")
    @classmethod
    def validate_structure_reference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_project_relative_path(value)


class CreateJobCommand(_GenerationInputs):
    """Strict service command before server identities and seeds exist.

    A future HTTP route must map JSON array payloads to this tuple-based
    domain command instead of weakening the durable service contract.
    """


class JobRequest(_GenerationInputs):
    """Immutable schema-1 request persisted for one four-candidate job."""

    schema_version: Literal[1]
    job_id: UUID
    project_id: UUID
    retry_of_job_id: UUID | None
    catalog_id: str = Field(min_length=1)
    target_display_name: str = Field(min_length=1)
    target_relative_path: str
    seeds: FourSeeds
    created_at: AwareDatetime

    @field_validator("target_relative_path")
    @classmethod
    def validate_target_relative_path(cls, value: str) -> str:
        return validate_project_relative_path(value)

    @model_validator(mode="after")
    def validate_request_identity_and_seeds(self) -> Self:
        if self.retry_of_job_id == self.job_id:
            raise ValueError("a job cannot retry itself")
        if len(set(self.seeds)) != 4:
            raise ValueError("job seeds must be unique")
        return self


class JobFailure(_StrictModel):
    """Structured persisted failure safe for later API translation."""

    code: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    user_message: str = Field(min_length=1)
    recommended_actions: tuple[str, ...]
    technical_details: str | None
    log_reference: str | None


class CandidateRecord(_StrictModel):
    """State and timing for one of the four persisted candidates."""

    candidate_index: CandidateIndex
    seed: Seed
    status: CandidateStatus
    failure: JobFailure | None
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None

    @model_validator(mode="after")
    def validate_failure_status(self) -> Self:
        _validate_failure_status(self.status, self.failure)
        return self


class JobStateRecord(_StrictModel):
    """Mutable schema-1 aggregate state stored separately from the request."""

    schema_version: Literal[1]
    job_id: UUID
    project_id: UUID
    revision: int = Field(ge=0)
    status: JobStatus
    candidates: tuple[
        CandidateRecord,
        CandidateRecord,
        CandidateRecord,
        CandidateRecord,
    ]
    failure: JobFailure | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None

    @model_validator(mode="after")
    def validate_candidate_order_and_failure(self) -> Self:
        indices = tuple(candidate.candidate_index for candidate in self.candidates)
        if indices != (0, 1, 2, 3):
            raise ValueError("candidate records must be ordered from 0 through 3")
        _validate_failure_status(self.status, self.failure)
        return self


class JobSummary(_StrictModel):
    """Stable, index-safe job fields without prompts, paths, seeds, or failures."""

    job_id: UUID
    project_id: UUID
    retry_of_job_id: UUID | None
    target_semantic_id: str = Field(min_length=1)
    target_display_name: str = Field(min_length=1)
    resolution: Literal[16, 32, 64]
    parallelism: Literal[1, 2, 4]
    status: JobStatus
    revision: int = Field(ge=0)
    candidate_statuses: tuple[
        CandidateStatus,
        CandidateStatus,
        CandidateStatus,
        CandidateStatus,
    ]
    created_at: AwareDatetime
    updated_at: AwareDatetime


def validate_job_pair(request: JobRequest, state: JobStateRecord) -> None:
    """Reject cross-file identity, candidate-index, or seed inconsistencies."""

    matches_identity = (
        request.job_id == state.job_id
        and request.project_id == state.project_id
    )
    indices = tuple(candidate.candidate_index for candidate in state.candidates)
    seeds = tuple(candidate.seed for candidate in state.candidates)
    if (
        not matches_identity
        or indices != (0, 1, 2, 3)
        or seeds != request.seeds
    ):
        raise JobError(
            "INVALID_JOB_RECORD",
            "任务请求与状态记录不一致",
        )


def dump_job_request(request: JobRequest) -> bytes:
    """Serialize an immutable request as deterministic compact UTF-8 JSON."""

    return _dump_model(request)


def dump_job_state(state: JobStateRecord) -> bytes:
    """Serialize mutable state as deterministic compact UTF-8 JSON."""

    return _dump_model(state)


def _dump_model(model: _StrictModel) -> bytes:
    document = model.model_dump(mode="json")
    text = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (text + "\n").encode("utf-8")


def _validate_failure_status(
    status: JobStatus | CandidateStatus,
    failure: JobFailure | None,
) -> None:
    if status == "failed" and failure is None:
        raise ValueError("failed records require failure data")
    if status != "failed" and failure is not None:
        raise ValueError("failure data is only valid for failed records")
