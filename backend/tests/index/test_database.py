from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pydantic
import pytest

from aimctexturegen.index.database import ProjectIndex
from aimctexturegen.index.models import IndexSnapshot
from aimctexturegen.jobs.models import JobSummary
from aimctexturegen.projects.models import ProjectManifest, ProjectSummary


PROJECT_ID = UUID("abcdefab-cdef-4abc-8def-abcdefabcdef")
OTHER_PROJECT_ID = UUID("22222222-2222-4222-8222-222222222222")
JOB_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
RETRY_JOB_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def _manifest(
    project_id: UUID = PROJECT_ID,
    *,
    name: str = "Project",
    created_at: datetime = NOW,
    updated_at: datetime = NOW,
) -> ProjectManifest:
    return ProjectManifest(
        schema_version=2,
        project_id=project_id,
        project_name=name,
        edition="java",
        java_pack_format=34,
        supported_formats=None,
        catalog_id="java-format-34-dev",
        source_sha256="source-secret-" + "a" * 50,
        created_at=created_at,
        updated_at=updated_at,
        default_resolution=16,
        default_parallelism=1,
        style_references=(),
    )


def _project_summary(manifest: ProjectManifest) -> ProjectSummary:
    return ProjectSummary(
        project_id=manifest.project_id,
        project_name=manifest.project_name,
        edition=manifest.edition,
        java_pack_format=manifest.java_pack_format,
        catalog_id=manifest.catalog_id,
        created_at=manifest.created_at,
        updated_at=manifest.updated_at,
    )


def _job_summary(
    job_id: UUID = JOB_ID,
    *,
    project_id: UUID = PROJECT_ID,
    retry_of_job_id: UUID | None = None,
    status: str = "queued",
    revision: int = 0,
    created_at: datetime = NOW,
    updated_at: datetime = NOW,
) -> JobSummary:
    candidate_status = "pending" if status == "queued" else "completed"
    return JobSummary(
        job_id=job_id,
        project_id=project_id,
        retry_of_job_id=retry_of_job_id,
        target_semantic_id="minecraft:deepslate",
        target_display_name="Deepslate",
        resolution=16,
        parallelism=1,
        status=status,
        revision=revision,
        candidate_statuses=(candidate_status,) * 4,
        created_at=created_at,
        updated_at=updated_at,
    )


