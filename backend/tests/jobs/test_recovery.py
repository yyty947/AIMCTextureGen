import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

from aimctexturegen.index.database import ProjectIndex
from aimctexturegen.index.models import IndexSnapshot
from aimctexturegen.index.service import IndexService
from aimctexturegen.jobs.models import (
    JobFailure,
    JobRequest,
    JobStateRecord,
    dump_job_state,
)
from aimctexturegen.jobs.recovery import RecoveryService
from aimctexturegen.jobs.state_machine import (
    cancel_state,
    transition_candidate_state,
    transition_job_state,
)
from aimctexturegen.jobs.store import JobStore, LoadedJob
from aimctexturegen.projects.models import (
    ProjectManifest,
    ProjectManifestV1,
    dump_project_manifest,
    load_project_manifest,
)
from aimctexturegen.projects.repository import ProjectRepository


PROJECT_ID = UUID("10000000-0000-4000-8000-000000000001")
JOB_IDS = {
    "queued": UUID("20000000-0000-4000-8000-000000000001"),
    "generating": UUID("20000000-0000-4000-8000-000000000002"),
    "postprocessing": UUID("20000000-0000-4000-8000-000000000003"),
    "completed": UUID("20000000-0000-4000-8000-000000000004"),
    "failed": UUID("20000000-0000-4000-8000-000000000005"),
    "canceled": UUID("20000000-0000-4000-8000-000000000006"),
}
MALFORMED_JOB_ID = UUID("20000000-0000-4000-8000-000000000099")
CREATED_AT = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
ACTIVE_AT = CREATED_AT + timedelta(hours=1)
RECOVERED_AT = CREATED_AT + timedelta(hours=2)
COMPLETED_AT = RECOVERED_AT + timedelta(seconds=1)
SECOND_RECOVERY_AT = COMPLETED_AT + timedelta(hours=1)
SECOND_COMPLETED_AT = SECOND_RECOVERY_AT + timedelta(seconds=1)


def _manifest() -> ProjectManifest:
    return ProjectManifest(
        schema_version=2,
        project_id=PROJECT_ID,
        project_name="Recovery project",
        edition="java",
        java_pack_format=34,
        supported_formats=None,
        catalog_id="java-dev-format-34",
        source_sha256="ab" * 32,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
        default_resolution=16,
        default_parallelism=1,
        style_references=(),
    )


def _write_project(projects_root: Path) -> Path:
    project_root = projects_root / str(PROJECT_ID)
    for relative in ("source", "pack", "uploads", "jobs"):
        (project_root / relative).mkdir(parents=True, exist_ok=True)
    (project_root / "source" / "imported-pack.zip").write_bytes(b"snapshot")
    (project_root / "pack" / "pack.mcmeta").write_bytes(b"working-copy")
    (project_root / "project.json").write_bytes(
        dump_project_manifest(_manifest())
    )
    return project_root


def _request(status: str, offset: int) -> JobRequest:
    return JobRequest(
        schema_version=1,
        job_id=JOB_IDS[status],
        project_id=PROJECT_ID,
        retry_of_job_id=None,
        catalog_id="java-dev-format-34",
        target_semantic_id=f"minecraft:{status}",
        target_display_name=status.title(),
        target_relative_path=f"assets/minecraft/textures/block/{status}.png",
        prompt=f"{status} texture",
        resolution=16,
        parallelism=1,
        style_references=("assets/minecraft/textures/block/stone.png",),
        structure_reference=None,
        seeds=(
            offset * 10 + 1,
            offset * 10 + 2,
            offset * 10 + 3,
            offset * 10 + 4,
        ),
        created_at=CREATED_AT + timedelta(seconds=offset),
    )


def _persist(store: JobStore, loaded: LoadedJob, state) -> LoadedJob:
    return store.replace_state(
        PROJECT_ID,
        loaded.request.job_id,
        state,
        expected_revision=loaded.state.revision,
    )


