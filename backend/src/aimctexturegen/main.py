import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from aimctexturegen.api import projects as projects_api
from aimctexturegen.catalog.models import CatalogProfile
from aimctexturegen.catalog.registry import CatalogRegistry
from aimctexturegen.core.errors import ApiProblem, problem_response
from aimctexturegen.core.request_limits import ImportBodyLimitMiddleware
from aimctexturegen.packs.java_adapter import JavaPackAdapter
from aimctexturegen.projects._directory_guard import is_reparse_point
from aimctexturegen.projects.models import ProjectManifest
from aimctexturegen.projects.workspace import ProjectWorkspace


_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PROJECT_ROOT = _REPOSITORY_ROOT / "projects"
_DEFAULT_CATALOG_ROOT = _REPOSITORY_ROOT / "catalogs" / "java"
_LOGGER = logging.getLogger(__name__)


class WorkspaceService(Protocol):
    def import_pack(self, source: Path, project_name: str) -> ProjectManifest: ...


class CatalogService(Protocol):
    def for_pack_format(self, pack_format: int) -> CatalogProfile: ...


@dataclass(frozen=True)
class AppServices:
    workspace: WorkspaceService
    catalogs: CatalogService
    project_root: Path


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
    app = FastAPI(title="AIMCTextureGen API", version="0.1.0")
    app.state.services = services
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
        _request: Request,
        _error: RequestValidationError,
    ):
        return problem_response(
            ApiProblem(
                status_code=422,
                code="INVALID_REQUEST",
                stage="request_validation",
                user_message="请求格式无效；导入只接受项目名称和 ZIP 文件上传",
                recommended_actions=("选择 ZIP 文件并重新提交",),
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


app = create_app()
