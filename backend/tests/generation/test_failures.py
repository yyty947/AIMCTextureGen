from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image

from aimctexturegen.comfy.client import ComfyOutputImage
from aimctexturegen.comfy.errors import (
    ComfyDisconnectedError,
    ComfyExecutionError,
    ComfyQueueError,
    ComfyTimeoutError,
)
from aimctexturegen.comfy.registry import ManifestRegistry
from aimctexturegen.generation.service import (
    CreateGenerationCommand,
    ExecutionContext,
    GenerationService,
)
from aimctexturegen.jobs.store import JobStore
from aimctexturegen.processing.errors import ProcessingError
from aimctexturegen.projects.models import ProjectManifest, dump_project_manifest
from aimctexturegen.projects.repository import ProjectRepository
from aimctexturegen.references.models import PackReferenceSelection, ReferenceSelections
from aimctexturegen.references.service import ReferenceService
from aimctexturegen.references.store import ProjectReferenceStore


PROJECT_ID = UUID("30303030-3030-4030-8030-303030303030")
JOB_ID = UUID("40404040-4040-4040-8040-404040404040")
NOW = datetime(2026, 8, 3, 17, 0, tzinfo=timezone.utc)
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


def _write_project(projects_root: Path) -> Path:
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
                project_name="Failure project",
                edition="java",
                java_pack_format=34,
                supported_formats=None,
                catalog_id="java-dev-format-34",
                source_sha256="cd" * 32,
                created_at=NOW,
                updated_at=NOW,
                default_resolution=16,
                default_parallelism=1,
                style_references=(),
            )
        )
    )
    return project_root


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
        seed_source=iter((111, 222, 333, 444)).__next__,
        job_id_source=lambda: JOB_ID,
        clock=lambda: NOW,
    )


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
                    relative_path="assets/minecraft/textures/block/stone.png",
                ),
            ),
            structure=None,
        ),
    )


def _hash_tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class FailingClient:
    def __init__(self, error: Exception, *, during: str = "submit") -> None:
        self._error = error
        self._during = during

    def upload_image(self, data: bytes, filename: str) -> dict:
        return {"name": filename}

    def submit_prompt(self, workflow: dict) -> str:
        if self._during == "submit":
            raise self._error
        return "prompt-0"

    def wait_completion(self, prompt_id: str, **_kwargs) -> dict:
        if self._during == "wait":
            raise self._error
        return {"outputs": {"19": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}}}

    def declared_output_images(
        self,
        history_entry: dict,
        *,
        output_node_id: str,
    ) -> tuple[ComfyOutputImage, ...]:
        return (
            ComfyOutputImage(filename="out.png", subfolder="", type="output"),
        )

    def get_output_image(self, image: ComfyOutputImage) -> bytes:
        return _png_bytes(size=1024, color=(1, 2, 3))


def _oom_execution_error() -> ComfyExecutionError:
    return ComfyExecutionError("CUDA out of memory while executing prompt")


@pytest.mark.parametrize(
    ("error", "during", "code"),
    [
        (ComfyQueueError("queue rejected"), "submit", "COMFY_QUEUE_REJECTED"),
        (ComfyDisconnectedError("socket dropped"), "wait", "COMFY_DISCONNECTED"),
        (ComfyTimeoutError("deadline exceeded"), "wait", "COMFY_TIMEOUT"),
        (_oom_execution_error(), "wait", "GPU_OUT_OF_MEMORY"),
        (ComfyExecutionError("sampler failed"), "wait", "COMFY_EXECUTION_FAILED"),
    ],
)
def test_execution_failure_is_persisted_without_parameter_mutation(
    tmp_path: Path,
    error: Exception,
    during: str,
    code: str,
) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root)
    service = _service(projects_root)
    created = service.create_job(PROJECT_ID, _command())
    before_source = _hash_tree(project_root / "source")
    before_pack = _hash_tree(project_root / "pack")

    failed = service.run_job(
        PROJECT_ID,
        JOB_ID,
        ExecutionContext(
            client=FailingClient(error, during=during),
            cancel_requested=lambda: False,
        ),
    )

    assert failed.state.status == "failed"
    assert failed.state.failure is not None
    assert failed.state.failure.error_code == code
    assert failed.request == created.request
    assert before_source == _hash_tree(project_root / "source")
    assert before_pack == _hash_tree(project_root / "pack")
    if code == "GPU_OUT_OF_MEMORY":
        assert failed.state.failure.recommended_actions == (
            "用更低并行度重新创建一个新任务",
            "关闭其他占用显存的应用程序",
            "停止其他 ComfyUI 实例",
        )


def test_processing_failure_is_persisted_without_mutating_request_or_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root)
    service = _service(projects_root)
    created = service.create_job(PROJECT_ID, _command())
    before_source = _hash_tree(project_root / "source")
    before_pack = _hash_tree(project_root / "pack")

    class SuccessClient(FailingClient):
        def __init__(self) -> None:
            super().__init__(ComfyQueueError("unused"))

        def submit_prompt(self, workflow: dict) -> str:
            return "prompt-0"

        def wait_completion(self, prompt_id: str, **_kwargs) -> dict:
            return {
                "outputs": {
                    "19": {
                        "images": [
                            {"filename": "out.png", "subfolder": "", "type": "output"}
                        ]
                    }
                }
            }

    def fail_processing(*_args, **_kwargs):
        raise ProcessingError("BROKEN_REPORT", "cannot postprocess")

    monkeypatch.setattr(service._artifacts, "process_and_publish", fail_processing)

    failed = service.run_job(
        PROJECT_ID,
        JOB_ID,
        ExecutionContext(
            client=SuccessClient(),
            cancel_requested=lambda: False,
        ),
    )

    assert failed.state.status == "failed"
    assert failed.state.failure is not None
    assert failed.state.failure.error_code == "POSTPROCESSING_FAILED"
    assert failed.request == created.request
    assert before_source == _hash_tree(project_root / "source")
    assert before_pack == _hash_tree(project_root / "pack")
