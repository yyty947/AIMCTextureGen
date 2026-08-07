from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from PIL import Image

from aimctexturegen.catalog.registry import CatalogRegistry
from aimctexturegen.comfy.manifests import (
    ModelProfileManifestV2,
    RuntimeManifest,
    manifest_sha256,
)
from aimctexturegen.comfy.registry import ManifestRegistry
from aimctexturegen.generation.artifacts import CandidateArtifactStore
from aimctexturegen.generation.coordinator import GenerationCoordinator
from aimctexturegen.generation.service import CreateGenerationCommand, GenerationService
from aimctexturegen.jobs.generation_state import (
    complete_candidate,
    confirm_canceled,
    mark_batch_raw_ready,
    request_cancel,
    start_batch,
    start_generation,
)
from aimctexturegen.jobs.models_v3 import ArtifactKind
from aimctexturegen.jobs.store import JobStore
from aimctexturegen.main import AppServices, create_app
from aimctexturegen.projects.models import ProjectManifest, dump_project_manifest
from aimctexturegen.projects.repository import ProjectRepository
from aimctexturegen.references.models import PackReferenceSelection, ReferenceSelections
from aimctexturegen.references.service import ReferenceService
from aimctexturegen.references.store import ProjectReferenceStore


CATALOG_ROOT = Path(__file__).parents[3] / "catalogs" / "java"
REPO_ROOT = Path(__file__).resolve().parents[3]
PROJECT_ID = UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
OTHER_PROJECT_ID = UUID("ffffffff-1111-4222-8333-444444444444")
JOB_ID = UUID("12345678-1234-4abc-8def-123456789abc")
RETRY_JOB_ID = UUID("abcdefab-cdef-4abc-8def-abcdefabcdef")
NOW = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
STYLE_REFERENCE = "assets/minecraft/textures/block/stone.png"


def _png_bytes(
    *,
    size: int = 16,
    color: tuple[int, int, int] = (64, 64, 64),
) -> bytes:
    payload = io.BytesIO()
    Image.new("RGB", (size, size), color).save(payload, format="PNG")
    return payload.getvalue()


def _write_project(projects_root: Path, project_id: UUID = PROJECT_ID) -> None:
    project_root = projects_root / str(project_id)
    (project_root / "source").mkdir(parents=True)
    pack_root = project_root / "pack"
    pack_root.mkdir()
    (project_root / "jobs").mkdir()
    (project_root / "uploads").mkdir()
    (project_root / "source" / "imported-pack.zip").write_bytes(b"snapshot")
    (pack_root / "pack.mcmeta").write_bytes(b"metadata")
    target = pack_root / Path(*STYLE_REFERENCE.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_png_bytes())
    (project_root / "project.json").write_bytes(
        dump_project_manifest(
            ProjectManifest(
                schema_version=2,
                project_id=project_id,
                project_name=f"Project {project_id}",
                edition="java",
                java_pack_format=34,
                supported_formats=None,
                catalog_id="java-dev-format-34",
                source_sha256="0" * 64,
                created_at=NOW,
                updated_at=NOW,
                default_resolution=16,
                default_parallelism=1,
                style_references=(),
            )
        )
    )


def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)

    return httpx.Response(500) if False else __import__("asyncio").run(send())


def _assert_error(
    response: httpx.Response,
    *,
    status_code: int,
    code: str,
    stage: str | None = None,
) -> dict[str, object]:
    assert response.status_code == status_code, response.text
    body = response.json()
    assert set(body) == {
        "code",
        "stage",
        "user_message",
        "recommended_actions",
        "technical_details",
    }
    assert body["code"] == code
    if stage is not None:
        assert body["stage"] == stage
    assert "C:\\" not in response.text
    return body


def make_runtime() -> dict:
    return {
        "schema_version": 1,
        "runtime_id": "comfyui-windows-nvidia",
        "runtime_version": "0.29.2",
        "platform": "windows",
        "architecture": "x86_64",
        "gpu_vendor": "nvidia",
        "host_requirements": "Windows x64 with NVIDIA CUDA",
        "release_url": "https://example.com/runtime",
        "source_commit": "3" * 40,
        "licenses": [{"name": "GPL-3.0", "source_url": "https://example.com/license"}],
        "archive": {
            "artifact_id": "runtime",
            "file_name": "runtime.7z",
            "source_url": "https://example.com/runtime.7z",
            "revision": "v0.29.2",
            "byte_size": 1024,
            "sha256": "1" * 64,
            "destination": "downloads/runtime.7z",
            "allowed_hosts": ["example.com"],
            "license": {"name": "GPL-3.0", "source_url": "https://example.com/license"},
        },
        "expected_archive_root": "runtime",
        "required_paths": ("python_embeded/python.exe",),
        "startup_argument_template": ("python_embeded/python.exe",),
        "health_endpoint": "/system_stats",
        "expected_runtime_identity": "0.29.2",
        "extraction_headroom_bytes": 1024,
        "headroom_is_estimate": True,
        "revision_notes": "test",
    }


