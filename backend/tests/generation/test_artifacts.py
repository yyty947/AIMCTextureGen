import hashlib
import json
import os
import subprocess
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
    CandidateLineage,
    ExecutionBatch,
    FrozenReferences,
    GenerationAdvanced,
    GenerationJobState,
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


def _create_directory_reparse_point(link: Path, target: Path) -> None:
    target.mkdir()
    try:
        if os.name == "nt":
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    f"New-Item -ItemType Junction -Path '{link}' -Target '{target}' | Out-Null",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                pytest.skip(f"cannot create a junction: {result.stderr.strip()}")
        else:
            link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"cannot create a directory reparse point: {error}")
    is_junction = getattr(link, "is_junction", lambda: False)()
    if not link.is_symlink() and not is_junction:
        pytest.skip("the platform could not create a directory reparse point")


def _replace_candidate_record(
    job_store: JobStore,
    loaded,
    candidate_index: int,
    artifacts: "CandidateArtifacts",
    *,
    status: str,
    lineage: CandidateLineage | None,
):
    candidates = list(loaded.state.candidates)
    candidate = candidates[candidate_index]
    candidates[candidate_index] = candidate.model_copy(
        update={
            "status": status,
            "artifacts": artifacts,
            "lineage": lineage,
            "started_at": CREATED_AT,
            "finished_at": CREATED_AT,
        }
    )
    state = GenerationJobState.model_validate(
        {
            **loaded.state.model_dump(),
            "revision": loaded.state.revision + 1,
            "status": "generating",
            "started_at": CREATED_AT,
            "finished_at": None,
            "updated_at": CREATED_AT,
            "candidates": tuple(candidates),
        }
    )
    return job_store.replace_state(
        loaded.request.project_id,
        loaded.request.job_id,
        state,
        expected_revision=loaded.state.revision,
    )


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
    import aimctexturegen.generation.artifacts as artifact_module

    def fail_publish(*args, **kwargs):
        raise OSError("rename blocked")

    monkeypatch.setattr(artifact_module, "_publish_directory", fail_publish)

    with pytest.raises(GenerationError) as captured:
        store.publish_raw_batch(
            loaded,
            _batch(0),
            (_png_bytes(), _png_bytes(color=(4, 5, 6))),
            canvas_size=1024,
        )

    assert captured.value.code == "JOB_STORAGE_UNAVAILABLE"
    assert list((loaded.root / "raw").iterdir()) == []


def test_raw_batch_post_rename_failure_removes_final_and_temp_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded = _loaded_generation_job(tmp_path / "projects")
    store = _artifact_store(tmp_path / "projects")
    import aimctexturegen.generation.artifacts as artifact_module

    def fail_published_directory_verification(*args, **kwargs) -> None:
        raise OSError("post-rename reopen blocked")

    monkeypatch.setattr(
        artifact_module,
        "_verify_published_directory",
        fail_published_directory_verification,
    )

    with pytest.raises(GenerationError) as captured:
        store.publish_raw_batch(
            loaded,
            _batch(0),
            (_png_bytes(), _png_bytes(color=(4, 5, 6))),
            canvas_size=1024,
        )

    assert captured.value.code == "JOB_STORAGE_UNAVAILABLE"
    assert not (loaded.root / "raw/batch-0").exists()
    assert not (loaded.root / "raw/.batch-0.tmp").exists()


