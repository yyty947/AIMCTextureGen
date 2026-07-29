"""Disposable SQLite storage for project and job query summaries."""

from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from aimctexturegen.index.models import IndexSnapshot
from aimctexturegen.jobs.models import JobSummary
from aimctexturegen.projects._directory_guard import is_reparse_point
from aimctexturegen.projects.models import ProjectManifest, ProjectSummary


_SCHEMA_VERSION = 1
_BUSY_TIMEOUT_SECONDS = 5.0
_BUSY_TIMEOUT_MILLISECONDS = 5_000
_UUID_CHECK = """
    length({column}) = 36
    AND substr({column}, 9, 1) = '-'
    AND substr({column}, 14, 1) = '-'
    AND substr({column}, 19, 1) = '-'
    AND substr({column}, 24, 1) = '-'
    AND {column} = lower({column})
    AND length(replace({column}, '-', '')) = 32
    AND replace({column}, '-', '') NOT GLOB '*[^0-9a-f]*'
"""
_CREATE_PROJECTS = f"""
CREATE TABLE projects (
    project_id TEXT PRIMARY KEY CHECK ({_UUID_CHECK.format(column="project_id")}),
    project_name TEXT NOT NULL,
    edition TEXT NOT NULL CHECK (edition = 'java'),
    java_pack_format INTEGER NOT NULL,
    catalog_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""
_CREATE_JOBS = f"""
CREATE TABLE jobs (
    job_id TEXT PRIMARY KEY CHECK ({_UUID_CHECK.format(column="job_id")}),
    project_id TEXT NOT NULL
        CHECK ({_UUID_CHECK.format(column="project_id")})
        REFERENCES projects(project_id) ON DELETE CASCADE,
    retry_of_job_id TEXT
        CHECK (
            retry_of_job_id IS NULL
            OR ({_UUID_CHECK.format(column="retry_of_job_id")})
        ),
    target_semantic_id TEXT NOT NULL,
    target_display_name TEXT NOT NULL,
    resolution INTEGER NOT NULL CHECK (resolution IN (16, 32, 64)),
    parallelism INTEGER NOT NULL CHECK (parallelism IN (1, 2, 4)),
    status TEXT NOT NULL CHECK (
        status IN (
            'queued', 'generating', 'postprocessing',
            'completed', 'failed', 'canceled'
        )
    ),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    candidate_status_0 TEXT NOT NULL,
    candidate_status_1 TEXT NOT NULL,
    candidate_status_2 TEXT NOT NULL,
    candidate_status_3 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""
_CREATE_JOB_INDEX = """
CREATE INDEX jobs_project_updated
ON jobs(project_id, updated_at DESC, job_id ASC)
"""
_PROJECT_UPSERT = """
INSERT INTO projects (
    project_id, project_name, edition, java_pack_format, catalog_id,
    created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(project_id) DO UPDATE SET
    project_name = excluded.project_name,
    edition = excluded.edition,
    java_pack_format = excluded.java_pack_format,
    catalog_id = excluded.catalog_id,
    created_at = excluded.created_at,
    updated_at = excluded.updated_at
"""
_JOB_UPSERT = """
INSERT INTO jobs (
    job_id, project_id, retry_of_job_id, target_semantic_id,
    target_display_name, resolution, parallelism, status, revision,
    candidate_status_0, candidate_status_1, candidate_status_2,
    candidate_status_3, created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(job_id) DO UPDATE SET
    project_id = excluded.project_id,
    retry_of_job_id = excluded.retry_of_job_id,
    target_semantic_id = excluded.target_semantic_id,
    target_display_name = excluded.target_display_name,
    resolution = excluded.resolution,
    parallelism = excluded.parallelism,
    status = excluded.status,
    revision = excluded.revision,
    candidate_status_0 = excluded.candidate_status_0,
    candidate_status_1 = excluded.candidate_status_1,
    candidate_status_2 = excluded.candidate_status_2,
    candidate_status_3 = excluded.candidate_status_3,
    created_at = excluded.created_at,
    updated_at = excluded.updated_at
"""
_PROJECT_SELECT = """
SELECT project_id, project_name, edition, java_pack_format, catalog_id,
       created_at, updated_at
FROM projects
ORDER BY updated_at DESC, project_id ASC
"""
_JOB_SELECT = """
SELECT job_id, project_id, retry_of_job_id, target_semantic_id,
       target_display_name, resolution, parallelism, status, revision,
       candidate_status_0, candidate_status_1, candidate_status_2,
       candidate_status_3, created_at, updated_at
FROM jobs
WHERE project_id = ?
ORDER BY updated_at DESC, job_id ASC
"""


class ProjectIndex:
    """Query project and job summaries without owning canonical product state."""

    def __init__(self, projects_root: Path) -> None:
        self._projects_root = Path(os.path.abspath(projects_root))

    @property
    def database_path(self) -> Path:
        return self._projects_root / ".aimctexturegen" / "index.sqlite3"

    @property
    def temporary_path(self) -> Path:
        return self.database_path.with_name("index.sqlite3.tmp")

    def upsert_project(self, manifest: ProjectManifest) -> None:
        """Insert or refresh one project summary in an explicit transaction."""

        if not isinstance(manifest, ProjectManifest):
            raise TypeError("manifest must be a ProjectManifest")
        summary = _project_summary(manifest)
        with self._operation_connection() as connection:
            _write_transaction(
                connection,
                lambda: connection.execute(
                    _PROJECT_UPSERT,
                    _project_values(summary),
                ),
            )

    def upsert_job(self, summary: JobSummary) -> None:
        """Insert or refresh one job summary in an explicit transaction."""

        if not isinstance(summary, JobSummary):
            raise TypeError("summary must be a JobSummary")
        with self._operation_connection() as connection:
            _write_transaction(
                connection,
                lambda: connection.execute(_JOB_UPSERT, _job_values(summary)),
            )

    def list_projects(self) -> tuple[ProjectSummary, ...]:
        """Return frozen project summaries in stable query order."""

        with self._operation_connection() as connection:
            return tuple(
                _project_from_row(row)
                for row in connection.execute(_PROJECT_SELECT)
            )

    def list_jobs(self, project_id: UUID) -> tuple[JobSummary, ...]:
        """Return frozen job summaries for one canonical project UUID."""

        if not isinstance(project_id, UUID):
            raise TypeError("project_id must be a UUID")
        with self._operation_connection() as connection:
            return tuple(
                _job_from_row(row)
                for row in connection.execute(_JOB_SELECT, (str(project_id),))
            )

    def replace_snapshot(self, snapshot: IndexSnapshot) -> None:
        """Build, validate, close, and atomically publish a complete new index."""

        if not isinstance(snapshot, IndexSnapshot):
            raise TypeError("snapshot must be an IndexSnapshot")
        self._ensure_metadata_root()
        self._reject_unknown_final_schema()
        self._remove_temporary_files()
        try:
            self._populate_snapshot(self.temporary_path, snapshot)
            self._validate_snapshot(self.temporary_path, snapshot)
            self._publish_snapshot(self.temporary_path, self.database_path)
        finally:
            self._remove_temporary_files()

    def _populate_snapshot(
        self,
        path: Path,
        snapshot: IndexSnapshot,
    ) -> None:
        with self._connection(path) as connection:
            def populate() -> None:
                connection.executemany(
                    _PROJECT_UPSERT,
                    tuple(_project_values(summary) for summary in snapshot.projects),
                )
                connection.executemany(
                    _JOB_UPSERT,
                    tuple(_job_values(summary) for summary in snapshot.jobs),
                )

            _write_transaction(connection, populate)

    def _validate_snapshot(
        self,
        path: Path,
        snapshot: IndexSnapshot,
    ) -> None:
        with self._connection(path) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise sqlite3.DatabaseError("index integrity check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise sqlite3.DatabaseError("index foreign key check failed")
            project_count = connection.execute(
                "SELECT COUNT(*) FROM projects"
            ).fetchone()[0]
            job_count = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            if project_count != len(snapshot.projects) or job_count != len(
                snapshot.jobs
            ):
                raise sqlite3.DatabaseError("index snapshot row count mismatch")

    def _publish_snapshot(self, source: Path, destination: Path) -> None:
        os.replace(source, destination)

    @contextmanager
    def _operation_connection(self) -> Iterator[sqlite3.Connection]:
        self._ensure_metadata_root()
        with self._connection(self.database_path) as connection:
            yield connection

    @contextmanager
    def _connection(self, path: Path) -> Iterator[sqlite3.Connection]:
        _require_safe_database_path(path)
        connection = sqlite3.connect(
            path,
            timeout=_BUSY_TIMEOUT_SECONDS,
            isolation_level=None,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MILLISECONDS}")
            connection.execute("PRAGMA foreign_keys = ON")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise sqlite3.DatabaseError("SQLite foreign keys are unavailable")
            _ensure_schema(connection)
            yield connection
        finally:
            connection.close()

    def _ensure_metadata_root(self) -> None:
        _require_plain_directory(self._projects_root)
        metadata_root = self.database_path.parent
        try:
            metadata_root.mkdir()
        except FileExistsError:
            pass
        _require_plain_directory(metadata_root)

    def _remove_temporary_files(self) -> None:
        for path in (
            self.temporary_path,
            self.temporary_path.with_name(self.temporary_path.name + "-journal"),
        ):
            try:
                status = os.lstat(path)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(status.st_mode) or is_reparse_point(path, status):
                raise sqlite3.DatabaseError("unsafe temporary index path")
            path.unlink()

    def _reject_unknown_final_schema(self) -> None:
        path = self.database_path
        _require_safe_database_path(path)
        if not path.exists():
            return
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(path, timeout=_BUSY_TIMEOUT_SECONDS)
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        except sqlite3.DatabaseError:
            return
        finally:
            if connection is not None:
                connection.close()
        if version not in (0, _SCHEMA_VERSION):
            raise sqlite3.DatabaseError(
                f"unsupported index schema version: {version}"
            )


def _ensure_schema(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version == _SCHEMA_VERSION:
        return
    if version != 0:
        raise sqlite3.DatabaseError(
            f"unsupported index schema version: {version}"
        )
    existing = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%'
          AND type IN ('table', 'index', 'view', 'trigger')
        LIMIT 1
        """
    ).fetchone()
    if existing is not None:
        raise sqlite3.DatabaseError("unsupported unversioned index schema")

    def create() -> None:
        connection.execute(_CREATE_PROJECTS)
        connection.execute(_CREATE_JOBS)
        connection.execute(_CREATE_JOB_INDEX)
        connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    _write_transaction(connection, create)


def _write_transaction(connection: sqlite3.Connection, operation) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        operation()
    except BaseException:
        connection.rollback()
        raise
    connection.commit()


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


def _project_values(summary: ProjectSummary) -> tuple[object, ...]:
    return (
        str(summary.project_id),
        summary.project_name,
        summary.edition,
        summary.java_pack_format,
        summary.catalog_id,
        _encode_timestamp(summary.created_at),
        _encode_timestamp(summary.updated_at),
    )


def _job_values(summary: JobSummary) -> tuple[object, ...]:
    return (
        str(summary.job_id),
        str(summary.project_id),
        (
            None
            if summary.retry_of_job_id is None
            else str(summary.retry_of_job_id)
        ),
        summary.target_semantic_id,
        summary.target_display_name,
        summary.resolution,
        summary.parallelism,
        summary.status,
        summary.revision,
        *summary.candidate_statuses,
        _encode_timestamp(summary.created_at),
        _encode_timestamp(summary.updated_at),
    )


def _project_from_row(row: sqlite3.Row) -> ProjectSummary:
    return ProjectSummary(
        project_id=UUID(row["project_id"]),
        project_name=row["project_name"],
        edition=row["edition"],
        java_pack_format=row["java_pack_format"],
        catalog_id=row["catalog_id"],
        created_at=_decode_timestamp(row["created_at"]),
        updated_at=_decode_timestamp(row["updated_at"]),
    )


def _job_from_row(row: sqlite3.Row) -> JobSummary:
    retry_of_job_id = row["retry_of_job_id"]
    return JobSummary(
        job_id=UUID(row["job_id"]),
        project_id=UUID(row["project_id"]),
        retry_of_job_id=(
            None if retry_of_job_id is None else UUID(retry_of_job_id)
        ),
        target_semantic_id=row["target_semantic_id"],
        target_display_name=row["target_display_name"],
        resolution=row["resolution"],
        parallelism=row["parallelism"],
        status=row["status"],
        revision=row["revision"],
        candidate_statuses=(
            row["candidate_status_0"],
            row["candidate_status_1"],
            row["candidate_status_2"],
            row["candidate_status_3"],
        ),
        created_at=_decode_timestamp(row["created_at"]),
        updated_at=_decode_timestamp(row["updated_at"]),
    )


def _encode_timestamp(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _decode_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _require_plain_directory(path: Path) -> None:
    try:
        status = os.lstat(path)
    except OSError as error:
        raise sqlite3.DatabaseError("index directory is unavailable") from error
    if not stat.S_ISDIR(status.st_mode) or is_reparse_point(path, status):
        raise sqlite3.DatabaseError("index directory is unsafe")


def _require_safe_database_path(path: Path) -> None:
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as error:
        raise sqlite3.DatabaseError("index path is unavailable") from error
    if not stat.S_ISREG(status.st_mode) or is_reparse_point(path, status):
        raise sqlite3.DatabaseError("index path is unsafe")
