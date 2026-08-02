"""Thin HTTP mapping for the managed inference setup surface."""

from __future__ import annotations

import logging
from typing import Protocol
from uuid import UUID

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, Field

from aimctexturegen.comfy.errors import (
    InstallBlockedError,
    InstallError,
    InstallNotFoundError,
    InstallValidationError,
    ManagerError,
    ProcessIdentityError,
)
from aimctexturegen.core.errors import ApiProblem

router = APIRouter(prefix="/api/system/inference", tags=["inference"])
_LOGGER = logging.getLogger(__name__)


class InferenceApplicationService(Protocol):
    def status(self) -> dict: ...

    def install_plan(self) -> dict: ...

    def begin_install(self, accepted_component_ids: list[str]): ...

    def operation(self, operation_id: UUID): ...

    def cancel_operation(self, operation_id: UUID): ...

    def start_comfyui(self) -> dict: ...

    def stop_comfyui(self) -> dict: ...

    def log_tail(self, max_bytes: int) -> str: ...


class _TransportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class BeginInstallRequest(_TransportModel):
    accepted_component_ids: list[str] = Field(min_length=1)


@router.get("")
def inference_status(request: Request) -> dict:
    try:
        return _service(request).status()
    except Exception as error:
        _LOGGER.exception("Unexpected inference status failure")
        raise _internal_problem("inference_status") from error


@router.get("/install-plan")
def install_plan(request: Request) -> dict:
    try:
        return _service(request).install_plan()
    except InstallError as error:
        raise _install_problem(error, "install_plan") from error
    except Exception as error:
        _LOGGER.exception("Unexpected install-plan failure")
        raise _internal_problem("install_plan") from error


@router.post("/installations", status_code=status.HTTP_202_ACCEPTED)
def begin_install(
    request: Request,
    payload: BeginInstallRequest,
) -> dict:
    try:
        operation = _service(request).begin_install(
            payload.accepted_component_ids
        )
        return _operation_payload(operation)
    except InstallError as error:
        raise _install_problem(error, "beginning_install") from error
    except Exception as error:
        _LOGGER.exception("Unexpected install creation failure")
        raise _internal_problem("beginning_install") from error


@router.get("/installations/{installation_id}")
def installation_detail(request: Request, installation_id: str) -> dict:
    try:
        operation = _service(request).operation(_parse_operation_id(installation_id))
        return _operation_payload(operation)
    except InstallError as error:
        raise _install_problem(error, "loading_installation") from error
    except Exception as error:
        _LOGGER.exception("Unexpected installation detail failure")
        raise _internal_problem("loading_installation") from error


@router.post("/installations/{installation_id}/cancel")
def cancel_installation(request: Request, installation_id: str) -> dict:
    try:
        operation = _service(request).cancel_operation(
            _parse_operation_id(installation_id)
        )
        return _operation_payload(operation)
    except InstallError as error:
        raise _install_problem(error, "canceling_installation") from error
    except Exception as error:
        _LOGGER.exception("Unexpected installation cancellation failure")
        raise _internal_problem("canceling_installation") from error


@router.post("/comfyui/start")
def start_comfyui(request: Request) -> dict:
    try:
        return _service(request).start_comfyui()
    except ManagerError as error:
        raise _manager_problem(error, "starting_comfyui") from error
    except Exception as error:
        _LOGGER.exception("Unexpected ComfyUI start failure")
        raise _internal_problem("starting_comfyui") from error


@router.post("/comfyui/stop")
def stop_comfyui(request: Request) -> dict:
    try:
        return _service(request).stop_comfyui()
    except ManagerError as error:
        raise _manager_problem(error, "stopping_comfyui") from error
    except Exception as error:
        _LOGGER.exception("Unexpected ComfyUI stop failure")
        raise _internal_problem("stopping_comfyui") from error


@router.get("/comfyui/log")
def comfyui_log(request: Request, max_bytes: int = 4096) -> dict:
    if max_bytes < 1:
        raise ApiProblem(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="INVALID_LOG_REQUEST",
            stage="reading_comfyui_log",
            user_message="日志字节数无效",
            recommended_actions=("使用 1 到 65536 之间的数值",),
            technical_details=None,
        )
    try:
        return {
            "content": _service(request).log_tail(max_bytes=max_bytes)
        }
    except Exception as error:
        _LOGGER.exception("Unexpected log tail failure")
        raise _internal_problem("reading_comfyui_log") from error


def _service(request: Request) -> InferenceApplicationService:
    service = getattr(request.app.state.services, "inference", None)
    if service is None:
        raise ApiProblem(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="INFERENCE_UNAVAILABLE",
            stage="inference",
            user_message="推理环境服务不可用",
            recommended_actions=("重启应用后重试",),
            technical_details=None,
        )
    return service


def _operation_payload(operation) -> dict:
    return operation.model_dump(mode="json")


def _parse_operation_id(raw_value: str) -> UUID:
    try:
        value = UUID(raw_value)
    except (ValueError, AttributeError) as error:
        raise ApiProblem(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="INVALID_INSTALLATION_ID",
            stage="loading_installation",
            user_message="安装操作标识无效",
            recommended_actions=("刷新安装状态后重试",),
            technical_details=None,
        ) from error
    if str(value) != raw_value:
        raise ApiProblem(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="INVALID_INSTALLATION_ID",
            stage="loading_installation",
            user_message="安装操作标识无效",
            recommended_actions=("刷新安装状态后重试",),
            technical_details=None,
        )
    return value


def _install_problem(error: InstallError, stage: str) -> ApiProblem:
    if isinstance(error, InstallValidationError):
        code = "INSTALL_CONFIRMATION_STALE"
        status_code = status.HTTP_409_CONFLICT
        actions = ("重新生成安装计划并再次确认",)
    elif isinstance(error, InstallBlockedError):
        code = "INSTALL_BLOCKED"
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
        actions = ("修复主机或磁盘问题后重试",)
    elif isinstance(error, InstallNotFoundError):
        code = "INSTALL_NOT_FOUND"
        status_code = status.HTTP_404_NOT_FOUND
        actions = ("刷新安装状态后重试",)
    else:
        code = "INSTALL_STATE_INVALID"
        status_code = status.HTTP_409_CONFLICT
        actions = ("取消当前安装后重试",)
    return ApiProblem(
        status_code=status_code,
        code=code,
        stage=stage,
        user_message=str(error),
        recommended_actions=actions,
        technical_details=None,
    )


def _manager_problem(error: ManagerError, stage: str) -> ApiProblem:
    if isinstance(error, ProcessIdentityError):
        code = "PROCESS_IDENTITY_MISMATCH"
        actions = ("停止并重新启动受管运行时",)
    else:
        code = "COMFYUI_START_FAILED"
        actions = ("检查端口与运行环境后重试",)
    return ApiProblem(
        status_code=status.HTTP_409_CONFLICT,
        code=code,
        stage=stage,
        user_message=str(error),
        recommended_actions=actions,
        technical_details=None,
    )


def _internal_problem(stage: str) -> ApiProblem:
    return ApiProblem(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_ERROR",
        stage=stage,
        user_message="处理推理环境请求时发生内部错误",
        recommended_actions=("重试操作；若问题持续，请查看应用日志",),
        technical_details=None,
    )
