from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from aimctexturegen.jobs.errors import JobError
from aimctexturegen.jobs.generation_state import (
    complete_candidate,
    complete_generation,
    confirm_canceled,
    fail_generation,
    mark_batch_raw_ready,
    record_progress,
    recover_generation_interruption,
    request_cancel,
    start_batch,
    start_generation,
)
from aimctexturegen.jobs.models_v3 import (
    CandidateArtifacts,
    CandidateLineage,
    ExecutionBatch,
    FrozenReferences,
    GenerationAdvanced,
    GenerationCandidateRecord,
    GenerationFailure,
    GenerationJobRequest,
    GenerationJobState,
    GenerationModelBinding,
    GenerationTarget,
    StoredArtifact,
)


NOW = datetime(2026, 8, 3, 11, 0, tzinfo=timezone.utc)
PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
JOB_ID = UUID("22222222-2222-4222-8222-222222222222")


def artifact(path: str, *, kind: str = "raw") -> StoredArtifact:
    return StoredArtifact(
        kind=kind,
        relative_path=path,
        sha256="ab" * 32,
        byte_size=16,
        media_type="image/png" if kind != "report" else "application/json",
        width=16 if kind != "report" else None,
        height=16 if kind != "report" else None,
    )


def request() -> GenerationJobRequest:
    return GenerationJobRequest(
        schema_version=3,
        job_id=JOB_ID,
        project_id=PROJECT_ID,
        parent_job_id=None,
        target=GenerationTarget(
            catalog_id="java-dev-format-34",
            target_semantic_id="minecraft:deepslate",
            target_display_name="Deepslate",
            target_relative_path="assets/minecraft/textures/block/deepslate.png",
        ),
        prompt={
            "prompt_version": "java-block-prompt-v1",
            "positive_prompt": "pixel art stone",
            "negative_prompt": "text watermark",
            "user_prompt": "cold stone",
        },
        resolution=16,
        parallelism=2,
        execution_batches=(
            ExecutionBatch(batch_index=0, candidate_indices=(0, 1), seed=100),
            ExecutionBatch(batch_index=1, candidate_indices=(2, 3), seed=101),
        ),
        references=FrozenReferences(style=(artifact("inputs/style/00.png"),), structure=()),
        advanced=GenerationAdvanced(
            style_strength=0.7,
            denoise_strength=None,
            lora_weight=1.0,
        ),
        model_profile=GenerationModelBinding(
            profile_id="sdxl-mapchip-ipadapter",
            profile_version="2",
            profile_manifest_sha256="aa" * 32,
            runtime_id="comfyui-windows-nvidia",
            runtime_version="0.29.2",
            runtime_manifest_sha256="bb" * 32,
            workflow_variant="text2img-style",
            workflow_sha256="cc" * 32,
            output_node_id="19",
        ),
        created_at=NOW,
    )


def state() -> GenerationJobState:
    return GenerationJobState.initial_from_request(request())


def complete_artifacts(index: int, batch_seed: int) -> CandidateArtifacts:
    return CandidateArtifacts(
        raw=artifact(f"raw/batch-{batch_seed}-{index}.png"),
        final=artifact(f"processed/{index}.png", kind="final"),
        nearest=artifact(f"previews/{index}-nearest.png", kind="nearest"),
        tile=artifact(f"previews/{index}-tile.png", kind="tile"),
        report=artifact(f"reports/{index}.json", kind="report"),
    )


def failure(code: str = "GPU_OUT_OF_MEMORY") -> GenerationFailure:
    return GenerationFailure(
        error_code=code,
        stage="generating",
        user_message="生成失败",
        recommended_actions=("降低并行数",),
        technical_details=None,
        retryable=True,
        occurred_at=NOW,
    )


