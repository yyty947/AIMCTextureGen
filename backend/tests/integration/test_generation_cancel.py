from __future__ import annotations

import hashlib
import time
import warnings
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from starlette.exceptions import StarletteDeprecationWarning

warnings.filterwarnings("ignore", category=StarletteDeprecationWarning)
from fastapi.testclient import TestClient

from backend.tests.integration.test_generation_flow import (
    _build_app,
    _import_project,
    _phase5_pack,
    _request_payload,
    _tree_hashes,
    _wait_for_job,
)
from backend.tests.fakes.comfy_server import FakeComfyServer


@pytest.mark.parametrize("output_count", [0, 3, 5])
def test_native_batch_output_count_rejects_the_whole_batch(
    output_count: int,
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    with FakeComfyServer(
        generation_behavior="success",
        output_count=output_count,
    ) as server:
        app = _build_app(projects_root, server)
        with TestClient(app) as client:
            project_id = _import_project(
                client,
                _phase5_pack(tmp_path),
                f"Atomic output count {output_count}",
            )
            project_root = projects_root / str(project_id)
            before = {
                name: _tree_hashes(project_root / name)
                for name in ("source", "pack")
            }
            created = client.post(
                f"/api/projects/{project_id}/jobs",
                json=_request_payload(parallelism=4),
            )
            assert created.status_code == 201, created.text
            job_id = UUID(created.json()["request"]["job_id"])
            started = client.post(
                f"/api/projects/{project_id}/jobs/{job_id}/start"
            )
            assert started.status_code == 200, started.text

            terminal = _wait_for_job(client, project_id, job_id)
            assert terminal["state"]["status"] == "failed"
            assert terminal["state"]["failure"]["error_code"] == (
                "OUTPUT_CONTRACT_VIOLATION"
            )
            raw_root = project_root / "jobs" / str(job_id) / "raw"
            if raw_root.exists():
                assert not list(raw_root.rglob("*.png"))
            assert {
                name: _tree_hashes(project_root / name)
                for name in ("source", "pack")
            } == before


@pytest.mark.parametrize(
    ("corrupt_index", "wrong_size_index"),
    [(2, None), (None, 1)],
)
def test_corrupt_or_wrong_size_output_rejects_the_whole_batch(
    corrupt_index: int | None,
    wrong_size_index: int | None,
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    with FakeComfyServer(
        generation_behavior="success",
        corrupt_index=corrupt_index,
        wrong_size_index=wrong_size_index,
    ) as server:
        app = _build_app(projects_root, server)
        with TestClient(app) as client:
            project_id = _import_project(
                client,
                _phase5_pack(tmp_path),
                "Atomic invalid output",
            )
            project_root = projects_root / str(project_id)
            created = client.post(
                f"/api/projects/{project_id}/jobs",
                json=_request_payload(parallelism=4),
            )
            assert created.status_code == 201, created.text
            job_id = UUID(created.json()["request"]["job_id"])
            started = client.post(
                f"/api/projects/{project_id}/jobs/{job_id}/start"
            )
            assert started.status_code == 200, started.text

            terminal = _wait_for_job(client, project_id, job_id)
            assert terminal["state"]["status"] == "failed"
            assert terminal["state"]["failure"]["error_code"] == (
                "OUTPUT_CONTRACT_VIOLATION"
            )
            raw_root = project_root / "jobs" / str(job_id) / "raw"
            if raw_root.exists():
                assert not list(raw_root.rglob("*.png"))


@pytest.mark.parametrize(
    ("failure_code", "server_kwargs"),
    [
        ("COMFY_DISCONNECTED", {"generation_behavior": "disconnect"}),
        ("COMFY_TIMEOUT", {"generation_behavior": "timeout"}),
        ("COMFY_QUEUE_REJECTED", {"generation_behavior": "success", "prompt_behavior": "queue-error"}),
        ("COMFY_EXECUTION_FAILED", {"generation_behavior": "execution-error"}),
        ("GPU_OUT_OF_MEMORY", {"generation_behavior": "oom"}),
    ],
)
def test_real_graph_persists_exact_typed_failure(
    failure_code: str,
    server_kwargs: dict[str, str],
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    with FakeComfyServer(**server_kwargs) as server:
        app = _build_app(
            projects_root,
            server,
            completion_timeout_seconds=0.08,
        )
        with TestClient(app) as client:
            project_id = _import_project(
                client,
                _phase5_pack(tmp_path),
                f"Typed failure {failure_code}",
            )
            created = client.post(
                f"/api/projects/{project_id}/jobs",
                json=_request_payload(parallelism=4),
            )
            assert created.status_code == 201, created.text
            job_id = UUID(created.json()["request"]["job_id"])
            started = client.post(
                f"/api/projects/{project_id}/jobs/{job_id}/start"
            )
            assert started.status_code == 200, started.text

            terminal = _wait_for_job(client, project_id, job_id)
            assert terminal["state"]["status"] == "failed"
            failure = terminal["state"]["failure"]
            assert failure["error_code"] == failure_code
            assert failure["retryable"] is True


def test_cancel_retains_candidate_zero_confirms_queue_absence_and_keeps_pack_unchanged(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    with FakeComfyServer(
        generation_behavior="success",
        hold_after_prompt_count=1,
    ) as server:
        app = _build_app(projects_root, server, completion_timeout_seconds=1.0)
        with TestClient(app) as client:
            project_id = _import_project(
                client,
                _phase5_pack(tmp_path),
                "Confirmed cancellation",
            )
            project_root = projects_root / str(project_id)
            before = _tree_hashes(project_root / "pack")
            created = client.post(
                f"/api/projects/{project_id}/jobs",
                json=_request_payload(parallelism=1),
            )
            assert created.status_code == 201, created.text
            job_id = UUID(created.json()["request"]["job_id"])
            started = client.post(
                f"/api/projects/{project_id}/jobs/{job_id}/start"
            )
            assert started.status_code == 200, started.text

            active_detail: dict | None = None
            with client.websocket_connect(
                f"/api/projects/{project_id}/jobs/{job_id}/events"
            ) as socket:
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    message = socket.receive_json()
                    if message["type"] == "heartbeat":
                        continue
                    assert message["type"] == "snapshot"
                    active_detail = message["job"]
                    statuses = [
                        candidate["status"]
                        for candidate in active_detail["state"]["candidates"]
                    ]
                    if (
                        len(server.prompt_ids) >= 2
                        and statuses[0] == "completed"
                        and statuses[1] == "generating"
                    ):
                        break
            assert active_detail is not None
            assert len(server.prompt_ids) >= 2
            assert active_detail["state"]["candidates"][0]["status"] == (
                "completed"
            )
            current_prompt = server.prompt_ids[1]

            canceled = client.post(
                f"/api/projects/{project_id}/jobs/{job_id}/cancel"
            )
            assert canceled.status_code == 200, canceled.text
            terminal = _wait_for_job(client, project_id, job_id)
            assert terminal["state"]["status"] == "canceled"
            assert [
                candidate["status"]
                for candidate in terminal["state"]["candidates"]
            ] == ["completed", "canceled", "canceled", "canceled"]
            assert server.interrupt_calls == [current_prompt]

            queue = httpx.get(f"{server.base_url}/queue")
            assert queue.status_code == 200
            assert queue.json() == {"queue_running": [], "queue_pending": []}

            retained = client.get(
                f"/api/projects/{project_id}/jobs/{job_id}"
                "/candidates/0/artifacts/final"
            )
            assert retained.status_code == 200, retained.text
            assert hashlib.sha256(retained.content).hexdigest() == (
                retained.headers["etag"]
            )
            missing = client.get(
                f"/api/projects/{project_id}/jobs/{job_id}"
                "/candidates/1/artifacts/raw"
            )
            assert missing.status_code == 404, missing.text
            assert _tree_hashes(project_root / "pack") == before


def test_second_project_cannot_create_until_first_job_is_terminal(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    with FakeComfyServer(generation_behavior="success") as server:
        app = _build_app(projects_root, server)
        with TestClient(app) as client:
            first_project = _import_project(
                client,
                _phase5_pack(tmp_path / "first"),
                "First conflict project",
            )
            second_project = _import_project(
                client,
                _phase5_pack(tmp_path / "second"),
                "Second conflict project",
            )
            first = client.post(
                f"/api/projects/{first_project}/jobs",
                json=_request_payload(),
            )
            assert first.status_code == 201, first.text
            first_job = UUID(first.json()["request"]["job_id"])

            conflict = client.post(
                f"/api/projects/{second_project}/jobs",
                json=_request_payload(),
            )
            assert conflict.status_code == 409, conflict.text
            assert conflict.json()["code"] == "GENERATION_JOB_CONFLICT"

            canceled = client.post(
                f"/api/projects/{first_project}/jobs/{first_job}/cancel"
            )
            assert canceled.status_code == 200, canceled.text
            assert canceled.json()["state"]["status"] == "canceled"

            second = client.post(
                f"/api/projects/{second_project}/jobs",
                json=_request_payload(),
            )
            assert second.status_code == 201, second.text
            assert second.json()["state"]["status"] == "queued"
