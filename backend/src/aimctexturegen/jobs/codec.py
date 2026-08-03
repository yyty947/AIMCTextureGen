"""Schema-aware durable job codecs without legacy byte rewriting."""

from __future__ import annotations

import json

from aimctexturegen.jobs.errors import JobError
from aimctexturegen.jobs.models import (
    JobRequest,
    JobStateRecord,
    dump_job_request,
    dump_job_state,
    validate_job_pair,
)
from aimctexturegen.jobs.models_v3 import (
    GenerationJobRequest,
    GenerationJobState,
    dump_generation_request,
    dump_generation_state,
    validate_generation_pair,
)


DurableJobRequest = JobRequest | GenerationJobRequest
DurableJobState = JobStateRecord | GenerationJobState
MAX_JOB_JSON_BYTES = 1024 * 1024


def load_job_request(payload: bytes) -> DurableJobRequest:
    schema_version = _load_schema_version(payload)
    if schema_version in {1, 2}:
        return JobRequest.model_validate_json(payload, strict=True)
    if schema_version == 3:
        return GenerationJobRequest.model_validate_json(payload, strict=True)
    raise ValueError(f"unsupported job request schema: {schema_version}")


def load_job_state(payload: bytes) -> DurableJobState:
    schema_version = _load_schema_version(payload)
    if schema_version == 1:
        return JobStateRecord.model_validate_json(payload, strict=True)
    if schema_version == 2:
        return GenerationJobState.model_validate_json(payload, strict=True)
    raise ValueError(f"unsupported job state schema: {schema_version}")


def dump_durable_request(request: DurableJobRequest) -> bytes:
    if isinstance(request, JobRequest):
        return dump_job_request(request)
    if isinstance(request, GenerationJobRequest):
        return dump_generation_request(request)
    raise TypeError("request must be a durable job request")


def dump_durable_state(state: DurableJobState) -> bytes:
    if isinstance(state, JobStateRecord):
        return dump_job_state(state)
    if isinstance(state, GenerationJobState):
        return dump_generation_state(state)
    raise TypeError("state must be a durable job state")


def validate_durable_pair(
    request: DurableJobRequest,
    state: DurableJobState,
) -> None:
    if isinstance(request, JobRequest) and isinstance(state, JobStateRecord):
        validate_job_pair(request, state)
        return
    if isinstance(request, GenerationJobRequest) and isinstance(
        state, GenerationJobState
    ):
        validate_generation_pair(request, state)
        return
    raise JobError("INVALID_JOB_RECORD", "任务请求与状态记录不一致")


def _load_schema_version(payload: bytes) -> int:
    if len(payload) > MAX_JOB_JSON_BYTES:
        raise ValueError("job JSON exceeds its size limit")
    document = json.loads(payload)
    if not isinstance(document, dict):
        raise ValueError("job JSON must be an object")
    schema_version = document.get("schema_version")
    if type(schema_version) is not int:
        raise ValueError("schema_version must be an integer")
    return schema_version
