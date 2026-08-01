from datetime import datetime, timedelta, timezone
from itertools import product
from uuid import uuid4

import pytest

from aimctexturegen.jobs.errors import JobError
from aimctexturegen.jobs.models import (
    CandidateRecord,
    CandidateStatus,
    JobFailure,
    JobStateRecord,
    JobStatus,
    dump_job_state,
)
from aimctexturegen.jobs.state_machine import (
    cancel_state,
    recover_interrupted_state,
    transition_candidate_state,
    transition_job_state,
)


CREATED = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
NOW = CREATED + timedelta(minutes=5)
SEEDS = (101, 202, 303, 404)
JOB_STATUSES: tuple[JobStatus, ...] = (
    "queued",
    "generating",
    "postprocessing",
    "completed",
    "failed",
    "canceled",
)
CANDIDATE_STATUSES: tuple[CandidateStatus, ...] = (
    "pending",
    "generating",
    "postprocessing",
    "completed",
    "failed",
    "canceled",
)
LEGAL_JOB_EDGES = {
    ("queued", "generating"),
    ("queued", "canceled"),
    ("generating", "postprocessing"),
    ("generating", "failed"),
    ("generating", "canceled"),
    ("postprocessing", "completed"),
    ("postprocessing", "failed"),
    ("postprocessing", "canceled"),
}
LEGAL_CANDIDATE_EDGES = {
    ("pending", "generating"),
    ("pending", "failed"),
    ("pending", "canceled"),
    ("generating", "postprocessing"),
    ("generating", "failed"),
    ("generating", "canceled"),
    ("postprocessing", "completed"),
    ("postprocessing", "failed"),
    ("postprocessing", "canceled"),
}


def _failure(
    *,
    code: str = "GPU_OUT_OF_MEMORY",
    stage: str = "generating",
) -> JobFailure:
    return JobFailure(
        code=code,
        stage=stage,
        user_message="生成失败",
        recommended_actions=("重试任务",),
        technical_details=None,
        log_reference=None,
    )


def _candidate(
    index: int,
    status: CandidateStatus = "pending",
) -> CandidateRecord:
    terminal = status in {"completed", "failed", "canceled"}
    return CandidateRecord(
        candidate_index=index,
        seed=SEEDS[index],
        status=status,
        failure=_failure() if status == "failed" else None,
        started_at=CREATED if status != "pending" else None,
        finished_at=CREATED if terminal else None,
    )


def _state(
    status: JobStatus = "queued",
    *,
    candidates: tuple[CandidateRecord, ...] | None = None,
) -> JobStateRecord:
    terminal = status in {"completed", "failed", "canceled"}
    if candidates is None:
        default_candidate_status = {
            "postprocessing": "completed",
            "completed": "completed",
            "failed": "failed",
            "canceled": "canceled",
        }.get(status, "pending")
        candidates = tuple(
            _candidate(index, default_candidate_status)
            for index in range(4)
        )
    return JobStateRecord.model_validate(
        {
            "schema_version": 1,
            "job_id": uuid4(),
            "project_id": uuid4(),
            "revision": 7,
            "status": status,
            "candidates": candidates,
            "failure": _failure() if status == "failed" else None,
            "created_at": CREATED,
            "updated_at": CREATED,
            "started_at": None if status == "queued" else CREATED,
            "finished_at": CREATED if terminal else None,
        }
    )


LEGAL_JOB_CASES = sorted(LEGAL_JOB_EDGES)
ILLEGAL_JOB_CASES = sorted(set(product(JOB_STATUSES, repeat=2)) - LEGAL_JOB_EDGES)


@pytest.mark.parametrize(("source", "target"), LEGAL_JOB_CASES)
def test_transition_job_state_accepts_every_legal_edge_once(
    source: JobStatus,
    target: JobStatus,
) -> None:
    state = _state(source)
    before = dump_job_state(state)
    failure = _failure() if target == "failed" else None

    result = transition_job_state(state, target, now=NOW, failure=failure)

    assert result.status == target
    assert result.failure == failure
    assert result.revision == state.revision + 1
    assert result.updated_at == NOW
    if target == "canceled":
        for source_candidate, result_candidate in zip(
            state.candidates,
            result.candidates,
            strict=True,
        ):
            if source_candidate.status in {
                "pending",
                "generating",
                "postprocessing",
            }:
                assert result_candidate.status == "canceled"
            else:
                assert result_candidate == source_candidate
    assert dump_job_state(state) == before


