import logging
import os
import stat
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from aimctexturegen.api import jobs as jobs_api
from aimctexturegen.api import projects as projects_api
from aimctexturegen.api import system as system_api
from aimctexturegen.catalog.models import CatalogProfile
from aimctexturegen.catalog.registry import CatalogRegistry
from aimctexturegen.comfy.registry import ManifestRegistry
from aimctexturegen.core.errors import ApiProblem, problem_response
from aimctexturegen.core.request_limits import ImportBodyLimitMiddleware
from aimctexturegen.index.database import ProjectIndex
from aimctexturegen.index.service import IndexService
from aimctexturegen.jobs.models import CreateJobCommand
from aimctexturegen.jobs.recovery import RecoveryReport, RecoveryService
from aimctexturegen.jobs.service import JobService
from aimctexturegen.jobs.store import JobStore, LoadedJob
from aimctexturegen.packs.java_adapter import JavaPackAdapter
from aimctexturegen.projects._directory_guard import is_reparse_point
from aimctexturegen.projects.models import ProjectManifest
from aimctexturegen.projects.repository import ProjectRepository
from aimctexturegen.projects.service import ProjectService
from aimctexturegen.projects.workspace import ProjectWorkspace


_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PROJECT_ROOT = _REPOSITORY_ROOT / "projects"
_DEFAULT_CATALOG_ROOT = _REPOSITORY_ROOT / "catalogs" / "java"
_LOGGER = logging.getLogger(__name__)


class WorkspaceService(Protocol):
    def import_pack(self, source: Path, project_name: str) -> ProjectManifest: ...


class CatalogService(Protocol):
    def for_pack_format(self, pack_format: int) -> CatalogProfile: ...


class JobApplicationService(Protocol):
    def create_job(
        self,
        project_id,
        command: CreateJobCommand,
        *,
        model_profile=None,
    ) -> LoadedJob: ...


class RecoveryRunner(Protocol):
    def run(self) -> RecoveryReport: ...


@dataclass(frozen=True)
class AppServices:
    workspace: WorkspaceService
    catalogs: CatalogService
    project_root: Path
    repository: ProjectRepository | None = None
    project_service: ProjectService | None = None
    job_store: JobStore | None = None
    job_service: JobService | JobApplicationService | None = None
    project_index: ProjectIndex | None = None
    index_service: IndexService | None = None
    recovery_service: RecoveryService | RecoveryRunner | None = None
    manifest_registry: ManifestRegistry | None = None
    _project_service_injected: bool = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        project_service_injected = self.project_service is not None
        repository = (
            ProjectRepository(self.project_root)
            if self.repository is None
            else self.repository
        )
        store = JobStore(repository) if self.job_store is None else self.job_store
        project_index = (
            ProjectIndex(self.project_root)
            if self.project_index is None
            else self.project_index
        )
        index_service = (
            IndexService(
                repository=repository,
                store=store,
                index=project_index,
            )
            if self.index_service is None
            else self.index_service
        )
        project_service = (
            ProjectService(
                workspace=self.workspace,
                repository=repository,
                catalogs=self.catalogs,
                index=index_service,
            )
            if self.project_service is None
            else self.project_service
        )
        job_service = (
            JobService(
                repository=repository,
                catalogs=self.catalogs,
                store=store,
                index=index_service,
            )
            if self.job_service is None
            else self.job_service
        )
        recovery_service = (
            RecoveryService(
                repository=repository,
                store=store,
                index=index_service,
            )
            if self.recovery_service is None
            else self.recovery_service
        )
        manifest_registry = (
            ManifestRegistry.load(_REPOSITORY_ROOT)
            if self.manifest_registry is None
            else self.manifest_registry
        )
        object.__setattr__(self, "repository", repository)
        object.__setattr__(self, "project_service", project_service)
        object.__setattr__(self, "job_store", store)
        object.__setattr__(self, "job_service", job_service)
        object.__setattr__(self, "project_index", project_index)
        object.__setattr__(self, "index_service", index_service)
        object.__setattr__(self, "recovery_service", recovery_service)
        object.__setattr__(self, "manifest_registry", manifest_registry)
        object.__setattr__(
            self,
            "_project_service_injected",
            project_service_injected,
        )


