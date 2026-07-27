import logging
import os
import stat
import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Request, status

from aimctexturegen.catalog.registry import UnsupportedPackFormat
from aimctexturegen.api.multipart_import import (
    MultipartImportError,
    parse_import_multipart,
)
from aimctexturegen.core.errors import ApiProblem
from aimctexturegen.packs.coverage import CoverageReport
from aimctexturegen.packs.java_adapter import PackValidationError
from aimctexturegen.projects._directory_guard import (
    is_reparse_point,
)
from aimctexturegen.projects.models import (
    ProjectManifest,
    ProjectSummary,
)
from aimctexturegen.projects.repository import (
    ProjectRepository,
    ProjectRepositoryError,
)
from aimctexturegen.projects.service import ProjectService, ProjectServiceError


MAX_IMPORT_BYTES = 512 * 1024 * 1024
MAX_MULTIPART_OVERHEAD_BYTES = 1024 * 1024
MAX_IMPORT_BODY_BYTES = MAX_IMPORT_BYTES + MAX_MULTIPART_OVERHEAD_BYTES
_UPLOAD_PREFIX = ".import-upload-"
_UPLOAD_SUFFIX = ".zip"
_LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])

FileIdentity = tuple[int, int]


@router.post(
    "/import",
    status_code=status.HTTP_201_CREATED,
    response_model=ProjectManifest,
)
async def import_project(
    request: Request,
) -> ProjectManifest:
    services = request.app.state.services
    temporary_path: Path | None = None
    temporary_identity: FileIdentity | None = None
    project_root_identity: FileIdentity | None = None

    try:
        project_root_identity = _ensure_project_root(services.project_root)
        descriptor, temporary_path, temporary_identity = _create_upload_file(
            services.project_root,
            project_root_identity,
        )
        with os.fdopen(descriptor, "w+b") as temporary_file:
            parsed = await parse_import_multipart(
                request,
                temporary_file,
                request.app.state.max_import_bytes,
            )
            temporary_file.flush()
            _require_directory_identity(
                services.project_root,
                project_root_identity,
            )
            _require_regular_file_identity(
                temporary_path,
                temporary_identity,
            )
            return _project_service(services).import_pack(
                temporary_path,
                parsed.project_name,
            )
    except ApiProblem:
        raise
    except MultipartImportError as error:
        raise ApiProblem(
            status_code=error.status_code,
            code=error.code,
            stage=error.stage,
            user_message=error.user_message,
            recommended_actions=("检查项目名称和 ZIP 文件后重新提交",),
            technical_details=None,
        ) from error
    except PackValidationError as error:
        raise _pack_problem(error) from error
    except UnsupportedPackFormat as error:
        supported = ", ".join(str(value) for value in error.supported) or "none"
        raise ApiProblem(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="UNSUPPORTED_PACK_FORMAT",
            stage="importing",
            user_message="资源包的主资源格式当前不受支持",
            recommended_actions=("选择受支持资源格式的 Java 资源包",),
            technical_details=(
                f"pack_format={error.pack_format}; supported={supported}"
            ),
        ) from error
    except (ProjectRepositoryError, ProjectServiceError) as error:
        raise _project_domain_problem(error, "importing") from error
    except Exception as error:
        _LOGGER.exception("Unexpected project import failure")
        raise _internal_problem("importing") from error
    finally:
        if (
            temporary_path is not None
            and temporary_identity is not None
            and project_root_identity is not None
        ):
            _remove_exact_upload(
                temporary_path,
                temporary_identity,
                services.project_root,
                project_root_identity,
            )


@router.get("", response_model=tuple[ProjectSummary, ...])
def list_projects(request: Request) -> tuple[ProjectSummary, ...]:
    try:
        return _project_service(request.app.state.services).list_projects()
    except (ProjectRepositoryError, ProjectServiceError) as error:
        raise _project_domain_problem(error, "listing_projects") from error
    except Exception as error:
        _LOGGER.exception("Unexpected project list failure")
        raise _internal_problem("listing_projects") from error


@router.get("/{project_id}", response_model=ProjectManifest)
def get_project(request: Request, project_id: str) -> ProjectManifest:
    try:
        parsed_id = _parse_project_id(project_id)
        return _project_service(request.app.state.services).get_project(parsed_id)
    except ApiProblem:
        raise
    except (ProjectRepositoryError, ProjectServiceError) as error:
        raise _project_domain_problem(error, "loading_project") from error
    except Exception as error:
        _LOGGER.exception("Unexpected project manifest load failure")
        raise _internal_problem("loading_project") from error


