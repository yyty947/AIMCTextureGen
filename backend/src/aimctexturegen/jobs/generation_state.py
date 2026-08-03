"""Pure schema-3 generation state transitions."""

from __future__ import annotations

from datetime import datetime

from aimctexturegen.jobs.errors import JobError
from aimctexturegen.jobs.models_v3 import (
    CandidateArtifacts,
    GenerationBatchRecord,
    GenerationCandidateRecord,
    GenerationFailure,
    GenerationJobState,
    StoredArtifact,
)


_NONTERMINAL_CANDIDATES = frozenset(
    {"pending", "generating", "raw_ready", "postprocessing"}
)


def start_generation(state: GenerationJobState, *, now: datetime) -> GenerationJobState:
    if state.status != "queued":
        raise _invalid_transition()
    return _replace_state(
        state,
        status="generating",
        started_at=now,
        updated_at=now,
    )


def request_cancel(state: GenerationJobState, *, now: datetime) -> GenerationJobState:
    if state.status in {"completed", "failed", "canceled"}:
        raise _invalid_transition()
    if state.cancel_requested_at is not None:
        return state
    return _replace_state(state, cancel_requested_at=now, updated_at=now)


def start_batch(
    state: GenerationJobState, batch_index: int, *, now: datetime
) -> GenerationJobState:
    batch = _batch(state, batch_index)
    if state.status not in {"generating", "postprocessing"} or batch.status != "pending":
        raise _invalid_transition()
    replacement = _replace_batch(
        batch,
        status="generating",
        started_at=now,
        prompt_id=batch.prompt_id,
        sampling_step=0,
        sampling_maximum=1,
    )
    candidates = list(state.candidates)
    for candidate_index in batch.candidate_indices:
        candidate = candidates[candidate_index]
        candidates[candidate_index] = _replace_candidate(
            candidate,
            status="generating",
            started_at=now,
        )
    return _replace_state(
        state,
        batches=_replace_batch_tuple(state, replacement),
        candidates=tuple(candidates),
        updated_at=now,
    )


def record_progress(
    state: GenerationJobState,
    batch_index: int,
    value: int,
    maximum: int,
    *,
    now: datetime,
) -> GenerationJobState:
    batch = _batch(state, batch_index)
    if batch.status != "generating":
        raise _invalid_transition()
    replacement = _replace_batch(
        batch,
        sampling_step=value,
        sampling_maximum=maximum,
    )
    return _replace_state(
        state,
        batches=_replace_batch_tuple(state, replacement),
        updated_at=now,
    )


def mark_batch_raw_ready(
    state: GenerationJobState,
    batch_index: int,
    artifacts: tuple[StoredArtifact, ...],
    *,
    now: datetime,
) -> GenerationJobState:
    batch = _batch(state, batch_index)
    if batch.status != "generating" or len(artifacts) != len(batch.candidate_indices):
        raise _invalid_transition()
    replacement = _replace_batch(
        batch,
        status="raw_ready",
        prompt_id=None,
        raw_artifacts=artifacts,
        finished_at=now,
    )
    candidates = list(state.candidates)
    for position, candidate_index in enumerate(batch.candidate_indices):
        candidate = candidates[candidate_index]
        candidates[candidate_index] = _replace_candidate(
            candidate,
            status="raw_ready",
            artifacts=CandidateArtifacts(raw=artifacts[position]),
        )
    return _replace_state(
        state,
        status="postprocessing",
        batches=_replace_batch_tuple(state, replacement),
        candidates=tuple(candidates),
        updated_at=now,
    )


def complete_candidate(
    state: GenerationJobState,
    candidate_index: int,
    artifacts: CandidateArtifacts,
    *,
    now: datetime,
) -> GenerationJobState:
    candidate = state.candidates[candidate_index]
    if candidate.status not in {"raw_ready", "postprocessing"}:
        raise _invalid_transition()
    replacement = _replace_candidate(
        candidate,
        status="completed",
        artifacts=artifacts,
        finished_at=now,
    )
    candidates = list(state.candidates)
    candidates[candidate_index] = replacement
    batch = _batch(state, candidate.batch_index)
    batch_status = (
        "completed"
        if all(
            (candidates[index].status in {"completed", "inherited"})
            for index in batch.candidate_indices
        )
        else batch.status
    )
    batch_replacement = (
        _replace_batch(batch, status="completed", prompt_id=None)
        if batch_status == "completed"
        else batch
    )
    return _replace_state(
        state,
        batches=_replace_batch_tuple(state, batch_replacement),
        candidates=tuple(candidates),
        updated_at=now,
    )


def complete_generation(state: GenerationJobState, *, now: datetime) -> GenerationJobState:
    if any(candidate.status not in {"completed", "inherited"} for candidate in state.candidates):
        raise _invalid_transition()
    return _replace_state(
        state,
        status="completed",
        batches=tuple(
            _replace_batch(batch, prompt_id=None)
            for batch in state.batches
        ),
        updated_at=now,
        finished_at=now,
        failure=None,
    )