def _candidate(
    store: JobStore,
    loaded: LoadedJob,
    index: int,
    status: str,
) -> LoadedJob:
    return _persist(
        store,
        loaded,
        transition_candidate_state(
            loaded.state,
            index,
            status,
            now=ACTIVE_AT,
        ),
    )


def _job(
    store: JobStore,
    loaded: LoadedJob,
    status: str,
    *,
    failure: JobFailure | None = None,
) -> LoadedJob:
    return _persist(
        store,
        loaded,
        transition_job_state(
            loaded.state,
            status,
            now=ACTIVE_AT,
            failure=failure,
        ),
    )


def _prepare_jobs(store: JobStore) -> None:
    queued = store.create(_request("queued", 1))
    assert queued.state.status == "queued"

    generating = _job(
        store,
        store.create(_request("generating", 2)),
        "generating",
    )
    generating = _candidate(store, generating, 0, "generating")
    generating = _candidate(store, generating, 0, "postprocessing")
    generating = _candidate(store, generating, 0, "completed")
    generating = _candidate(store, generating, 1, "generating")
    assert tuple(item.status for item in generating.state.candidates) == (
        "completed",
        "generating",
        "pending",
        "pending",
    )

    postprocessing = _job(
        store,
        store.create(_request("postprocessing", 3)),
        "generating",
    )
    postprocessing = _candidate(
        store,
        postprocessing,
        0,
        "generating",
    )
    postprocessing = _candidate(
        store,
        postprocessing,
        0,
        "postprocessing",
    )
    postprocessing = _job(store, postprocessing, "postprocessing")
    assert postprocessing.state.status == "postprocessing"

    completed = _job(
        store,
        store.create(_request("completed", 4)),
        "generating",
    )
    for candidate_index in range(4):
        completed = _candidate(
            store,
            completed,
            candidate_index,
            "generating",
        )
        completed = _candidate(
            store,
            completed,
            candidate_index,
            "postprocessing",
        )
        completed = _candidate(
            store,
            completed,
            candidate_index,
            "completed",
        )
    completed = _job(store, completed, "postprocessing")
    completed = _job(store, completed, "completed")

    failure = JobFailure(
        code="GENERATION_FAILED",
        stage="generating",
        user_message="Generation failed",
        recommended_actions=("Retry",),
        technical_details=None,
        log_reference=None,
    )
    failed = _job(
        store,
        store.create(_request("failed", 5)),
        "generating",
    )
    _job(store, failed, "failed", failure=failure)

    canceled = store.create(_request("canceled", 6))
    _persist(store, canceled, cancel_state(canceled.state, now=ACTIVE_AT))


def _write_malformed_job(project_root: Path) -> None:
    job_root = project_root / "jobs" / str(MALFORMED_JOB_ID)
    job_root.mkdir()
    for name in ("raw", "processed", "previews", "reports"):
        (job_root / name).mkdir()
    (job_root / "request.json").write_text("{}", encoding="utf-8")
    (job_root / "state.json").write_bytes(b"{broken")


