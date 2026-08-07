from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID
import warnings

import httpx
from starlette.exceptions import StarletteDeprecationWarning

warnings.filterwarnings("ignore", category=StarletteDeprecationWarning)
from fastapi.testclient import TestClient

from aimctexturegen.generation.artifacts import CandidateArtifactStore
from aimctexturegen.generation.service import (
    _state_with_prompt_id,
)
from aimctexturegen.jobs.generation_state import (
    complete_candidate,
    fail_generation,
    mark_batch_raw_ready,
    start_batch,
    start_generation,
)
from aimctexturegen.jobs.models_v3 import GenerationFailure
from aimctexturegen.jobs.store import JobStore, LoadedJob

from backend.tests.fakes.comfy_server import FakeComfyServer
from backend.tests.integration.test_generation_flow import (
    _build_app,
    _command,
    _import_project,
    _phase5_pack,
    _png_bytes,
    _request_payload,
    _tree_hashes,
    _wait_for_job,
)


def _persist(store: JobStore, loaded: LoadedJob, replacement) -> LoadedJob:
    return store.replace_state(
        loaded.request.project_id,
        loaded.request.job_id,
        replacement,
        expected_revision=loaded.state.revision,
    )


def _make_generating_job(
    store: JobStore,
    loaded: LoadedJob,
    *,
    prompt_id: str,
) -> LoadedJob:
    now = datetime.now(timezone.utc)
    loaded = _persist(
        store,
        loaded,
        start_generation(loaded.state, now=now),
    )
    loaded = _persist(
        store,
        loaded,
        start_batch(loaded.state, 0, now=now),
    )
    return _persist(
        store,
        loaded,
        _state_with_prompt_id(loaded.state, 0, prompt_id),
    )


def _make_postprocessing_job(
    store: JobStore,
    loaded: LoadedJob,
) -> LoadedJob:
    now = datetime.now(timezone.utc)
    loaded = _persist(
        store,
        loaded,
        start_generation(loaded.state, now=now),
    )
    loaded = _persist(
        store,
        loaded,
        start_batch(loaded.state, 0, now=now),
    )
    artifacts = CandidateArtifactStore(store)
    raw = artifacts.publish_raw_batch(
        loaded,
        loaded.request.execution_batches[0],
        (_png_bytes(size=1024),),
        canvas_size=1024,
    )
    loaded = store.load(loaded.request.project_id, loaded.request.job_id)
    return _persist(
        store,
        loaded,
        mark_batch_raw_ready(loaded.state, 0, raw, now=now),
    )


def _make_failed_retry_parent(
    store: JobStore,
    generation,
    project_id: UUID,
) -> LoadedJob:
    parent = generation.create_job(project_id, _command(parallelism=1))
    artifacts = CandidateArtifactStore(store)
    now = datetime.now(timezone.utc)

    loaded = _persist(
        store,
        parent,
        start_generation(parent.state, now=now),
    )
    loaded = _persist(
        store,
        loaded,
        start_batch(loaded.state, 0, now=now),
    )
    batch_zero = loaded.request.execution_batches[0]
    raw_zero = artifacts.publish_raw_batch(
        loaded,
        batch_zero,
        (_png_bytes(size=1024, color=(64, 32, 160)),),
        canvas_size=1024,
    )
    loaded = store.load(project_id, loaded.request.job_id)
    loaded = _persist(
        store,
        loaded,
        mark_batch_raw_ready(loaded.state, 0, raw_zero, now=now),
    )
    loaded = store.load(project_id, loaded.request.job_id)
    processed_zero = artifacts.process_and_publish(
        loaded,
        candidate_index=0,
        resolution=16,
    )
    loaded = store.load(project_id, loaded.request.job_id)
    loaded = _persist(
        store,
        loaded,
        complete_candidate(
            loaded.state,
            0,
            processed_zero,
            now=now,
        ),
    )

    loaded = store.load(project_id, loaded.request.job_id)
    loaded = _persist(
        store,
        loaded,
        start_batch(loaded.state, 1, now=now),
    )
    batch_one = loaded.request.execution_batches[1]
    raw_one = artifacts.publish_raw_batch(
        loaded,
        batch_one,
        (_png_bytes(size=1024, color=(160, 64, 32)),),
        canvas_size=1024,
    )
    loaded = store.load(project_id, loaded.request.job_id)
    loaded = _persist(
        store,
        loaded,
        mark_batch_raw_ready(loaded.state, 1, raw_one, now=now),
    )
    loaded = store.load(project_id, loaded.request.job_id)
    failure = GenerationFailure(
        error_code="COMFY_EXECUTION_FAILED",
        stage="postprocessing",
        user_message="synthetic parent batch failed",
        recommended_actions=("retry",),
        technical_details=None,
        retryable=True,
        occurred_at=now,
    )
    return _persist(
        store,
        loaded,
        fail_generation(loaded.state, failure, now=now),
    )