def make_profile_v2(*, support_state: str = "verified") -> dict:
    return {
        "schema_version": 2,
        "profile_id": "sdxl-mapchip-ipadapter",
        "profile_version": "2",
        "support_state": support_state,
        "compatible_runtime_ids": ("comfyui-windows-nvidia",),
        "compatible_runtime_versions": ("0.29.2",),
        "capabilities": {
            "text_to_image": True,
            "structure_reference": True,
            "style_reference_min": 0,
            "style_reference_max": 8,
            "native_multi_reference": True,
            "requires_custom_nodes": True,
        },
        "artifacts": [
            {
                "artifact_id": "checkpoint",
                "file_name": "model.safetensors",
                "source_url": "https://example.com/model",
                "revision": "v1",
                "byte_size": 1024,
                "sha256": "2" * 64,
                "destination": "models/checkpoints/model.safetensors",
                "allowed_hosts": ["example.com"],
                "license": {"name": "Apache-2.0", "source_url": "https://example.com/license"},
            },
            {
                "artifact_id": "custom-node",
                "file_name": "node.zip",
                "source_url": "https://example.com/node.zip",
                "revision": "v1",
                "byte_size": 1024,
                "sha256": "4" * 64,
                "destination": "custom_nodes/node.zip",
                "allowed_hosts": ["example.com"],
                "license": {"name": "GPL-3.0", "source_url": "https://example.com/license"},
            },
        ],
        "workflows": [
            {
                "variant": "text2img-no-style",
                "relative_path": "sdxl-mapchip-ipadapter-v2/text2img-no-style.api.json",
                "sha256": hashlib.sha256(b"text2img-no-style").hexdigest(),
                "output_node_id": "19",
            },
            {
                "variant": "text2img-style",
                "relative_path": "sdxl-mapchip-ipadapter-v2/text2img-style.api.json",
                "sha256": hashlib.sha256(b"text2img-style").hexdigest(),
                "output_node_id": "19",
            },
            {
                "variant": "img2img-no-style",
                "relative_path": "sdxl-mapchip-ipadapter-v2/img2img-no-style.api.json",
                "sha256": hashlib.sha256(b"img2img-no-style").hexdigest(),
                "output_node_id": "19",
            },
            {
                "variant": "img2img-style",
                "relative_path": "sdxl-mapchip-ipadapter-v2/img2img-style.api.json",
                "sha256": hashlib.sha256(b"img2img-style").hexdigest(),
                "output_node_id": "19",
            },
        ],
        "required_node_classes": ("CheckpointLoaderSimple",),
        "output_contract": {"format": "png", "color_mode": "rgb", "canvas_size": 1024},
        "profile_defaults": {
            "resolution": 16,
            "parallelism": 1,
            "candidate_count": 4,
        },
        "user_limitations": "test",
        "revision_notes": None,
    }


def _profile_evidence(profile: ModelProfileManifestV2) -> dict[str, object]:
    return {
        "profile_id": profile.profile_id,
        "profile_version": profile.profile_version,
        "profile_manifest_sha256": manifest_sha256(profile),
        "results": [
            {
                "variant": "text2img-no-style",
                "batch_size": 1,
                "output_count": 1,
                "peak_vram_mib": 4096,
                "peak_process_ram_mib": 6144,
                "peak_system_ram_mib": 8192,
                "elapsed_seconds": 12.5,
                "postprocess_status": "completed",
                "status": "completed",
            },
            {
                "variant": "text2img-no-style",
                "batch_size": 2,
                "output_count": 2,
                "peak_vram_mib": 6144,
                "peak_process_ram_mib": 7168,
                "peak_system_ram_mib": 9216,
                "elapsed_seconds": 18.25,
                "postprocess_status": "completed",
                "status": "completed",
            },
            {
                "variant": "text2img-no-style",
                "batch_size": 4,
                "output_count": 4,
                "peak_vram_mib": 8192,
                "peak_process_ram_mib": 9216,
                "peak_system_ram_mib": 11264,
                "elapsed_seconds": 31.75,
                "postprocess_status": "completed",
                "status": "completed",
            },
        ],
    }


