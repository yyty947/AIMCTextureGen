import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import httpx

from aimctexturegen.catalog.registry import CatalogRegistry
from aimctexturegen.index.database import ProjectIndex
from aimctexturegen.index.service import IndexService
from aimctexturegen.jobs.recovery import (
    RecoveryIssue,
    RecoveryReport,
    RecoveryService,
)
from aimctexturegen.jobs.service import JobService
from aimctexturegen.jobs.store import JobStore
from aimctexturegen.main import AppServices, create_app
from aimctexturegen.projects.repository import ProjectRepository
from aimctexturegen.projects.service import ProjectService


CATALOG_ROOT = Path(__file__).parents[3] / "catalogs" / "java"
PROJECT_ID = UUID("abcdefab-cdef-4abc-8def-abcdefabcdef")
JOB_ID = UUID("11111111-2222-4333-8444-555555555555")


class _RecoveryStub:
    def __init__(self, report: RecoveryReport) -> None:
        self.report = report
        self.calls = 0

    def run(self) -> RecoveryReport:
        self.calls += 1
        return self.report


def _request(app, path: str) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(
            app=app,
            raise_app_exceptions=False,
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.get(path)

    return asyncio.run(send())


def test_lifespan_runs_injected_recovery_and_exposes_path_free_report(
    tmp_path: Path,
) -> None:
    report = RecoveryReport(
        project_count=2,
        job_count=3,
        recovered_job_count=1,
        issues=(
            RecoveryIssue(
                project_id=PROJECT_ID,
                job_id=JOB_ID,
                code="CORRUPT_JOB_RECORD",
                user_message="任务记录损坏或不一致",
            ),
        ),
        completed_at=datetime(2026, 7, 29, 9, 30, tzinfo=timezone.utc),
    )
    recovery = _RecoveryStub(report)
    services = AppServices(
        workspace=object(),
        catalogs=CatalogRegistry(CATALOG_ROOT),
        project_root=tmp_path / "injected-projects",
        recovery_service=recovery,
    )
    app = create_app(services=services)

    async def run_lifespan() -> httpx.Response:
        async with app.router.lifespan_context(app):
            assert app.state.recovery_report is report
            return await asyncio.to_thread(_request, app, "/api/system/recovery")

    response = asyncio.run(run_lifespan())

    assert recovery.calls == 1
    assert response.status_code == 200, response.text
    assert response.json() == {
        "project_count": 2,
        "job_count": 3,
        "recovered_job_count": 1,
        "issues": [
            {
                "project_id": str(PROJECT_ID),
                "job_id": str(JOB_ID),
                "code": "CORRUPT_JOB_RECORD",
                "user_message": "任务记录损坏或不一致",
            }
        ],
        "completed_at": "2026-07-29T09:30:00Z",
    }
    assert str(tmp_path) not in response.text


def test_default_lifespan_wires_real_services_and_closes_index_connections(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    app = create_app(project_root=projects_root, catalog_root=CATALOG_ROOT)
    services = app.state.services

    assert isinstance(services.repository, ProjectRepository)
    assert isinstance(services.project_service, ProjectService)
    assert isinstance(services.job_store, JobStore)
    assert isinstance(services.job_service, JobService)
    assert isinstance(services.project_index, ProjectIndex)
    assert isinstance(services.index_service, IndexService)
    assert isinstance(services.recovery_service, RecoveryService)

    async def run_lifespan() -> RecoveryReport:
        async with app.router.lifespan_context(app):
            report = app.state.recovery_report
            assert services.project_index.database_path.is_file()
            renamed = services.project_index.database_path.with_name(
                "index.sqlite3.closed-check"
            )
            services.project_index.database_path.rename(renamed)
            renamed.rename(services.project_index.database_path)
            return report

    report = asyncio.run(run_lifespan())

    assert report.project_count == 0
    assert report.job_count == 0
    assert report.recovered_job_count == 0
    assert report.issues == ()
