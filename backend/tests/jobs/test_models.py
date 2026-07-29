import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from aimctexturegen.jobs.errors import JobError
from aimctexturegen.jobs.models import (
    CandidateRecord,
    CreateJobCommand,
    JobFailure,
    JobRequest,
    JobStateRecord,
    JobSummary,
    dump_job_request,
    dump_job_state,
    validate_job_pair,
)


NOW = datetime(2026, 7, 29, 9, 30, tzinfo=timezone.utc)
SEEDS = (11, 22, 33, 44)


def _request(**updates: object) -> JobRequest:
    values = {
        "schema_version": 1,
        "job_id": uuid4(),
        "project_id": uuid4(),
        "retry_of_job_id": None,
        "catalog_id": "java-dev-format-34",
        "target_semantic_id": "minecraft:deepslate",
        "target_display_name": "Deepslate",
        "target_relative_path": (
            "assets/minecraft/textures/block/deepslate.png"
        ),
        "prompt": "cold blue-gray stone",
        "resolution": 16,
        "parallelism": 1,
        "style_references": (
            "assets/minecraft/textures/block/stone.png",
        ),
        "structure_reference": None,
        "seeds": SEEDS,
        "created_at": NOW,
        **updates,
    }
    return JobRequest.model_validate(values)


def _candidate(
    index: int,
    *,
    seed: int | None = None,
    status: str = "pending",
    failure: JobFailure | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> CandidateRecord:
    candidate_seed = (
        SEEDS[index]
        if seed is None and 0 <= index < len(SEEDS)
        else 55 if seed is None else seed
    )
    return CandidateRecord.model_validate(
        {
            "candidate_index": index,
            "seed": candidate_seed,
            "status": status,
            "failure": failure,
            "started_at": started_at,
            "finished_at": finished_at,
        }
    )


def _state(
    request: JobRequest,
    **updates: object,
) -> JobStateRecord:
    values = {
        "schema_version": 1,
        "job_id": request.job_id,
        "project_id": request.project_id,
        "revision": 0,
        "status": "queued",
        "candidates": tuple(_candidate(index) for index in range(4)),
        "failure": None,
        "created_at": NOW,
        "updated_at": NOW,
        "started_at": None,
        "finished_at": None,
        **updates,
    }
    return JobStateRecord.model_validate(values)


def _failure() -> JobFailure:
    return JobFailure(
        code="GPU_OUT_OF_MEMORY",
        stage="generating",
        user_message="生成失败：显卡显存不足",
        recommended_actions=("降低候选并行数",),
        technical_details="allocation failed",
        log_reference="logs/job.log",
    )


def _candidate_values(index: int, status: str) -> dict[str, object]:
    active = status in {"generating", "postprocessing"}
    terminal = status in {"completed", "failed", "canceled"}
    return {
        "candidate_index": index,
        "seed": SEEDS[index],
        "status": status,
        "failure": (
            _failure().model_dump()
            if status == "failed"
            else None
        ),
        "started_at": NOW if active or status == "completed" else None,
        "finished_at": NOW if terminal else None,
    }


def _state_values(
    request: JobRequest,
    status: str,
    candidate_statuses: tuple[str, str, str, str],
) -> dict[str, object]:
    terminal = status in {"completed", "failed", "canceled"}
    return {
        "schema_version": 1,
        "job_id": request.job_id,
        "project_id": request.project_id,
        "revision": 0,
        "status": status,
        "candidates": tuple(
            _candidate_values(index, candidate_status)
            for index, candidate_status in enumerate(candidate_statuses)
        ),
        "failure": (
            _failure().model_dump()
            if status == "failed"
            else None
        ),
        "created_at": NOW,
        "updated_at": NOW,
        "started_at": None if status in {"queued", "canceled"} else NOW,
        "finished_at": NOW if terminal else None,
    }


def _json_bytes(values: dict[str, object]) -> bytes:
    return json.dumps(
        values,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def test_job_request_accepts_the_locked_four_candidate_contract() -> None:
    request = _request()

    assert request.schema_version == 1
    assert request.resolution == 16
    assert request.parallelism == 1
    assert request.seeds == SEEDS
    assert request.style_references == (
        "assets/minecraft/textures/block/stone.png",
    )


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("schema_version", "1"),
        ("job_id", str(uuid4())),
        ("resolution", "16"),
        ("resolution", 48),
        ("parallelism", "1"),
        ("parallelism", 3),
        ("seeds", [11, 22, 33, 44]),
        ("seeds", (True, 22, 33, 44)),
        ("style_references", ["assets/minecraft/textures/block/stone.png"]),
    ],
)
def test_job_request_rejects_coercion_and_out_of_contract_values(
    field: str,
    invalid: object,
) -> None:
    values = _request().model_dump()
    values[field] = invalid

    with pytest.raises(ValidationError):
        JobRequest.model_validate(values)