def test_lazy_schema_is_version_one_and_enforces_project_foreign_keys(tmp_path):
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    index = ProjectIndex(projects_root)

    index.upsert_project(_manifest())

    with sqlite3.connect(index.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert tables == {"projects", "jobs"}

    with pytest.raises(sqlite3.IntegrityError):
        index.upsert_job(_job_summary(project_id=OTHER_PROJECT_ID))


@pytest.mark.parametrize(
    "invalid_id",
    (
        str(PROJECT_ID).upper(),
        "abcdefab-cdef-4abc-8def-abcde-abcdef",
    ),
)
def test_schema_rejects_noncanonical_uuid_text(tmp_path, invalid_id):
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    index = ProjectIndex(projects_root)
    index.upsert_project(_manifest())

    with sqlite3.connect(index.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE projects SET project_id = ?",
                (invalid_id,),
            )


def test_project_queries_sort_instants_and_return_frozen_summaries(tmp_path):
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    index = ProjectIndex(projects_root)
    later_in_utc = NOW + timedelta(minutes=1)
    same_instant_with_offset = later_in_utc.astimezone(
        timezone(timedelta(hours=8))
    )
    first = _manifest(PROJECT_ID, name="First", updated_at=NOW)
    second = _manifest(
        OTHER_PROJECT_ID,
        name="Second",
        created_at=same_instant_with_offset,
        updated_at=same_instant_with_offset,
    )

    index.upsert_project(first)
    index.upsert_project(second)

    summaries = index.list_projects()
    assert summaries == (_project_summary(second), _project_summary(first))
    assert all(isinstance(item, ProjectSummary) for item in summaries)
    with pytest.raises(pydantic.ValidationError):
        summaries[0].project_name = "changed"  # type: ignore[misc]


def test_job_queries_sort_and_preserve_retry_lineage(tmp_path):
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    index = ProjectIndex(projects_root)
    index.upsert_project(_manifest())
    original = _job_summary(updated_at=NOW)
    retry = _job_summary(
        RETRY_JOB_ID,
        retry_of_job_id=JOB_ID,
        created_at=NOW + timedelta(minutes=2),
        updated_at=NOW + timedelta(minutes=2),
    )

    index.upsert_job(original)
    index.upsert_job(retry)

    summaries = index.list_jobs(PROJECT_ID)
    assert summaries == (retry, original)
    assert summaries[0].retry_of_job_id == JOB_ID
    assert all(isinstance(item, JobSummary) for item in summaries)
    with pytest.raises(pydantic.ValidationError):
        summaries[0].revision = 99  # type: ignore[misc]


def test_job_upsert_is_revision_monotonic_and_equal_revision_is_idempotent(
    tmp_path,
):
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    index = ProjectIndex(projects_root)
    index.upsert_project(_manifest())
    initial = _job_summary()
    newest = _job_summary(
        status="completed",
        revision=2,
        updated_at=NOW + timedelta(minutes=2),
    )
    delayed_older = _job_summary(
        status="queued",
        revision=1,
        updated_at=NOW + timedelta(minutes=1),
    )
    conflicting_equal = _job_summary(
        status="failed",
        revision=2,
        updated_at=NOW + timedelta(minutes=3),
    )

    index.upsert_job(initial)
    index.upsert_job(newest)
    index.upsert_job(delayed_older)
    index.upsert_job(conflicting_equal)

    assert index.list_jobs(PROJECT_ID) == (newest,)


@pytest.mark.parametrize(
    "column",
    (
        "candidate_status_0",
        "candidate_status_1",
        "candidate_status_2",
        "candidate_status_3",
    ),
)
def test_schema_rejects_invalid_candidate_status_in_every_column(
    tmp_path,
    column,
):
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    index = ProjectIndex(projects_root)
    index.upsert_project(_manifest())
    index.upsert_job(_job_summary())

    with sqlite3.connect(index.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"UPDATE jobs SET {column} = ? WHERE job_id = ?",
                ("unknown-state", str(JOB_ID)),
            )


def test_index_contains_only_rebuildable_summary_fields(tmp_path):
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    index = ProjectIndex(projects_root)
    project = _manifest()
    job = _job_summary()

    index.replace_snapshot(
        IndexSnapshot(
            projects=(_project_summary(project),),
            jobs=(job,),
        )
    )

    with sqlite3.connect(index.database_path) as connection:
        schema = "\n".join(
            row[0]
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
            )
        ).lower()
        dump = "\n".join(connection.iterdump()).lower()
    for forbidden in (
        "prompt",
        "seed",
        "path",
        "technical",
        "source-secret",
        "assets/minecraft",
    ):
        assert forbidden not in schema
        assert forbidden not in dump


def test_unknown_schema_version_is_rejected_without_upgrade(tmp_path):
    projects_root = tmp_path / "projects"
    metadata_root = projects_root / ".aimctexturegen"
    metadata_root.mkdir(parents=True)
    database_path = metadata_root / "index.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA user_version = 2")

    index = ProjectIndex(projects_root)
    with pytest.raises(sqlite3.DatabaseError, match="schema version"):
        index.list_projects()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2

    with pytest.raises(sqlite3.DatabaseError, match="schema version"):
        index.replace_snapshot(IndexSnapshot(projects=(), jobs=()))

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_nonempty_unversioned_database_is_preserved_byte_for_byte(tmp_path):
    projects_root = tmp_path / "projects"
    metadata_root = projects_root / ".aimctexturegen"
    metadata_root.mkdir(parents=True)
    database_path = metadata_root / "index.sqlite3"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("CREATE TABLE future_data (value TEXT NOT NULL)")
        connection.execute(
            "INSERT INTO future_data (value) VALUES (?)",
            ("must-survive",),
        )
        connection.commit()
    finally:
        connection.close()
    before = database_path.read_bytes()

    index = ProjectIndex(projects_root)
    with pytest.raises(sqlite3.DatabaseError, match="unversioned"):
        index.replace_snapshot(IndexSnapshot(projects=(), jobs=()))

    assert database_path.read_bytes() == before
    assert not index.temporary_path.exists()