@pytest.mark.parametrize(("source", "target"), ILLEGAL_JOB_CASES)
def test_transition_job_state_rejects_every_illegal_edge_without_mutation(
    source: JobStatus,
    target: JobStatus,
) -> None:
    state = _state(source)
    before = dump_job_state(state)

    with pytest.raises(JobError) as error:
        transition_job_state(
            state,
            target,
            now=NOW,
            failure=_failure() if target == "failed" else None,
        )

    assert error.value.code == "INVALID_JOB_TRANSITION"
    assert dump_job_state(state) == before


def test_job_started_at_is_set_once_and_terminal_transitions_finish() -> None:
    queued = _state("queued")
    generating = transition_job_state(queued, "generating", now=NOW)
    later = NOW + timedelta(minutes=1)
    candidates_completed = generating
    for candidate_index in range(4):
        candidates_completed = transition_candidate_state(
            candidates_completed,
            candidate_index,
            "generating",
            now=later,
        )
        candidates_completed = transition_candidate_state(
            candidates_completed,
            candidate_index,
            "postprocessing",
            now=later,
        )
        candidates_completed = transition_candidate_state(
            candidates_completed,
            candidate_index,
            "completed",
            now=later,
        )
    postprocessing = transition_job_state(
        candidates_completed,
        "postprocessing",
        now=later,
    )
    finished = transition_job_state(
        postprocessing,
        "completed",
        now=later + timedelta(minutes=1),
    )

    assert generating.started_at == NOW
    assert postprocessing.started_at == NOW
    assert finished.started_at == NOW
    assert generating.finished_at is None
    assert postprocessing.finished_at is None
    assert finished.finished_at == later + timedelta(minutes=1)


@pytest.mark.parametrize("status", ["generating", "postprocessing"])
def test_failed_job_transition_terminalizes_nonterminal_candidates(
    status: JobStatus,
) -> None:
    candidates = (
        _candidate(0, "pending"),
        _candidate(1, "generating"),
        _candidate(2, "completed"),
        _candidate(3, "canceled"),
    )
    state = _state(status, candidates=candidates)
    failure = _failure()

    result = transition_job_state(
        state,
        "failed",
        now=NOW,
        failure=failure,
    )

    assert tuple(candidate.status for candidate in result.candidates) == (
        "canceled",
        "failed",
        "completed",
        "canceled",
    )
    assert result.candidates[0].finished_at == NOW
    assert result.candidates[1].failure == failure
    assert result.candidates[2:] == state.candidates[2:]
    assert result.revision == state.revision + 1


def test_completed_job_transition_requires_every_candidate_completed() -> None:
    candidates = (
        _candidate(0, "completed"),
        _candidate(1, "completed"),
        _candidate(2, "completed"),
        _candidate(3, "pending"),
    )
    state = _state("postprocessing", candidates=candidates)

    with pytest.raises(JobError) as error:
        transition_job_state(state, "completed", now=NOW)

    assert error.value.code == "INVALID_JOB_TRANSITION"


def test_job_failure_is_required_only_when_transitioning_to_failed() -> None:
    generating = _state("generating")
    with pytest.raises(JobError) as missing:
        transition_job_state(generating, "failed", now=NOW)
    assert missing.value.code == "INVALID_JOB_TRANSITION"

    with pytest.raises(JobError) as unexpected:
        transition_job_state(
            generating,
            "postprocessing",
            now=NOW,
            failure=_failure(),
        )
    assert unexpected.value.code == "INVALID_JOB_TRANSITION"


LEGAL_CANDIDATE_CASES = sorted(LEGAL_CANDIDATE_EDGES)
ILLEGAL_CANDIDATE_CASES = sorted(
    set(product(CANDIDATE_STATUSES, repeat=2)) - LEGAL_CANDIDATE_EDGES
)
AGGREGATE_CANDIDATE_CASES = [
    *[
        ("queued", source, target, False)
        for source, target in LEGAL_CANDIDATE_CASES
        if source == "pending"
    ],
    *[
        (aggregate_status, source, target, True)
        for aggregate_status in ("generating", "postprocessing")
        for source, target in LEGAL_CANDIDATE_CASES
    ],
]