@pytest.mark.parametrize(
    "seeds",
    [
        (11, 11, 33, 44),
        (-1, 22, 33, 44),
        (11, 22, 33, 9_007_199_254_740_992),
        (11, 22, 33),
        (11, 22, 33, 44, 55),
    ],
)
def test_job_request_rejects_nonunique_out_of_range_or_nonfour_seeds(
    seeds: tuple[int, ...],
) -> None:
    with pytest.raises(ValidationError):
        _request(seeds=seeds)


@pytest.mark.parametrize("prompt", ["", "   ", "x" * 4001])
def test_job_request_rejects_empty_or_overlong_prompt(prompt: str) -> None:
    with pytest.raises(ValidationError):
        _request(prompt=prompt)


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("target_relative_path", "../escape.png"),
        ("target_relative_path", r"assets\block.png"),
        ("style_references", ()),
        (
            "style_references",
            tuple(f"assets/minecraft/textures/block/{index}.png" for index in range(9)),
        ),
        ("style_references", ("C:/drive.png",)),
        ("structure_reference", "/absolute.png"),
    ],
)
def test_job_request_rejects_invalid_reference_contracts(
    field: str,
    invalid: object,
) -> None:
    with pytest.raises(ValidationError):
        _request(**{field: invalid})


def test_create_command_has_only_user_supplied_fields_and_shared_validation() -> None:
    command = CreateJobCommand(
        target_semantic_id="minecraft:deepslate",
        prompt="cold blue-gray stone",
        resolution=32,
        parallelism=2,
        style_references=("assets/minecraft/textures/block/stone.png",),
        structure_reference="uploads/structure-references/layout.png",
    )

    assert set(command.model_dump()) == {
        "target_semantic_id",
        "prompt",
        "resolution",
        "parallelism",
        "style_references",
        "structure_reference",
    }
    with pytest.raises(ValidationError):
        CreateJobCommand.model_validate(
            {
                **command.model_dump(),
                "job_id": uuid4(),
            }
        )
    with pytest.raises(ValidationError):
        CreateJobCommand.model_validate(
            {
                **command.model_dump(),
                "style_references": ("../escape.png",),
            }
        )


def test_candidate_and_state_require_ordered_four_candidate_records() -> None:
    request = _request()
    with pytest.raises(ValidationError):
        _candidate(4)

    candidates = tuple(_candidate(index) for index in (1, 0, 2, 3))
    with pytest.raises(ValidationError):
        _state(request, candidates=candidates)


@pytest.mark.parametrize(
    ("status", "failure"),
    [
        ("failed", None),
        ("pending", _failure()),
        ("completed", _failure()),
    ],
)
def test_candidate_failure_is_present_only_for_failed_status(
    status: str,
    failure: JobFailure | None,
) -> None:
    with pytest.raises(ValidationError):
        _candidate(0, status=status, failure=failure)


@pytest.mark.parametrize(
    ("status", "failure"),
    [
        ("failed", None),
        ("queued", _failure()),
        ("completed", _failure()),
    ],
)
def test_job_failure_is_present_only_for_failed_status(
    status: str,
    failure: JobFailure | None,
) -> None:
    request = _request()
    with pytest.raises(ValidationError):
        _state(request, status=status, failure=failure)


