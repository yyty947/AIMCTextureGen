import io
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from PIL import Image

from aimctexturegen.catalog.registry import CatalogRegistry
from aimctexturegen.index.database import ProjectIndex
from aimctexturegen.index.service import IndexService
from aimctexturegen.jobs.models import JobRequest
from aimctexturegen.jobs.recovery import RecoveryReport, RecoveryService
from aimctexturegen.jobs.state_machine import (
    transition_candidate_state,
    transition_job_state,
)
from aimctexturegen.jobs.store import JobStore, LoadedJob
from aimctexturegen.packs.java_adapter import JavaPackAdapter
from aimctexturegen.projects.models import (
    ProjectManifest,
    ProjectManifestV1,
    load_project_manifest,
)
from aimctexturegen.projects.repository import ProjectRepository
from aimctexturegen.projects.workspace import ProjectWorkspace


REPOSITORY_ROOT = Path(__file__).parents[3]
CATALOG_ROOT = REPOSITORY_ROOT / "catalogs" / "java"
JOB_IDS = {
    "queued": UUID("30000000-0000-4000-8000-000000000001"),
    "active": UUID("30000000-0000-4000-8000-000000000002"),
    "completed": UUID("30000000-0000-4000-8000-000000000003"),
}


@dataclass(frozen=True)
class _ApplicationInstance:
    repository: ProjectRepository
    store: JobStore
    index: ProjectIndex
    index_service: IndexService
    recovery_report: RecoveryReport

    @classmethod
    def start(cls, projects_root: Path) -> "_ApplicationInstance":
        repository = ProjectRepository(projects_root)
        store = JobStore(repository)
        index = ProjectIndex(projects_root)
        index_service = IndexService(
            repository=repository,
            store=store,
            index=index,
        )
        recovery_report = RecoveryService(
            repository=repository,
            store=store,
            index=index_service,
        ).run()
        return cls(
            repository=repository,
            store=store,
            index=index,
            index_service=index_service,
            recovery_report=recovery_report,
        )


def _png_bytes() -> bytes:
    payload = io.BytesIO()
    Image.new("RGB", (2, 2), (64, 64, 64)).save(payload, format="PNG")
    return payload.getvalue()


def _create_pack(path: Path) -> None:
    metadata = json.dumps(
        {
            "pack": {
                "pack_format": 34,
                "description": "Restart recovery synthetic pack",
            }
        },
        separators=(",", ":"),
    ).encode("utf-8")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("pack.mcmeta", metadata)
        archive.writestr(
            "assets/minecraft/textures/block/stone.png",
            _png_bytes(),
        )


def _request(
    project_id: UUID,
    status: str,
    created_at: datetime,
    seed_base: int,
) -> JobRequest:
    return JobRequest(
        schema_version=1,
        job_id=JOB_IDS[status],
        project_id=project_id,
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
            seed_base + 1,
            seed_base + 2,
            seed_base + 3,
            seed_base + 4,
        ),
        created_at=created_at,
    )


def _persist(
    store: JobStore,
    project_id: UUID,
    loaded: LoadedJob,
    state,
) -> LoadedJob:
    return store.replace_state(
        project_id,
        loaded.request.job_id,
        state,
        expected_revision=loaded.state.revision,
    )


def _job_transition(
    store: JobStore,
    project_id: UUID,
    loaded: LoadedJob,
    status: str,
    now: datetime,
) -> LoadedJob:
    return _persist(
        store,
        project_id,
        loaded,
        transition_job_state(loaded.state, status, now=now),
    )


def _candidate_transition(
    store: JobStore,
    project_id: UUID,
    loaded: LoadedJob,
    candidate_index: int,
    status: str,
    now: datetime,
) -> LoadedJob:
    return _persist(
        store,
        project_id,
        loaded,
        transition_candidate_state(
            loaded.state,
            candidate_index,
            status,
            now=now,
        ),
    )