@router.get("/{project_id}/coverage", response_model=CoverageReport)
def get_project_coverage(request: Request, project_id: str) -> CoverageReport:
    try:
        parsed_id = _parse_project_id(project_id)
        return _project_service(request.app.state.services).get_coverage(parsed_id)
    except ApiProblem:
        raise
    except (ProjectRepositoryError, ProjectServiceError) as error:
        raise _project_domain_problem(error, "classifying_coverage") from error
    except Exception as error:
        _LOGGER.exception("Unexpected project coverage failure")
        raise _internal_problem("classifying_coverage") from error


def _project_service(services) -> ProjectService:
    repository = ProjectRepository(services.project_root)
    return ProjectService(
        workspace=services.workspace,
        repository=repository,
        catalogs=services.catalogs,
    )


def _ensure_project_root(project_root: Path) -> FileIdentity:
    try:
        project_root.mkdir(parents=True, exist_ok=True)
        status_result = os.lstat(project_root)
    except OSError as error:
        raise ApiProblem(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="PROJECT_STORAGE_UNAVAILABLE",
            stage="uploading",
            user_message="无法使用项目存储目录",
            recommended_actions=("检查项目目录权限后重试",),
            technical_details=None,
        ) from error
    if not stat.S_ISDIR(status_result.st_mode) or is_reparse_point(
        project_root,
        status_result,
    ):
        raise ApiProblem(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="UNSAFE_PROJECT_ROOT",
            stage="uploading",
            user_message="项目存储目录不安全",
            recommended_actions=("选择不包含链接或重解析点的项目目录",),
            technical_details=None,
        )
    return _identity(status_result)


def _create_upload_file(
    project_root: Path,
    root_identity: FileIdentity,
) -> tuple[int, Path, FileIdentity]:
    _require_directory_identity(project_root, root_identity)
    try:
        descriptor, raw_path = tempfile.mkstemp(
            prefix=_UPLOAD_PREFIX,
            suffix=_UPLOAD_SUFFIX,
            dir=project_root,
        )
    except OSError as error:
        raise ApiProblem(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="PROJECT_STORAGE_UNAVAILABLE",
            stage="uploading",
            user_message="无法暂存上传的资源包",
            recommended_actions=("检查项目目录权限和可用空间后重试",),
            technical_details=None,
        ) from error
    temporary_path = Path(raw_path)
    try:
        status_result = os.fstat(descriptor)
        if not stat.S_ISREG(status_result.st_mode):
            raise OSError("Temporary upload is not a regular file")
        _require_directory_identity(project_root, root_identity)
        return descriptor, temporary_path, _identity(status_result)
    except BaseException:
        os.close(descriptor)
        _remove_unbound_upload(temporary_path, project_root)
        raise


def _parse_project_id(raw_project_id: str) -> UUID:
    try:
        project_id = UUID(raw_project_id)
    except (ValueError, AttributeError) as error:
        raise ApiProblem(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="INVALID_PROJECT_ID",
            stage="loading_project",
            user_message="项目标识无效",
            recommended_actions=("返回项目列表并重新选择项目",),
            technical_details=None,
        ) from error
    if str(project_id) != raw_project_id:
        raise ApiProblem(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="INVALID_PROJECT_ID",
            stage="loading_project",
            user_message="项目标识无效",
            recommended_actions=("返回项目列表并重新选择项目",),
            technical_details=None,
        )
    return project_id


def _plain_directory_identity(
    path: Path,
    *,
    code: str,
    message: str,
) -> FileIdentity:
    try:
        status_result = os.lstat(path)
    except FileNotFoundError:
        raise
    except OSError as error:
        raise ApiProblem(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code=code,
            stage="loading_project",
            user_message=message,
            recommended_actions=("检查项目存储目录后重试",),
            technical_details=None,
        ) from error
    if not stat.S_ISDIR(status_result.st_mode) or is_reparse_point(path, status_result):
        raise ApiProblem(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code=code,
            stage="loading_project",
            user_message=message,
            recommended_actions=("检查项目存储目录后重试",),
            technical_details=None,
        )
    return _identity(status_result)


