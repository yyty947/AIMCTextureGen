from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from aimctexturegen.jobs.models import MAX_SAFE_SEED
from aimctexturegen.jobs.models_v3 import (
    CandidateArtifacts,
    CandidateLineage,
    CompiledPrompt,
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


NOW = datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc)
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


def refs(*, styles: int = 1, structures: int = 0) -> FrozenReferences:
    return FrozenReferences(
        style=tuple(
            artifact(f"inputs/style/{index:02d}.png")
            for index in range(styles)
        ),
        structure=tuple(
            artifact("inputs/structure.png")
            for _ in range(structures)
        ),
    )


def binding(variant: str = "text2img-style") -> GenerationModelBinding:
    return GenerationModelBinding(
        profile_id="sdxl-mapchip-ipadapter",
        profile_version="2",
        profile_manifest_sha256="aa" * 32,
        runtime_id="comfyui-windows-nvidia",
        runtime_version="0.29.2",
        runtime_manifest_sha256="bb" * 32,
        workflow_variant=variant,
        workflow_sha256="cc" * 32,
        output_node_id="19",
    )


def batch(index: int, candidates: tuple[int, ...], seed: int = 100) -> ExecutionBatch:
    return ExecutionBatch(
        batch_index=index,
        candidate_indices=candidates,
        seed=seed + index,
    )


def generation_request(**updates: object) -> GenerationJobRequest:
    values = {
        "schema_version": 3,
        "job_id": JOB_ID,
        "project_id": PROJECT_ID,
        "parent_job_id": None,
        "target": {
            "catalog_id": "java-dev-format-34",
            "target_semantic_id": "minecraft:deepslate",
            "target_display_name": "Deepslate",
            "target_relative_path": "assets/minecraft/textures/block/deepslate.png",
        },
        "prompt": {
            "prompt_version": "java-block-prompt-v1",
            "positive_prompt": "pixel art deepslate",
            "negative_prompt": "border watermark",
            "user_prompt": "cold stone",
        },
        "resolution": 16,
        "parallelism": 1,
        "execution_batches": tuple(
            batch(index, (index,)) for index in range(4)
        ),
        "references": refs(),
        "advanced": {
            "style_strength": 0.7,
            "denoise_strength": None,
            "lora_weight": 1.0,
        },
        "model_profile": binding(),
        "created_at": NOW,
        **updates,
    }
    return GenerationJobRequest.model_validate(values)


def complete_artifacts(candidate_index: int) -> CandidateArtifacts:
    return CandidateArtifacts(
        raw=artifact(f"raw/{candidate_index}.png"),
        final=artifact(f"processed/{candidate_index}.png", kind="final"),
        nearest=artifact(f"previews/{candidate_index}-nearest.png", kind="nearest"),
        tile=artifact(f"previews/{candidate_index}-tile.png", kind="tile"),
        report=artifact(f"reports/{candidate_index}.json", kind="report"),
    )


@pytest.mark.parametrize(
    ("parallelism", "groups"),
    [
        (1, ((0,), (1,), (2,), (3,))),
        (2, ((0, 1), (2, 3))),
        (4, ((0, 1, 2, 3),)),
    ],
)
def test_schema3_accepts_only_exact_native_batch_partition(parallelism, groups):
    request = generation_request(
        parallelism=parallelism,
        execution_batches=tuple(
            batch(index, candidates) for index, candidates in enumerate(groups)
        ),
    )
    assert tuple(item.candidate_indices for item in request.execution_batches) == groups


def test_schema3_rejects_duplicate_or_missing_candidates():
    with pytest.raises(ValidationError):
        generation_request(
            parallelism=2,
            execution_batches=(batch(0, (0, 1)), batch(1, (1, 3))),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("unexpected", True),
        (
            "execution_batches",
            (
                {"batch_index": 0, "candidate_indices": (0,), "seed": 100},
                {"batch_index": 2, "candidate_indices": (1,), "seed": 102},
                {"batch_index": 3, "candidate_indices": (2,), "seed": 103},
                {"batch_index": 4, "candidate_indices": (3,), "seed": 104},
            ),
        ),
        (
            "references",
            {
                "style": tuple(
                    {
                        "kind": "raw",
                        "relative_path": f"inputs/style/{index:02d}.png",
                        "sha256": "ab" * 32,
                        "byte_size": 16,
                        "media_type": "image/png",
                        "width": 16,
                        "height": 16,
                    }
                    for index in range(9)
                ),
                "structure": (),
            },
        ),
        (
            "references",
            {
                "style": (),
                "structure": (
                    {
                        "kind": "raw",
                        "relative_path": "inputs/structure.png",
                        "sha256": "ab" * 32,
                        "byte_size": 16,
                        "media_type": "image/png",
                        "width": 16,
                        "height": 16,
                    },
                    {
                        "kind": "raw",
                        "relative_path": "inputs/structure.png",
                        "sha256": "ab" * 32,
                        "byte_size": 16,
                        "media_type": "image/png",
                        "width": 16,
                        "height": 16,
                    },
                ),
            },
        ),
        ("model_profile", binding("img2img-style")),
    ],
)
def test_schema3_request_rejects_unknown_fields_and_invalid_reference_contracts(
    field: str,
    value: object,
) -> None:
    values = generation_request().model_dump()
    values[field] = value
    with pytest.raises(ValidationError):
        GenerationJobRequest.model_validate(values)


