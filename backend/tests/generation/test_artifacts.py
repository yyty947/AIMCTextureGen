import hashlib
import json
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image

from aimctexturegen.comfy.client import ComfyOutputImage
from aimctexturegen.generation.artifacts import CandidateArtifactStore
from aimctexturegen.generation.errors import GenerationError
from aimctexturegen.jobs.models_v3 import (
    ExecutionBatch,
    FrozenReferences,
    GenerationAdvanced,
    GenerationJobRequest,
    GenerationModelBinding,
    GenerationTarget,
    StoredArtifact,
)
from aimctexturegen.jobs.store import JobInputFile, JobInputSnapshot, JobStore
from aimctexturegen.projects.models import ProjectManifest, dump_project_manifest
from aimctexturegen.projects.repository import ProjectRepository


CREATED_AT = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_PROJECT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
JOB_ID = UUID("22222222-2222-4222-8222-222222222222")
PARENT_JOB_ID = UUID("33333333-3333-4333-8333-333333333333")


def _manifest(project_id: UUID = PROJECT_ID) -> ProjectManifest:
    return ProjectManifest(
        schema_version=2,
        project_id=project_id,
        project_name="Artifact store project",
        edition="java",
        java_pack_format=34,
        supported_formats=None,
        catalog_id="java-dev-format-34",
        source_sha256="ab" * 32,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
        default_resolution=16,
        default_parallelism=1,
        style_references=(),
    )


def _write_project(projects_root: Path, project_id: UUID = PROJECT_ID) -> Path:
    project_root = projects_root / str(project_id)
    (project_root / "source").mkdir(parents=True)
    (project_root / "pack").mkdir()
    (project_root / "source" / "imported-pack.zip").write_bytes(b"snapshot")
    (project_root / "pack" / "pack.mcmeta").write_bytes(b"metadata")
    (project_root / "project.json").write_bytes(dump_project_manifest(_manifest(project_id)))
    return project_root


def _generation_request(
    *,
    project_id: UUID = PROJECT_ID,
    job_id: UUID = JOB_ID,
    parent_job_id: UUID | None = None,
) -> GenerationJobRequest:
    return GenerationJobRequest(
        schema_version=3,
        job_id=job_id,
        project_id=project_id,
        parent_job_id=parent_job_id,
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
            ExecutionBatch(batch_index=0, candidate_indices=(0, 1), seed=101),
            ExecutionBatch(batch_index=1, candidate_indices=(2, 3), seed=202),
        ),
        references=FrozenReferences(
            style=(
                {
                    "kind": "raw",
                    "relative_path": "inputs/style/00.png",
                    "sha256": hashlib.sha256(b"style-image").hexdigest(),
                    "byte_size": 11,
                    "media_type": "image/png",
                    "width": 16,
                    "height": 16,
                },
            ),
            structure=(),
        ),
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
            workflow_variant="text2img-style",
            workflow_sha256="cc" * 32,
            output_node_id="19",
        ),
        created_at=CREATED_AT,
    )