def test_generation_state_happy_path_increments_one_revision_per_transition():
    current = start_generation(state(), now=NOW + timedelta(seconds=1))
    assert current.revision == 1
    current = start_batch(current, 0, now=NOW + timedelta(seconds=2))
    assert current.revision == 2
    current = record_progress(current, 0, 3, 10, now=NOW + timedelta(seconds=3))
    assert current.revision == 3
    current = mark_batch_raw_ready(
        current,
        0,
        artifacts=(artifact("raw/batch-0-0.png"), artifact("raw/batch-0-1.png")),
        now=NOW + timedelta(seconds=4),
    )
    assert current.revision == 4
    current = complete_candidate(
        current,
        0,
        complete_artifacts(0, 100),
        now=NOW + timedelta(seconds=5),
    )
    assert current.revision == 5


def test_request_cancel_is_idempotent_after_first_timestamp():
    current = request_cancel(start_generation(state(), now=NOW), now=NOW + timedelta(seconds=1))
    again = request_cancel(current, now=NOW + timedelta(seconds=2))
    assert again == current


def test_confirm_canceled_preserves_completed_and_inherited_candidates():
    current = start_generation(state(), now=NOW)
    current = start_batch(current, 0, now=NOW + timedelta(seconds=1))
    current = mark_batch_raw_ready(
        current,
        0,
        artifacts=(artifact("raw/0.png"), artifact("raw/1.png")),
        now=NOW + timedelta(seconds=2),
    )
    current = complete_candidate(
        current,
        0,
        complete_artifacts(0, 100),
        now=NOW + timedelta(seconds=3),
    )
    inherited = current.candidates[1].model_copy(
        update={
            "status": "inherited",
            "artifacts": complete_artifacts(1, 100),
            "lineage": CandidateLineage(
                parent_job_id=uuid4(),
                parent_candidate_index=1,
            ),
            "started_at": NOW,
            "finished_at": NOW + timedelta(seconds=3),
        }
    )
    current = current.model_copy(
        update={
            "candidates": (
                current.candidates[0],
                inherited,
                current.candidates[2],
                current.candidates[3],
            )
        }
    )
    current = request_cancel(current, now=NOW + timedelta(seconds=4))
    canceled = confirm_canceled(current, now=NOW + timedelta(seconds=5))
    assert canceled.status == "canceled"
    assert canceled.candidates[0].status == "completed"
    assert canceled.candidates[1].status == "inherited"
    assert {c.status for c in canceled.candidates[2:]} == {"canceled"}


def test_confirm_canceled_requires_cancel_request():
    with pytest.raises(JobError):
        confirm_canceled(state(), now=NOW)


def test_cancel_confirmation_failure_stays_nonterminal_with_state_failure():
    current = request_cancel(start_generation(state(), now=NOW), now=NOW + timedelta(seconds=1))
    failed = fail_generation(
        current,
        failure("CANCEL_CONFIRMATION_FAILED"),
        now=NOW + timedelta(seconds=2),
    )
    assert failed.status == "generating"
    assert failed.failure is not None
    assert failed.failure.error_code == "CANCEL_CONFIRMATION_FAILED"
    assert failed.finished_at is None


def test_recover_generation_interruption_fails_active_and_preserves_completed():
    current = start_generation(state(), now=NOW)
    current = start_batch(current, 0, now=NOW + timedelta(seconds=1))
    current = mark_batch_raw_ready(
        current,
        0,
        artifacts=(artifact("raw/0.png"), artifact("raw/1.png")),
        now=NOW + timedelta(seconds=2),
    )
    current = complete_candidate(
        current,
        0,
        complete_artifacts(0, 100),
        now=NOW + timedelta(seconds=3),
    )
    recovered = recover_generation_interruption(current, now=NOW + timedelta(seconds=4))
    assert recovered.status == "failed"
    assert recovered.failure is not None
    assert recovered.failure.error_code == "JOB_INTERRUPTED"
    assert recovered.candidates[0].status == "completed"
    assert recovered.candidates[1].status == "failed"