@pytest.mark.parametrize(("source", "target"), LEGAL_CANDIDATE_CASES)
def test_transition_candidate_state_accepts_every_legal_edge_once(
    source: CandidateStatus,
    target: CandidateStatus,
) -> None:
    candidates = tuple(
        _candidate(index, source if index == 2 else "pending")
        for index in range(4)
    )
    state = _state("generating", candidates=candidates)
    failure = _failure() if target == "failed" else None

    result = transition_candidate_state(
        state,
        2,
        target,
        now=NOW,
        failure=failure,
    )

    assert result.revision == state.revision + 1
    assert result.updated_at == NOW
    assert result.status == state.status
    assert result.candidates[:2] == state.candidates[:2]
    assert result.candidates[3] == state.candidates[3]
    assert result.candidates[2].status == target
    assert result.candidates[2].failure == failure


@pytest.mark.parametrize(("source", "target"), ILLEGAL_CANDIDATE_CASES)
def test_transition_candidate_state_rejects_every_illegal_edge_without_mutation(
    source: CandidateStatus,
    target: CandidateStatus,
) -> None:
    candidates = tuple(
        _candidate(index, source if index == 1 else "pending")
        for index in range(4)
    )
    state = _state("generating", candidates=candidates)
    before = dump_job_state(state)

    with pytest.raises(JobError) as error:
        transition_candidate_state(
            state,
            1,
            target,
            now=NOW,
            failure=_failure() if target == "failed" else None,
        )

    assert error.value.code == "INVALID_JOB_TRANSITION"
    assert dump_job_state(state) == before


def test_candidate_started_at_is_set_once_and_terminal_transition_finishes() -> None:
    state = _state("generating")
    generating = transition_candidate_state(
        state,
        0,
        "generating",
        now=NOW,
    )
    later = NOW + timedelta(minutes=1)
    postprocessing = transition_candidate_state(
        generating,
        0,
        "postprocessing",
        now=later,
    )
    completed = transition_candidate_state(
        postprocessing,
        0,
        "completed",
        now=later + timedelta(minutes=1),
    )

    assert generating.candidates[0].started_at == NOW
    assert postprocessing.candidates[0].started_at == NOW
    assert completed.candidates[0].started_at == NOW
    assert generating.candidates[0].finished_at is None
    assert completed.candidates[0].finished_at == later + timedelta(minutes=1)


def test_candidate_failure_and_index_are_validated_before_update() -> None:
    state = _state("generating")
    for candidate_index, target, failure in (
        (4, "generating", None),
        (0, "failed", None),
        (0, "generating", _failure()),
    ):
        with pytest.raises(JobError) as error:
            transition_candidate_state(
                state,
                candidate_index,
                target,
                now=NOW,
                failure=failure,
            )
        assert error.value.code == "INVALID_JOB_TRANSITION"


@pytest.mark.parametrize(
    ("aggregate_status", "candidate_source", "candidate_target", "allowed"),
    AGGREGATE_CANDIDATE_CASES,
)
def test_candidate_transition_respects_aggregate_lifecycle_matrix(
    aggregate_status: JobStatus,
    candidate_source: CandidateStatus,
    candidate_target: CandidateStatus,
    allowed: bool,
) -> None:
    state = _state(
        aggregate_status,
        candidates=tuple(
            _candidate(index, candidate_source if index == 0 else "pending")
            for index in range(4)
        ),
    )
    failure = _failure() if candidate_target == "failed" else None

    if allowed:
        result = transition_candidate_state(
            state,
            0,
            candidate_target,
            now=NOW,
            failure=failure,
        )
        assert result.candidates[0].status == candidate_target
        return

    before = dump_job_state(state)
    with pytest.raises(JobError) as error:
        transition_candidate_state(
            state,
            0,
            candidate_target,
            now=NOW,
            failure=failure,
        )
    assert error.value.code == "INVALID_JOB_TRANSITION"
    assert dump_job_state(state) == before


def test_public_transitions_cannot_create_a_queued_active_candidate_for_recovery() -> None:
    queued = _state("queued")
    before = dump_job_state(queued)

    with pytest.raises(JobError) as error:
        transition_candidate_state(
            queued,
            0,
            "generating",
            now=NOW,
        )

    assert error.value.code == "INVALID_JOB_TRANSITION"
    assert recover_interrupted_state(queued, now=NOW) is queued
    assert dump_job_state(queued) == before
    assert all(candidate.status == "pending" for candidate in queued.candidates)