def test_processed_post_rename_failure_removes_final_and_temp_directories(
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
    import aimctexturegen.generation.artifacts as artifact_module

    def fail_published_directory_verification(*args, **kwargs) -> None:
        raise OSError("post-rename reopen blocked")

    monkeypatch.setattr(
        artifact_module,
        "_verify_published_directory",
        fail_published_directory_verification,
    )

    with pytest.raises(GenerationError) as captured:
        store.process_and_publish(loaded, candidate_index=0, resolution=16)

    assert captured.value.code == "JOB_STORAGE_UNAVAILABLE"
    assert not (loaded.root / "processed/candidate-0").exists()
    assert not (loaded.root / "processed/.candidate-0.tmp").exists()


def test_reset_identity_failure_does_not_leave_temporary_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded = _loaded_generation_job(tmp_path / "projects")
    store = _artifact_store(tmp_path / "projects")
    import aimctexturegen.generation.artifacts as artifact_module

    def fail_capture(_path: Path) -> None:
        raise OSError("identity capture blocked")

    monkeypatch.setattr(artifact_module, "capture_directory_identity", fail_capture)

    with pytest.raises(GenerationError) as captured:
        store.publish_raw_batch(
            loaded,
            _batch(0),
            (_png_bytes(), _png_bytes(color=(4, 5, 6))),
            canvas_size=1024,
        )

    assert captured.value.code == "JOB_STORAGE_UNAVAILABLE"
    assert not (loaded.root / "raw/.batch-0.tmp").exists()


def test_reset_identity_verification_failure_removes_temporary_directory_and_preserves_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded = _loaded_generation_job(tmp_path / "projects")
    store = _artifact_store(tmp_path / "projects")
    import aimctexturegen.generation.artifacts as artifact_module

    real_matches = artifact_module.matches_directory_identity
    verification_attempts = 0

    def fail_temporary_identity_verification(path: Path, expected) -> bool:
        nonlocal verification_attempts
        if path.name == ".batch-0.tmp" and verification_attempts == 0:
            verification_attempts += 1
            raise artifact_module.DirectoryGuardError(
                "identity verification blocked"
            )
        return real_matches(path, expected)

    monkeypatch.setattr(
        artifact_module,
        "matches_directory_identity",
        fail_temporary_identity_verification,
    )

    with pytest.raises(GenerationError) as captured:
        store.publish_raw_batch(
            loaded,
            _batch(0),
            (_png_bytes(), _png_bytes(color=(4, 5, 6))),
            canvas_size=1024,
        )

    assert captured.value.code == "JOB_STORAGE_UNAVAILABLE"
    assert isinstance(captured.value.__cause__, artifact_module.DirectoryGuardError)
    assert str(captured.value.__cause__) == "identity verification blocked"
    assert not (loaded.root / "raw/.batch-0.tmp").exists()


def test_reset_parent_guard_failure_after_creation_removes_temporary_directory_and_preserves_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded = _loaded_generation_job(tmp_path / "projects")
    store = _artifact_store(tmp_path / "projects")
    import aimctexturegen.generation.artifacts as artifact_module

    real_require_parent = artifact_module._PublicationGuard.require_parent
    temporary_parent_checks = 0

    def fail_after_creation(self, path: Path) -> None:
        nonlocal temporary_parent_checks
        if path.name == ".batch-0.tmp":
            temporary_parent_checks += 1
            if temporary_parent_checks == 3:
                raise artifact_module.DirectoryGuardError(
                    "post-create parent guard blocked"
                )
        real_require_parent(self, path)

    monkeypatch.setattr(
        artifact_module._PublicationGuard,
        "require_parent",
        fail_after_creation,
    )

    with pytest.raises(GenerationError) as captured:
        store.publish_raw_batch(
            loaded,
            _batch(0),
            (_png_bytes(), _png_bytes(color=(4, 5, 6))),
            canvas_size=1024,
        )

    assert captured.value.code == "JOB_STORAGE_UNAVAILABLE"
    assert isinstance(captured.value.__cause__, artifact_module.DirectoryGuardError)
    assert str(captured.value.__cause__) == "post-create parent guard blocked"
    assert not (loaded.root / "raw/.batch-0.tmp").exists()


def test_raw_batch_rejects_reparse_parent_before_writing_outside_job_root(tmp_path: Path) -> None:
    loaded = _loaded_generation_job(tmp_path / "projects")
    store = _artifact_store(tmp_path / "projects")
    outside = tmp_path / "outside-raw"
    raw_root = loaded.root / "raw"
    raw_root.rmdir()
    _create_directory_reparse_point(raw_root, outside)

    with pytest.raises(GenerationError) as captured:
        store.publish_raw_batch(
            loaded,
            _batch(0),
            (_png_bytes(), _png_bytes(color=(4, 5, 6))),
            canvas_size=1024,
        )

    assert captured.value.code == "JOB_STORAGE_UNAVAILABLE"
    assert list(outside.iterdir()) == []


def test_process_and_publish_rejects_reparse_parent_before_writing_outside_job_root(
    tmp_path: Path,
) -> None:
    loaded = _loaded_generation_job(tmp_path / "projects")
    store = _artifact_store(tmp_path / "projects")
    store.publish_raw_batch(
        loaded,
        _batch(0),
        (_png_bytes(), _png_bytes(color=(8, 9, 10))),
        canvas_size=1024,
    )
    outside = tmp_path / "outside-processed"
    processed_root = loaded.root / "processed"
    processed_root.rmdir()
    _create_directory_reparse_point(processed_root, outside)

    with pytest.raises(GenerationError) as captured:
        store.process_and_publish(loaded, candidate_index=0, resolution=16)

    assert captured.value.code == "JOB_STORAGE_UNAVAILABLE"
    assert list(outside.iterdir()) == []


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


def test_raw_post_publication_verification_failure_removes_visible_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded = _loaded_generation_job(tmp_path / "projects")
    store = _artifact_store(tmp_path / "projects")
    import aimctexturegen.generation.artifacts as artifact_module

    real_validate = artifact_module._validate_stored_png

    def fail_after_publication(path: Path, *args, **kwargs):
        if path.parent.name == "batch-0":
            raise GenerationError(
                "OUTPUT_CONTRACT_VIOLATION",
                "生成输出不符合受控契约",
            )
        return real_validate(path, *args, **kwargs)

    monkeypatch.setattr(artifact_module, "_validate_stored_png", fail_after_publication)

    with pytest.raises(GenerationError) as captured:
        store.publish_raw_batch(
            loaded,
            _batch(0),
            (_png_bytes(), _png_bytes(color=(4, 5, 6))),
            canvas_size=1024,
        )

    assert captured.value.code == "OUTPUT_CONTRACT_VIOLATION"
    assert not (loaded.root / "raw/batch-0").exists()
    assert not (loaded.root / "raw/.batch-0.tmp").exists()


def test_processed_post_publication_verification_failure_removes_visible_candidate(
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
    import aimctexturegen.generation.artifacts as artifact_module

    def fail_after_publication(*args, **kwargs):
        raise GenerationError(
            "OUTPUT_CONTRACT_VIOLATION",
            "生成输出不符合受控契约",
        )

    monkeypatch.setattr(
        artifact_module,
        "_reopen_candidate_artifacts",
        fail_after_publication,
    )

    with pytest.raises(GenerationError) as captured:
        store.process_and_publish(loaded, candidate_index=0, resolution=16)

    assert captured.value.code == "OUTPUT_CONTRACT_VIOLATION"
    assert not (loaded.root / "processed/candidate-0").exists()
    assert not (loaded.root / "processed/.candidate-0.tmp").exists()


@pytest.mark.parametrize(
    ("requested_kind", "source_artifact", "lineage", "should_raise"),
    [
        (
            "nearest",
            "candidate-0",
            CandidateLineage(parent_job_id=PARENT_JOB_ID, parent_candidate_index=0),
            True,
        ),
        (
            "final",
            "candidate-1",
            CandidateLineage(parent_job_id=PARENT_JOB_ID, parent_candidate_index=0),
            True,
        ),
        (
            "final",
            "candidate-0",
            CandidateLineage(parent_job_id=OTHER_PROJECT_ID, parent_candidate_index=0),
            True,
        ),
        (
            "final",
            "candidate-0",
            CandidateLineage(parent_job_id=PARENT_JOB_ID, parent_candidate_index=0),
            False,
        ),
    ],
)
def test_resolve_enforces_inherited_artifact_candidate_kind_and_lineage(
    tmp_path: Path,
    requested_kind: str,
    source_artifact: str,
    lineage: CandidateLineage,
    should_raise: bool,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    job_store = _store(projects_root)
    parent = job_store.create_generation(
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
    parent_candidate_artifacts = (
        store.process_and_publish(parent, candidate_index=0, resolution=16),
        store.process_and_publish(parent, candidate_index=1, resolution=16),
    )
    parent = _replace_candidate_record(
        job_store,
        parent,
        0,
        parent_candidate_artifacts[0],
        status="completed",
        lineage=None,
    )
    parent = _replace_candidate_record(
        job_store,
        parent,
        1,
        parent_candidate_artifacts[1],
        status="completed",
        lineage=None,
    )
    child = job_store.create_generation(
        _generation_request(job_id=JOB_ID, parent_job_id=PARENT_JOB_ID),
        _snapshot(),
    )
    child = _replace_candidate_record(
        job_store,
        child,
        0,
        parent_candidate_artifacts[0],
        status="inherited",
        lineage=lineage,
    )
    source = (
        parent_candidate_artifacts[0]
        if source_artifact == "candidate-0"
        else parent_candidate_artifacts[1]
    )
    inherited = source.final
    assert inherited is not None

    if should_raise:
        with pytest.raises(GenerationError) as captured:
            store.resolve(
                PROJECT_ID,
                JOB_ID,
                0,
                requested_kind,  # type: ignore[arg-type]
                inherited_from=StoredArtifact.model_validate(inherited.model_dump()),
            )

        assert captured.value.code == "OUTPUT_CONTRACT_VIOLATION"
    else:
        resolved = store.resolve(
            PROJECT_ID,
            JOB_ID,
            0,
            requested_kind,  # type: ignore[arg-type]
            inherited_from=StoredArtifact.model_validate(inherited.model_dump()),
        )
        assert resolved == parent.root / inherited.relative_path


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
