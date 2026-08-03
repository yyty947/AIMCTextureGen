from __future__ import annotations

import hashlib
import io
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image

from aimctexturegen.comfy.client import ComfyOutputImage
from aimctexturegen.comfy.registry import ManifestRegistry
from aimctexturegen.generation.service import (
    CreateGenerationCommand,
    ExecutionContext,
    GenerationService,
)
from aimctexturegen.jobs.store import JobStore
from aimctexturegen.projects.models import ProjectManifest, dump_project_manifest
from aimctexturegen.projects.repository import ProjectRepository
from aimctexturegen.references.models import PackReferenceSelection, ReferenceSelections
from aimctexturegen.references.service import ReferenceService
from aimctexturegen.references.store import ProjectReferenceStore


PROJECT_ID = UUID("10101010-1010-4010-8010-101010101010")
JOB_ID = UUID("20202020-2020-4020-8020-202020202020")
NOW = datetime(2026, 8, 3, 16, 0, tzinfo=timezone.utc)
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
                project_name="Execution project",
                edition="java",
                java_pack_format=34,
                supported_formats=None,
                catalog_id="java-dev-format-34",
                source_sha256="ab" * 32,
                created_at=NOW,
                updated_at=NOW,
                default_resolution=16,
                default_parallelism=2,
                style_references=(),
            )
        )
    )
    return project_root


def _verified_registry(root: Path = REPO_ROOT) -> ManifestRegistry:
    loaded = ManifestRegistry.load(REPO_ROOT)
    profile = loaded.profile("sdxl-mapchip-ipadapter", "2").model_copy(
        update={"support_state": "verified"}
    )
    return ManifestRegistry(
        root=root,
        runtimes=loaded.runtimes,
        profiles={(profile.profile_id, profile.profile_version): profile},
    )


def _service(
    projects_root: Path,
    *,
    manifest_root: Path = REPO_ROOT,
    job_ids: tuple[UUID, ...] = (JOB_ID,),
) -> GenerationService:
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
        manifests=_verified_registry(manifest_root),
        seed_source=iter((101, 202)).__next__,
        job_id_source=iter(job_ids).__next__,
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


def _hash_tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class FakeComfyClient:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, bytes]] = []
        self.submitted_batch_sizes: list[int] = []
        self.events: list[str] = []
        self._prompt_index = 0
        self._history: dict[str, dict] = {}
        self._outputs: dict[str, bytes] = {}

    def upload_image(self, data: bytes, filename: str) -> dict:
        self.uploads.append((filename, data))
        return {"name": filename}

    def submit_prompt(self, workflow: dict) -> str:
        prompt_id = f"prompt-{self._prompt_index}"
        self._prompt_index += 1
        self.events.append(f"submit:{prompt_id}")
        batch_size = 0
        for node in workflow.values():
            if not isinstance(node, dict):
                continue
            inputs = node.get("inputs")
            if not isinstance(inputs, dict):
                continue
            if isinstance(inputs.get("batch_size"), int):
                batch_size = int(inputs["batch_size"])
                break
            if isinstance(inputs.get("amount"), int):
                batch_size = int(inputs["amount"])
                break
        self.submitted_batch_sizes.append(batch_size)
        images: list[dict[str, str]] = []
        for index in range(batch_size):
            filename = f"{prompt_id}-candidate-{index}.png"
            self._outputs[filename] = _png_bytes(size=1024, color=(10 + index, 20, 30))
            images.append({"filename": filename, "subfolder": "", "type": "output"})
        self._history[prompt_id] = {"outputs": {"19": {"images": images}}}
        return prompt_id

    def wait_completion(self, prompt_id: str, **_kwargs) -> dict:
        self.events.append(f"wait:{prompt_id}")
        return self._history[prompt_id]

    def declared_output_images(
        self,
        history_entry: dict,
        *,
        output_node_id: str,
    ) -> tuple[ComfyOutputImage, ...]:
        return tuple(
            ComfyOutputImage(
                filename=image["filename"],
                subfolder=image["subfolder"],
                type="output",
            )
            for image in history_entry["outputs"][output_node_id]["images"]
        )

    def get_output_image(self, image: ComfyOutputImage) -> bytes:
        return self._outputs[image.filename]


