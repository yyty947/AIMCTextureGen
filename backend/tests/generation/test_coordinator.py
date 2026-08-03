from __future__ import annotations

import io
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image

from aimctexturegen.catalog.registry import CatalogRegistry
from aimctexturegen.comfy.registry import ManifestRegistry
from aimctexturegen.generation.coordinator import GenerationCoordinator
from aimctexturegen.generation.errors import GenerationError
from aimctexturegen.generation.service import CreateGenerationCommand, GenerationService
from aimctexturegen.jobs.generation_state import (
    confirm_canceled,
    recover_generation_interruption,
    request_cancel,
    start_batch,
    start_generation,
)
from aimctexturegen.jobs.models_v3 import (
    GenerationBatchRecord,
    GenerationCandidateRecord,
    GenerationFailure,
    GenerationJobState,
)
from aimctexturegen.jobs.store import JobStore, LoadedJob
from aimctexturegen.projects.models import ProjectManifest, dump_project_manifest
from aimctexturegen.projects.repository import ProjectRepository
from aimctexturegen.references.models import PackReferenceSelection, ReferenceSelections
from aimctexturegen.references.service import ReferenceService
from aimctexturegen.references.store import ProjectReferenceStore


CATALOG_ROOT = Path(__file__).parents[3] / "catalogs" / "java"
REPO_ROOT = Path(__file__).resolve().parents[3]
PROJECT_A = UUID("11111111-aaaa-4111-8111-111111111111")
PROJECT_B = UUID("22222222-bbbb-4222-8222-222222222222")
JOB_A = UUID("33333333-cccc-4333-8333-333333333333")
JOB_B = UUID("44444444-dddd-4444-8444-444444444444")
NOW = datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc)


def _png_bytes(size: int = 16, color: tuple[int, int, int] = (50, 50, 50)) -> bytes:
    payload = io.BytesIO()
    Image.new("RGB", (size, size), color).save(payload, format="PNG")
    return payload.getvalue()


