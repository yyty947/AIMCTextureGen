from __future__ import annotations

import io
import threading
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from PIL import Image
import pytest
from starlette.exceptions import StarletteDeprecationWarning

from aimctexturegen.catalog.registry import CatalogRegistry
from aimctexturegen.comfy.manifests import ModelProfileManifestV2, RuntimeManifest
from aimctexturegen.comfy.registry import ManifestRegistry
from aimctexturegen.generation.events import JobEventBroker
from aimctexturegen.generation.service import CreateGenerationCommand, GenerationService
from aimctexturegen.jobs.generation_state import start_generation
from aimctexturegen.jobs.store import JobStore
from aimctexturegen.main import AppServices, create_app
from aimctexturegen.projects.models import ProjectManifest, dump_project_manifest
from aimctexturegen.projects.repository import ProjectRepository
from aimctexturegen.references.models import PackReferenceSelection, ReferenceSelections
from aimctexturegen.references.service import ReferenceService
from aimctexturegen.references.store import ProjectReferenceStore

warnings.filterwarnings("ignore", category=StarletteDeprecationWarning)
from starlette.testclient import TestClient


CATALOG_ROOT = Path(__file__).parents[3] / "catalogs" / "java"
PROJECT_ID = UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
JOB_ID = UUID("12345678-1234-4abc-8def-123456789abc")
NOW = datetime(2026, 8, 3, 11, 0, tzinfo=timezone.utc)
STYLE_REFERENCE = "assets/minecraft/textures/block/stone.png"


def _png_bytes(
    *,
    size: int = 16,
    color: tuple[int, int, int] = (64, 64, 64),
) -> bytes:
    payload = io.BytesIO()
    Image.new("RGB", (size, size), color).save(payload, format="PNG")
    return payload.getvalue()


def _write_project(projects_root: Path) -> None:
    project_root = projects_root / str(PROJECT_ID)
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
                project_id=PROJECT_ID,
                project_name="WebSocket project",
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


def make_profile_v2() -> dict:
    return {
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
                "sha256": "a" * 64,
                "output_node_id": "19",
            },
            {
                "variant": "text2img-style",
                "relative_path": "sdxl-mapchip-ipadapter-v2/text2img-style.api.json",
                "sha256": "b" * 64,
                "output_node_id": "19",
            },
            {
                "variant": "img2img-no-style",
                "relative_path": "sdxl-mapchip-ipadapter-v2/img2img-no-style.api.json",
                "sha256": "c" * 64,
                "output_node_id": "19",
            },
            {
                "variant": "img2img-style",
                "relative_path": "sdxl-mapchip-ipadapter-v2/img2img-style.api.json",
                "sha256": "d" * 64,
                "output_node_id": "19",
            },
        ],
        "required_node_classes": ("CheckpointLoaderSimple",),
        "output_contract": {"format": "png", "color_mode": "rgb", "canvas_size": 1024},
        "profile_defaults": {"resolution": 16, "parallelism": 1, "candidate_count": 4},
        "user_limitations": "test",
        "revision_notes": None,
    }


class _InferenceStub:
    def ensure_generation_ready(self, _binding):
        raise AssertionError("WebSocket tests never start generation")

    def stop_comfyui(self):
        return {"state": "stopped"}

    def shutdown(self):
        return None


class _NoOpRecovery:
    def recover(self):
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


def _app(tmp_path: Path):
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
    runtime = RuntimeManifest.model_validate(make_runtime())
    profile = ModelProfileManifestV2.model_validate(make_profile_v2())
    registry = ManifestRegistry(
        root=tmp_path,
        runtimes={runtime.runtime_id: runtime},
        profiles={(profile.profile_id, profile.profile_version): profile},
    )
    generation = GenerationService(
        repository=repository,
        catalogs=catalogs,
        references=references,
        store=store,
        manifests=registry,
        seed_source=iter((101, 102, 103, 104)).__next__,
        job_id_source=iter((JOB_ID,)).__next__,
        clock=lambda: NOW,
    )
    broker = JobEventBroker()
    services = AppServices(
        workspace=object(),
        catalogs=catalogs,
        project_root=projects_root,
        repository=repository,
        job_store=store,
        manifest_registry=registry,
        reference_service=references,
        generation_service=generation,
        job_events=broker,
        inference=_InferenceStub(),
        recovery_service=_NoOpRecovery(),
    )
    return create_app(services=services), generation, store, broker


