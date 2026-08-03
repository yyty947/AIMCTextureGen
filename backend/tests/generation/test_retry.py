from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from PIL import Image

from aimctexturegen.comfy.registry import ManifestRegistry
from aimctexturegen.generation.service import CreateGenerationCommand, GenerationService
from aimctexturegen.jobs.generation_state import (
    complete_candidate,
    fail_generation,
    mark_batch_raw_ready,
    start_batch,
    start_generation,
)
from aimctexturegen.jobs.models_v3 import CandidateArtifacts, GenerationFailure
from aimctexturegen.jobs.store import JobStore
from aimctexturegen.projects.models import ProjectManifest, dump_project_manifest
from aimctexturegen.projects.repository import ProjectRepository
from aimctexturegen.references.models import PackReferenceSelection, ReferenceSelections
from aimctexturegen.references.service import ReferenceService
from aimctexturegen.references.store import ProjectReferenceStore


PROJECT_ID = UUID("50505050-5050-4050-8050-505050505050")
PARENT_JOB_ID = UUID("60606060-6060-4060-8060-606060606060")
CHILD_JOB_ID = UUID("70707070-7070-4070-8070-707070707070")
NOW = datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc)
CATALOG_ROOT = Path(__file__).parents[3] / "catalogs" / "java"
REPO_ROOT = Path(__file__).resolve().parents[3]


def _png_bytes(
    *,
    size: int = 16,
    color: tuple[int, int, int] = (40, 40, 40),
) -> bytes:
    payload = io.BytesIO()
    Image.new("RGB", (size, size), color).save(payload, format="PNG")
    return payload.getvalue()


def _write_project(projects_root: Path) -> None:
    project_root = projects_root / str(PROJECT_ID)
    (project_root / "source").mkdir(parents=True)
    pack_root = project_root / "pack"
    pack_root.mkdir()
    (project_root / "source" / "imported-pack.zip").write_bytes(b"snapshot")
    (project_root / "pack" / "pack.mcmeta").write_bytes(b"metadata")
    stone = pack_root / "assets/minecraft/textures/block/stone.png"
    stone.parent.mkdir(parents=True, exist_ok=True)
    stone.write_bytes(_png_bytes())
    (project_root / "project.json").write_bytes(
        dump_project_manifest(
            ProjectManifest(
                schema_version=2,
                project_id=PROJECT_ID,
                project_name="Retry project",
                edition="java",
                java_pack_format=34,
                supported_formats=None,
                catalog_id="java-dev-format-34",
                source_sha256="ef" * 32,
                created_at=NOW,
                updated_at=NOW,
                default_resolution=16,
                default_parallelism=2,
                style_references=(),
            )
        )
    )


def _verified_registry() -> ManifestRegistry:
    loaded = ManifestRegistry.load(REPO_ROOT)
    profile = loaded.profile("sdxl-mapchip-ipadapter", "2").model_copy(
        update={"support_state": "verified"}
    )
    return ManifestRegistry(
        root=REPO_ROOT,
        runtimes=loaded.runtimes,
        profiles={(profile.profile_id, profile.profile_version): profile},
    )


def _service(projects_root: Path) -> GenerationService:
    repository = ProjectRepository(projects_root)
    references = ReferenceService(
        repository=repository,
        catalogs=__import__(
            "aimctexturegen.catalog.registry", fromlist=["CatalogRegistry"]
        ).CatalogRegistry(CATALOG_ROOT),
        store=ProjectReferenceStore(repository),
    )
    return GenerationService(
        repository=repository,
        catalogs=__import__(
            "aimctexturegen.catalog.registry", fromlist=["CatalogRegistry"]
        ).CatalogRegistry(CATALOG_ROOT),
        references=references,
        store=JobStore(repository),
        manifests=_verified_registry(),
        seed_source=iter((101, 202)).__next__,
        job_id_source=iter((PARENT_JOB_ID, CHILD_JOB_ID)).__next__,
        clock=lambda: NOW,
    )


def _command() -> CreateGenerationCommand:
    return CreateGenerationCommand(
        target_semantic_id="minecraft:deepslate",
        user_description="cold stone",
        user_negative_prompt="",
        resolution=16,
        parallelism=2,
        references=ReferenceSelections(
            style=(
                PackReferenceSelection(
                    source="pack",
                    relative_path="assets/minecraft/textures/block/stone.png",
                ),
            ),
            structure=None,
        ),
    )


def test_retry_job_inherits_complete_candidates_and_preserves_raw_ready_batches(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    service = _service(projects_root)
    parent = service.create_job(PROJECT_ID, _command())
    state = start_generation(parent.state, now=NOW)
    parent = service._store.replace_state(PROJECT_ID, PARENT_JOB_ID, state, expected_revision=0)
    state = start_batch(parent.state, 0, now=NOW)
    parent = service._store.replace_state(
        PROJECT_ID, PARENT_JOB_ID, state, expected_revision=parent.state.revision
    )
    raw_batch_0 = service._artifacts.publish_raw_batch(
        parent,
        parent.request.execution_batches[0],
        (_png_bytes(size=1024, color=(10, 20, 30)), _png_bytes(size=1024, color=(40, 50, 60))),
        canvas_size=1024,
    )
    state = mark_batch_raw_ready(parent.state, 0, raw_batch_0, now=NOW)
    parent = service._store.replace_state(
        PROJECT_ID, PARENT_JOB_ID, state, expected_revision=parent.state.revision
    )
    processed_0 = service._artifacts.process_and_publish(parent, candidate_index=0, resolution=16)
    state = complete_candidate(parent.state, 0, processed_0, now=NOW)
    parent = service._store.replace_state(
        PROJECT_ID, PARENT_JOB_ID, state, expected_revision=parent.state.revision
    )
    failure = GenerationFailure(
        error_code="COMFY_TIMEOUT",
        stage="generation",
        user_message="timed out",
        recommended_actions=("retry",),
        technical_details=None,
        retryable=True,
        occurred_at=NOW,
    )
    state = fail_generation(parent.state, failure, now=NOW)
    parent = service._store.replace_state(
        PROJECT_ID, PARENT_JOB_ID, state, expected_revision=parent.state.revision
    )
    parent_request_before = parent.request

    retried = service.retry_job(PROJECT_ID, PARENT_JOB_ID)

    assert retried.request.job_id == CHILD_JOB_ID
    assert retried.request.parent_job_id == PARENT_JOB_ID
    assert retried.request.prompt == parent.request.prompt
    assert retried.request.parallelism == parent.request.parallelism
    assert retried.request.execution_batches == parent.request.execution_batches
    assert retried.request.advanced == parent.request.advanced
    assert retried.request.references == parent.request.references
    assert retried.state.candidates[0].status == "inherited"
    assert retried.state.candidates[0].lineage is not None
    assert retried.state.candidates[0].lineage.parent_job_id == PARENT_JOB_ID
    assert retried.state.candidates[1].status == "raw_ready"
    assert retried.state.batches[0].status == "raw_ready"
    assert retried.state.batches[1].status == "pending"
    assert (retried.root / "raw" / "batch-0" / "candidate-1.png").is_file()
    assert (
        retried.root / "inputs" / "references.json"
    ).read_bytes() == (parent.root / "inputs" / "references.json").read_bytes()
    assert parent.request == parent_request_before
    assert parent.state.status == "failed"
