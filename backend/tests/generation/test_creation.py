from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image

from aimctexturegen.comfy.manifests import ModelProfileManifestV2, RuntimeManifest
from aimctexturegen.comfy.registry import ManifestRegistry
from aimctexturegen.generation.errors import GenerationError
from aimctexturegen.generation.service import (
    CreateGenerationCommand,
    GenerationService,
    build_execution_batches,
    build_generation_profile_binding,
)
from aimctexturegen.jobs.models_v3 import ExecutionBatch
from aimctexturegen.jobs.store import JobStore
from aimctexturegen.projects.models import ProjectManifest, dump_project_manifest
from aimctexturegen.projects.repository import ProjectRepository
from aimctexturegen.references.models import (
    PackReferenceSelection,
    ReferenceSelections,
    UploadReferenceSelection,
)
from aimctexturegen.references.service import ReferenceService
from aimctexturegen.references.store import ProjectReferenceStore

PROJECT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
JOB_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
CATALOG_ROOT = Path(__file__).parents[3] / "catalogs" / "java"
SEED_SOURCE = (101, 202, 303, 404)


def _png(path: Path, *, color: tuple[int, int, int] = (64, 64, 64)) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(payload, format="PNG")
    data = payload.getvalue()
    path.write_bytes(data)
    return data


def _manifest() -> ProjectManifest:
    return ProjectManifest(
        schema_version=2,
        project_id=PROJECT_ID,
        project_name="Generation project",
        edition="java",
        java_pack_format=34,
        supported_formats=None,
        catalog_id="java-dev-format-34",
        source_sha256="ef" * 32,
        created_at=NOW,
        updated_at=NOW,
        default_resolution=16,
        default_parallelism=1,
        style_references=(),
    )


def _write_project(
    projects_root: Path,
    *,
    include_target: bool = False,
    style_color: tuple[int, int, int] = (40, 40, 40),
) -> Path:
    project_root = projects_root / str(PROJECT_ID)
    (project_root / "source").mkdir(parents=True)
    pack_root = project_root / "pack"
    pack_root.mkdir()
    (project_root / "source" / "imported-pack.zip").write_bytes(b"snapshot")
    (project_root / "pack" / "pack.mcmeta").write_bytes(b"metadata")
    _png(pack_root / "assets/minecraft/textures/block/stone.png", color=style_color)
    if include_target:
        _png(pack_root / "assets/minecraft/textures/block/deepslate.png")
    (project_root / "project.json").write_bytes(dump_project_manifest(_manifest()))
    return project_root


class SequenceSeeds:
    def __init__(self, values: tuple[int, ...]) -> None:
        self._values = iter(values)
        self.calls = 0

    def __call__(self) -> int:
        self.calls += 1
        return next(self._values)


class SequenceIds:
    def __init__(self, values: tuple[UUID, ...]) -> None:
        self._values = iter(values)
        self.calls = 0

    def __call__(self) -> UUID:
        self.calls += 1
        return next(self._values)


def _registry(tmp_path: Path, *, support_state: str = "verified") -> ManifestRegistry:
    runtime = RuntimeManifest.model_validate(make_runtime())
    profile = ModelProfileManifestV2.model_validate(
        make_profile_v2(
            support_state=support_state,
            workflows=[
                {
                    **workflow,
                    "sha256": hashlib.sha256(workflow["variant"].encode("utf-8")).hexdigest(),
                }
                for workflow in make_profile_v2()["workflows"]
            ],
        )
    )
    return ManifestRegistry(
        root=tmp_path,
        runtimes={runtime.runtime_id: runtime},
        profiles={(profile.profile_id, profile.profile_version): profile},
    )


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


def make_profile_v2(**overrides: object) -> dict:
    manifest = {
        "schema_version": 2,
        "profile_id": "sdxl-mapchip-ipadapter",
        "profile_version": "2",
        "support_state": "verified",
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
                "revision": "abcdef",
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
                "sha256": None,
                "output_node_id": "19",
            },
            {
                "variant": "text2img-style",
                "relative_path": "sdxl-mapchip-ipadapter-v2/text2img-style.api.json",
                "sha256": None,
                "output_node_id": "19",
            },
            {
                "variant": "img2img-no-style",
                "relative_path": "sdxl-mapchip-ipadapter-v2/img2img-no-style.api.json",
                "sha256": None,
                "output_node_id": "19",
            },
            {
                "variant": "img2img-style",
                "relative_path": "sdxl-mapchip-ipadapter-v2/img2img-style.api.json",
                "sha256": None,
                "output_node_id": "19",
            },
        ],
        "required_node_classes": ("CheckpointLoaderSimple",),
        "output_contract": {"format": "png", "color_mode": "rgb", "canvas_size": 1024},
        "profile_defaults": {},
        "user_limitations": "test",
        "revision_notes": None,
    }
    manifest.update(overrides)
    return manifest


