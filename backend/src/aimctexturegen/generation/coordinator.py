"""Application-owned coordination for one global generation job."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

from aimctexturegen.comfy.client import QueueSnapshot
from aimctexturegen.generation.errors import GenerationError
from aimctexturegen.generation.events import JobEventBroker
from aimctexturegen.generation.service import CreateGenerationCommand, ExecutionContext
from aimctexturegen.jobs.generation_state import (
    confirm_canceled,
    fail_generation,
    request_cancel,
)
from aimctexturegen.jobs.models_v3 import GenerationFailure, GenerationJobRequest, GenerationJobState
from aimctexturegen.jobs.store import JobStore, LoadedJob
from aimctexturegen.projects.repository import ProjectRepository


_NONTERMINAL_STATUSES = frozenset({"queued", "generating", "postprocessing"})
_INTERRUPTED_CODE = "JOB_INTERRUPTED"


class GenerationServicePort(Protocol):
    def create_job(self, project_id: UUID, command: CreateGenerationCommand) -> LoadedJob: ...

    def retry_job(self, project_id: UUID, parent_job_id: UUID) -> LoadedJob: ...

    def run_job(self, project_id: UUID, job_id: UUID, context: ExecutionContext) -> LoadedJob: ...


class ManagedInferencePort(Protocol):
    def ensure_generation_ready(self, binding): ...

    def stop_comfyui(self): ...


@dataclass(frozen=True)
class _ActiveRun:
    project_id: UUID
    job_id: UUID
    cancel_event: threading.Event


class GenerationCoordinator:
    def __init__(
        self,
        *,
        repository: ProjectRepository,
        store: JobStore,
        service: GenerationServicePort,
        inference: ManagedInferencePort,
        events: JobEventBroker | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        cancel_confirmation_timeout_seconds: float = 5.0,
        queue_poll_interval_seconds: float = 0.1,
    ) -> None:
        self._repository = repository
        self._store = store
        self._service = service
        self._inference = inference
        self.events = events or JobEventBroker()
        self._clock = (
            (lambda: datetime.now(timezone.utc)) if clock is None else clock
        )
        self._sleep = sleep
        self._cancel_confirmation_timeout_seconds = max(
            0.0, cancel_confirmation_timeout_seconds
        )
        self._queue_poll_interval_seconds = max(0.01, queue_poll_interval_seconds)
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._active_run: _ActiveRun | None = None
        self._active_client = None
        self._active_prompt_id: str | None = None
        self._closed = False

    def create_job(self, project_id: UUID, command: CreateGenerationCommand) -> LoadedJob:
        with self._lock:
            self._ensure_open()
            current = self._scan_current_nonterminal_job()
            if current is not None:
                raise _conflict_error(current)
            loaded = self._service.create_job(project_id, command)
            self._publish(loaded)
            return loaded

    def start(self, project_id: UUID, job_id: UUID) -> LoadedJob:
        with self._lock:
            self._ensure_open()
            loaded = self._store.load(project_id, job_id)
            request = _require_generation_request(loaded)
            state = _require_generation_state(loaded)
            current = self._scan_current_nonterminal_job()
            if current is not None and current != (project_id, job_id):
                raise _conflict_error(current)
            if state.status not in _NONTERMINAL_STATUSES:
                return loaded
            if self._worker is not None and self._worker.is_alive():
                if self._active_run == _ActiveRun(project_id, job_id, self._active_run.cancel_event):
                    return loaded
                if self._active_run is not None and (
                    self._active_run.project_id,
                    self._active_run.job_id,
                ) == (project_id, job_id):
                    return loaded
            self._cleanup_orphan_prompts(request.project_id, request.job_id)
            cancel_event = threading.Event()
            worker = threading.Thread(
                target=self._run_worker,
                args=(project_id, job_id, cancel_event),
                name=f"aimc-generation-{job_id}",
                daemon=True,
            )
            self._active_run = _ActiveRun(project_id, job_id, cancel_event)
            self._worker = worker
            worker.start()
            return loaded

    def cancel(self, project_id: UUID, job_id: UUID) -> LoadedJob:
        with self._lock:
            self._ensure_open()
        loaded = self._store.load(project_id, job_id)
        state = _require_generation_state(loaded)
        if state.status in {"completed", "failed", "canceled"}:
            return loaded
        if state.cancel_requested_at is None:
            loaded = self._store.replace_state(
                project_id,
                job_id,
                request_cancel(state, now=self._clock()),
                expected_revision=state.revision,
            )
            self._publish(loaded)
            state = loaded.state
        prompt_id = _active_prompt_id(state)
        active_client = None
        cancel_event = None
        with self._lock:
            if self._active_run is not None and (
                self._active_run.project_id,
                self._active_run.job_id,
            ) == (project_id, job_id):
                cancel_event = self._active_run.cancel_event
                active_client = self._active_client
        if cancel_event is not None:
            cancel_event.set()
        if prompt_id is None:
            return self._confirm_canceled(project_id, job_id)
        if active_client is not None:
            active_client.interrupt(prompt_id)
        if self._wait_for_prompt_clear(active_client, prompt_id):
            return self._confirm_canceled(project_id, job_id)
        self._inference.stop_comfyui()
        if self._wait_for_prompt_clear(active_client, prompt_id):
            return self._confirm_canceled(project_id, job_id)
        return self._persist_cancel_confirmation_failed(project_id, job_id)

    def retry(self, project_id: UUID, job_id: UUID) -> LoadedJob:
        with self._lock:
            self._ensure_open()
            current = self._scan_current_nonterminal_job()
            if current is not None:
                raise _conflict_error(current)
            loaded = self._service.retry_job(project_id, job_id)
            self._publish(loaded)
            return loaded

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            active_run = self._active_run
        if active_run is not None:
            active_run.cancel_event.set()
        worker = self._worker
        if worker is not None:
            worker.join(timeout=2.0)

    def _run_worker(
        self,
        project_id: UUID,
        job_id: UUID,
        cancel_event: threading.Event,
    ) -> None:
        try:
            loaded = self._store.load(project_id, job_id)
            request = _require_generation_request(loaded)
            client = self._inference.ensure_generation_ready(request.model_profile)
            with self._lock:
                if self._active_run is not None and (
                    self._active_run.project_id,
                    self._active_run.job_id,
                ) == (project_id, job_id):
                    self._active_client = client
            committed = self._service.run_job(
                project_id,
                job_id,
                ExecutionContext(
                    client=client,
                    cancel_requested=cancel_event.is_set,
                    prompt_registered=lambda prompt_id: self._register_prompt(
                        project_id,
                        job_id,
                        prompt_id,
                    ),
                    state_committed=self._publish,
                ),
            )
            self._publish(committed)
        finally:
            with self._lock:
                if self._active_run is not None and (
                    self._active_run.project_id,
                    self._active_run.job_id,
                ) == (project_id, job_id):
                    self._active_run = None
                    self._active_client = None
                    self._active_prompt_id = None
                    self._worker = None

    def _register_prompt(self, project_id: UUID, job_id: UUID, prompt_id: str) -> None:
        with self._lock:
            if self._active_run is None:
                return
            if (self._active_run.project_id, self._active_run.job_id) != (
                project_id,
                job_id,
            ):
                return
            self._active_prompt_id = prompt_id

    def _confirm_canceled(self, project_id: UUID, job_id: UUID) -> LoadedJob:
        while True:
            loaded = self._store.load(project_id, job_id)
            state = _require_generation_state(loaded)
            if state.status == "canceled":
                return loaded
            committed = self._store.replace_state(
                project_id,
                job_id,
                confirm_canceled(state, now=self._clock()),
                expected_revision=state.revision,
            )
            self._publish(committed)
            return committed

    def _persist_cancel_confirmation_failed(
        self,
        project_id: UUID,
        job_id: UUID,
    ) -> LoadedJob:
        while True:
            loaded = self._store.load(project_id, job_id)
            state = _require_generation_state(loaded)
            failure = GenerationFailure(
                error_code="CANCEL_CONFIRMATION_FAILED",
                stage=state.status,
                user_message="已请求取消，但仍无法确认受管推理已停止",
                recommended_actions=("再次取消该任务", "查看受管 ComfyUI 日志",),
                technical_details=None,
                retryable=True,
                occurred_at=self._clock(),
            )
            committed = self._store.replace_state(
                project_id,
                job_id,
                fail_generation(state, failure, now=self._clock()),
                expected_revision=state.revision,
            )
            self._publish(committed)
            return committed

    def _wait_for_prompt_clear(self, client, prompt_id: str) -> bool:
        if client is None:
            return False
        deadline = time.monotonic() + self._cancel_confirmation_timeout_seconds
        while True:
            snapshot = client.queue_snapshot()
            if prompt_id not in snapshot.running_prompt_ids and prompt_id not in snapshot.pending_prompt_ids:
                return True
            if time.monotonic() >= deadline:
                return False
            self._sleep(self._queue_poll_interval_seconds)

    def _cleanup_orphan_prompts(self, project_id: UUID, job_id: UUID) -> None:
        for loaded in self._scan_generation_jobs():
            if (loaded.request.project_id, loaded.request.job_id) == (project_id, job_id):
                continue
            request = _require_generation_request(loaded)
            state = _require_generation_state(loaded)
            if state.status != "failed" or state.failure is None:
                continue
            if state.failure.error_code != _INTERRUPTED_CODE:
                continue
            prompt_id = _active_prompt_id(state)
            if prompt_id is None:
                continue
            client = self._inference.ensure_generation_ready(request.model_profile)
            snapshot = client.queue_snapshot()
            if prompt_id in snapshot.running_prompt_ids or prompt_id in snapshot.pending_prompt_ids:
                client.interrupt(prompt_id)
                if not self._wait_for_prompt_clear(client, prompt_id):
                    self._inference.stop_comfyui()

    def _scan_current_nonterminal_job(self) -> tuple[UUID, UUID] | None:
        for loaded in self._scan_generation_jobs():
            state = _require_generation_state(loaded)
            if state.status in _NONTERMINAL_STATUSES:
                return (loaded.request.project_id, loaded.request.job_id)
        return None

    def _scan_generation_jobs(self) -> list[LoadedJob]:
        jobs: list[LoadedJob] = []
        for manifest in self._repository.list_manifests().manifests:
            jobs.extend(
                loaded
                for loaded in self._store.scan(manifest.project_id).jobs
                if isinstance(loaded.request, GenerationJobRequest)
                and isinstance(loaded.state, GenerationJobState)
            )
        jobs.sort(key=lambda loaded: str(loaded.request.job_id))
        jobs.sort(key=lambda loaded: loaded.request.created_at, reverse=True)
        return jobs

    def _publish(self, loaded: LoadedJob) -> None:
        if isinstance(loaded.state, GenerationJobState):
            self.events.publish(
                loaded.request.project_id,
                loaded.request.job_id,
                loaded.state.revision,
            )

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("generation coordinator is shutting down")


def _active_prompt_id(state: GenerationJobState) -> str | None:
    for batch in state.batches:
        if batch.prompt_id:
            return batch.prompt_id
    return None


def _require_generation_request(loaded: LoadedJob) -> GenerationJobRequest:
    if not isinstance(loaded.request, GenerationJobRequest):
        raise GenerationError("COMFY_EXECUTION_FAILED", "任务记录不是 Phase 5 生成任务")
    return loaded.request


def _require_generation_state(loaded: LoadedJob) -> GenerationJobState:
    if not isinstance(loaded.state, GenerationJobState):
        raise GenerationError("COMFY_EXECUTION_FAILED", "任务状态不是 Phase 5 生成状态")
    return loaded.state


def _conflict_error(current_job: tuple[UUID, UUID]) -> GenerationError:
    return GenerationError(
        "GENERATION_JOB_CONFLICT",
        "当前已有一个未结束的生成任务",
        recommended_actions=("查看或取消当前任务后再试",),
        current_job=current_job,
    )