def _workflow_seed(prompt_payload: dict) -> int:
    for node in prompt_payload["prompt"].values():
        inputs = node.get("inputs")
        if isinstance(inputs, dict) and isinstance(inputs.get("seed"), int):
            return inputs["seed"]
    raise AssertionError("fake prompt did not contain a sampler seed")


def test_restart_recovery_preserves_queue_and_artifacts_and_cleans_orphan_prompt(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    orphan_prompt = "orphan-prompt-from-before-restart"
    with FakeComfyServer(
        generation_behavior="success",
        queue_running=[[0, orphan_prompt, {}, {}]],
    ) as server:
        app_one = _build_app(projects_root, server)
        with TestClient(app_one) as client_one:
            project_id = _import_project(
                client_one,
                _phase5_pack(tmp_path),
                "Restart recovery synthetic project",
            )
            project_root = projects_root / str(project_id)
            completed = client_one.post(
                f"/api/projects/{project_id}/jobs",
                json=_request_payload(parallelism=4),
            )
            assert completed.status_code == 201, completed.text
            completed_job_id = UUID(completed.json()["request"]["job_id"])
            start_completed = client_one.post(
                f"/api/projects/{project_id}/jobs/{completed_job_id}/start"
            )
            assert start_completed.status_code == 200, start_completed.text
            completed_detail = _wait_for_job(
                client_one,
                project_id,
                completed_job_id,
            )
            assert completed_detail["state"]["status"] == "completed"

            queued = client_one.post(
                f"/api/projects/{project_id}/jobs",
                json=_request_payload(parallelism=1),
            )
            assert queued.status_code == 201, queued.text
            queued_job_id = UUID(queued.json()["request"]["job_id"])

        services_one = app_one.state.services
        assert services_one.generation_service is not None
        assert services_one.job_store is not None
        store = services_one.job_store
        generating = services_one.generation_service.create_job(
            project_id,
            _command(parallelism=1),
        )
        postprocessing = services_one.generation_service.create_job(
            project_id,
            _command(parallelism=1),
        )
        generating = _make_generating_job(
            store,
            generating,
            prompt_id=orphan_prompt,
        )
        postprocessing = _make_postprocessing_job(store, postprocessing)

        completed_job_before = _tree_hashes(
            project_root / "jobs" / str(completed_job_id)
        )
        completed_request_before = (
            project_root / "jobs" / str(completed_job_id) / "request.json"
        ).read_bytes()
        project_before = (project_root / "project.json").read_bytes()
        source_before = _tree_hashes(project_root / "source")
        pack_before = _tree_hashes(project_root / "pack")
        queued_request_before = (
            project_root / "jobs" / str(queued_job_id) / "request.json"
        ).read_bytes()
        index_path = services_one.project_index.database_path
        index_path.unlink()

        app_two = _build_app(projects_root, server)
        with TestClient(app_two) as client_two:
            queued_after = client_two.get(
                f"/api/projects/{project_id}/jobs/{queued_job_id}"
            )
            assert queued_after.status_code == 200, queued_after.text
            assert queued_after.json()["state"]["status"] == "queued"

            for loaded in (generating, postprocessing):
                recovered = client_two.get(
                    f"/api/projects/{project_id}/jobs/{loaded.request.job_id}"
                )
                assert recovered.status_code == 200, recovered.text
                recovered_state = recovered.json()["state"]
                assert recovered_state["status"] == "failed"
                assert recovered_state["failure"]["error_code"] == "JOB_INTERRUPTED"
                assert recovered_state["failure"]["stage"] in {
                    "generating",
                    "postprocessing",
                }

            completed_after = client_two.get(
                f"/api/projects/{project_id}/jobs/{completed_job_id}"
            )
            assert completed_after.status_code == 200, completed_after.text
            assert completed_after.json()["state"]["status"] == "completed"
            for candidate_index in range(4):
                for kind in ("raw", "final", "nearest", "tile", "report"):
                    artifact = client_two.get(
                        f"/api/projects/{project_id}/jobs/{completed_job_id}"
                        f"/candidates/{candidate_index}/artifacts/{kind}"
                    )
                    assert artifact.status_code == 200, artifact.text

            blocked = client_two.post(
                f"/api/projects/{project_id}/jobs",
                json=_request_payload(),
            )
            assert blocked.status_code == 409, blocked.text
            assert blocked.json()["code"] == "GENERATION_JOB_CONFLICT"

            continued = client_two.post(
                f"/api/projects/{project_id}/jobs/{queued_job_id}/start"
            )
            assert continued.status_code == 200, continued.text
            queued_terminal = _wait_for_job(
                client_two,
                project_id,
                queued_job_id,
            )
            assert queued_terminal["state"]["status"] == "completed"

            assert server.protocol_events.index(
                f"interrupt:{orphan_prompt}"
            ) < server.protocol_events.index("prompt:prompt-1")
            queue = httpx.get(f"{server.base_url}/queue")
            assert queue.status_code == 200
            assert queue.json() == {"queue_running": [], "queue_pending": []}
            assert app_two.state.services.project_index.database_path.is_file()

        assert (project_root / "project.json").read_bytes() == project_before
        assert _tree_hashes(project_root / "source") == source_before
        assert _tree_hashes(project_root / "pack") == pack_before
        assert (
            project_root / "jobs" / str(completed_job_id) / "request.json"
        ).read_bytes() == completed_request_before
        assert _tree_hashes(
            project_root / "jobs" / str(completed_job_id)
        ) == completed_job_before
        assert (
            project_root / "jobs" / str(queued_job_id) / "request.json"
        ).read_bytes() == queued_request_before


def test_retry_inherits_completed_candidates_postprocesses_complete_raw_and_reruns_incomplete_batches(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    with FakeComfyServer(generation_behavior="success") as server:
        app_one = _build_app(projects_root, server)
        with TestClient(app_one) as client_one:
            project_id = _import_project(
                client_one,
                _phase5_pack(tmp_path),
                "Lineage retry synthetic project",
            )

        services_one = app_one.state.services
        assert services_one.generation_service is not None
        assert services_one.job_store is not None
        parent = _make_failed_retry_parent(
            services_one.job_store,
            services_one.generation_service,
            project_id,
        )
        parent_raw_one = (
            parent.root / "raw" / "batch-1" / "candidate-1.png"
        ).read_bytes()
        pack_before = _tree_hashes(projects_root / str(project_id) / "pack")

        app_two = _build_app(projects_root, server)
        with TestClient(app_two) as client_two:
            retry = client_two.post(
                f"/api/projects/{project_id}/jobs/{parent.request.job_id}/retry"
            )
            assert retry.status_code == 201, retry.text
            child = retry.json()
            child_job_id = UUID(child["request"]["job_id"])
            assert child["request"]["parent_job_id"] == str(parent.request.job_id)
            assert child["request"]["execution_batches"] == [
                batch.model_dump(mode="json")
                for batch in parent.request.execution_batches
            ]
            assert [
                candidate["status"] for candidate in child["state"]["candidates"]
            ] == ["inherited", "raw_ready", "pending", "pending"]

            started = client_two.post(
                f"/api/projects/{project_id}/jobs/{child_job_id}/start"
            )
            assert started.status_code == 200, started.text
            terminal = _wait_for_job(client_two, project_id, child_job_id)
            assert terminal["state"]["status"] == "completed"
            assert [
                candidate["status"] for candidate in terminal["state"]["candidates"]
            ] == ["inherited", "completed", "completed", "completed"]
            assert terminal["state"]["candidates"][0]["lineage"] == {
                "parent_job_id": str(parent.request.job_id),
                "parent_candidate_index": 0,
            }
            assert len(server.prompt_ids) == 2
            assert [
                _workflow_seed(payload) for payload in server.prompt_payloads
            ] == [
                parent.request.execution_batches[2].seed,
                parent.request.execution_batches[3].seed,
            ]

            child_raw_one = client_two.get(
                f"/api/projects/{project_id}/jobs/{child_job_id}"
                "/candidates/1/artifacts/raw"
            )
            assert child_raw_one.status_code == 200, child_raw_one.text
            assert child_raw_one.content == parent_raw_one
            inherited_final = client_two.get(
                f"/api/projects/{project_id}/jobs/{child_job_id}"
                "/candidates/0/artifacts/final"
            )
            assert inherited_final.status_code == 200, inherited_final.text
            assert hashlib.sha256(inherited_final.content).hexdigest() == (
                inherited_final.headers["etag"]
            )

        assert _tree_hashes(projects_root / str(project_id) / "pack") == pack_before