def _registry(
    tmp_path: Path,
    *,
    support_state: str = "verified",
    include_evidence: bool = True,
) -> ManifestRegistry:
    runtime = RuntimeManifest.model_validate(make_runtime())
    profile = ModelProfileManifestV2.model_validate(
        make_profile_v2(support_state=support_state)
    )
    profile_evidence = (
        {
            (profile.profile_id, profile.profile_version): _profile_evidence(profile),
        }
        if include_evidence
        else None
    )
    return ManifestRegistry(
        root=tmp_path,
        runtimes={runtime.runtime_id: runtime},
        profiles={(profile.profile_id, profile.profile_version): profile},
        profile_evidence=profile_evidence,
    )


class _InferenceStub:
    class _Client:
        def upload_image(self, _payload: bytes, name: str) -> dict[str, str]:
            return {"name": name}

        def submit_prompt(self, _workflow: dict) -> str:
            return "prompt-1"

        def wait_completion(self, *_args, **_kwargs):
            raise RuntimeError("synthetic transport failure")

    def __init__(self) -> None:
        self._client = self._Client()

    def ensure_generation_ready(self, _binding):
        return self._client

    def stop_comfyui(self):
        return {"state": "stopped"}

    def shutdown(self):
        return None


class _CoordinatorStub:
    def __init__(self, generation: GenerationService, store: JobStore, repository: ProjectRepository) -> None:
        self._generation = generation
        self._store = store
        self._repository = repository

    def create_job(self, project_id: UUID, command: CreateGenerationCommand):
        current = self.current_job()
        if current is not None:
            from aimctexturegen.generation.errors import GenerationError

            raise GenerationError(
                "GENERATION_JOB_CONFLICT",
                "当前已有一个未结束的生成任务",
                recommended_actions=("查看或取消当前任务后再试",),
                current_job=(current.project_id, current.job_id),
            )
        return self._generation.create_job(project_id, command)

    def start(self, project_id: UUID, job_id: UUID):
        return self._store.load(project_id, job_id)

    def cancel(self, project_id: UUID, job_id: UUID):
        loaded = self._store.load(project_id, job_id)
        state = loaded.state
        if state.status in {"completed", "failed", "canceled"}:
            return loaded
        requested = request_cancel(state, now=NOW)
        committed = self._store.replace_state(
            project_id,
            job_id,
            requested,
            expected_revision=state.revision,
        )
        return self._store.replace_state(
            project_id,
            job_id,
            confirm_canceled(committed.state, now=NOW),
            expected_revision=committed.state.revision,
        )

    def retry(self, project_id: UUID, job_id: UUID):
        current = self.current_job()
        if current is not None:
            from aimctexturegen.generation.errors import GenerationError

            raise GenerationError(
                "GENERATION_JOB_CONFLICT",
                "当前已有一个未结束的生成任务",
                recommended_actions=("查看或取消当前任务后再试",),
                current_job=(current.project_id, current.job_id),
            )
        return self._generation.retry_job(project_id, job_id)

    def current_job(self):
        for manifest in self._repository.list_manifests().manifests:
            for loaded in self._store.scan(manifest.project_id).jobs:
                if loaded.state.status in {"queued", "generating", "postprocessing"}:
                    return type(
                        "CurrentJob",
                        (),
                        {
                            "project_id": loaded.request.project_id,
                            "job_id": loaded.request.job_id,
                            "status": loaded.state.status,
                        },
                    )()
        return None

    def shutdown(self) -> None:
        return None


def _command() -> CreateGenerationCommand:
    return CreateGenerationCommand(
        target_semantic_id="minecraft:deepslate",
        user_description="cold stone",
        user_negative_prompt="",
        resolution=16,
        parallelism=1,
        references=ReferenceSelections(
            style=(
                PackReferenceSelection(
                    source="pack",
                    relative_path=STYLE_REFERENCE,
                ),
            ),
            structure=None,
        ),
    )