@pytest.mark.parametrize("status", ["completed", "failed", "canceled"])
def test_candidate_transition_rejects_terminal_aggregate_state(
    status: JobStatus,
) -> None:
    state = _state(status)
    before = dump_job_state(state)

    with pytest.raises(JobError) as error:
        transition_candidate_state(
            state,
            0,
            "generating",
            now=NOW,
        )

    assert error.value.code == "INVALID_JOB_TRANSITION"
    assert dump_job_state(state) == before


@pytest.mark.parametrize("status", ["queued", "generating", "postprocessing"])
def test_cancel_changes_all_nonterminal_candidates_in_one_revision(
    status: JobStatus,
) -> None:
    candidates = (
        tuple(_candidate(index, "pending") for index in range(4))
        if status == "queued"
        else (
            _candidate(0, "pending"),
            _candidate(1, "generating"),
            _candidate(2, "completed"),
            _candidate(3, "failed"),
        )
    )
    state = _state(status, candidates=candidates)

    result = cancel_state(state, now=NOW)

    assert result.status == "canceled"
    assert result.failure is None
    assert result.finished_at == NOW
    assert result.updated_at == NOW
    assert result.revision == state.revision + 1
    expected_statuses = (
        ("canceled",) * 4
        if status == "queued"
        else ("canceled", "canceled", "completed", "failed")
    )
    assert (
        tuple(candidate.status for candidate in result.candidates)
        == expected_statuses
    )
    if status != "queued":
        assert result.candidates[2:] == state.candidates[2:]
    assert result.candidates[0].finished_at == NOW
    assert result.candidates[1].finished_at == NOW


@pytest.mark.parametrize("status", ["completed", "failed", "canceled"])
def test_cancel_rejects_terminal_jobs_without_mutation(status: JobStatus) -> None:
    state = _state(status)
    before = dump_job_state(state)

    with pytest.raises(JobError) as error:
        cancel_state(state, now=NOW)

    assert error.value.code == "INVALID_JOB_TRANSITION"
    assert dump_job_state(state) == before


@pytest.mark.parametrize("status", ["queued", "completed", "failed", "canceled"])
def test_recovery_leaves_queued_and_terminal_states_byte_equivalent(
    status: JobStatus,
) -> None:
    state = _state(status)
    before = dump_job_state(state)

    result = recover_interrupted_state(state, now=NOW)

    assert result is state
    assert dump_job_state(result) == before


@pytest.mark.parametrize("status", ["generating", "postprocessing"])
def test_recovery_fails_active_jobs_and_classifies_nonterminal_candidates(
    status: JobStatus,
) -> None:
    candidates = (
        _candidate(0, "completed"),
        _candidate(1, "generating"),
        _candidate(2, "postprocessing"),
        _candidate(3, "pending"),
    )
    state = _state(status, candidates=candidates)

    result = recover_interrupted_state(state, now=NOW)

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.code == "JOB_INTERRUPTED"
    assert result.failure.stage == status
    assert result.revision == state.revision + 1
    assert result.updated_at == NOW
    assert result.finished_at == NOW
    assert tuple(candidate.status for candidate in result.candidates) == (
        "completed",
        "failed",
        "failed",
        "canceled",
    )
    assert result.candidates[0] == state.candidates[0]
    assert result.candidates[1].failure is not None
    assert result.candidates[1].failure.code == "JOB_INTERRUPTED"
    assert result.candidates[2].failure is not None
    assert result.candidates[2].failure.code == "JOB_INTERRUPTED"
    assert result.candidates[3].failure is None
    assert all(
        candidate.finished_at == NOW for candidate in result.candidates[1:]
    )


def test_recovery_preserves_existing_terminal_candidate_records() -> None:
    candidates = (
        _candidate(0, "completed"),
        _candidate(1, "failed"),
        _candidate(2, "canceled"),
        _candidate(3, "pending"),
    )
    state = _state("generating", candidates=candidates)

    result = recover_interrupted_state(state, now=NOW)

    assert result.candidates[:3] == state.candidates[:3]
    assert result.candidates[3].status == "canceled"