def _write_project(projects_root: Path, project_id: UUID, name: str) -> None:
    project_root = projects_root / str(project_id)
    (project_root / "source").mkdir(parents=True)
    pack_root = project_root / "pack"
    pack_root.mkdir()
    (project_root / "jobs").mkdir()
    (project_root / "uploads").mkdir()
    (project_root / "source" / "imported-pack.zip").write_bytes(b"snapshot")
    (project_root / "pack" / "pack.mcmeta").write_bytes(b"metadata")
    stone = pack_root / "assets/minecraft/textures/block/stone.png"
    stone.parent.mkdir(parents=True, exist_ok=True)
    stone.write_bytes(_png_bytes())
    (project_root / "project.json").write_bytes(
        dump_project_manifest(
            ProjectManifest(
                schema_version=2,
                project_id=project_id,
                project_name=name,
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


def _real_generation_service(
    projects_root: Path,
    *,
    job_ids: tuple[UUID, ...] = (JOB_A, JOB_B),
) -> GenerationService:
    repository = ProjectRepository(projects_root)
    catalogs = CatalogRegistry(CATALOG_ROOT)
    references = ReferenceService(
        repository=repository,
        catalogs=catalogs,
        store=ProjectReferenceStore(repository),
    )
    return GenerationService(
        repository=repository,
        catalogs=catalogs,
        references=references,
        store=JobStore(repository),
        manifests=_verified_registry(),
        seed_source=iter((101, 202, 303, 404)).__next__,
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


def _state_with_prompt_id(
    state: GenerationJobState,
    batch_index: int,
    prompt_id: str,
) -> GenerationJobState:
    batches = list(state.batches)
    batch = batches[batch_index]
    batches[batch_index] = GenerationBatchRecord.model_validate(
        {
            **batch.model_dump(),
            "prompt_id": prompt_id,
        }
    )
    return GenerationJobState.model_validate(
        {
            **state.model_dump(),
            "revision": state.revision + 1,
            "batches": tuple(item.model_dump() for item in batches),
            "updated_at": state.updated_at,
        }
    )


def _state_with_completed_first_active_second(
    state: GenerationJobState,
) -> GenerationJobState:
    started_at = NOW + timedelta(seconds=1)
    completed_at = NOW + timedelta(seconds=2)
    batches = list(state.batches)
    batches[0] = GenerationBatchRecord.model_validate(
        {
            **batches[0].model_dump(),
            "status": "completed",
            "prompt_id": "prompt-batch-0",
            "raw_artifacts": tuple(
                {
                    "kind": "raw",
                    "relative_path": f"raw/batch-0-{index}.png",
                    "sha256": "ab" * 32,
                    "byte_size": 16,
                    "media_type": "image/png",
                    "width": 16,
                    "height": 16,
                }
                for index in (0, 1)
            ),
            "started_at": started_at,
            "finished_at": completed_at,
        }
    )
    batches[1] = GenerationBatchRecord.model_validate(
        {
            **batches[1].model_dump(),
            "status": "generating",
            "prompt_id": "prompt-batch-1",
            "started_at": started_at,
            "finished_at": None,
        }
    )
    candidates = list(state.candidates)
    for index in (0, 1):
        candidates[index] = GenerationCandidateRecord.model_validate(
            {
                **candidates[index].model_dump(),
                "status": "completed",
                "artifacts": {
                    "raw": {
                        "kind": "raw",
                        "relative_path": f"raw/candidate-{index}.png",
                        "sha256": "ab" * 32,
                        "byte_size": 16,
                        "media_type": "image/png",
                        "width": 16,
                        "height": 16,
                    },
                    "final": {
                        "kind": "final",
                        "relative_path": f"processed/candidate-{index}/final.png",
                        "sha256": "cd" * 32,
                        "byte_size": 16,
                        "media_type": "image/png",
                        "width": 16,
                        "height": 16,
                    },
                    "nearest": {
                        "kind": "nearest",
                        "relative_path": f"processed/candidate-{index}/nearest.png",
                        "sha256": "ef" * 32,
                        "byte_size": 16,
                        "media_type": "image/png",
                        "width": 16,
                        "height": 16,
                    },
                    "tile": {
                        "kind": "tile",
                        "relative_path": f"processed/candidate-{index}/tile.png",
                        "sha256": "12" * 32,
                        "byte_size": 16,
                        "media_type": "image/png",
                        "width": 16,
                        "height": 16,
                    },
                    "report": {
                        "kind": "report",
                        "relative_path": f"processed/candidate-{index}/report.json",
                        "sha256": "34" * 32,
                        "byte_size": 16,
                        "media_type": "application/json",
                    },
                },
                "started_at": started_at,
                "finished_at": completed_at,
            }
        )
    for index in (2, 3):
        candidates[index] = GenerationCandidateRecord.model_validate(
            {
                **candidates[index].model_dump(),
                "status": "generating",
                "started_at": started_at,
                "finished_at": None,
            }
        )
    return GenerationJobState.model_validate(
        {
            **state.model_dump(),
            "revision": state.revision + 1,
            "status": "generating",
            "batches": tuple(batch.model_dump() for batch in batches),
            "candidates": tuple(candidate.model_dump() for candidate in candidates),
            "started_at": started_at,
            "finished_at": None,
            "updated_at": completed_at,
        }
    )


class _BlockingRunService:
    def __init__(
        self,
        base: GenerationService,
        store: JobStore,
        *,
        confirm_on_cancel: bool = False,
    ) -> None:
        self._base = base
        self._store = store
        self._confirm_on_cancel = confirm_on_cancel
        self.run_calls: list[tuple[UUID, UUID]] = []
        self.run_started = threading.Event()
        self.allow_finish = threading.Event()
        self.cancel_seen = threading.Event()
        self.confirmed_on_cancel = threading.Event()
        self.shutdown_seen = threading.Event()
        self.recorded_contexts = []

    def create_job(self, project_id: UUID, command: CreateGenerationCommand) -> LoadedJob:
        return self._base.create_job(project_id, command)

    def retry_job(self, project_id: UUID, parent_job_id: UUID) -> LoadedJob:
        return self._base.retry_job(project_id, parent_job_id)

    def run_job(self, project_id: UUID, job_id: UUID, context) -> LoadedJob:
        self.run_calls.append((project_id, job_id))
        self.recorded_contexts.append(context)
        loaded = self._store.load(project_id, job_id)
        loaded = self._store.replace_state(
            project_id,
            job_id,
            start_generation(loaded.state, now=NOW),
            expected_revision=loaded.state.revision,
        )
        if context.state_committed is not None:
            context.state_committed(loaded)
        loaded = self._store.replace_state(
            project_id,
            job_id,
            start_batch(loaded.state, 0, now=NOW),
            expected_revision=loaded.state.revision,
        )
        if context.state_committed is not None:
            context.state_committed(loaded)
        prompt_id = "prompt-owned"
        if context.prompt_registered is not None:
            context.prompt_registered(prompt_id)
        loaded = self._store.replace_state(
            project_id,
            job_id,
            _state_with_prompt_id(loaded.state, 0, prompt_id),
            expected_revision=loaded.state.revision,
        )
        if context.state_committed is not None:
            context.state_committed(loaded)
        self.run_started.set()
        shutdown_requested = getattr(
            context,
            "shutdown_requested",
            lambda: False,
        )
        while (
            not context.cancel_requested()
            and not shutdown_requested()
            and not self.allow_finish.wait(0.01)
        ):
            pass
        if context.cancel_requested():
            self.cancel_seen.set()
        if shutdown_requested():
            self.shutdown_seen.set()
        if self._confirm_on_cancel and context.cancel_requested():
            current = self._store.load(project_id, job_id)
            self._store.replace_state(
                project_id,
                job_id,
                confirm_canceled(current.state, now=NOW + timedelta(seconds=20)),
                expected_revision=current.state.revision,
            )
            self.confirmed_on_cancel.set()
        return self._store.load(project_id, job_id)


class _CancelableRunService:
    def __init__(self, base: GenerationService, store: JobStore) -> None:
        self._base = base
        self._store = store
        self.run_started = threading.Event()

    def create_job(self, project_id: UUID, command: CreateGenerationCommand) -> LoadedJob:
        return self._base.create_job(project_id, command)

    def retry_job(self, project_id: UUID, parent_job_id: UUID) -> LoadedJob:
        return self._base.retry_job(project_id, parent_job_id)

    def run_job(self, project_id: UUID, job_id: UUID, context) -> LoadedJob:
        self.run_started.set()
        while not context.cancel_requested():
            time.sleep(0.001)
        return self._store.load(project_id, job_id)


class _QueueClient:
    def __init__(self, store: JobStore, project_id: UUID, job_id: UUID) -> None:
        self._store = store
        self._project_id = project_id
        self._job_id = job_id
        self.snapshots: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
        self.interrupt_calls: list[str | None] = []
        self.interrupt_error: Exception | None = None
        self.interrupt_wait_for: threading.Event | None = None
        self.queue_snapshot_errors: list[Exception] = []
        self.require_cancel_request = True

    def queue_snapshot(self):
        if self.queue_snapshot_errors:
            raise self.queue_snapshot_errors.pop(0)
        if self.snapshots:
            running, pending = self.snapshots.pop(0)
        else:
            running, pending = ((), ())
        return type(
            "QueueSnapshot",
            (),
            {"running_prompt_ids": running, "pending_prompt_ids": pending},
        )()

    def interrupt(self, prompt_id: str | None = None) -> None:
        state = self._store.load(self._project_id, self._job_id).state
        if self.require_cancel_request:
            assert state.cancel_requested_at is not None
        self.interrupt_calls.append(prompt_id)
        if self.interrupt_error is not None:
            if self.interrupt_wait_for is not None:
                self.interrupt_wait_for.wait(timeout=0.2)
            raise self.interrupt_error


class _InferenceStub:
    def __init__(self, client: _QueueClient) -> None:
        self.client = client
        self.ensure_calls = 0
        self.stop_calls = 0
        self.stop_result = {"state": "stopped"}
        self.stop_error: Exception | None = None

    def ensure_generation_ready(self, _binding):
        self.ensure_calls += 1
        return self.client

    def stop_comfyui(self):
        self.stop_calls += 1
        if self.stop_error is not None:
            raise self.stop_error
        return self.stop_result


def test_create_job_enforces_one_global_nonterminal_slot_from_canonical_scan(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root, PROJECT_A, "A")
    _write_project(projects_root, PROJECT_B, "B")
    repository = ProjectRepository(projects_root)
    store = JobStore(repository)
    service = _real_generation_service(projects_root)
    coordinator = GenerationCoordinator(
        repository=repository,
        store=store,
        service=service,
        inference=_InferenceStub(_QueueClient(store, PROJECT_A, JOB_A)),
        clock=lambda: NOW + timedelta(seconds=10),
    )
    barrier = threading.Barrier(2)
    results: list[LoadedJob] = []
    failures: list[GenerationError] = []

    def create(project_id: UUID) -> None:
        barrier.wait(timeout=1.0)
        try:
            results.append(coordinator.create_job(project_id, _command()))
        except GenerationError as error:
            failures.append(error)

    first = threading.Thread(target=create, args=(PROJECT_A,), daemon=True)
    second = threading.Thread(target=create, args=(PROJECT_B,), daemon=True)
    first.start()
    second.start()
    first.join(timeout=2.0)
    second.join(timeout=2.0)

    assert len(results) == 1
    assert len(failures) == 1
    assert failures[0].code == "GENERATION_JOB_CONFLICT"
    assert failures[0].current_job == (
        results[0].request.project_id,
        results[0].request.job_id,
    )
    scanned = [
        loaded
        for manifest in repository.list_manifests().manifests
        for loaded in store.scan(manifest.project_id).jobs
    ]
    assert [loaded.state.status for loaded in scanned] == ["queued"]


def test_start_is_background_idempotent_and_publishes_commits(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root, PROJECT_A, "A")
    repository = ProjectRepository(projects_root)
    store = JobStore(repository)
    real_service = _real_generation_service(projects_root, job_ids=(JOB_A,))
    service = _BlockingRunService(real_service, store)
    client = _QueueClient(store, PROJECT_A, JOB_A)
    coordinator = GenerationCoordinator(
        repository=repository,
        store=store,
        service=service,
        inference=_InferenceStub(client),
        clock=lambda: NOW + timedelta(seconds=10),
    )
    created = coordinator.create_job(PROJECT_A, _command())

    started = coordinator.start(PROJECT_A, created.request.job_id)
    again = coordinator.start(PROJECT_A, created.request.job_id)
    revision = coordinator.events.wait_for_change(
        PROJECT_A,
        created.request.job_id,
        after_revision=started.state.revision,
        timeout=1.0,
    )
    service.allow_finish.set()
    coordinator.shutdown()

    assert started.state.status == "queued"
    assert again.request.job_id == created.request.job_id
    assert service.run_started.wait(timeout=1.0)
    assert service.run_calls == [(PROJECT_A, created.request.job_id)]
    assert revision is not None


def test_cancel_confirms_only_after_prompt_leaves_queue(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root, PROJECT_A, "A")
    repository = ProjectRepository(projects_root)
    store = JobStore(repository)
    real_service = _real_generation_service(projects_root, job_ids=(JOB_A,))
    service = _BlockingRunService(real_service, store)
    client = _QueueClient(store, PROJECT_A, JOB_A)
    client.snapshots = [
        (("prompt-owned",), ()),
        ((), ()),
    ]
    inference = _InferenceStub(client)
    coordinator = GenerationCoordinator(
        repository=repository,
        store=store,
        service=service,
        inference=inference,
        clock=lambda: NOW + timedelta(seconds=10),
        cancel_confirmation_timeout_seconds=0.2,
        queue_poll_interval_seconds=0.01,
    )
    created = coordinator.create_job(PROJECT_A, _command())
    coordinator.start(PROJECT_A, created.request.job_id)
    assert service.run_started.wait(timeout=1.0)

    canceled = coordinator.cancel(PROJECT_A, created.request.job_id)

    assert canceled.state.status == "canceled"
    assert canceled.state.cancel_requested_at is not None
    assert client.interrupt_calls == ["prompt-owned"]


def test_cancel_targets_latest_active_batch_prompt_and_releases_it_after_completion(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root, PROJECT_A, "A")
    repository = ProjectRepository(projects_root)
    store = JobStore(repository)
    real_service = _real_generation_service(projects_root, job_ids=(JOB_A,))
    service = _CancelableRunService(real_service, store)
    client = _QueueClient(store, PROJECT_A, JOB_A)
    client.snapshots = [(("prompt-batch-1",), ()), ((), ())]
    inference = _InferenceStub(client)
    coordinator = GenerationCoordinator(
        repository=repository,
        store=store,
        service=service,
        inference=inference,
        clock=lambda: NOW + timedelta(seconds=10),
        cancel_confirmation_timeout_seconds=0.2,
        queue_poll_interval_seconds=0.01,
    )
    created = coordinator.create_job(PROJECT_A, _command())
    active = _state_with_completed_first_active_second(created.state)
    store.replace_state(
        PROJECT_A,
        created.request.job_id,
        active,
        expected_revision=created.state.revision,
    )

    coordinator.start(PROJECT_A, created.request.job_id)
    assert service.run_started.wait(timeout=1.0)
    canceled = coordinator.cancel(PROJECT_A, created.request.job_id)
    coordinator.shutdown()

    assert canceled.state.status == "canceled"
    assert client.interrupt_calls == ["prompt-batch-1"]
    assert all(batch.prompt_id is None for batch in canceled.state.batches)


def test_orphan_cleanup_targets_latest_interrupted_batch_prompt(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root, PROJECT_A, "A")
    _write_project(projects_root, PROJECT_B, "B")
    repository = ProjectRepository(projects_root)
    store = JobStore(repository)
    real_service = _real_generation_service(projects_root, job_ids=(JOB_A, JOB_B))
    orphan = real_service.create_job(PROJECT_A, _command())
    active = _state_with_completed_first_active_second(orphan.state)
    store.replace_state(
        PROJECT_A,
        orphan.request.job_id,
        active,
        expected_revision=orphan.state.revision,
    )
    recovered = recover_generation_interruption(active, now=NOW + timedelta(seconds=3))
    store.replace_state(
        PROJECT_A,
        orphan.request.job_id,
        recovered,
        expected_revision=active.revision,
    )
    client = _QueueClient(store, PROJECT_A, orphan.request.job_id)
    client.require_cancel_request = False
    client.snapshots = [(("prompt-batch-1",), ()), ((), ())]
    coordinator = GenerationCoordinator(
        repository=repository,
        store=store,
        service=real_service,
        inference=_InferenceStub(client),
        clock=lambda: NOW + timedelta(seconds=10),
        cancel_confirmation_timeout_seconds=0.2,
        queue_poll_interval_seconds=0.01,
    )

    new_job = coordinator.create_job(PROJECT_B, _command())
    coordinator._cleanup_orphan_prompts(PROJECT_B, new_job.request.job_id)

    assert client.interrupt_calls == ["prompt-batch-1"]


@pytest.mark.parametrize(
    ("failure_kind", "expected_detail"),
    (
        ("interrupt", "interrupt failed"),
        ("queue", "queue snapshot failed"),
        ("stop", "safe stop failed"),
    ),
)
def test_cancel_confirmation_failures_are_persisted_and_retryable(
    tmp_path: Path,
    failure_kind: str,
    expected_detail: str,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root, PROJECT_A, "A")
    repository = ProjectRepository(projects_root)
    store = JobStore(repository)
    real_service = _real_generation_service(projects_root, job_ids=(JOB_A,))
    service = _BlockingRunService(real_service, store)
    client = _QueueClient(store, PROJECT_A, JOB_A)
    inference = _InferenceStub(client)
    client.snapshots = [(('prompt-owned',), ())] * 20
    if failure_kind == "interrupt":
        client.interrupt_error = RuntimeError(expected_detail)
    elif failure_kind == "queue":
        client.queue_snapshot_errors = [RuntimeError(expected_detail)]
    else:
        inference.stop_error = RuntimeError(expected_detail)
    coordinator = GenerationCoordinator(
        repository=repository,
        store=store,
        service=service,
        inference=inference,
        clock=lambda: NOW + timedelta(seconds=10),
        cancel_confirmation_timeout_seconds=0.05,
        queue_poll_interval_seconds=0.01,
    )
    created = coordinator.create_job(PROJECT_A, _command())
    coordinator.start(PROJECT_A, created.request.job_id)
    assert service.run_started.wait(timeout=1.0)

    failed = coordinator.cancel(PROJECT_A, created.request.job_id)

    assert failed.state.status in {"generating", "postprocessing"}
    assert failed.state.failure is not None
    assert failed.state.failure.error_code == "CANCEL_CONFIRMATION_FAILED"
    assert expected_detail in (failed.state.failure.technical_details or "")
    with pytest.raises(GenerationError) as raised:
        coordinator.create_job(PROJECT_B, _command())
    assert raised.value.code == "GENERATION_JOB_CONFLICT"

    if failure_kind == "interrupt":
        client.interrupt_error = None
        client.snapshots = [(('prompt-owned',), ()), ((), ())]
        retried = coordinator.cancel(PROJECT_A, created.request.job_id)
        assert retried.state.status == "canceled"
    coordinator.shutdown()


def test_cancel_without_prompt_confirms_queued_job_immediately(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root, PROJECT_A, "A")
    repository = ProjectRepository(projects_root)
    store = JobStore(repository)
    real_service = _real_generation_service(projects_root, job_ids=(JOB_A,))
    coordinator = GenerationCoordinator(
        repository=repository,
        store=store,
        service=real_service,
        inference=_InferenceStub(_QueueClient(store, PROJECT_A, JOB_A)),
        clock=lambda: NOW + timedelta(seconds=10),
    )
    created = coordinator.create_job(PROJECT_A, _command())

    canceled = coordinator.cancel(PROJECT_A, created.request.job_id)

    assert canceled.state.status == "canceled"
    assert canceled.state.failure is None


def test_failed_cancel_confirmation_keeps_nonterminal_slot_occupied(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root, PROJECT_A, "A")
    _write_project(projects_root, PROJECT_B, "B")
    repository = ProjectRepository(projects_root)
    store = JobStore(repository)
    real_service = _real_generation_service(projects_root, job_ids=(JOB_A, JOB_B))
    service = _BlockingRunService(real_service, store)
    client = _QueueClient(store, PROJECT_A, JOB_A)
    client.snapshots = [(("prompt-owned",), ())] * 50
    inference = _InferenceStub(client)
    coordinator = GenerationCoordinator(
        repository=repository,
        store=store,
        service=service,
        inference=inference,
        clock=lambda: NOW + timedelta(seconds=10),
        cancel_confirmation_timeout_seconds=0.05,
        queue_poll_interval_seconds=0.01,
    )
    created = coordinator.create_job(PROJECT_A, _command())
    coordinator.start(PROJECT_A, created.request.job_id)
    assert service.run_started.wait(timeout=1.0)

    failed_confirmation = coordinator.cancel(PROJECT_A, created.request.job_id)

    assert failed_confirmation.state.status in {"generating", "postprocessing"}
    assert failed_confirmation.state.failure is not None
    assert failed_confirmation.state.failure.error_code == "CANCEL_CONFIRMATION_FAILED"
    with pytest.raises(GenerationError) as raised:
        coordinator.create_job(PROJECT_B, _command())
    assert raised.value.code == "GENERATION_JOB_CONFLICT"
    coordinator.shutdown()


def test_cancel_confirmation_failure_cannot_be_terminalized_by_worker_signal(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root, PROJECT_A, "A")
    repository = ProjectRepository(projects_root)
    store = JobStore(repository)
    real_service = _real_generation_service(projects_root, job_ids=(JOB_A,))
    service = _BlockingRunService(
        real_service,
        store,
        confirm_on_cancel=True,
    )
    client = _QueueClient(store, PROJECT_A, JOB_A)
    client.snapshots = [(('prompt-owned',), ())] * 50
    client.interrupt_error = RuntimeError("interrupt failed")
    client.interrupt_wait_for = service.confirmed_on_cancel
    coordinator = GenerationCoordinator(
        repository=repository,
        store=store,
        service=service,
        inference=_InferenceStub(client),
        clock=lambda: NOW + timedelta(seconds=10),
        cancel_confirmation_timeout_seconds=0.05,
        queue_poll_interval_seconds=0.01,
    )
    created = coordinator.create_job(PROJECT_A, _command())
    coordinator.start(PROJECT_A, created.request.job_id)
    assert service.run_started.wait(timeout=1.0)

    failed = coordinator.cancel(PROJECT_A, created.request.job_id)

    assert failed.state.status in {"generating", "postprocessing"}
    assert failed.state.failure is not None
    assert failed.state.failure.error_code == "CANCEL_CONFIRMATION_FAILED"
    coordinator.shutdown()


def test_shutdown_leaves_active_job_nonterminal_for_restart_recovery(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root, PROJECT_A, "A")
    repository = ProjectRepository(projects_root)
    store = JobStore(repository)
    real_service = _real_generation_service(projects_root, job_ids=(JOB_A,))
    service = _BlockingRunService(real_service, store)
    client = _QueueClient(store, PROJECT_A, JOB_A)
    coordinator = GenerationCoordinator(
        repository=repository,
        store=store,
        service=service,
        inference=_InferenceStub(client),
        clock=lambda: NOW + timedelta(seconds=10),
    )
    created = coordinator.create_job(PROJECT_A, _command())
    coordinator.start(PROJECT_A, created.request.job_id)
    assert service.run_started.wait(timeout=1.0)

    coordinator.shutdown()
    loaded = store.load(PROJECT_A, created.request.job_id)

    assert loaded.state.status in {"generating", "postprocessing"}


def test_shutdown_signals_lifecycle_stop_without_user_cancellation(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root, PROJECT_A, "A")
    repository = ProjectRepository(projects_root)
    store = JobStore(repository)
    real_service = _real_generation_service(projects_root, job_ids=(JOB_A,))
    service = _BlockingRunService(real_service, store)
    client = _QueueClient(store, PROJECT_A, JOB_A)
    coordinator = GenerationCoordinator(
        repository=repository,
        store=store,
        service=service,
        inference=_InferenceStub(client),
        clock=lambda: NOW + timedelta(seconds=10),
    )
    created = coordinator.create_job(PROJECT_A, _command())
    coordinator.start(PROJECT_A, created.request.job_id)
    assert service.run_started.wait(timeout=1.0)

    coordinator.shutdown()

    assert service.shutdown_seen.is_set()
    assert not service.cancel_seen.is_set()
    assert store.load(PROJECT_A, created.request.job_id).state.status in {
        "generating",
        "postprocessing",
    }