def _snapshot() -> JobInputSnapshot:
    digest = hashlib.sha256(b"style-image").hexdigest()
    payload = (
        json.dumps(
            {
                "style": [{"relative_path": "inputs/style/00.png", "sha256": digest}],
                "structure": [],
            },
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    return JobInputSnapshot(
        references_json=payload,
        files=(
            JobInputFile(
                relative_path="style/00.png",
                payload=b"style-image",
                sha256=digest,
            ),
        ),
    )


def _store(projects_root: Path) -> JobStore:
    return JobStore(ProjectRepository(projects_root))


def _artifact_store(projects_root: Path) -> CandidateArtifactStore:
    return CandidateArtifactStore(_store(projects_root))


def _png_bytes(*, size: int = 1024, mode: str = "RGB", color=(1, 2, 3)) -> bytes:
    image = Image.new(mode, (size, size), color)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _loaded_generation_job(projects_root: Path):
    _write_project(projects_root)
    return _store(projects_root).create_generation(_generation_request(), _snapshot())


def _batch(index: int) -> ExecutionBatch:
    return _generation_request().execution_batches[index]


def test_raw_batch_is_all_or_nothing(tmp_path: Path) -> None:
    loaded = _loaded_generation_job(tmp_path / "projects")
    store = _artifact_store(tmp_path / "projects")

    with pytest.raises(GenerationError) as captured:
        store.publish_raw_batch(
            loaded,
            _batch(0),
            (
                _png_bytes(),
                b"not-a-png",
            ),
            canvas_size=1024,
        )

    assert captured.value.code == "OUTPUT_CONTRACT_VIOLATION"
    assert list((loaded.root / "raw").iterdir()) == []


@pytest.mark.parametrize(
    "payloads",
    [
        (),
        (_png_bytes(),),
        (_png_bytes(), _png_bytes(), _png_bytes()),
    ],
)
def test_raw_batch_rejects_missing_or_extra_outputs(
    tmp_path: Path,
    payloads: tuple[bytes, ...],
) -> None:
    loaded = _loaded_generation_job(tmp_path / "projects")
    store = _artifact_store(tmp_path / "projects")

    with pytest.raises(GenerationError) as captured:
        store.publish_raw_batch(loaded, _batch(0), payloads, canvas_size=1024)

    assert captured.value.code == "OUTPUT_CONTRACT_VIOLATION"
    assert list((loaded.root / "raw").iterdir()) == []


@pytest.mark.parametrize(
    "invalid_payload",
    [
        _png_bytes(size=512),
        _png_bytes(size=1024, mode="RGBA", color=(1, 2, 3, 255)),
    ],
)
def test_raw_batch_rejects_profile_contract_violations(
    tmp_path: Path,
    invalid_payload: bytes,
) -> None:
    loaded = _loaded_generation_job(tmp_path / "projects")
    store = _artifact_store(tmp_path / "projects")

    with pytest.raises(GenerationError) as captured:
        store.publish_raw_batch(
            loaded,
            _batch(0),
            (_png_bytes(), invalid_payload),
            canvas_size=1024,
        )

    assert captured.value.code == "OUTPUT_CONTRACT_VIOLATION"
    assert list((loaded.root / "raw").iterdir()) == []


def test_raw_batch_maps_declared_output_order_to_candidate_indices(tmp_path: Path) -> None:
    loaded = _loaded_generation_job(tmp_path / "projects")
    store = _artifact_store(tmp_path / "projects")

    artifacts = store.publish_raw_batch(
        loaded,
        _batch(0),
        (
            _png_bytes(color=(10, 20, 30)),
            _png_bytes(color=(40, 50, 60)),
        ),
        canvas_size=1024,
    )

    assert tuple(artifact.relative_path for artifact in artifacts) == (
        "raw/batch-0/candidate-0.png",
        "raw/batch-0/candidate-1.png",
    )
    assert tuple((loaded.root / artifact.relative_path).exists() for artifact in artifacts) == (
        True,
        True,
    )


def test_raw_batch_publication_failure_leaves_no_visible_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = _loaded_generation_job(tmp_path / "projects")
    store = _artifact_store(tmp_path / "projects")

    original_replace = Path.replace

    def fail_replace(source: Path, destination: Path):
        if destination.name == "batch-0":
            raise OSError("rename blocked")
        return original_replace(source, destination)

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(GenerationError) as captured:
        store.publish_raw_batch(
            loaded,
            _batch(0),
            (_png_bytes(), _png_bytes(color=(4, 5, 6))),
            canvas_size=1024,
        )

    assert captured.value.code == "JOB_STORAGE_UNAVAILABLE"
    assert list((loaded.root / "raw").iterdir()) == []


def test_process_and_publish_writes_one_candidate_atomically_and_resolves_report(tmp_path: Path) -> None:
    loaded = _loaded_generation_job(tmp_path / "projects")
    store = _artifact_store(tmp_path / "projects")
    raw, *_ = store.publish_raw_batch(
        loaded,
        _batch(0),
        (_png_bytes(), _png_bytes(color=(8, 9, 10))),
        canvas_size=1024,
    )

    artifacts = store.process_and_publish(loaded, candidate_index=0, resolution=16)

    assert artifacts.raw == raw
    assert artifacts.final is not None and artifacts.final.width == 16
    assert artifacts.nearest is not None and artifacts.nearest.width > 16
    assert artifacts.tile is not None and artifacts.tile.width == artifacts.nearest.width * 3
    report_path = store.resolve(
        project_id=PROJECT_ID,
        job_id=JOB_ID,
        candidate_index=0,
        kind="report",
    )
    assert json.loads(report_path.read_bytes())["resolution"] == 16


def test_process_and_publish_failure_leaves_previous_complete_artifacts_intact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded = _loaded_generation_job(tmp_path / "projects")
    store = _artifact_store(tmp_path / "projects")
    store.publish_raw_batch(
        loaded,
        _batch(0),
        (_png_bytes(), _png_bytes(color=(8, 9, 10))),
        canvas_size=1024,
    )
    first = store.process_and_publish(loaded, candidate_index=0, resolution=16)
    before = {
        artifact.relative_path: (loaded.root / artifact.relative_path).read_bytes()
        for artifact in (
            first.raw,
            first.final,
            first.nearest,
            first.tile,
            first.report,
        )
        if artifact is not None
    }

    def fail_process_candidate(*args, **kwargs):
        raise RuntimeError("postprocess blocked")

    monkeypatch.setattr("aimctexturegen.generation.artifacts.process_candidate", fail_process_candidate)

    with pytest.raises(GenerationError) as captured:
        store.process_and_publish(loaded, candidate_index=0, resolution=16)

    assert captured.value.code == "POSTPROCESSING_FAILED"
    after = {
        path: (loaded.root / path).read_bytes()
        for path in before
    }
    assert after == before


def test_process_and_publish_report_references_match_published_hashes(tmp_path: Path) -> None:
    loaded = _loaded_generation_job(tmp_path / "projects")
    store = _artifact_store(tmp_path / "projects")
    store.publish_raw_batch(
        loaded,
        _batch(0),
        (_png_bytes(), _png_bytes(color=(8, 9, 10))),
        canvas_size=1024,
    )

    artifacts = store.process_and_publish(loaded, candidate_index=0, resolution=16)
    report = json.loads((loaded.root / artifacts.report.relative_path).read_bytes())

    assert report["output"]["sha256"] == artifacts.final.sha256
    assert report["previews"]["nearest_neighbor"]["sha256"] == artifacts.nearest.sha256
    assert report["previews"]["tile_3x3"]["sha256"] == artifacts.tile.sha256


def test_resolve_rejects_cross_project_lineage(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root, PROJECT_ID)
    _write_project(projects_root, OTHER_PROJECT_ID)
    parent = _store(projects_root).create_generation(
        _generation_request(job_id=PARENT_JOB_ID),
        _snapshot(),
    )
    store = _artifact_store(projects_root)
    store.publish_raw_batch(
        parent,
        _batch(0),
        (_png_bytes(), _png_bytes(color=(8, 9, 10))),
        canvas_size=1024,
    )
    parent_artifacts = store.process_and_publish(parent, candidate_index=0, resolution=16)
    child = _store(projects_root).create_generation(
        _generation_request(project_id=OTHER_PROJECT_ID, job_id=JOB_ID, parent_job_id=PARENT_JOB_ID),
        _snapshot(),
    )

    with pytest.raises(GenerationError) as captured:
        store.resolve(
            project_id=OTHER_PROJECT_ID,
            job_id=child.request.job_id,
            candidate_index=0,
            kind="final",
            inherited_from=StoredArtifact.model_validate(parent_artifacts.final.model_dump()),
        )

    assert captured.value.code == "OUTPUT_CONTRACT_VIOLATION"


def test_resolve_rejects_path_traversal_hash_mismatch_and_unknown_kind(tmp_path: Path) -> None:
    loaded = _loaded_generation_job(tmp_path / "projects")
    store = _artifact_store(tmp_path / "projects")
    store.publish_raw_batch(
        loaded,
        _batch(0),
        (_png_bytes(), _png_bytes(color=(8, 9, 10))),
        canvas_size=1024,
    )
    artifacts = store.process_and_publish(loaded, candidate_index=0, resolution=16)
    final_path = loaded.root / artifacts.final.relative_path
    final_path.write_bytes(b"tampered")

    with pytest.raises(GenerationError) as hash_error:
        store.resolve(PROJECT_ID, JOB_ID, 0, "final")
    assert hash_error.value.code == "OUTPUT_CONTRACT_VIOLATION"

    with pytest.raises(GenerationError) as traversal_error:
        store.resolve(
            PROJECT_ID,
            JOB_ID,
            0,
            "final",
            inherited_from=artifacts.final.model_copy(
                update={"relative_path": "../escape.png"}
            ),
        )
    assert traversal_error.value.code == "OUTPUT_CONTRACT_VIOLATION"

    with pytest.raises(GenerationError):
        store.resolve(PROJECT_ID, JOB_ID, 0, "unknown-kind")  # type: ignore[arg-type]