def test_job_events_socket_sends_snapshot_then_only_higher_revisions_and_heartbeats(
    tmp_path: Path,
) -> None:
    app, generation, store, broker = _app(tmp_path)
    created = generation.create_job(PROJECT_ID, _command())

    with TestClient(app) as client:
        with client.websocket_connect(
            f"/api/projects/{PROJECT_ID}/jobs/{created.request.job_id}/events"
        ) as socket:
            first = socket.receive_json()
            first_started = store.replace_state(
                PROJECT_ID,
                created.request.job_id,
                start_generation(created.state, now=NOW),
                expected_revision=created.state.revision,
            )
            broker.publish(PROJECT_ID, created.request.job_id, first_started.state.revision)
            second = socket.receive_json()
            heartbeat = socket.receive_json()

        assert first["type"] == "snapshot"
        assert first["revision"] == 0
    assert first["job"]["state"]["revision"] == 0
    assert second == {
        "type": "snapshot",
        "revision": 1,
        "job": {
            "request": first_started.request.model_dump(mode="json"),
            "state": first_started.state.model_dump(mode="json"),
        },
    }
    assert heartbeat == {"type": "heartbeat"}


def test_websocket_disconnect_is_read_only_and_does_not_cancel_job(
    tmp_path: Path,
) -> None:
    app, generation, store, _broker = _app(tmp_path)
    created = generation.create_job(PROJECT_ID, _command())

    with TestClient(app) as client:
        with client.websocket_connect(
            f"/api/projects/{PROJECT_ID}/jobs/{created.request.job_id}/events"
        ) as socket:
            assert socket.receive_json()["type"] == "snapshot"

    assert store.load(PROJECT_ID, created.request.job_id).state.status == "queued"


def test_websocket_missing_job_and_corrupt_job_close_without_path_details(
    tmp_path: Path,
) -> None:
    app, generation, _store, _broker = _app(tmp_path)
    created = generation.create_job(PROJECT_ID, _command())
    corrupt_state = (
        tmp_path
        / "projects"
        / str(PROJECT_ID)
        / "jobs"
        / str(created.request.job_id)
        / "state.json"
    )
    corrupt_state.write_text('{"secret":"C:/private"', encoding="utf-8")

    with TestClient(app) as client:
        with client.websocket_connect(
            f"/api/projects/{PROJECT_ID}/jobs/ffffffff-ffff-4fff-8fff-ffffffffffff/events"
        ) as socket:
            with pytest.raises(Exception) as missing:
                socket.receive_json()
        with client.websocket_connect(
            f"/api/projects/{PROJECT_ID}/jobs/{created.request.job_id}/events"
        ) as socket:
            with pytest.raises(Exception) as corrupt:
                socket.receive_json()

    assert "C:/" not in str(missing.value)
    assert "C:/" not in str(corrupt.value)


def test_websocket_reloads_latest_snapshot_after_revision_gaps(
    tmp_path: Path,
) -> None:
    app, generation, store, broker = _app(tmp_path)
    created = generation.create_job(PROJECT_ID, _command())
    with TestClient(app) as client:
        with client.websocket_connect(
            f"/api/projects/{PROJECT_ID}/jobs/{created.request.job_id}/events"
        ) as socket:
            assert socket.receive_json()["revision"] == 0
            next_state = store.replace_state(
                PROJECT_ID,
                created.request.job_id,
                start_generation(created.state, now=NOW),
                expected_revision=created.state.revision,
            )

            def publish_later() -> None:
                time.sleep(0.05)
                broker.publish(PROJECT_ID, created.request.job_id, 7)

            worker = threading.Thread(target=publish_later, daemon=True)
            worker.start()
            latest = socket.receive_json()

    worker.join(timeout=1.0)
    assert latest["type"] == "snapshot"
    assert latest["revision"] == next_state.state.revision
    assert latest["job"]["state"]["revision"] == next_state.state.revision