def create_app(
    project_root: Path | None = None,
    catalog_root: Path | None = None,
    max_import_bytes: int | None = None,
    max_import_body_bytes: int | None = None,
    services: AppServices | None = None,
) -> FastAPI:
    if services is None:
        configured_project_root = _resolve_project_root(
            _DEFAULT_PROJECT_ROOT if project_root is None else project_root
        )
        configured_catalog_root = (
            _DEFAULT_CATALOG_ROOT if catalog_root is None else catalog_root
        ).resolve()
        catalogs = CatalogRegistry(configured_catalog_root)
        services = AppServices(
            workspace=ProjectWorkspace(
                configured_project_root,
                JavaPackAdapter(),
                catalogs,
            ),
            catalogs=catalogs,
            project_root=configured_project_root,
        )
    elif project_root is not None or catalog_root is not None:
        raise ValueError("services cannot be combined with project_root or catalog_root")
    @asynccontextmanager
    async def lifespan(runtime_app: FastAPI):
        _ensure_runtime_project_root(runtime_app.state.services.project_root)
        recovery_service = runtime_app.state.services.recovery_service
        if recovery_service is None:
            raise RuntimeError("recovery service is unavailable")
        runtime_app.state.recovery_report = recovery_service.run()
        runtime_app.state.startup_complete = True
        try:
            yield
        finally:
            runtime_app.state.startup_complete = False

    app = FastAPI(
        title="AIMCTextureGen API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.services = services
    app.state.startup_complete = False
    app.state.max_import_bytes = (
        projects_api.MAX_IMPORT_BYTES
        if max_import_bytes is None
        else max_import_bytes
    )
    configured_body_limit = (
        projects_api.MAX_IMPORT_BODY_BYTES
        if max_import_body_bytes is None
        else max_import_body_bytes
    )
    if app.state.max_import_bytes <= 0 or configured_body_limit <= 0:
        raise ValueError("import size limits must be positive")
    app.add_middleware(
        ImportBodyLimitMiddleware,
        max_body_bytes=configured_body_limit,
    )

    @app.exception_handler(ApiProblem)
    async def api_problem_handler(
        _request: Request,
        problem: ApiProblem,
    ):
        return problem_response(problem)

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request,
        _error: RequestValidationError,
    ):
        is_job_request = "/jobs" in request.url.path
        return problem_response(
            ApiProblem(
                status_code=422,
                code="INVALID_REQUEST",
                stage="request_validation",
                user_message=(
                    "任务请求格式无效"
                    if is_job_request
                    else "请求格式无效；导入只接受项目名称和 ZIP 文件上传"
                ),
                recommended_actions=(
                    ("检查任务参数后重新提交",)
                    if is_job_request
                    else ("选择 ZIP 文件并重新提交",)
                ),
                technical_details=None,
            )
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request,
        error: StarletteHTTPException,
    ):
        if request.url.path == "/api/projects/import" and error.status_code == 400:
            return problem_response(
                ApiProblem(
                    status_code=400,
                    code="INVALID_MULTIPART",
                    stage="request_validation",
                    user_message="multipart 上传内容无效",
                    recommended_actions=("重新选择 ZIP 文件并提交",),
                    technical_details=None,
                )
            )
        return problem_response(
            ApiProblem(
                status_code=error.status_code,
                code="HTTP_ERROR",
                stage="request_processing",
                user_message="请求无法处理",
                recommended_actions=(),
                technical_details=None,
            )
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(
        _request: Request,
        _error: Exception,
    ):
        _LOGGER.exception("Unhandled API error", exc_info=_error)
        return problem_response(
            ApiProblem(
                status_code=500,
                code="INTERNAL_ERROR",
                stage="request_processing",
                user_message="处理请求时发生内部错误",
                recommended_actions=("重试操作；若问题持续，请查看应用日志",),
                technical_details=None,
            )
        )

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {"status": "ok", "schema_version": 1}

    app.include_router(projects_api.router)
    app.include_router(jobs_api.router)
    app.include_router(system_api.router)

    return app


def _resolve_project_root(project_root: Path) -> Path:
    try:
        status_result = os.lstat(project_root)
    except FileNotFoundError:
        return project_root.resolve()
    except OSError as error:
        raise ValueError("project root cannot be inspected safely") from error
    if not stat.S_ISDIR(status_result.st_mode) or is_reparse_point(
        project_root,
        status_result,
    ):
        raise ValueError("project root must be a plain directory, not a reparse point")
    return project_root.resolve()


def _ensure_runtime_project_root(project_root: Path) -> None:
    try:
        project_root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise RuntimeError("project root cannot be created safely") from error
    _resolve_project_root(project_root)


app = create_app()
