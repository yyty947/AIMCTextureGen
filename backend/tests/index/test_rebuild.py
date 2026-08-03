from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import UUID

import pytest

from aimctexturegen.index.database import ProjectIndex
from aimctexturegen.index.models import IndexSnapshot
from aimctexturegen.index.service import IndexService, IndexUnavailableError
from aimctexturegen.jobs.models import (
    CandidateRecord,
    JobRequest,
    JobStateRecord,
    JobSummary,
)
from aimctexturegen.jobs.models_v3 import (
    ExecutionBatch,
    FrozenReferences,
    GenerationAdvanced,
    GenerationCandidateRecord,
    GenerationJobRequest,
    GenerationJobState,
    GenerationModelBinding,
    GenerationTarget,
)
from aimctexturegen.jobs.store import JobScanResult, JobStore, LoadedJob
from aimctexturegen.projects.models import (
    ProjectManifest,
    ProjectSummary,
    dump_project_manifest,
)
from aimctexturegen.projects.repository import ProjectRepository, ProjectScanResult


PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
JOB_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CORRUPT_JOB_ID = UUID("00000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def _manifest() -> ProjectManifest:
    return ProjectManifest(
        schema_version=2,
        project_id=PROJECT_ID,
        project_name="Project",
        edition="java",
        java_pack_format=34,
        supported_formats=None,
        catalog_id="java-format-34-dev",
        source_sha256="c" * 64,
        created_at=NOW,
        updated_at=NOW,
        default_resolution=16,
        default_parallelism=1,
        style_references=(),
    )


def _project_summary() -> ProjectSummary:
    manifest = _manifest()
    return ProjectSummary(
        project_id=manifest.project_id,
        project_name=manifest.project_name,
        edition=manifest.edition,
        java_pack_format=manifest.java_pack_format,
        catalog_id=manifest.catalog_id,
        created_at=manifest.created_at,
        updated_at=manifest.updated_at,
    )


def _loaded_job(job_id: UUID = JOB_ID) -> LoadedJob:
    request = JobRequest(
        schema_version=1,
        job_id=job_id,
        project_id=PROJECT_ID,
        retry_of_job_id=None,
        catalog_id="java-format-34-dev",
        target_semantic_id="minecraft:deepslate",
        target_display_name="Deepslate",
        target_relative_path="assets/minecraft/textures/block/deepslate.png",
        prompt="private prompt must stay in JSON",
        resolution=16,
        parallelism=1,
        style_references=("assets/minecraft/textures/block/stone.png",),
        structure_reference=None,
        seeds=(11, 22, 33, 44),
        created_at=NOW,
    )
    candidates = tuple(
        CandidateRecord(
            candidate_index=index,
            seed=seed,
            status="pending",
            failure=None,
            started_at=None,
            finished_at=None,
        )
        for index, seed in enumerate(request.seeds)
    )
    state = JobStateRecord(
        schema_version=1,
        job_id=job_id,
        project_id=PROJECT_ID,
        revision=0,
        status="queued",
        candidates=candidates,
        failure=None,
        created_at=NOW,
        updated_at=NOW,
        started_at=None,
        finished_at=None,
    )
    return LoadedJob(request=request, state=state, root=Path("not-indexed"))


def _loaded_generation_job(job_id: UUID = JOB_ID) -> LoadedJob:
    request = GenerationJobRequest(
        schema_version=3,
        job_id=job_id,
        project_id=PROJECT_ID,
        parent_job_id=None,
        target=GenerationTarget(
            catalog_id="java-format-34-dev",
            target_semantic_id="minecraft:deepslate",
            target_display_name="Deepslate",
            target_relative_path="assets/minecraft/textures/block/deepslate.png",
        ),
        prompt={
            "prompt_version": "java-block-prompt-v1",
            "positive_prompt": "pixel art stone",
            "negative_prompt": "text watermark",
            "user_prompt": "private prompt must stay in JSON",
        },
        resolution=16,
        parallelism=4,
        execution_batches=(ExecutionBatch(batch_index=0, candidate_indices=(0, 1, 2, 3), seed=99),),
        references=FrozenReferences(style=(), structure=()),
        advanced=GenerationAdvanced(
            style_strength=None,
            denoise_strength=None,
            lora_weight=1.0,
        ),
        model_profile=GenerationModelBinding(
            profile_id="sdxl-mapchip-ipadapter",
            profile_version="2",
            profile_manifest_sha256="aa" * 32,
            runtime_id="comfyui-windows-nvidia",
            runtime_version="0.29.2",
            runtime_manifest_sha256="bb" * 32,
            workflow_variant="text2img-no-style",
            workflow_sha256="cc" * 32,
            output_node_id="19",
        ),
        created_at=NOW,
    )
    state = GenerationJobState(
        schema_version=2,
        job_id=job_id,
        project_id=PROJECT_ID,
        revision=0,
        status="queued",
        batches=(
            {
                "batch_index": 0,
                "candidate_indices": (0, 1, 2, 3),
                "seed": 99,
                "status": "pending",
                "prompt_id": None,
                "sampling_step": None,
                "sampling_maximum": None,
                "raw_artifacts": (),
                "failure": None,
                "started_at": None,
                "finished_at": None,
            },
        ),
        candidates=tuple(
            GenerationCandidateRecord(
                candidate_index=index,
                batch_index=0,
                position_in_batch=index,
                batch_seed=99,
                status="pending",
                artifacts={},
                lineage=None,
                failure=None,
                started_at=None,
                finished_at=None,
            )
            for index in range(4)
        ),
        failure=None,
        cancel_requested_at=None,
        created_at=NOW,
        updated_at=NOW,
        started_at=None,
        finished_at=None,
    )
    return LoadedJob(request=request, state=state, root=Path("not-indexed"))


def _job_summary() -> JobSummary:
    return JobSummary(
        job_id=JOB_ID,
        project_id=PROJECT_ID,
        retry_of_job_id=None,
        target_semantic_id="minecraft:deepslate",
        target_display_name="Deepslate",
        resolution=16,
        parallelism=1,
        status="queued",
        revision=0,
        candidate_statuses=("pending",) * 4,
        created_at=NOW,
        updated_at=NOW,
    )


def _snapshot() -> IndexSnapshot:
    return IndexSnapshot(
        projects=(_project_summary(),),
        jobs=(_job_summary(),),
    )


def test_replace_snapshot_survives_reopen_and_recreation(tmp_path):
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    index = ProjectIndex(projects_root)
    snapshot = _snapshot()

    index.replace_snapshot(snapshot)
    reopened = ProjectIndex(projects_root)
    assert reopened.list_projects() == snapshot.projects
    assert reopened.list_jobs(PROJECT_ID) == snapshot.jobs

    index.database_path.unlink()
    reopened.replace_snapshot(snapshot)
    assert reopened.list_projects() == snapshot.projects
    assert reopened.list_jobs(PROJECT_ID) == snapshot.jobs


@pytest.mark.parametrize(
    "method_name",
    ["_populate_snapshot", "_validate_snapshot", "_publish_snapshot"],
)
def test_failed_snapshot_replacement_preserves_old_index_and_removes_temp(
    tmp_path,
    monkeypatch,
    method_name,
):
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    index = ProjectIndex(projects_root)
    old_snapshot = IndexSnapshot(projects=(_project_summary(),), jobs=())
    index.replace_snapshot(old_snapshot)

    def fail(*_args, **_kwargs):
        raise sqlite3.DatabaseError(f"injected {method_name} failure")

    monkeypatch.setattr(index, method_name, fail)
    with pytest.raises(sqlite3.DatabaseError, match="injected"):
        index.replace_snapshot(_snapshot())

    assert ProjectIndex(projects_root).list_projects() == old_snapshot.projects
    assert ProjectIndex(projects_root).list_jobs(PROJECT_ID) == ()
    assert not index.temporary_path.exists()


@dataclass
class _Repository:
    manifests: tuple[ProjectManifest, ...]

    def list_manifests(self) -> ProjectScanResult:
        return ProjectScanResult(manifests=self.manifests, issues=())


@dataclass
class _Store:
    jobs: tuple[LoadedJob, ...]

    def scan(self, project_id: UUID) -> JobScanResult:
        assert project_id == PROJECT_ID
        return JobScanResult(jobs=self.jobs, issues=())


def test_service_rebuilds_summaries_from_canonical_models(tmp_path):
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    index = ProjectIndex(projects_root)
    service = IndexService(
        repository=_Repository((_manifest(),)),
        store=_Store((_loaded_job(),)),
        index=index,
    )

    snapshot = service.rebuild()

    assert snapshot == _snapshot()
    assert service.list_projects() == snapshot.projects
    assert service.list_jobs(PROJECT_ID) == snapshot.jobs
    database_bytes = index.database_path.read_bytes()
    assert b"private prompt" not in database_bytes
    assert b"assets/minecraft" not in database_bytes


def test_service_rebuilds_generation_job_summaries_from_schema3_models(tmp_path):
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    index = ProjectIndex(projects_root)
    service = IndexService(
        repository=_Repository((_manifest(),)),
        store=_Store((_loaded_generation_job(),)),
        index=index,
    )

    snapshot = service.rebuild()

    assert tuple(job.job_id for job in snapshot.jobs) == (JOB_ID,)
    assert snapshot.jobs[0].retry_of_job_id is None
    assert snapshot.jobs[0].candidate_statuses == ("pending",) * 4


def test_semantic_row_corruption_rebuilds_once_then_retries(
    tmp_path,
    monkeypatch,
):
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    index = ProjectIndex(projects_root)
    index.upsert_project(_manifest())
    connection = sqlite3.connect(index.database_path)
    try:
        connection.execute(
            "UPDATE projects SET updated_at = 'not-a-timestamp'"
        )
        connection.commit()
    finally:
        connection.close()
    replace_calls = 0
    original_replace_snapshot = index.replace_snapshot

    def counting_replace_snapshot(snapshot):
        nonlocal replace_calls
        replace_calls += 1
        original_replace_snapshot(snapshot)

    monkeypatch.setattr(index, "replace_snapshot", counting_replace_snapshot)
    service = IndexService(
        repository=_Repository((_manifest(),)),
        store=_Store((_loaded_job(),)),
        index=index,
    )

    assert service.list_projects() == (_project_summary(),)
    assert replace_calls == 1


def test_rebuild_cannot_erase_a_concurrent_job_upsert(tmp_path):
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    index = ProjectIndex(projects_root)
    index.upsert_project(_manifest())
    index.upsert_job(_job_summary())
    scan_started = Event()
    release_scan = Event()

    class BlockingStore:
        def scan(self, project_id: UUID) -> JobScanResult:
            assert project_id == PROJECT_ID
            scan_started.set()
            assert release_scan.wait(timeout=5)
            return JobScanResult(jobs=(_loaded_job(),), issues=())

    service = IndexService(
        repository=_Repository((_manifest(),)),
        store=BlockingStore(),
        index=index,
    )
    newer = _job_summary().model_copy(
        update={
            "status": "canceled",
            "revision": 1,
            "candidate_statuses": ("canceled",) * 4,
        }
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        rebuild = executor.submit(service.rebuild)
        assert scan_started.wait(timeout=5)
        upsert = executor.submit(service.upsert_job, newer)
        try:
            upsert.result(timeout=0.25)
        except FutureTimeout:
            pass
        finally:
            release_scan.set()
        rebuild.result(timeout=5)
        upsert.result(timeout=5)

    assert service.list_jobs(PROJECT_ID) == (newer,)


def test_concurrent_rebuilds_share_one_temporary_index_path_safely(tmp_path):
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    first_populated = Event()
    release_first = Event()
    second_scan_started = Event()

    class HoldingIndex(ProjectIndex):
        def _populate_snapshot(self, path, snapshot):
            super()._populate_snapshot(path, snapshot)
            first_populated.set()
            assert release_first.wait(timeout=5)

    class SignalingRepository(_Repository):
        def list_manifests(self) -> ProjectScanResult:
            second_scan_started.set()
            return super().list_manifests()

    first_index = HoldingIndex(projects_root)
    second_index = ProjectIndex(projects_root)
    first_service = IndexService(
        repository=_Repository((_manifest(),)),
        store=_Store((_loaded_job(),)),
        index=first_index,
    )
    second_service = IndexService(
        repository=SignalingRepository((_manifest(),)),
        store=_Store((_loaded_job(),)),
        index=second_index,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(first_service.rebuild)
        assert first_populated.wait(timeout=5)
        second = executor.submit(second_service.rebuild)
        second_entered = second_scan_started.wait(timeout=0.25)
        try:
            if second_entered:
                second.result(timeout=5)
        finally:
            release_first.set()
        first.result(timeout=5)
        second.result(timeout=5)

    assert second_service.list_projects() == (_project_summary(),)
    assert second_service.list_jobs(PROJECT_ID) == (_job_summary(),)
    assert not first_index.temporary_path.exists()


def test_service_rebuild_with_real_store_indexes_valid_job_beside_corrupt_job(
    tmp_path,
):
    projects_root = tmp_path / "projects"
    project_root = projects_root / str(PROJECT_ID)
    project_root.mkdir(parents=True)
    (project_root / "project.json").write_bytes(
        dump_project_manifest(_manifest())
    )
    repository = ProjectRepository(projects_root)
    store = JobStore(repository)
    valid = store.create(_loaded_job().request)
    corrupt = store.create(_loaded_job(CORRUPT_JOB_ID).request)
    corrupt_bytes = b"{corrupt state remains canonical input"
    (corrupt.root / "state.json").write_bytes(corrupt_bytes)
    index = ProjectIndex(projects_root)
    service = IndexService(
        repository=repository,
        store=store,
        index=index,
    )

    snapshot = service.rebuild()

    assert tuple(summary.job_id for summary in snapshot.jobs) == (
        valid.request.job_id,
    )
    assert tuple(summary.job_id for summary in index.list_jobs(PROJECT_ID)) == (
        valid.request.job_id,
    )
    scan = store.scan(PROJECT_ID)
    assert tuple(issue.job_id for issue in scan.issues) == (CORRUPT_JOB_ID,)
    assert (corrupt.root / "state.json").read_bytes() == corrupt_bytes


class _FlakyIndex:
    def __init__(
        self,
        *,
        fail_retry: bool = False,
        fail_rebuild: bool = False,
        operation_error: type[Exception] = sqlite3.DatabaseError,
    ) -> None:
        self.fail_retry = fail_retry
        self.fail_rebuild = fail_rebuild
        self.operation_error = operation_error
        self.calls = 0
        self.rebuild_calls = 0

    def list_projects(self):
        self.calls += 1
        if self.calls == 1 or self.fail_retry:
            raise self.operation_error("index operation unavailable")
        return (_project_summary(),)

    def replace_snapshot(self, _snapshot):
        self.rebuild_calls += 1
        if self.fail_rebuild:
            raise OSError("cannot publish rebuilt index")


def test_guarded_query_rebuilds_once_then_retries():
    index = _FlakyIndex()
    service = IndexService(
        repository=_Repository((_manifest(),)),
        store=_Store((_loaded_job(),)),
        index=index,
    )

    assert service.list_projects() == (_project_summary(),)
    assert index.calls == 2
    assert index.rebuild_calls == 1


def test_guarded_os_error_rebuilds_once_then_retries():
    index = _FlakyIndex(operation_error=OSError)
    service = IndexService(
        repository=_Repository((_manifest(),)),
        store=_Store((_loaded_job(),)),
        index=index,
    )

    assert service.list_projects() == (_project_summary(),)
    assert index.calls == 2
    assert index.rebuild_calls == 1


def test_second_os_error_is_stable_after_one_rebuild():
    index = _FlakyIndex(fail_retry=True, operation_error=OSError)
    service = IndexService(
        repository=_Repository((_manifest(),)),
        store=_Store((_loaded_job(),)),
        index=index,
    )

    with pytest.raises(IndexUnavailableError) as raised:
        service.list_projects()

    assert raised.value.code == "INDEX_UNAVAILABLE"
    assert index.calls == 2
    assert index.rebuild_calls == 1


def test_second_database_failure_is_stable_and_does_not_change_json(tmp_path):
    marker = tmp_path / "state.json"
    marker.write_bytes(b'{"canonical":true}\n')
    index = _FlakyIndex(fail_retry=True)
    service = IndexService(
        repository=_Repository((_manifest(),)),
        store=_Store((_loaded_job(),)),
        index=index,
    )

    with pytest.raises(IndexUnavailableError) as raised:
        service.list_projects()

    assert raised.value.code == "INDEX_UNAVAILABLE"
    assert index.calls == 2
    assert index.rebuild_calls == 1
    assert marker.read_bytes() == b'{"canonical":true}\n'


def test_rebuild_publication_failure_after_database_error_is_stable():
    index = _FlakyIndex(fail_rebuild=True)
    service = IndexService(
        repository=_Repository((_manifest(),)),
        store=_Store((_loaded_job(),)),
        index=index,
    )

    with pytest.raises(IndexUnavailableError) as raised:
        service.list_projects()

    assert raised.value.code == "INDEX_UNAVAILABLE"
    assert index.calls == 1
    assert index.rebuild_calls == 1
