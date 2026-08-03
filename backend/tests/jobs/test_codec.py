from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from aimctexturegen.jobs.codec import (
    dump_durable_request,
    dump_durable_state,
    load_job_request,
    load_job_state,
    validate_durable_pair,
)
from aimctexturegen.jobs.errors import JobError
from aimctexturegen.jobs.models import (
    CandidateRecord,
    JobRequest,
    JobStateRecord,
    ModelProfileBinding,
    dump_job_request,
    dump_job_state,
)
from aimctexturegen.jobs.models_v3 import (
    CandidateArtifacts,
    ExecutionBatch,
    FrozenReferences,
    GenerationAdvanced,
    GenerationCandidateRecord,
    GenerationJobRequest,
    GenerationJobState,
    GenerationModelBinding,
    GenerationTarget,
    StoredArtifact,
)


NOW = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
JOB_ID = UUID("22222222-2222-4222-8222-222222222222")


def legacy_request(schema_version: int = 1) -> JobRequest:
    values = {
        "schema_version": schema_version,
        "job_id": JOB_ID,
        "project_id": PROJECT_ID,
        "retry_of_job_id": None,
        "catalog_id": "java-dev-format-34",
        "target_semantic_id": "minecraft:deepslate",
        "target_display_name": "Deepslate",
        "target_relative_path": "assets/minecraft/textures/block/deepslate.png",
        "prompt": "cold stone",
        "resolution": 16,
        "parallelism": 1,
        "style_references": ("assets/minecraft/textures/block/stone.png",),
        "structure_reference": None,
        "seeds": (11, 22, 33, 44),
        "created_at": NOW,
        "model_profile": None,
        "execution_eligibility": "legacy_unbound",
    }
    if schema_version == 2:
        values["model_profile"] = ModelProfileBinding(
            profile_id="sdxl-mapchip-ipadapter",
            profile_version="1",
            profile_manifest_sha256="aa" * 32,
            runtime_id="comfyui-windows-nvidia",
            runtime_version="0.29.2",
            runtime_manifest_sha256="bb" * 32,
            workflow_kind="text2img",
            workflow_sha256="cc" * 32,
        )
        values["execution_eligibility"] = "bound"
    return JobRequest(**values)


def legacy_state() -> JobStateRecord:
    return JobStateRecord(
        schema_version=1,
        job_id=JOB_ID,
        project_id=PROJECT_ID,
        revision=0,
        status="queued",
        candidates=tuple(
            CandidateRecord(
                candidate_index=index,
                seed=seed,
                status="pending",
                failure=None,
                started_at=None,
                finished_at=None,
            )
            for index, seed in enumerate((11, 22, 33, 44))
        ),
        failure=None,
        created_at=NOW,
        updated_at=NOW,
        started_at=None,
        finished_at=None,
    )


def generation_request() -> GenerationJobRequest:
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
        parallelism=4,
        execution_batches=(ExecutionBatch(batch_index=0, candidate_indices=(0, 1, 2, 3), seed=99),),
        references=FrozenReferences(style=(), structure=()),
        advanced=GenerationAdvanced(
            style_strength=None,
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
            workflow_variant="text2img-no-style",
            workflow_sha256="cc" * 32,
            output_node_id="19",
        ),
        created_at=NOW,
    )


def generation_state() -> GenerationJobState:
    empty = CandidateArtifacts()
    return GenerationJobState(
        schema_version=2,
        job_id=JOB_ID,
        project_id=PROJECT_ID,
        revision=0,
        status="queued",
        batches=(
            {
                "batch_index": 0,
                "candidate_indices": (0, 1, 2, 3),
                "seed": 99,
                "status": "pending",
                "prompt_id": None,
                "sampling_step": None,
                "sampling_maximum": None,
                "raw_artifacts": (),
                "failure": None,
                "started_at": None,
                "finished_at": None,
            },
        ),
        candidates=tuple(
            GenerationCandidateRecord(
                candidate_index=index,
                batch_index=0,
                position_in_batch=index,
                batch_seed=99,
                status="pending",
                artifacts=empty,
                lineage=None,
                failure=None,
                started_at=None,
                finished_at=None,
            )
            for index in range(4)
        ),
        failure=None,
        cancel_requested_at=None,
        created_at=NOW,
        updated_at=NOW,
        started_at=None,
        finished_at=None,
    )


def test_codec_preserves_legacy_schema1_and_schema2_request_bytes():
    for request in (legacy_request(1), legacy_request(2)):
        assert dump_durable_request(request) == dump_job_request(request)


def test_codec_loads_legacy_and_generation_records_without_cross_parsing():
    assert isinstance(load_job_request(dump_job_request(legacy_request())), JobRequest)
    assert isinstance(load_job_state(dump_job_state(legacy_state())), JobStateRecord)
    assert isinstance(
        load_job_request(dump_durable_request(generation_request())),
        GenerationJobRequest,
    )
    assert isinstance(
        load_job_state(dump_durable_state(generation_state())),
        GenerationJobState,
    )


def test_codec_round_trips_generation_records():
    request = generation_request()
    state = generation_state()
    assert load_job_request(dump_durable_request(request)) == request
    assert load_job_state(dump_durable_state(state)) == state


def test_validate_durable_pair_rejects_mixed_record_families():
    with pytest.raises(JobError) as raised:
        validate_durable_pair(legacy_request(), generation_state())
    assert raised.value.code == "INVALID_JOB_RECORD"


def test_validate_durable_pair_accepts_generation_family():
    validate_durable_pair(generation_request(), generation_state())
