from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import time
import warnings
from pathlib import Path
from uuid import UUID

from starlette.exceptions import StarletteDeprecationWarning

warnings.filterwarnings("ignore", category=StarletteDeprecationWarning)
from fastapi.testclient import TestClient
from PIL import Image

from aimctexturegen.catalog.registry import CatalogRegistry
from aimctexturegen.comfy.client import ComfyClient
from aimctexturegen.comfy.manifests import manifest_sha256
from aimctexturegen.comfy.registry import ManifestRegistry
from aimctexturegen.generation.events import JobEventBroker
from aimctexturegen.generation.service import CreateGenerationCommand, GenerationService
from aimctexturegen.index.database import ProjectIndex
from aimctexturegen.index.service import IndexService
from aimctexturegen.jobs.recovery import RecoveryService
from aimctexturegen.jobs.store import JobStore
from aimctexturegen.main import AppServices, create_app
from aimctexturegen.packs.java_adapter import JavaPackAdapter
from aimctexturegen.projects.repository import ProjectRepository
from aimctexturegen.projects.workspace import ProjectWorkspace
from aimctexturegen.references.models import ReferenceSelections
from aimctexturegen.references.service import ReferenceService
from aimctexturegen.references.store import ProjectReferenceStore

from backend.tests.fakes.comfy_server import FakeComfyServer


ROOT = Path(__file__).parents[3]
CATALOG = ROOT / "catalogs" / "java"


def _png_bytes(
    *,
    size: int = 16,
    color: tuple[int, int, int] = (32, 96, 160),
) -> bytes:
    payload = io.BytesIO()
    Image.new("RGB", (size, size), color).save(payload, format="PNG")
    return payload.getvalue()


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _phase5_pack(tmp_path: Path) -> Path:
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    assert powershell is not None
    output = tmp_path / "phase5-synthetic.zip"
    result = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "tools/Generate-SyntheticPack.ps1"),
            "-OutputPath",
            str(output),
            "-Phase5",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return output