@pytest.mark.parametrize(
    ("status", "updates"),
    [
        ("pending", {"started_at": NOW}),
        ("pending", {"finished_at": NOW}),
        ("generating", {"started_at": None}),
        ("generating", {"finished_at": NOW}),
        ("postprocessing", {"started_at": None}),
        ("postprocessing", {"finished_at": NOW}),
        ("completed", {"started_at": None}),
        ("completed", {"finished_at": None}),
        ("failed", {"finished_at": None}),
        ("canceled", {"finished_at": None}),
        (
            "completed",
            {
                "started_at": NOW,
                "finished_at": datetime(2026, 7, 29, 9, 29, tzinfo=timezone.utc),
            },
        ),
    ],
)
def test_candidate_rejects_status_timestamp_incoherence(
    status: str,
    updates: dict[str, object],
) -> None:
    values = {
        **_candidate_values(0, status),
        **updates,
    }

    with pytest.raises(ValidationError):
        CandidateRecord.model_validate(values)


@pytest.mark.parametrize(
    ("status", "candidate_statuses", "updates"),
    [
        ("queued", ("pending",) * 4, {"started_at": NOW}),
        ("queued", ("pending",) * 4, {"finished_at": NOW}),
        ("generating", ("pending",) * 4, {"started_at": None}),
        ("generating", ("pending",) * 4, {"finished_at": NOW}),
        ("postprocessing", ("postprocessing",) * 4, {"started_at": None}),
        ("completed", ("completed",) * 4, {"finished_at": None}),
        (
            "failed",
            ("completed", "failed", "canceled", "canceled"),
            {"finished_at": None},
        ),
        (
            "canceled",
            ("completed", "failed", "canceled", "canceled"),
            {"finished_at": None},
        ),
        (
            "queued",
            ("pending",) * 4,
            {"updated_at": datetime(2026, 7, 29, 9, 29, tzinfo=timezone.utc)},
        ),
        (
            "generating",
            ("pending",) * 4,
            {"started_at": datetime(2026, 7, 29, 9, 31, tzinfo=timezone.utc)},
        ),
        (
            "completed",
            ("completed",) * 4,
            {
                "started_at": NOW,
                "finished_at": datetime(2026, 7, 29, 9, 29, tzinfo=timezone.utc),
            },
        ),
    ],
)
def test_job_state_rejects_status_and_timestamp_incoherence(
    status: str,
    candidate_statuses: tuple[str, str, str, str],
    updates: dict[str, object],
) -> None:
    request = _request()
    values = {
        **_state_values(request, status, candidate_statuses),
        **updates,
    }

    with pytest.raises(ValidationError):
        JobStateRecord.model_validate(values)


@pytest.mark.parametrize(
    ("status", "candidate_statuses"),
    [
        ("queued", ("generating", "pending", "pending", "pending")),
        ("completed", ("completed", "completed", "completed", "pending")),
        ("failed", ("completed", "failed", "canceled", "generating")),
        ("canceled", ("completed", "failed", "canceled", "pending")),
    ],
)
def test_strict_json_rejects_aggregate_candidate_lifecycle_mismatch(
    status: str,
    candidate_statuses: tuple[str, str, str, str],
) -> None:
    request = _request()
    payload = _json_bytes(
        _state_values(request, status, candidate_statuses)
    )

    with pytest.raises(ValidationError):
        JobStateRecord.model_validate_json(payload, strict=True)


@pytest.mark.parametrize(
    ("status", "candidate_statuses", "candidate_index", "field", "timestamp"),
    [
        (
            "generating",
            ("generating", "pending", "pending", "pending"),
            0,
            "started_at",
            datetime(2026, 7, 29, 9, 29, tzinfo=timezone.utc),
        ),
        (
            "canceled",
            ("completed", "failed", "canceled", "canceled"),
            2,
            "finished_at",
            datetime(2026, 7, 29, 9, 31, tzinfo=timezone.utc),
        ),
    ],
)
def test_job_state_rejects_candidate_timestamps_outside_job_lifetime(
    status: str,
    candidate_statuses: tuple[str, str, str, str],
    candidate_index: int,
    field: str,
    timestamp: datetime,
) -> None:
    request = _request()
    values = _state_values(request, status, candidate_statuses)
    candidates = list(values["candidates"])
    candidates[candidate_index] = {
        **candidates[candidate_index],
        field: timestamp,
    }
    values["candidates"] = tuple(candidates)

    with pytest.raises(ValidationError):
        JobStateRecord.model_validate(values)