def _services(
    tmp_path: Path,
    *,
    support_state: str = "verified",
    include_evidence: bool = True,
) -> tuple[AppServices, GenerationService, JobStore, ProjectRepository]:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    _write_project(projects_root, OTHER_PROJECT_ID)
    repository = ProjectRepository(projects_root)
    catalogs = CatalogRegistry(CATALOG_ROOT)
    store = JobStore(repository)
    references = ReferenceService(
        repository=repository,
        catalogs=catalogs,
        store=ProjectReferenceStore(repository),
    )
    registry = _registry(
        tmp_path,
        support_state=support_state,
        include_evidence=include_evidence,
    )
    generation = GenerationService(
        repository=repository,
        catalogs=catalogs,
        references=references,
        store=store,
        manifests=registry,
        seed_source=iter((101, 102, 103, 104)).__next__,
        job_id_source=iter((JOB_ID, RETRY_JOB_ID)).__next__,
        clock=lambda: NOW,
    )
    coordinator = _CoordinatorStub(generation, store, repository)
    services = AppServices(
        workspace=object(),
        catalogs=catalogs,
        project_root=projects_root,
        repository=repository,
        job_store=store,
        manifest_registry=registry,
        reference_service=references,
        generation_service=generation,
        generation_coordinator=coordinator,
        inference=_InferenceStub(),
    )
    return services, generation, store, repository


def _app(
    tmp_path: Path,
    *,
    support_state: str = "verified",
    include_evidence: bool = True,
):
    services, generation, store, repository = _services(
        tmp_path,
        support_state=support_state,
        include_evidence=include_evidence,
    )
    app = create_app(services=services)
    return app, generation, store, repository


def _publish_completed_candidate(
    generation: GenerationService,
    store: JobStore,
    loaded,
    *,
    candidate_index: int = 0,
) -> object:
    batch = loaded.request.execution_batches[0]
    generating = store.replace_state(
        loaded.request.project_id,
        loaded.request.job_id,
        start_generation(loaded.state, now=NOW),
        expected_revision=loaded.state.revision,
    )
    active_batch = store.replace_state(
        loaded.request.project_id,
        loaded.request.job_id,
        start_batch(generating.state, batch.batch_index, now=NOW),
        expected_revision=generating.state.revision,
    )
    raw = generation._artifacts.publish_raw_batch(
        active_batch,
        batch,
        (_png_bytes(size=1024, color=(1, 2, 3)),),
        canvas_size=1024,
    )
    raw_ready = store.replace_state(
        loaded.request.project_id,
        loaded.request.job_id,
        mark_batch_raw_ready(active_batch.state, batch.batch_index, raw, now=NOW),
        expected_revision=active_batch.state.revision,
    )
    artifacts = generation._artifacts.process_and_publish(
        raw_ready,
        candidate_index=candidate_index,
        resolution=16,
    )
    return store.replace_state(
        loaded.request.project_id,
        loaded.request.job_id,
        complete_candidate(raw_ready.state, candidate_index, artifacts, now=NOW),
        expected_revision=raw_ready.state.revision,
    )


def test_generation_options_and_current_job_surface_verified_defaults_and_slot_status(
    tmp_path: Path,
) -> None:
    app, generation, _store, _repository = _app(tmp_path)
    created = generation.create_job(PROJECT_ID, _command())

    options = _request(app, "GET", f"/api/projects/{PROJECT_ID}/generation-options")
    current = _request(app, "GET", "/api/generation/current")

    assert options.status_code == 200, options.text
    body = options.json()
    assert body["candidate_count"] == 4
    assert body["allowed_parallelism"] == [1, 2, 4]
    assert body["defaults"] == {"resolution": 16, "parallelism": 1}
    assert body["profile"]["profile_id"] == "sdxl-mapchip-ipadapter"
    assert body["profile"]["profile_version"] == "2"
    assert body["profile"]["support_state"] == "verified"
    assert body["resource_hints"] == [
        {
            "parallelism": 1,
            "peak_vram_mib": 4096,
            "peak_process_ram_mib": 6144,
            "peak_system_ram_mib": 8192,
            "elapsed_seconds": 12.5,
        },
        {
            "parallelism": 2,
            "peak_vram_mib": 6144,
            "peak_process_ram_mib": 7168,
            "peak_system_ram_mib": 9216,
            "elapsed_seconds": 18.25,
        },
        {
            "parallelism": 4,
            "peak_vram_mib": 8192,
            "peak_process_ram_mib": 9216,
            "peak_system_ram_mib": 11264,
            "elapsed_seconds": 31.75,
        },
    ]
    assert body["targets"][0]["semantic_id"] == "minecraft:deepslate"
    assert current.status_code == 200, current.text
    assert current.json() == {
        "project_id": str(PROJECT_ID),
        "job_id": str(created.request.job_id),
        "status": "queued",
    }