class _FastComfyClient(ComfyClient):
    """Use the real transport with a bounded test deadline for timeout cases."""

    def __init__(self, *args, completion_timeout_seconds: float, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._test_completion_timeout_seconds = completion_timeout_seconds

    def wait_completion(
        self,
        prompt_id: str,
        *,
        timeout: float = 60.0,
        progress=None,
        cancel_requested=None,
    ) -> dict:
        return super().wait_completion(
            prompt_id,
            timeout=min(timeout, self._test_completion_timeout_seconds),
            progress=progress,
            cancel_requested=cancel_requested,
        )


class _Inference:
    def __init__(
        self,
        server: FakeComfyServer,
        *,
        completion_timeout_seconds: float,
    ) -> None:
        self.client = _FastComfyClient(
            server.base_url,
            ws_base_url=server.ws_url,
            completion_timeout_seconds=completion_timeout_seconds,
        )

    def ensure_generation_ready(self, _binding):
        return self.client

    def stop_comfyui(self):
        return {"state": "stopped"}

    def shutdown(self):
        self.client.close()


def _build_app(
    projects_root: Path,
    server: FakeComfyServer,
    *,
    completion_timeout_seconds: float = 0.25,
):
    projects_root.mkdir(parents=True, exist_ok=True)
    workspace = ProjectWorkspace(projects_root, JavaPackAdapter(), CatalogRegistry(CATALOG))
    repository = ProjectRepository(projects_root)
    store = JobStore(repository)
    catalogs = CatalogRegistry(CATALOG)
    references = ReferenceService(
        repository=repository,
        catalogs=catalogs,
        store=ProjectReferenceStore(repository),
    )
    loaded = ManifestRegistry.load(ROOT)
    profile = loaded.profile("sdxl-mapchip-ipadapter", "2").model_copy(
        update={"support_state": "verified"}
    )
    registry = ManifestRegistry(
        root=ROOT,
        runtimes=loaded.runtimes,
        profiles={(profile.profile_id, profile.profile_version): profile},
    )
    generation = GenerationService(
        repository=repository,
        catalogs=catalogs,
        references=references,
        store=store,
        manifests=registry,
        profile_evidence={
            (profile.profile_id, profile.profile_version): {
                "profile_id": profile.profile_id,
                "profile_version": profile.profile_version,
                "profile_manifest_sha256": manifest_sha256(profile),
                "resource_hints": [
                    {
                        "parallelism": value,
                        "peak_vram_mib": 1,
                        "peak_process_ram_mib": 1,
                        "peak_system_ram_mib": 1,
                        "elapsed_seconds": 1,
                    }
                    for value in (1, 2, 4)
                ],
            }
        },
    )
    index = ProjectIndex(projects_root)
    index_service = IndexService(repository=repository, store=store, index=index)
    services = AppServices(
        workspace=workspace,
        catalogs=catalogs,
        project_root=projects_root,
        repository=repository,
        job_store=store,
        project_index=index,
        index_service=index_service,
        recovery_service=RecoveryService(
            repository=repository,
            store=store,
            index=index_service,
        ),
        manifest_registry=registry,
        inference=_Inference(
            server,
            completion_timeout_seconds=completion_timeout_seconds,
        ),
        reference_service=references,
        generation_service=generation,
        job_events=JobEventBroker(),
    )
    return create_app(services=services)


def _import_project(client: TestClient, pack: Path, name: str) -> UUID:
    response = client.post(
        "/api/projects/import",
        data={"project_name": name},
        files={"pack": (pack.name, pack.read_bytes(), "application/zip")},
    )
    assert response.status_code == 201, response.text
    return UUID(response.json()["project_id"])


def _request_payload(
    *,
    parallelism: int = 4,
    references: dict | None = None,
) -> dict:
    return {
        "target": {"semantic_id": "minecraft:deepslate"},
        "description": "cold synthetic stone",
        "negative_prompt": "",
        "resolution": 16,
        "parallelism": parallelism,
        "references": references or {"style": [], "structure": None},
        "denoise": None,
        "style_weight": None,
    }


def _command(*, parallelism: int = 1) -> CreateGenerationCommand:
    return CreateGenerationCommand(
        target_semantic_id="minecraft:deepslate",
        user_description="cold synthetic stone",
        user_negative_prompt="",
        resolution=16,
        parallelism=parallelism,
        references=ReferenceSelections(style=(), structure=None),
        denoise=None,
        style_weight=None,
    )


def _wait_for_job(
    client: TestClient,
    project_id: UUID,
    job_id: UUID,
    *,
    timeout_seconds: float = 5.0,
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    latest: dict | None = None
    with client.websocket_connect(
        f"/api/projects/{project_id}/jobs/{job_id}/events"
    ) as socket:
        while time.monotonic() < deadline:
            message = socket.receive_json()
            if message["type"] == "heartbeat":
                continue
            assert message["type"] == "snapshot"
            latest = message["job"]
            if latest["state"]["status"] in {"completed", "failed", "canceled"}:
                return latest
    assert latest is not None
    raise AssertionError(
        f"job did not become terminal: {latest['state']['status']}"
    )


def test_real_http_websocket_service_graph_completes_four_candidates_and_preserves_source_pack(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    with FakeComfyServer(generation_behavior="success") as server:
        app = _build_app(projects_root, server)
        with TestClient(app) as client:
            project_id = _import_project(
                client,
                _phase5_pack(tmp_path),
                "Phase 5 HTTP and WebSocket synthetic flow",
            )
            project_root = projects_root / str(project_id)
            before = {
                name: _tree_hashes(project_root / name)
                for name in ("source", "pack")
            }

            options = client.get(
                f"/api/projects/{project_id}/generation-options"
            )
            assert options.status_code == 200, options.text
            option_data = options.json()
            assert option_data["candidate_count"] == 4
            assert option_data["allowed_parallelism"] == [1, 2, 4]
            assert [item["semantic_id"] for item in option_data["targets"]] == [
                "minecraft:deepslate"
            ]

            pack_references = client.get(
                f"/api/projects/{project_id}/references/pack"
            )
            assert pack_references.status_code == 200, pack_references.text
            assert [item["relative_path"] for item in pack_references.json()] == [
                "assets/minecraft/textures/block/custom_unknown.png",
                "assets/minecraft/textures/block/stone.png",
            ]

            uploaded_ids: dict[str, str] = {}
            for kind, color in (
                ("style", (200, 64, 32)),
                ("structure", (32, 160, 80)),
            ):
                uploaded = client.post(
                    f"/api/projects/{project_id}/references",
                    params={"kind": kind},
                    headers={"content-type": "image/png"},
                    content=_png_bytes(color=color),
                )
                assert uploaded.status_code == 201, uploaded.text
                uploaded_ids[kind] = uploaded.json()["reference_id"]
                listed = client.get(
                    f"/api/projects/{project_id}/references",
                    params={"kind": kind},
                )
                assert listed.status_code == 200, listed.text
                assert [item["reference_id"] for item in listed.json()] == [
                    uploaded_ids[kind]
                ]

            created = client.post(
                f"/api/projects/{project_id}/jobs",
                json=_request_payload(
                    parallelism=4,
                    references={
                        "style": [
                            {
                                "source": "upload",
                                "reference_id": uploaded_ids["style"],
                            }
                        ],
                        "structure": {
                            "source": "upload",
                            "reference_id": uploaded_ids["structure"],
                        },
                    },
                ),
            )
            assert created.status_code == 201, created.text
            created_detail = created.json()
            assert created_detail["request"]["schema_version"] == 3
            job_id = UUID(created_detail["request"]["job_id"])

            revisions: list[int] = []
            with client.websocket_connect(
                f"/api/projects/{project_id}/jobs/{job_id}/events"
            ) as socket:
                first = socket.receive_json()
                assert first["type"] == "snapshot"
                revisions.append(first["revision"])
                assert first["job"]["state"]["status"] == "queued"

                started = client.post(
                    f"/api/projects/{project_id}/jobs/{job_id}/start"
                )
                assert started.status_code == 200, started.text

                final: dict | None = None
                for _ in range(200):
                    message = socket.receive_json()
                    if message["type"] == "heartbeat":
                        continue
                    assert message["type"] == "snapshot"
                    assert message["revision"] > revisions[-1]
                    revisions.append(message["revision"])
                    if message["job"]["state"]["status"] == "completed":
                        final = message["job"]
                        break
                assert final is not None

            assert revisions == sorted(set(revisions))
            assert final["state"]["status"] == "completed"
            assert [
                candidate["status"] for candidate in final["state"]["candidates"]
            ] == ["completed"] * 4
            assert len(server.upload_names) == 2

            for candidate_index in range(4):
                artifact_payloads: dict[str, bytes] = {}
                for kind in ("raw", "final", "nearest", "tile"):
                    artifact = client.get(
                        f"/api/projects/{project_id}/jobs/{job_id}"
                        f"/candidates/{candidate_index}/artifacts/{kind}"
                    )
                    assert artifact.status_code == 200, artifact.text
                    assert artifact.headers["content-type"].startswith("image/png")
                    assert artifact.headers["etag"] == hashlib.sha256(
                        artifact.content
                    ).hexdigest()
                    artifact_payloads[kind] = artifact.content
                    with Image.open(io.BytesIO(artifact.content)) as image:
                        image.load()
                        assert image.format == "PNG"
                        assert image.mode == "RGB"
                report = client.get(
                    f"/api/projects/{project_id}/jobs/{job_id}"
                    f"/candidates/{candidate_index}/artifacts/report"
                )
                assert report.status_code == 200, report.text
                assert report.headers["content-type"].startswith("application/json")
                report_data = report.json()
                assert report_data["schema_version"] == 1
                assert report_data["input"]["width"] == 1024
                assert report_data["output"]["sha256"] == hashlib.sha256(
                    artifact_payloads["final"]
                ).hexdigest()
                assert report_data["previews"]["nearest_neighbor"]["sha256"] == hashlib.sha256(
                    artifact_payloads["nearest"]
                ).hexdigest()
                assert report_data["previews"]["tile_3x3"]["sha256"] == hashlib.sha256(
                    artifact_payloads["tile"]
                ).hexdigest()

            assert {
                name: _tree_hashes(project_root / name)
                for name in ("source", "pack")
            } == before
