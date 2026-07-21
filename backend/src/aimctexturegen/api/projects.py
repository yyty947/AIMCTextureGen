import logging
import os
import stat
import tempfile
from pathlib import Path
from typing import Annotated, BinaryIO
from uuid import UUID

from fastapi import APIRouter, File, Form, Request, UploadFile, status
from pydantic import ValidationError

from aimctexturegen.catalog.registry import UnsupportedPackFormat
from aimctexturegen.core.errors import ApiProblem
from aimctexturegen.packs.coverage import (
    CoverageReport,
    CoverageValidationError,
    classify_coverage,
)
from aimctexturegen.packs.java_adapter import PackValidationError
from aimctexturegen.projects._directory_guard import is_reparse_point
from aimctexturegen.projects.models import ProjectManifest


MAX_IMPORT_BYTES = 512 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
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
    project_name: Annotated[str, Form()],
    pack: Annotated[UploadFile, File()],
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
            await _copy_bounded_upload(pack, temporary_file)
            temporary_file.flush()
            _require_directory_identity(
                services.project_root,
                project_root_identity,
            )
            _require_regular_file_identity(
                temporary_path,
                temporary_identity,
            )
            return services.workspace.import_pack(temporary_path, project_name)
    except ApiProblem:
        raise
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
    except Exception as error:
        _LOGGER.exception("Unexpected project import failure")
        raise _internal_problem("importing") from error
    finally:
        try:
            await pack.close()
        except Exception:
            _LOGGER.exception("Unable to close uploaded project file")
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


@router.get("/{project_id}", response_model=ProjectManifest)
def get_project(request: Request, project_id: str) -> ProjectManifest:
    try:
        parsed_id = _parse_project_id(project_id)
        manifest, _ = _load_project(request.app.state.services.project_root, parsed_id)
        return manifest
    except ApiProblem:
        raise
    except Exception as error:
        _LOGGER.exception("Unexpected project manifest load failure")
        raise _internal_problem("loading_project") from error


@router.get("/{project_id}/coverage", response_model=CoverageReport)
def get_project_coverage(request: Request, project_id: str) -> CoverageReport:
    try:
        parsed_id = _parse_project_id(project_id)
        services = request.app.state.services
        manifest, project_directory = _load_project(
            services.project_root,
            parsed_id,
        )
        try:
            profile = services.catalogs.for_pack_format(manifest.java_pack_format)
        except UnsupportedPackFormat as error:
            raise ApiProblem(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="UNSUPPORTED_PACK_FORMAT",
                stage="classifying_coverage",
                user_message="项目记录的资源格式当前不受支持",
                recommended_actions=("重新导入资源包或恢复对应目录配置",),
                technical_details=None,
            ) from error
        if profile.catalog_id != manifest.catalog_id:
            raise _corrupt_manifest_problem()
        try:
            return classify_coverage(project_directory / "pack", profile)
        except CoverageValidationError as error:
            raise ApiProblem(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code=error.code,
                stage="classifying_coverage",
                user_message=error.user_message,
                recommended_actions=("检查项目工作副本，或重新导入原始资源包",),
                technical_details=None,
            ) from error
    except ApiProblem:
        raise
    except Exception as error:
        _LOGGER.exception("Unexpected project coverage failure")
        raise _internal_problem("classifying_coverage") from error


async def _copy_bounded_upload(pack: UploadFile, destination: BinaryIO) -> None:
    total = 0
    while chunk := await pack.read(UPLOAD_CHUNK_BYTES):
        total += len(chunk)
        if total > MAX_IMPORT_BYTES:
            raise ApiProblem(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                code="IMPORT_TOO_LARGE",
                stage="uploading",
                user_message="上传的资源包超过允许大小",
                recommended_actions=("选择更小的 ZIP 资源包后重试",),
                technical_details=None,
            )
        destination.write(chunk)


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
    if str(project_id) != raw_project_id.lower():
        raise ApiProblem(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="INVALID_PROJECT_ID",
            stage="loading_project",
            user_message="项目标识无效",
            recommended_actions=("返回项目列表并重新选择项目",),
            technical_details=None,
        )
    return project_id


def _load_project(
    project_root: Path,
    project_id: UUID,
) -> tuple[ProjectManifest, Path]:
    if not project_root.exists():
        raise _project_not_found_problem()
    root_identity = _plain_directory_identity(
        project_root,
        code="UNSAFE_PROJECT_ROOT",
        message="项目存储目录不安全",
    )
    project_directory = project_root / str(project_id)
    try:
        project_identity = _plain_directory_identity(
            project_directory,
            code="UNSAFE_PROJECT_PATH",
            message="项目目录不安全",
        )
    except FileNotFoundError as error:
        raise _project_not_found_problem() from error

    manifest_path = project_directory / "project.json"
    try:
        manifest_status = os.lstat(manifest_path)
        if (
            not stat.S_ISREG(manifest_status.st_mode)
            or is_reparse_point(manifest_path, manifest_status)
            or manifest_status.st_size > MAX_MANIFEST_BYTES
        ):
            raise OSError("Project manifest is not a bounded regular file")
        with manifest_path.open("rb") as manifest_file:
            handle_status = os.fstat(manifest_file.fileno())
            if (
                not stat.S_ISREG(handle_status.st_mode)
                or _identity(handle_status) != _identity(manifest_status)
            ):
                raise OSError("Project manifest changed before reading")
            payload = manifest_file.read(MAX_MANIFEST_BYTES + 1)
            if len(payload) > MAX_MANIFEST_BYTES:
                raise OSError("Project manifest exceeds its size limit")
            _require_directory_identity(project_root, root_identity)
            _require_directory_identity(project_directory, project_identity)
            final_manifest_status = os.lstat(manifest_path)
            final_handle_status = os.fstat(manifest_file.fileno())
            if (
                _identity(final_manifest_status) != _identity(manifest_status)
                or _identity(final_handle_status) != _identity(manifest_status)
                or is_reparse_point(manifest_path, final_manifest_status)
            ):
                raise OSError("Project manifest changed while reading")
        manifest = ProjectManifest.model_validate_json(payload, strict=True)
    except (OSError, ValidationError, ValueError) as error:
        raise _corrupt_manifest_problem() from error
    if manifest.project_id != project_id:
        raise _corrupt_manifest_problem()
    return manifest, project_directory


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


def _project_not_found_problem() -> ApiProblem:
    return ApiProblem(
        status_code=status.HTTP_404_NOT_FOUND,
        code="PROJECT_NOT_FOUND",
        stage="loading_project",
        user_message="未找到该项目",
        recommended_actions=("返回项目列表并重新选择项目",),
        technical_details=None,
    )


def _corrupt_manifest_problem() -> ApiProblem:
    return ApiProblem(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="CORRUPT_PROJECT_MANIFEST",
        stage="loading_project",
        user_message="项目清单损坏或与项目目录不一致",
        recommended_actions=("从备份恢复项目，或重新导入原始资源包",),
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