def _create_runtime_jobs(
    store: JobStore,
    project_id: UUID,
    created_at: datetime,
) -> None:
    store.create(_request(project_id, "queued", created_at, 10))

    active = store.create(
        _request(
            project_id,
            "active",
            created_at + timedelta(seconds=1),
            20,
        )
    )
    active = _job_transition(
        store,
        project_id,
        active,
        "generating",
        created_at + timedelta(minutes=1),
    )
    _candidate_transition(
        store,
        project_id,
        active,
        0,
        "generating",
        created_at + timedelta(minutes=1),
    )

    completed = store.create(
        _request(
            project_id,
            "completed",
            created_at + timedelta(seconds=2),
            30,
        )
    )
    completed = _job_transition(
        store,
        project_id,
        completed,
        "generating",
        created_at + timedelta(minutes=1),
    )
    for candidate_index in range(4):
        for status in ("generating", "postprocessing", "completed"):
            completed = _candidate_transition(
                store,
                project_id,
                completed,
                candidate_index,
                status,
                created_at + timedelta(minutes=1),
            )
    completed = _job_transition(
        store,
        project_id,
        completed,
        "postprocessing",
        created_at + timedelta(minutes=1),
    )
    _job_transition(
        store,
        project_id,
        completed,
        "completed",
        created_at + timedelta(minutes=1),
    )


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _write_schema_1_manifest(
    project_root: Path,
    manifest: ProjectManifest,
) -> bytes:
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


def test_restart_migrates_project_recovers_index_and_jobs_without_mutating_pack_assets(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    source = tmp_path / "synthetic-pack.zip"
    _create_pack(source)
    workspace = ProjectWorkspace(
        projects_root,
        JavaPackAdapter(),
        CatalogRegistry(CATALOG_ROOT),
    )
    manifest = workspace.import_pack(source, "Restart recovery")
    project_root = projects_root / str(manifest.project_id)

    first_instance = _ApplicationInstance.start(projects_root)
    created_at = datetime.now(timezone.utc) - timedelta(hours=1)
    _create_runtime_jobs(
        first_instance.store,
        manifest.project_id,
        created_at,
    )
    legacy_manifest = _write_schema_1_manifest(project_root, manifest)
    queued_path = (
        project_root / "jobs" / str(JOB_IDS["queued"]) / "state.json"
    )
    completed_path = (
        project_root / "jobs" / str(JOB_IDS["completed"]) / "state.json"
    )
    queued_before = queued_path.read_bytes()
    completed_before = completed_path.read_bytes()
    protected_before = {
        name: _tree_hashes(project_root / name)
        for name in ("source", "pack")
    }

    first_instance.index.database_path.unlink()
    second_instance = _ApplicationInstance.start(projects_root)

    migrated_payload = (project_root / "project.json").read_bytes()
    assert migrated_payload != legacy_manifest
    migrated_manifest, needs_migration = load_project_manifest(migrated_payload)
    assert needs_migration is False
    assert migrated_manifest == manifest
    assert second_instance.recovery_report.project_count == 1
    assert second_instance.recovery_report.job_count == 3
    assert second_instance.recovery_report.recovered_job_count == 1
    assert second_instance.recovery_report.issues == ()
    assert tuple(
        summary.project_id
        for summary in second_instance.index_service.list_projects()
    ) == (manifest.project_id,)
    jobs = {
        summary.job_id: summary
        for summary in second_instance.index_service.list_jobs(
            manifest.project_id
        )
    }
    assert set(jobs) == set(JOB_IDS.values())
    assert jobs[JOB_IDS["queued"]].status == "queued"
    assert jobs[JOB_IDS["active"]].status == "failed"
    assert jobs[JOB_IDS["completed"]].status == "completed"
    recovered = second_instance.store.load(
        manifest.project_id,
        JOB_IDS["active"],
    )
    assert recovered.state.failure is not None
    assert recovered.state.failure.code == "JOB_INTERRUPTED"
    assert queued_path.read_bytes() == queued_before
    assert completed_path.read_bytes() == completed_before
    assert {
        name: _tree_hashes(project_root / name)
        for name in ("source", "pack")
    } == protected_before