@pytest.mark.parametrize(
    ("status", "candidate_statuses"),
    [
        ("queued", ("pending",) * 4),
        ("generating", ("pending", "generating", "completed", "failed")),
        (
            "postprocessing",
            ("postprocessing", "completed", "failed", "canceled"),
        ),
        ("completed", ("completed",) * 4),
        ("failed", ("completed", "failed", "canceled", "canceled")),
        ("canceled", ("completed", "failed", "canceled", "canceled")),
    ],
)
def test_job_state_accepts_coherent_lifecycle_records(
    status: str,
    candidate_statuses: tuple[str, str, str, str],
) -> None:
    request = _request()

    state = JobStateRecord.model_validate(
        _state_values(request, status, candidate_statuses)
    )

    assert state.status == status
    assert tuple(candidate.status for candidate in state.candidates) == (
        candidate_statuses
    )


def test_models_reject_naive_timestamps_unknown_fields_and_mutation() -> None:
    with pytest.raises(ValidationError):
        _request(created_at=NOW.replace(tzinfo=None))

    request = _request()
    values = _state(request).model_dump()
    values["updated_at"] = NOW.replace(tzinfo=None)
    with pytest.raises(ValidationError):
        JobStateRecord.model_validate(values)

    values = request.model_dump()
    values["unexpected"] = True
    with pytest.raises(ValidationError):
        JobRequest.model_validate(values)

    with pytest.raises(ValidationError):
        request.prompt = "mutated"


@pytest.mark.parametrize("mismatch", ["job_id", "project_id", "seed"])
def test_validate_job_pair_rejects_cross_file_identity_or_seed_mismatch(
    mismatch: str,
) -> None:
    request = _request()
    state = _state(request)
    values = state.model_dump()
    if mismatch == "seed":
        candidates = list(state.candidates)
        candidates[2] = _candidate(2, seed=999)
        values["candidates"] = tuple(candidates)
    else:
        values[mismatch] = uuid4()
    mismatched = JobStateRecord.model_validate(values)

    with pytest.raises(JobError) as error:
        validate_job_pair(request, mismatched)

    assert error.value.code == "INVALID_JOB_RECORD"


def test_validate_job_pair_accepts_matching_request_and_state() -> None:
    request = _request()
    validate_job_pair(request, _state(request))


def test_job_dumps_are_sorted_compact_utf8_and_round_trip_strictly() -> None:
    request = _request(target_display_name="深板岩")
    state = _state(request)

    request_payload = dump_job_request(request)
    state_payload = dump_job_state(state)

    for payload in (request_payload, state_payload):
        text = payload.decode("utf-8")
        assert text.endswith("\n") and not text.endswith("\n\n")
        assert "\n" not in text[:-1]
        assert ": " not in text and ", " not in text
        assert json.loads(payload)
    assert request_payload.index(b'"catalog_id"') < request_payload.index(
        b'"created_at"'
    )
    assert JobRequest.model_validate_json(request_payload, strict=True) == request
    assert JobStateRecord.model_validate_json(state_payload, strict=True) == state


def test_job_summary_excludes_prompt_paths_seeds_and_failure_details() -> None:
    request = _request()
    state = _state(request)
    summary = JobSummary(
        job_id=request.job_id,
        project_id=request.project_id,
        retry_of_job_id=request.retry_of_job_id,
        target_semantic_id=request.target_semantic_id,
        target_display_name=request.target_display_name,
        resolution=request.resolution,
        parallelism=request.parallelism,
        status=state.status,
        revision=state.revision,
        candidate_statuses=tuple(
            candidate.status for candidate in state.candidates
        ),
        created_at=state.created_at,
        updated_at=state.updated_at,
    )

    assert set(summary.model_dump()) == {
        "job_id",
        "project_id",
        "retry_of_job_id",
        "target_semantic_id",
        "target_display_name",
        "resolution",
        "parallelism",
        "status",
        "revision",
        "candidate_statuses",
        "created_at",
        "updated_at",
    }
    assert all(
        forbidden not in summary.model_dump()
        for forbidden in (
            "prompt",
            "target_relative_path",
            "style_references",
            "structure_reference",
            "seeds",
            "failure",
        )
    )