def _reference_service(projects_root: Path) -> ReferenceService:
    repository = ProjectRepository(projects_root)
    return ReferenceService(
        repository=repository,
        catalogs=__import__("aimctexturegen.catalog.registry", fromlist=["CatalogRegistry"]).CatalogRegistry(CATALOG_ROOT),
        store=ProjectReferenceStore(repository),
    )


def _service(
    projects_root: Path,
    tmp_path: Path,
    *,
    support_state: str = "verified",
    seeds: SequenceSeeds | None = None,
    ids: SequenceIds | None = None,
) -> tuple[GenerationService, JobStore, ReferenceService, SequenceSeeds, SequenceIds]:
    repository = ProjectRepository(projects_root)
    store = JobStore(repository)
    reference_service = _reference_service(projects_root)
    seed_source = seeds or SequenceSeeds(SEED_SOURCE)
    id_source = ids or SequenceIds((JOB_ID,))
    service = GenerationService(
        repository=repository,
        catalogs=__import__("aimctexturegen.catalog.registry", fromlist=["CatalogRegistry"]).CatalogRegistry(CATALOG_ROOT),
        references=reference_service,
        store=store,
        manifests=_registry(tmp_path, support_state=support_state),
        seed_source=seed_source,
        job_id_source=id_source,
        clock=lambda: NOW,
    )
    return service, store, reference_service, seed_source, id_source


def _command(
    *,
    target_semantic_id: str = "minecraft:deepslate",
    parallelism: int = 1,
    references: ReferenceSelections | None = None,
    denoise: float | None = None,
    style_weight: float | None = None,
) -> CreateGenerationCommand:
    return CreateGenerationCommand(
        target_semantic_id=target_semantic_id,
        user_description="  cold   blue-gray\nstone  ",
        user_negative_prompt="  neon,   glossy ",
        resolution=16,
        parallelism=parallelism,
        references=references
        or ReferenceSelections(
            style=(
                PackReferenceSelection(
                    source="pack",
                    relative_path="assets/minecraft/textures/block/stone.png",
                ),
            ),
            structure=None,
        ),
        denoise=denoise,
        style_weight=style_weight,
    )


@pytest.mark.parametrize(
    ("parallelism", "expected"),
    [
        (1, ((0,), (1,), (2,), (3,))),
        (2, ((0, 1), (2, 3))),
        (4, ((0, 1, 2, 3),)),
    ],
)
def test_build_execution_batches_preserves_exact_native_partitions(
    parallelism: int,
    expected: tuple[tuple[int, ...], ...],
) -> None:
    source = SequenceSeeds(SEED_SOURCE)

    batches = build_execution_batches(parallelism=parallelism, seed_source=source)

    assert tuple(batch.candidate_indices for batch in batches) == expected
    assert [batch.seed for batch in batches] == list(SEED_SOURCE[: len(expected)])
    assert all(isinstance(batch, ExecutionBatch) for batch in batches)