def test_schema3_request_rejects_seed_above_safe_limit():
    with pytest.raises(ValidationError):
        generation_request(execution_batches=(batch(0, (0,), MAX_SAFE_SEED + 1), batch(1, (1,)), batch(2, (2,)), batch(3, (3,))))


@pytest.mark.parametrize(
    ("variant", "style_count", "structure_count"),
    [
        ("text2img-no-style", 1, 0),
        ("text2img-style", 0, 0),
        ("img2img-no-style", 1, 1),
        ("img2img-style", 0, 1),
        ("img2img-style", 1, 0),
    ],
)
def test_schema3_request_rejects_workflow_variant_reference_mismatches(
    variant: str,
    style_count: int,
    structure_count: int,
) -> None:
    with pytest.raises(ValidationError):
        generation_request(
            references=refs(styles=style_count, structures=structure_count),
            model_profile=binding(variant),
        )


def test_stored_artifact_rejects_path_escape():
    with pytest.raises(ValidationError):
        artifact("../escape.png")


@pytest.mark.parametrize(
    ("kind", "path"),
    [
        ("raw", "processed/0.png"),
        ("final", "raw/0.png"),
        ("nearest", "raw/0.png"),
        ("tile", "reports/0.png"),
        ("report", "previews/0.json"),
    ],
)
def test_artifact_kind_requires_schema3_layout_directory(kind, path):
    with pytest.raises(ValidationError):
        CandidateArtifacts.model_validate({kind: artifact(path, kind=kind)})


def test_candidate_record_rejects_wrong_batch_position_without_batch_membership():
    with pytest.raises(ValidationError):
        GenerationCandidateRecord(
            candidate_index=1,
            batch_index=0,
            position_in_batch=2,
            batch_seed=101,
            status="pending",
            artifacts=CandidateArtifacts(),
            lineage=None,
            failure=None,
            started_at=None,
            finished_at=None,
        )


def test_candidate_record_rejects_completed_without_complete_artifacts():
    with pytest.raises(ValidationError):
        GenerationCandidateRecord(
            candidate_index=0,
            batch_index=0,
            position_in_batch=0,
            batch_seed=100,
            status="completed",
            artifacts=CandidateArtifacts(raw=artifact("raw/0.png")),
            lineage=None,
            failure=None,
            started_at=NOW,
            finished_at=NOW,
        )


def test_candidate_record_rejects_inherited_without_lineage():
    with pytest.raises(ValidationError):
        GenerationCandidateRecord(
            candidate_index=0,
            batch_index=0,
            position_in_batch=0,
            batch_seed=100,
            status="inherited",
            artifacts=complete_artifacts(0),
            lineage=None,
            failure=None,
            started_at=NOW,
            finished_at=NOW,
        )


def test_generation_state_requires_batch_and_candidate_layout_to_match():
    with pytest.raises(ValidationError):
        GenerationJobState(
            schema_version=2,
            job_id=JOB_ID,
            project_id=PROJECT_ID,
            revision=0,
            status="queued",
            batches=(
                {
                    "batch_index": 0,
                    "candidate_indices": (0, 1),
                    "seed": 100,
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
            candidates=(
                GenerationCandidateRecord(
                    candidate_index=0,
                    batch_index=0,
                    position_in_batch=0,
                    batch_seed=100,
                    status="pending",
                    artifacts=CandidateArtifacts(),
                    lineage=None,
                    failure=None,
                    started_at=None,
                    finished_at=None,
                ),
                GenerationCandidateRecord(
                    candidate_index=1,
                    batch_index=0,
                    position_in_batch=2,
                    batch_seed=100,
                    status="pending",
                    artifacts=CandidateArtifacts(),
                    lineage=None,
                    failure=None,
                    started_at=None,
                    finished_at=None,
                ),
                GenerationCandidateRecord(
                    candidate_index=2,
                    batch_index=1,
                    position_in_batch=0,
                    batch_seed=101,
                    status="pending",
                    artifacts=CandidateArtifacts(),
                    lineage=None,
                    failure=None,
                    started_at=None,
                    finished_at=None,
                ),
                GenerationCandidateRecord(
                    candidate_index=3,
                    batch_index=1,
                    position_in_batch=1,
                    batch_seed=101,
                    status="pending",
                    artifacts=CandidateArtifacts(),
                    lineage=CandidateLineage(
                        parent_job_id=uuid4(),
                        parent_candidate_index=3,
                    ),
                    failure=None,
                    started_at=NOW,
                    finished_at=NOW,
                ),
            ),
            failure=None,
            cancel_requested_at=None,
            created_at=NOW,
            updated_at=NOW,
            started_at=None,
            finished_at=None,
        )