def _downgrade_manifest(project_root: Path) -> bytes:
    manifest = _manifest()
    values = {
        field: getattr(manifest, field)
        for field in ProjectManifestV1.model_fields
    }
    values["schema_version"] = 1
    legacy = ProjectManifestV1.model_validate(values)
    payload = (
        json.dumps(
            legacy.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    (project_root / "project.json").write_bytes(payload)
    return payload


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _state_bytes(project_root: Path) -> dict[str, bytes]:
    return {
        status: (
            project_root
            / "jobs"
            / str(job_id)
            / "state.json"
        ).read_bytes()
        for status, job_id in JOB_IDS.items()
    }


def _clock(*values: datetime):
    iterator = iter(values)
    return iterator.__next__


def test_startup_recovery_migrates_recovers_reports_and_rebuilds_idempotently(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_root = _write_project(projects_root)
    repository = ProjectRepository(projects_root)
    store = JobStore(repository)
    _prepare_jobs(store)
    _write_malformed_job(project_root)
    legacy_payload = _downgrade_manifest(project_root)
    protected_before = {
        name: _tree_hashes(project_root / name)
        for name in ("source", "pack")
    }
    state_before = _state_bytes(project_root)
    malformed_before = (
        project_root / "jobs" / str(MALFORMED_JOB_ID) / "state.json"
    ).read_bytes()

    index = ProjectIndex(projects_root)
    index.replace_snapshot(IndexSnapshot(projects=(), jobs=()))
    index_service = IndexService(
        repository=repository,
        store=store,
        index=index,
    )
    recovery = RecoveryService(
        repository=repository,
        store=store,
        index=index_service,
        clock=_clock(RECOVERED_AT, COMPLETED_AT),
    )

    report = recovery.run()

    assert report.project_count == 1
    assert report.job_count == 6
    assert report.recovered_job_count == 2
    assert report.completed_at == COMPLETED_AT
    assert len(report.issues) == 1
    assert report.issues[0].project_id == PROJECT_ID
    assert report.issues[0].job_id == MALFORMED_JOB_ID
    assert report.issues[0].code == "CORRUPT_JOB_RECORD"
    with pytest.raises(FrozenInstanceError):
        report.project_count = 2  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        report.issues[0].code = "CHANGED"  # type: ignore[misc]

    migrated_payload = (project_root / "project.json").read_bytes()
    assert migrated_payload != legacy_payload
    migrated, was_migrated = load_project_manifest(migrated_payload)
    assert was_migrated is False
    assert migrated.schema_version == 2
    assert {
        name: _tree_hashes(project_root / name)
        for name in ("source", "pack")
    } == protected_before

    state_after = _state_bytes(project_root)
    for status in ("queued", "completed", "failed", "canceled"):
        assert state_after[status] == state_before[status]
    generating = store.load(PROJECT_ID, JOB_IDS["generating"]).state
    assert generating.status == "failed"
    assert generating.failure is not None
    assert generating.failure.code == "JOB_INTERRUPTED"
    assert tuple(item.status for item in generating.candidates) == (
        "completed",
        "failed",
        "canceled",
        "canceled",
    )
    generating_before = JobStateRecord.model_validate_json(
        state_before["generating"]
    )
    assert generating.candidates[0] == generating_before.candidates[0]
    postprocessing = store.load(
        PROJECT_ID,
        JOB_IDS["postprocessing"],
    ).state
    assert postprocessing.status == "failed"
    assert postprocessing.failure is not None
    assert postprocessing.failure.code == "JOB_INTERRUPTED"
    assert (
        project_root / "jobs" / str(MALFORMED_JOB_ID) / "state.json"
    ).read_bytes() == malformed_before

    indexed = {
        summary.job_id: summary
        for summary in index_service.list_jobs(PROJECT_ID)
    }
    assert len(indexed) == 6
    assert indexed[JOB_IDS["queued"]].status == "queued"
    assert indexed[JOB_IDS["generating"]].status == "failed"
    assert indexed[JOB_IDS["postprocessing"]].status == "failed"
    assert indexed[JOB_IDS["completed"]].status == "completed"

    first_run_bytes = _state_bytes(project_root)
    second = RecoveryService(
        repository=repository,
        store=store,
        index=index_service,
        clock=_clock(SECOND_RECOVERY_AT, SECOND_COMPLETED_AT),
    ).run()

    assert second.project_count == 1
    assert second.job_count == 6
    assert second.recovered_job_count == 0
    assert second.completed_at == SECOND_COMPLETED_AT
    assert second.issues == report.issues
    assert _state_bytes(project_root) == first_run_bytes
    assert dump_job_state(generating) == first_run_bytes["generating"]
    assert {
        name: _tree_hashes(project_root / name)
        for name in ("source", "pack")
    } == protected_before