def test_generation_options_consumes_tracked_phase5_evidence(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    repository = ProjectRepository(projects_root)
    catalogs = CatalogRegistry(CATALOG_ROOT)
    store = JobStore(repository)
    references = ReferenceService(
        repository=repository,
        catalogs=catalogs,
        store=ProjectReferenceStore(repository),
    )
    generation = GenerationService(
        repository=repository,
        catalogs=catalogs,
        references=references,
        store=store,
        manifests=ManifestRegistry.load(REPO_ROOT),
        seed_source=iter((101, 102, 103, 104)).__next__,
        job_id_source=iter((JOB_ID, RETRY_JOB_ID)).__next__,
        clock=lambda: NOW,
    )

    options = generation.generation_options(PROJECT_ID)

    assert options["resource_hints"] == (
        {
            "parallelism": 1,
            "peak_vram_mib": 8516,
            "peak_process_ram_mib": 3644,
            "peak_system_ram_mib": 16271,
            "elapsed_seconds": 26.46799999999712,
        },
        {
            "parallelism": 2,
            "peak_vram_mib": 8535,
            "peak_process_ram_mib": 3644,
            "peak_system_ram_mib": 16355,
            "elapsed_seconds": 22.985000000000582,
        },
        {
            "parallelism": 4,
            "peak_vram_mib": 8491,
            "peak_process_ram_mib": 3661,
            "peak_system_ram_mib": 16356,
            "elapsed_seconds": 24.281000000002678,
        },
    )


@pytest.mark.parametrize(
    ("support_state", "include_evidence"),
    [
        ("candidate_unverified", True),
        ("verified", False),
    ],
    ids=("candidate-profile", "missing-evidence"),
)
def test_generation_options_fails_closed_without_verified_profile_evidence(
    tmp_path: Path,
    support_state: str,
    include_evidence: bool,
) -> None:
    app, _generation, _store, _repository = _app(
        tmp_path,
        support_state=support_state,
        include_evidence=include_evidence,
    )

    response = _request(app, "GET", f"/api/projects/{PROJECT_ID}/generation-options")

    _assert_error(
        response,
        status_code=422,
        code="PROFILE_NOT_READY",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("seed", 1),
        ("seeds", [1, 2, 3, 4]),
        ("compiled_positive", "bad"),
        ("workflow_variant", "text2img-style"),
        ("sampler", "euler"),
        ("steps", 28),
        ("cfg", 7),
        ("lora_weight", 1.2),
        ("artifact_path", "C:/private.png"),
        ("job_id", str(JOB_ID)),
        ("created_at", NOW.isoformat()),
    ],
    ids=(
        "seed",
        "seeds",
        "compiled-positive",
        "workflow-variant",
        "sampler",
        "steps",
        "cfg",
        "lora-weight",
        "artifact-path",
        "job-id",
        "created-at",
    ),
)
def test_create_generation_rejects_server_owned_or_unknown_transport_fields(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    app, _generation, _store, _repository = _app(tmp_path)
    payload = {
        "target": {"semantic_id": "minecraft:deepslate"},
        "description": "cold stone",
        "negative_prompt": "",
        "resolution": 16,
        "parallelism": 1,
        "references": {"style": [{"source": "pack", "relative_path": STYLE_REFERENCE}], "structure": None},
        "denoise": None,
        "style_weight": None,
        field: value,
    }

    response = _request(
        app,
        "POST",
        f"/api/projects/{PROJECT_ID}/jobs",
        json=payload,
    )

    _assert_error(
        response,
        status_code=422,
        code="INVALID_REQUEST",
        stage="request_validation",
    )


def test_generation_commands_round_trip_schema3_details_and_lineage_retry(
    tmp_path: Path,
) -> None:
    app, _generation, _store, _repository = _app(tmp_path)
    payload = {
        "target": {"semantic_id": "minecraft:deepslate"},
        "description": "cold stone",
        "negative_prompt": "",
        "resolution": 16,
        "parallelism": 1,
        "references": {"style": [{"source": "pack", "relative_path": STYLE_REFERENCE}], "structure": None},
        "denoise": None,
        "style_weight": None,
    }

    created = _request(
        app,
        "POST",
        f"/api/projects/{PROJECT_ID}/jobs",
        json=payload,
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["request"]["job_id"]
    started = _request(
        app,
        "POST",
        f"/api/projects/{PROJECT_ID}/jobs/{job_id}/start",
    )
    canceled = _request(
        app,
        "POST",
        f"/api/projects/{PROJECT_ID}/jobs/{job_id}/cancel",
    )
    retried = _request(
        app,
        "POST",
        f"/api/projects/{PROJECT_ID}/jobs/{job_id}/retry",
    )
    detail = _request(
        app,
        "GET",
        f"/api/projects/{PROJECT_ID}/jobs/{job_id}",
    )

    assert started.status_code == 200, started.text
    assert canceled.status_code == 200, canceled.text
    assert retried.status_code == 201, retried.text
    assert retried.json()["request"]["parent_job_id"] == job_id
    assert detail.status_code == 200, detail.text
    assert detail.json()["request"]["schema_version"] == 3
    assert detail.json()["state"]["job_id"] == job_id


def test_artifact_endpoint_serves_exact_bytes_with_etag_and_json_reports(
    tmp_path: Path,
) -> None:
    app, generation, store, _repository = _app(tmp_path)
    created = generation.create_job(PROJECT_ID, _command())
    completed = _publish_completed_candidate(generation, store, created)
    job_id = str(completed.request.job_id)
    final_artifact = completed.state.candidates[0].artifacts.final
    report_artifact = completed.state.candidates[0].artifacts.report
    assert final_artifact is not None
    assert report_artifact is not None

    image = _request(
        app,
        "GET",
        f"/api/projects/{PROJECT_ID}/jobs/{job_id}/candidates/0/artifacts/final",
    )
    report = _request(
        app,
        "GET",
        f"/api/projects/{PROJECT_ID}/jobs/{job_id}/candidates/0/artifacts/report",
    )

    assert image.status_code == 200, image.text
    assert image.headers["content-type"] == "image/png"
    assert image.headers["etag"] == final_artifact.sha256
    assert image.content == (completed.root / final_artifact.relative_path).read_bytes()
    assert report.status_code == 200, report.text
    assert report.headers["content-type"].startswith("application/json")
    assert report.headers["etag"] == report_artifact.sha256
    assert json.loads(report.text)["output"]["sha256"] == final_artifact.sha256


def test_artifact_endpoint_maps_absent_and_integrity_failures_without_paths(
    tmp_path: Path,
) -> None:
    app, generation, store, _repository = _app(tmp_path)
    created = generation.create_job(PROJECT_ID, _command())
    completed = _publish_completed_candidate(generation, store, created)
    job_id = str(completed.request.job_id)
    missing = _request(
        app,
        "GET",
        f"/api/projects/{PROJECT_ID}/jobs/{job_id}/candidates/1/artifacts/final",
    )
    final_artifact = completed.state.candidates[0].artifacts.final
    assert final_artifact is not None
    (completed.root / final_artifact.relative_path).write_bytes(b"broken")

    corrupt = _request(
        app,
        "GET",
        f"/api/projects/{PROJECT_ID}/jobs/{job_id}/candidates/0/artifacts/final",
    )

    _assert_error(
        missing,
        status_code=404,
        code="ARTIFACT_NOT_AVAILABLE",
    )
    _assert_error(
        corrupt,
        status_code=409,
        code="ARTIFACT_INTEGRITY_ERROR",
    )


def test_generation_routes_reject_noncanonical_ids_and_conflicts_stably(
    tmp_path: Path,
) -> None:
    app, generation, _store, _repository = _app(tmp_path)
    created = generation.create_job(PROJECT_ID, _command())
    payload = {
        "target": {"semantic_id": "minecraft:deepslate"},
        "description": "cold stone",
        "negative_prompt": "",
        "resolution": 16,
        "parallelism": 1,
        "references": {"style": [{"source": "pack", "relative_path": STYLE_REFERENCE}], "structure": None},
        "denoise": None,
        "style_weight": None,
    }

    invalid_project = _request(
        app,
        "GET",
        f"/api/projects/{str(PROJECT_ID).upper()}/generation-options",
    )
    invalid_job = _request(
        app,
        "POST",
        f"/api/projects/{PROJECT_ID}/jobs/{str(created.request.job_id).upper()}/start",
    )
    conflict = _request(
        app,
        "POST",
        f"/api/projects/{OTHER_PROJECT_ID}/jobs",
        json=payload,
    )

    _assert_error(
        invalid_project,
        status_code=400,
        code="INVALID_PROJECT_ID",
    )
    _assert_error(
        invalid_job,
        status_code=400,
        code="INVALID_JOB_ID",
    )
    body = _assert_error(
        conflict,
        status_code=409,
        code="GENERATION_JOB_CONFLICT",
    )
    assert body["recommended_actions"]