def fail_generation(
    state: GenerationJobState,
    failure: GenerationFailure,
    *,
    now: datetime,
) -> GenerationJobState:
    if (
        state.cancel_requested_at is not None
        and failure.error_code == "CANCEL_CONFIRMATION_FAILED"
        and state.status in {"generating", "postprocessing"}
    ):
        return _replace_state(
            state,
            failure=failure,
            updated_at=now,
        )

    candidates = []
    for candidate in state.candidates:
        if candidate.status in {"completed", "inherited", "failed", "canceled"}:
            candidates.append(candidate)
        elif candidate.status == "pending":
            candidates.append(
                _replace_candidate(
                    candidate,
                    status="canceled",
                    finished_at=now,
                )
            )
        else:
            candidates.append(
                _replace_candidate(
                    candidate,
                    status="failed",
                    failure=failure,
                    finished_at=now,
                )
            )
    batches = []
    for batch in state.batches:
        if batch.status in {"completed", "failed", "canceled"}:
            batches.append(batch)
        elif batch.status == "pending":
            batches.append(_replace_batch(batch, status="canceled", started_at=now, finished_at=now))
        else:
            batches.append(
                _replace_batch(
                    batch,
                    status="failed",
                    failure=failure,
                    finished_at=now,
                )
            )
    return _replace_state(
        state,
        status="failed",
        batches=tuple(batches),
        candidates=tuple(candidates),
        failure=failure,
        updated_at=now,
        finished_at=now,
    )


def confirm_canceled(state: GenerationJobState, *, now: datetime) -> GenerationJobState:
    if state.cancel_requested_at is None:
        raise _invalid_transition()
    candidates = []
    for candidate in state.candidates:
        if candidate.status in {"completed", "inherited"}:
            candidates.append(candidate)
        elif candidate.status == "failed":
            candidates.append(candidate)
        elif candidate.status == "canceled":
            candidates.append(candidate)
        else:
            candidates.append(
                _replace_candidate(
                    candidate,
                    status="canceled",
                    finished_at=now,
                )
            )
    batches = []
    for batch in state.batches:
        if all(candidates[index].status in {"completed", "inherited"} for index in batch.candidate_indices):
            batches.append(
                _replace_batch(
                    batch,
                    status="completed",
                    prompt_id=None,
                    finished_at=now,
                )
            )
        elif batch.status == "failed":
            batches.append(_replace_batch(batch, prompt_id=None))
        else:
            started_at = batch.started_at or now
            batches.append(
                _replace_batch(
                    batch,
                    status="canceled",
                    prompt_id=None,
                    started_at=started_at,
                    finished_at=now,
                )
            )
    return _replace_state(
        state,
        status="canceled",
        batches=tuple(batches),
        candidates=tuple(candidates),
        failure=None,
        updated_at=now,
        finished_at=now,
    )


def recover_generation_interruption(
    state: GenerationJobState, *, now: datetime
) -> GenerationJobState:
    if state.status not in {"generating", "postprocessing"}:
        return state
    failure = GenerationFailure(
        error_code="JOB_INTERRUPTED",
        stage=state.status,
        user_message="任务因应用重启而中断",
        recommended_actions=("重试该任务",),
        technical_details=None,
        retryable=True,
        occurred_at=now,
    )
    return fail_generation(state, failure, now=now)


def _batch(state: GenerationJobState, batch_index: int) -> GenerationBatchRecord:
    if type(batch_index) is not int or batch_index not in range(len(state.batches)):
        raise _invalid_transition()
    return state.batches[batch_index]


def _replace_batch_tuple(
    state: GenerationJobState,
    replacement: GenerationBatchRecord,
) -> tuple[GenerationBatchRecord, ...]:
    batches = list(state.batches)
    batches[replacement.batch_index] = replacement
    return tuple(batches)


def _replace_candidate(
    candidate: GenerationCandidateRecord,
    **updates: object,
) -> GenerationCandidateRecord:
    return GenerationCandidateRecord.model_validate(
        {
            **candidate.model_dump(),
            **updates,
        }
    )


def _replace_batch(
    batch: GenerationBatchRecord,
    **updates: object,
) -> GenerationBatchRecord:
    return GenerationBatchRecord.model_validate(
        {
            **batch.model_dump(),
            **updates,
        }
    )


def _replace_state(
    state: GenerationJobState,
    **updates: object,
) -> GenerationJobState:
    return GenerationJobState.model_validate(
        {
            **state.model_dump(),
            "revision": state.revision + 1,
            **updates,
        }
    )


def _invalid_transition() -> JobError:
    return JobError(
        "INVALID_JOB_TRANSITION",
        "当前任务状态不允许此操作",
    )