def test_run_job_executes_persisted_batches_in_order_and_commits_candidates(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root)
    service = _service(projects_root)
    created = service.create_job(PROJECT_ID, _command())
    fake_client = FakeComfyClient()
    registered: list[str] = []
    committed: list[tuple[int, tuple[str, str, str, str]]] = []
    before_source = _hash_tree(project_root / "source")
    before_pack = _hash_tree(project_root / "pack")

    completed = service.run_job(
        PROJECT_ID,
        JOB_ID,
        ExecutionContext(
            client=fake_client,
            cancel_requested=lambda: False,
            prompt_registered=registered.append,
            state_committed=lambda loaded: committed.append(
                (
                    loaded.state.revision,
                    tuple(candidate.status for candidate in loaded.state.candidates),
                )
            ),
        ),
    )

    assert created.request.execution_batches[0].candidate_indices == (0, 1)
    assert completed.state.status == "completed"
    assert fake_client.submitted_batch_sizes == [2, 2]
    assert [candidate.status for candidate in completed.state.candidates] == [
        "completed",
        "completed",
        "completed",
        "completed",
    ]
    assert registered == ["prompt-0", "prompt-1"]
    assert fake_client.events.index("wait:prompt-0") < fake_client.events.index(
        "submit:prompt-1"
    )
    assert all(str(JOB_ID) in name for name, _ in fake_client.uploads)
    assert any("style-00" in name for name, _ in fake_client.uploads)
    assert any(
        statuses == ("completed", "raw_ready", "pending", "pending")
        for _revision, statuses in committed
    )
    assert any(
        statuses == ("completed", "completed", "pending", "pending")
        for _revision, statuses in committed
    )
    assert before_source == _hash_tree(project_root / "source")
    assert before_pack == _hash_tree(project_root / "pack")


def test_prompt_registration_precedes_prompt_id_state_write(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    service = _service(projects_root)
    service.create_job(PROJECT_ID, _command())
    events: list[str] = []

    class OrderingClient(FakeComfyClient):
        def submit_prompt(self, workflow: dict) -> str:
            events.append("submit")
            return super().submit_prompt(workflow)

    completed = service.run_job(
        PROJECT_ID,
        JOB_ID,
        ExecutionContext(
            client=OrderingClient(),
            cancel_requested=lambda: False,
            prompt_registered=lambda _prompt_id: events.append("registered"),
            state_committed=lambda _loaded: events.append("state"),
        ),
    )

    assert completed.state.status == "completed"
    submit_index = events.index("submit")
    assert events[submit_index : submit_index + 3] == [
        "submit",
        "registered",
        "state",
    ]


def test_prompt_is_registered_when_prompt_id_state_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    service = _service(projects_root)
    service.create_job(PROJECT_ID, _command())
    original_replace_state = service._store.replace_state
    registered: list[str] = []

    def fail_prompt_id_write(
        project_id: UUID,
        job_id: UUID,
        replacement,
        *,
        expected_revision: int,
    ):
        if replacement.batches[0].prompt_id == "prompt-0":
            raise OSError("prompt id state write failed")
        return original_replace_state(
            project_id,
            job_id,
            replacement,
            expected_revision=expected_revision,
        )

    monkeypatch.setattr(service._store, "replace_state", fail_prompt_id_write)
    failed = service.run_job(
        PROJECT_ID,
        JOB_ID,
        ExecutionContext(
            client=FakeComfyClient(),
            cancel_requested=lambda: False,
            prompt_registered=registered.append,
        ),
    )

    assert registered == ["prompt-0"]
    assert failed.state.status == "failed"


def test_changed_workflow_bytes_fail_before_prompt_submission(tmp_path: Path) -> None:
    manifest_root = tmp_path / "manifest-root"
    shutil.copytree(REPO_ROOT / "workflows", manifest_root / "workflows")
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    service = _service(projects_root, manifest_root=manifest_root)
    service.create_job(PROJECT_ID, _command())

    workflow_path = (
        manifest_root
        / "workflows"
        / "sdxl-mapchip-ipadapter-v2"
        / "text2img-style.api.json"
    )
    workflow_path.write_bytes(workflow_path.read_bytes() + b"\nmutated")
    fake_client = FakeComfyClient()

    failed = service.run_job(
        PROJECT_ID,
        JOB_ID,
        ExecutionContext(client=fake_client, cancel_requested=lambda: False),
    )

    assert failed.state.status == "failed"
    assert failed.state.failure is not None
    assert failed.state.failure.error_code == "PROFILE_NOT_READY"
    assert fake_client.submitted_batch_sizes == []