def _require_directory_identity(
    path: Path,
    expected_identity: FileIdentity,
) -> None:
    try:
        actual_identity = _plain_directory_identity(
            path,
            code="UNSAFE_PROJECT_ROOT",
            message="项目存储目录不安全",
        )
    except FileNotFoundError as error:
        raise ApiProblem(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="UNSAFE_PROJECT_ROOT",
            stage="uploading",
            user_message="项目存储目录在上传期间发生变化",
            recommended_actions=("检查项目目录后重试",),
            technical_details=None,
        ) from error
    if actual_identity != expected_identity:
        raise ApiProblem(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="UNSAFE_PROJECT_ROOT",
            stage="uploading",
            user_message="项目存储目录在上传期间发生变化",
            recommended_actions=("检查项目目录后重试",),
            technical_details=None,
        )


def _require_regular_file_identity(
    path: Path,
    expected_identity: FileIdentity,
) -> None:
    try:
        status_result = os.lstat(path)
    except OSError as error:
        raise ApiProblem(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="UNSAFE_UPLOAD_PATH",
            stage="uploading",
            user_message="上传暂存文件在处理期间发生变化",
            recommended_actions=("重试导入；若问题持续，请检查项目目录安全性",),
            technical_details=None,
        ) from error
    if (
        not stat.S_ISREG(status_result.st_mode)
        or is_reparse_point(path, status_result)
        or _identity(status_result) != expected_identity
    ):
        raise ApiProblem(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="UNSAFE_UPLOAD_PATH",
            stage="uploading",
            user_message="上传暂存文件在处理期间发生变化",
            recommended_actions=("重试导入；若问题持续，请检查项目目录安全性",),
            technical_details=None,
        )


def _remove_exact_upload(
    path: Path,
    expected_identity: FileIdentity,
    project_root: Path,
    root_identity: FileIdentity,
) -> None:
    if path.parent != project_root or not path.name.startswith(_UPLOAD_PREFIX):
        return
    try:
        _require_directory_identity(project_root, root_identity)
        status_result = os.lstat(path)
        if (
            not stat.S_ISREG(status_result.st_mode)
            or is_reparse_point(path, status_result)
            or _identity(status_result) != expected_identity
        ):
            return
        path.unlink()
    except FileNotFoundError:
        return
    except Exception:
        _LOGGER.exception("Unable to remove exact temporary upload")


def _remove_unbound_upload(path: Path, project_root: Path) -> None:
    if path.parent != project_root or not path.name.startswith(_UPLOAD_PREFIX):
        return
    try:
        status_result = os.lstat(path)
        if stat.S_ISREG(status_result.st_mode) and not is_reparse_point(
            path,
            status_result,
        ):
            path.unlink()
    except OSError:
        _LOGGER.exception("Unable to remove failed temporary upload")


def _identity(status_result: os.stat_result) -> FileIdentity:
    return status_result.st_dev, status_result.st_ino


def _project_domain_problem(
    error: ProjectRepositoryError | ProjectServiceError,
    stage: str,
) -> ApiProblem:
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    actions = ("检查项目存储目录后重试",)
    if error.code == "PROJECT_NOT_FOUND":
        status_code = status.HTTP_404_NOT_FOUND
        actions = ("返回项目列表并重新选择项目",)
    elif error.code in {
        "UNSUPPORTED_PACK_FORMAT",
        "UNSAFE_PACK_ROOT",
        "INVALID_TEXTURE_PNG",
    }:
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
        actions = ("检查项目工作副本，或重新导入原始资源包",)
    elif error.code == "CORRUPT_PROJECT_MANIFEST":
        actions = ("从备份恢复项目，或重新导入原始资源包",)
    elif error.code == "INDEX_UNAVAILABLE" and stage == "importing":
        actions = ("项目已成功保存；请从项目列表重新打开，或重启应用重建索引",)
    return ApiProblem(
        status_code=status_code,
        code=error.code,
        stage=stage,
        user_message=error.user_message,
        recommended_actions=actions,
        technical_details=None,
    )


def _pack_problem(error: PackValidationError) -> ApiProblem:
    actions = ("检查资源包结构后重新打包并重试",)
    if error.code == "UNSAFE_PACK_PATH":
        actions = ("移除不安全路径后重新打包",)
    return ApiProblem(
        status_code=status.HTTP_400_BAD_REQUEST,
        code=error.code,
        stage="importing",
        user_message=error.user_message,
        recommended_actions=actions,
        technical_details=None,
    )


def _internal_problem(stage: str) -> ApiProblem:
    return ApiProblem(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_ERROR",
        stage=stage,
        user_message="处理请求时发生内部错误",
        recommended_actions=("重试操作；若问题持续，请查看应用日志",),
        technical_details=None,
    )
