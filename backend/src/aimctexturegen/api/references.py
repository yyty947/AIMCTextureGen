from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict
from starlette.requests import ClientDisconnect

from aimctexturegen.core.errors import ApiProblem
from aimctexturegen.references.models import PackReference, ReferenceKind, StoredReference
from aimctexturegen.references.service import ReferenceService, ReferenceServiceError
from aimctexturegen.references.validation import MAX_REFERENCE_BYTES


router = APIRouter(prefix="/api/projects/{project_id}/references", tags=["references"])
_LOGGER = logging.getLogger(__name__)
_MAX_STREAM_BYTES = MAX_REFERENCE_BYTES


class _TransportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


@router.get("/pack", response_model=tuple[PackReference, ...])
def list_pack_references(request: Request, project_id: str) -> tuple[PackReference, ...]:
    try:
        return _service(request).list_pack_references(_parse_project_id(project_id))
    except ApiProblem:
        raise
    except ReferenceServiceError as error:
        raise _reference_problem(error, "listing_pack_references") from error
    except Exception as error:
        _LOGGER.exception("Unexpected pack reference listing failure")
        raise _internal_problem("listing_pack_references") from error


@router.get("/pack/image")
def read_pack_reference(
    request: Request,
    project_id: str,
    relative_path: str = Query(min_length=1),
) -> Response:
    try:
        payload = _service(request).read_pack_reference(
            _parse_project_id(project_id),
            relative_path,
        )
        return Response(content=payload, media_type="image/png")
    except ApiProblem:
        raise
    except ReferenceServiceError as error:
        raise _reference_problem(error, "reading_pack_reference") from error
    except Exception as error:
        _LOGGER.exception("Unexpected pack reference read failure")
        raise _internal_problem("reading_pack_reference") from error


@router.get("", response_model=tuple[StoredReference, ...])
def list_uploaded_references(
    request: Request,
    project_id: str,
    kind: Literal["style", "structure"],
) -> tuple[StoredReference, ...]:
    try:
        return _service(request).list_uploads(
            _parse_project_id(project_id),
            kind,
        )
    except ApiProblem:
        raise
    except ReferenceServiceError as error:
        raise _reference_problem(error, "listing_uploaded_references") from error
    except Exception as error:
        _LOGGER.exception("Unexpected reference listing failure")
        raise _internal_problem("listing_uploaded_references") from error


@router.post("", status_code=status.HTTP_201_CREATED, response_model=StoredReference)
async def upload_reference(
    request: Request,
    project_id: str,
    kind: Literal["style", "structure"],
) -> StoredReference:
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("image/png"):
        raise ApiProblem(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            code="INVALID_REFERENCE_UPLOAD",
            stage="uploading_reference",
            user_message="参考图上传只接受 image/png 原始字节",
            recommended_actions=("改用 PNG 图片重新上传",),
            technical_details=None,
        )
    try:
        payload = await _read_bounded_body(request)
        return _service(request).upload(
            _parse_project_id(project_id),
            kind,
            payload,
        )
    except ApiProblem:
        raise
    except ReferenceServiceError as error:
        raise _reference_problem(error, "uploading_reference") from error
    except Exception as error:
        _LOGGER.exception("Unexpected reference upload failure")
        raise _internal_problem("uploading_reference") from error


@router.get("/{kind}/{reference_id}/image")
def read_uploaded_reference(
    request: Request,
    project_id: str,
    kind: Literal["style", "structure"],
    reference_id: str,
) -> Response:
    try:
        payload = _service(request).read_upload(
            _parse_project_id(project_id),
            kind,
            _parse_reference_id(reference_id),
        )
        return Response(content=payload, media_type="image/png")
    except ApiProblem:
        raise
    except ReferenceServiceError as error:
        raise _reference_problem(error, "reading_uploaded_reference") from error
    except Exception as error:
        _LOGGER.exception("Unexpected uploaded reference read failure")
        raise _internal_problem("reading_uploaded_reference") from error


@router.delete("/{kind}/{reference_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_uploaded_reference(
    request: Request,
    project_id: str,
    kind: Literal["style", "structure"],
    reference_id: str,
) -> Response:
    try:
        _service(request).delete(
            _parse_project_id(project_id),
            kind,
            _parse_reference_id(reference_id),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except ApiProblem:
        raise
    except ReferenceServiceError as error:
        raise _reference_problem(error, "deleting_uploaded_reference") from error
    except Exception as error:
        _LOGGER.exception("Unexpected uploaded reference delete failure")
        raise _internal_problem("deleting_uploaded_reference") from error


def _service(request: Request) -> ReferenceService:
    service = getattr(request.app.state.services, "reference_service", None)
    if service is None:
        raise ApiProblem(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="REFERENCES_UNAVAILABLE",
            stage="references",
            user_message="参考图服务不可用",
            recommended_actions=("重启应用后重试",),
            technical_details=None,
        )
    return service


async def _read_bounded_body(request: Request) -> bytes:
    total = 0
    chunks: list[bytes] = []
    try:
        async for chunk in request.stream():
            if not chunk:
                continue
            total += len(chunk)
            if total > _MAX_STREAM_BYTES:
                raise ApiProblem(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    code="REFERENCE_TOO_LARGE",
                    stage="uploading_reference",
                    user_message="参考图超过 16 MiB 上限",
                    recommended_actions=("压缩或缩小图片后重新上传",),
                    technical_details=None,
                )
            chunks.append(chunk)
    except ClientDisconnect as error:
        raise ApiProblem(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="REFERENCE_UPLOAD_INCOMPLETE",
            stage="uploading_reference",
            user_message="参考图上传在完成前断开",
            recommended_actions=("重新上传参考图",),
            technical_details=None,
        ) from error
    return b"".join(chunks)


def _parse_project_id(raw_value: str) -> UUID:
    return _parse_uuid(
        raw_value,
        code="INVALID_PROJECT_ID",
        stage="references",
        user_message="项目标识无效",
    )


def _parse_reference_id(raw_value: str) -> UUID:
    return _parse_uuid(
        raw_value,
        code="INVALID_REFERENCE_ID",
        stage="references",
        user_message="参考图标识无效",
    )


def _parse_uuid(
    raw_value: str,
    *,
    code: str,
    stage: str,
    user_message: str,
) -> UUID:
    try:
        value = UUID(raw_value)
    except (ValueError, AttributeError) as error:
        raise ApiProblem(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=code,
            stage=stage,
            user_message=user_message,
            recommended_actions=("返回列表后重新选择",),
            technical_details=None,
        ) from error
    if str(value) != raw_value:
        raise ApiProblem(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=code,
            stage=stage,
            user_message=user_message,
            recommended_actions=("返回列表后重新选择",),
            technical_details=None,
        )
    return value


def _reference_problem(error: ReferenceServiceError, stage: str) -> ApiProblem:
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    actions = ("检查参考图后重试",)
    if error.code == "REFERENCE_NOT_FOUND":
        status_code = status.HTTP_404_NOT_FOUND
        actions = ("刷新参考图列表后重新选择",)
    return ApiProblem(
        status_code=status_code,
        code=error.code,
        stage=stage,
        user_message=error.user_message,
        recommended_actions=actions,
        technical_details=None,
    )


def _internal_problem(stage: str) -> ApiProblem:
    return ApiProblem(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_ERROR",
        stage=stage,
        user_message="处理参考图请求时发生内部错误",
        recommended_actions=("重试操作；若问题持续，请查看应用日志",),
        technical_details=None,
    )
