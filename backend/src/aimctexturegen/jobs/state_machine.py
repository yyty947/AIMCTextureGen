"""Pure legal transitions for durable aggregate and candidate job state."""

from __future__ import annotations

from datetime import datetime

from aimctexturegen.jobs.errors import JobError
from aimctexturegen.jobs.models import (
    CandidateRecord,
    CandidateStatus,
    JobFailure,
    JobStateRecord,
    JobStatus,
)


_TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "canceled"})
_TERMINAL_CANDIDATE_STATUSES = frozenset({"completed", "failed", "canceled"})
_ACTIVE_JOB_STATUSES = frozenset({"generating", "postprocessing"})

_LEGAL_JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    "queued": frozenset({"generating", "canceled"}),
    "generating": frozenset({"postprocessing", "failed", "canceled"}),
    "postprocessing": frozenset({"completed", "failed", "canceled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "canceled": frozenset(),
}
_LEGAL_CANDIDATE_TRANSITIONS: dict[
    CandidateStatus,
    frozenset[CandidateStatus],
] = {
    "pending": frozenset({"generating", "failed", "canceled"}),
    "generating": frozenset({"postprocessing", "failed", "canceled"}),
    "postprocessing": frozenset({"completed", "failed", "canceled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "canceled": frozenset(),
}


def transition_job_state(
    state: JobStateRecord,
    target: JobStatus,
    *,
    now: datetime,
    failure: JobFailure | None = None,
) -> JobStateRecord:
    """Apply one legal aggregate transition and increment the revision once."""

    if target not in _LEGAL_JOB_TRANSITIONS[state.status]:
        raise _invalid_transition()
    _require_failure_for_target(target, failure)
    if target == "canceled":
        return _canceled_state(state, now)

    started_at = state.started_at
    if target == "generating" and started_at is None:
        started_at = now
    return _replace_state(
        state,
        status=target,
        failure=failure,
        updated_at=now,
        started_at=started_at,
        finished_at=now if target in _TERMINAL_JOB_STATUSES else None,
    )


def transition_candidate_state(
    state: JobStateRecord,
    candidate_index: int,
    target: CandidateStatus,
    *,
    now: datetime,
    failure: JobFailure | None = None,
) -> JobStateRecord:
    """Apply one legal candidate transition in one aggregate revision."""

    if state.status in _TERMINAL_JOB_STATUSES:
        raise _invalid_transition()
    if type(candidate_index) is not int or candidate_index not in range(4):
        raise _invalid_transition()
    candidate = state.candidates[candidate_index]
    if target not in _LEGAL_CANDIDATE_TRANSITIONS[candidate.status]:
        raise _invalid_transition()
    _require_failure_for_target(target, failure)

    started_at = candidate.started_at
    if target == "generating" and started_at is None:
        started_at = now
    replacement = _replace_candidate(
        candidate,
        status=target,
        failure=failure,
        started_at=started_at,
        finished_at=now if target in _TERMINAL_CANDIDATE_STATUSES else None,
    )
    candidates = list(state.candidates)
    candidates[candidate_index] = replacement
    return _replace_state(
        state,
        candidates=tuple(candidates),
        updated_at=now,
    )


def cancel_state(
    state: JobStateRecord,
    *,
    now: datetime,
) -> JobStateRecord:
    """Cancel an active or queued job and all nonterminal candidates at once."""

    return transition_job_state(state, "canceled", now=now)


def _canceled_state(
    state: JobStateRecord,
    now: datetime,
) -> JobStateRecord:
    candidates = tuple(
        candidate
        if candidate.status in _TERMINAL_CANDIDATE_STATUSES
        else _replace_candidate(
            candidate,
            status="canceled",
            failure=None,
            finished_at=now,
        )
        for candidate in state.candidates
    )
    return _replace_state(
        state,
        status="canceled",
        candidates=candidates,
        failure=None,
        updated_at=now,
        finished_at=now,
    )


def recover_interrupted_state(
    state: JobStateRecord,
    *,
    now: datetime,
) -> JobStateRecord:
    """Convert only interrupted active work into an explicit terminal failure."""

    if state.status not in _ACTIVE_JOB_STATUSES:
        return state

    candidates: list[CandidateRecord] = []
    for candidate in state.candidates:
        if candidate.status in {"generating", "postprocessing"}:
            candidates.append(
                _replace_candidate(
                    candidate,
                    status="failed",
                    failure=_interrupted_failure(candidate.status),
                    finished_at=now,
                )
            )
        elif candidate.status == "pending":
            candidates.append(
                _replace_candidate(
                    candidate,
                    status="canceled",
                    failure=None,
                    finished_at=now,
                )
            )
        else:
            candidates.append(candidate)

    return _replace_state(
        state,
        status="failed",
        candidates=tuple(candidates),
        failure=_interrupted_failure(state.status),
        updated_at=now,
        finished_at=now,
    )


def _replace_candidate(
    candidate: CandidateRecord,
    **updates: object,
) -> CandidateRecord:
    return CandidateRecord.model_validate(
        {
            **candidate.model_dump(),
            **updates,
        }
    )


def _replace_state(
    state: JobStateRecord,
    **updates: object,
) -> JobStateRecord:
    return JobStateRecord.model_validate(
        {
            **state.model_dump(),
            "revision": state.revision + 1,
            **updates,
        }
    )


def _require_failure_for_target(
    target: JobStatus | CandidateStatus,
    failure: JobFailure | None,
) -> None:
    if (target == "failed") != (failure is not None):
        raise _invalid_transition()


def _interrupted_failure(stage: str) -> JobFailure:
    return JobFailure(
        code="JOB_INTERRUPTED",
        stage=stage,
        user_message="任务因应用重启而中断",
        recommended_actions=("重试该任务",),
        technical_details=None,
        log_reference=None,
    )


def _invalid_transition() -> JobError:
    return JobError(
        "INVALID_JOB_TRANSITION",
        "当前任务状态不允许此操作",
    )