@pytest.mark.parametrize(
    ("parallelism", "expected", "workflow_variant"),
    [
        (1, ((0,), (1,), (2,), (3,)), "text2img-style"),
        (2, ((0, 1), (2, 3)), "text2img-style"),
        (4, ((0, 1, 2, 3),), "text2img-style"),
    ],
)
def test_create_freezes_exact_native_batches_and_inputs(
    tmp_path: Path,
    parallelism: int,
    expected: tuple[tuple[int, ...], ...],
    workflow_variant: str,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    service, store, _references, seeds, ids = _service(projects_root, tmp_path)

    loaded = service.create_job(PROJECT_ID, _command(parallelism=parallelism))

    assert tuple(batch.candidate_indices for batch in loaded.request.execution_batches) == expected
    assert [batch.seed for batch in loaded.request.execution_batches] == list(
        SEED_SOURCE[: len(expected)]
    )
    assert loaded.request.model_profile.workflow_variant == workflow_variant
    assert loaded.request.prompt.user_prompt == "cold blue-gray stone"
    assert (loaded.root / "inputs" / "references.json").is_file()
    assert store.load(PROJECT_ID, JOB_ID) == loaded
    assert seeds.calls == len(expected)
    assert ids.calls == 1


def test_create_uses_no_style_variant_when_no_style_reference_is_selected(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    service, _store, _references, _seeds, _ids = _service(projects_root, tmp_path)

    loaded = service.create_job(
        PROJECT_ID,
        _command(references=ReferenceSelections(style=(), structure=None)),
    )

    assert loaded.request.model_profile.workflow_variant == "text2img-no-style"
    assert loaded.request.references.style == ()


def test_create_uses_img2img_variant_when_structure_reference_is_selected(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    service, _store, references, _seeds, _ids = _service(projects_root, tmp_path)
    payload = _png(tmp_path / "structure.png", color=(80, 80, 80))
    uploaded = references.upload(PROJECT_ID, "structure", payload)

    loaded = service.create_job(
        PROJECT_ID,
        _command(
            references=ReferenceSelections(
                style=(),
                structure=UploadReferenceSelection(
                    source="upload",
                    reference_id=uploaded.reference_id,
                ),
            ),
            denoise=0.55,
        ),
    )

    assert loaded.request.model_profile.workflow_variant == "img2img-no-style"
    assert loaded.request.advanced.denoise_strength == 0.55
    assert len(loaded.request.references.structure) == 1


@pytest.mark.parametrize(
    ("include_target", "support_state", "command", "expected_code"),
    [
        (True, "verified", None, "JOB_TARGET_NOT_MISSING"),
        (False, "candidate_unverified", None, "PROFILE_NOT_READY"),
        (
            False,
            "verified",
            {"target_semantic_id": "minecraft:missing"},
            "JOB_TARGET_NOT_FOUND",
        ),
        (
            False,
            "verified",
            {"references": ReferenceSelections(style=(), structure=None), "style_weight": 1.2},
            "REFERENCE_INVALID",
        ),
    ],
)
def test_failed_validation_creates_no_job_directory_and_consumes_no_seed(
    tmp_path: Path,
    include_target: bool,
    support_state: str,
    command: dict | None,
    expected_code: str,
) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root, include_target=include_target)
    service, store, _references, seeds, ids = _service(
        projects_root,
        tmp_path,
        support_state=support_state,
    )

    kwargs = command or {}
    with pytest.raises(GenerationError) as captured:
        service.create_job(PROJECT_ID, _command(**kwargs))

    assert captured.value.code == expected_code
    assert seeds.calls == 0
    assert ids.calls == 0
    assert not (project_root / "jobs").exists()
    assert store.list(PROJECT_ID) == ()


def test_new_job_uses_new_native_batch_seeds(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    ids = SequenceIds(
        (
            UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        )
    )
    seeds = SequenceSeeds((11, 22, 33, 44, 55, 66, 77, 88))
    service, _store, _references, seed_source, _ids = _service(
        projects_root,
        tmp_path,
        seeds=seeds,
        ids=ids,
    )

    first = service.create_job(PROJECT_ID, _command(parallelism=1))
    second = service.create_job(PROJECT_ID, _command(parallelism=1))

    assert [batch.seed for batch in first.request.execution_batches] == [11, 22, 33, 44]
    assert [batch.seed for batch in second.request.execution_batches] == [55, 66, 77, 88]
    assert seed_source.calls == 8


def test_build_generation_profile_binding_derives_exact_variant_and_output_node(
    tmp_path: Path,
) -> None:
    binding = build_generation_profile_binding(
        _registry(tmp_path),
        profile_id="sdxl-mapchip-ipadapter",
        profile_version="2",
        style_reference_count=0,
        structure_reference_present=True,
    )

    assert binding.workflow_variant == "img2img-no-style"
    assert binding.output_node_id == "19"
    assert binding.profile_version == "2"


def test_build_generation_profile_binding_rejects_unverified_and_mismatched_workflows(
    tmp_path: Path,
) -> None:
    with pytest.raises(GenerationError) as profile_not_ready:
        build_generation_profile_binding(
            _registry(tmp_path, support_state="candidate_unverified"),
            profile_id="sdxl-mapchip-ipadapter",
            profile_version="2",
            style_reference_count=1,
            structure_reference_present=False,
        )
    assert profile_not_ready.value.code == "PROFILE_NOT_READY"
