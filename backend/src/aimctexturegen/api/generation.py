from __future__ import annotations

import asyncio
import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Body, Request, Response, WebSocket, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.websockets import WebSocketDisconnect

from aimctexturegen.api import jobs as legacy_jobs_api
from aimctexturegen.core.errors import ApiProblem
from aimctexturegen.generation.coordinator import CurrentGenerationJob, GenerationCoordinator
from aimctexturegen.generation.errors import GenerationError
from aimctexturegen.generation.events import JobEventBroker
from aimctexturegen.generation.service import CreateGenerationCommand, GenerationService
from aimctexturegen.jobs.errors import JobError
from aimctexturegen.jobs.models_v3 import ArtifactKind, GenerationJobRequest, GenerationJobState
from aimctexturegen.jobs.store import LoadedJob
from aimctexturegen.references.models import (
    PackReferenceSelection,
    ReferenceSelections,
    UploadReferenceSelection,
)


router = APIRouter(tags=["generation"])
_LOGGER = logging.getLogger(__name__)
_HEARTBEAT_SECONDS = 0.1


class _TransportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class TargetSelection(_TransportModel):
    semantic_id: str = Field(min_length=1)


class ReferenceSelectionModel(_TransportModel):
    source: Literal["pack", "upload"]
    relative_path: str | None = None
    reference_id: str | None = None


class ReferenceSelectionsModel(_TransportModel):
    style: list[ReferenceSelectionModel] = Field(default_factory=list, max_length=8)
    structure: ReferenceSelectionModel | None = None

    def to_domain(self) -> ReferenceSelections:
        style = tuple(_to_reference_selection(item) for item in self.style)
        structure = (
            None if self.structure is None else _to_upload_selection(self.structure)
        )
        return ReferenceSelections(style=style, structure=structure)


class CreateGenerationRequest(_TransportModel):
    target: TargetSelection
    description: str = Field(max_length=4000)
    negative_prompt: str = Field(default="", max_length=4000)
    resolution: Literal[16, 32, 64]
    parallelism: Literal[1, 2, 4]
    references: ReferenceSelectionsModel
    denoise: float | None = None
    style_weight: float | None = None

    def to_command(self) -> CreateGenerationCommand:
        return CreateGenerationCommand(
            target_semantic_id=self.target.semantic_id,
            user_description=self.description,
            user_negative_prompt=self.negative_prompt,
            resolution=self.resolution,
            parallelism=self.parallelism,
            references=self.references.to_domain(),
            denoise=self.denoise,
            style_weight=self.style_weight,
        )


class JobDetail(_TransportModel):
    request: GenerationJobRequest
    state: GenerationJobState


class CurrentJobModel(_TransportModel):
    project_id: UUID
    job_id: UUID
    status: str = Field(min_length=1)


@router.get("/api/projects/{project_id}/generation-options")
def generation_options(request: Request, project_id: str) -> dict[str, object]:
    try:
        return _generation_service(request).generation_options(_parse_project_id(project_id))
    except ApiProblem:
        raise
    except GenerationError as error:
        raise _generation_problem(error, "generation_options") from error
    except Exception as error:
        _LOGGER.exception("Unexpected generation-options failure")
        raise _internal_problem("generation_options") from error


@router.get("/api/generation/current", response_model=CurrentJobModel | None)
def current_generation(request: Request) -> CurrentJobModel | None:
    current = _generation_coordinator(request).current_job()
    if current is None:
        return None
    return CurrentJobModel(
        project_id=current.project_id,
        job_id=current.job_id,
        status=current.status,
    )


@router.post(
    "/api/projects/{project_id}/jobs",
    status_code=status.HTTP_201_CREATED,
    response_model=JobDetail | legacy_jobs_api.JobDetail,
)
def create_job(
    request: Request,
    project_id: str,
    payload: dict[str, object],
) -> JobDetail | legacy_jobs_api.JobDetail:
    try:
        generation_payload = CreateGenerationRequest.model_validate(payload)
    except ValidationError:
        try:
            legacy_payload = legacy_jobs_api.CreateJobRequest.model_validate(payload)
        except ValidationError as error:
            raise _invalid_request_problem() from error
        return legacy_jobs_api.create_job(request, project_id, legacy_payload)
    try:
        loaded = _generation_coordinator(request).create_job(
            _parse_project_id(project_id),
            generation_payload.to_command(),
        )
        return _detail(loaded)
    except ApiProblem:
        raise
    except GenerationError as error:
        raise _generation_problem(error, "creating_generation_job") from error
    except Exception as error:
        _LOGGER.exception("Unexpected generation create failure")
        raise _internal_problem("creating_generation_job") from error


@router.post("/api/projects/{project_id}/jobs/{job_id}/start", response_model=JobDetail)
def start_job(request: Request, project_id: str, job_id: str) -> JobDetail:
    try:
        loaded = _generation_coordinator(request).start(
            _parse_project_id(project_id),
            _parse_job_id(job_id),
        )
        return _detail(loaded)
    except ApiProblem:
        raise
    except GenerationError as error:
        raise _generation_problem(error, "starting_generation_job") from error
    except Exception as error:
        _LOGGER.exception("Unexpected generation start failure")
        raise _internal_problem("starting_generation_job") from error


@router.post(
    "/api/projects/{project_id}/jobs/{job_id}/cancel",
    response_model=JobDetail | legacy_jobs_api.JobDetail,
)
def cancel_job(
    request: Request,
    project_id: str,
    job_id: str,
    payload: dict[str, object] | None = Body(default=None),
) -> JobDetail | legacy_jobs_api.JobDetail:
    try:
        parsed_project_id = _parse_project_id(project_id)
        parsed_job_id = _parse_job_id(job_id)
        existing = _load_job_for_dispatch(
            request,
            parsed_project_id,
            parsed_job_id,
        )
        if not isinstance(existing.request, GenerationJobRequest):
            try:
                legacy_payload = legacy_jobs_api.CancelJobRequest.model_validate(
                    {} if payload is None else payload
                )
            except ValidationError as error:
                raise _invalid_request_problem() from error
            return legacy_jobs_api.cancel_job(
                request,
                project_id,
                job_id,
                legacy_payload,
            )
        loaded = _generation_coordinator(request).cancel(
            parsed_project_id,
            parsed_job_id,
        )
        return _detail(loaded)
    except ApiProblem:
        raise
    except GenerationError as error:
        raise _generation_problem(error, "canceling_generation_job") from error
    except JobError as error:
        raise legacy_jobs_api._job_domain_problem(error, "canceling_job") from error
    except Exception as error:
        _LOGGER.exception("Unexpected generation cancel failure")
        raise _internal_problem("canceling_generation_job") from error


@router.post(
    "/api/projects/{project_id}/jobs/{job_id}/retry",
    status_code=status.HTTP_201_CREATED,
    response_model=JobDetail | legacy_jobs_api.JobDetail,
)
def retry_job(
    request: Request,
    project_id: str,
    job_id: str,
) -> JobDetail | legacy_jobs_api.JobDetail:
    try:
        parsed_project_id = _parse_project_id(project_id)
        parsed_job_id = _parse_job_id(job_id)
        existing = _load_job_for_dispatch(
            request,
            parsed_project_id,
            parsed_job_id,
        )
        if not isinstance(existing.request, GenerationJobRequest):
            return legacy_jobs_api.retry_job(request, project_id, job_id)
        loaded = _generation_coordinator(request).retry(
            parsed_project_id,
            parsed_job_id,
        )
        return _detail(loaded)
    except ApiProblem:
        raise
    except GenerationError as error:
        raise _generation_problem(error, "retrying_generation_job") from error
    except JobError as error:
        raise legacy_jobs_api._job_domain_problem(error, "retrying_job") from error
    except Exception as error:
        _LOGGER.exception("Unexpected generation retry failure")
        raise _internal_problem("retrying_generation_job") from error


@router.get("/api/projects/{project_id}/jobs/{job_id}/candidates/{candidate_index}/artifacts/{artifact_kind}")
def get_artifact(
    request: Request,
    project_id: str,
    job_id: str,
    candidate_index: int,
    artifact_kind: ArtifactKind,
) -> Response:
    try:
        artifact = _generation_service(request).read_artifact(
            _parse_project_id(project_id),
            _parse_job_id(job_id),
            candidate_index,
            artifact_kind,
        )
        return Response(
            content=artifact.payload,
            media_type=artifact.media_type,
            headers={"ETag": artifact.etag},
        )
    except ApiProblem:
        raise
    except GenerationError as error:
        raise _generation_problem(error, "reading_generation_artifact") from error
    except Exception as error:
        _LOGGER.exception("Unexpected generation artifact failure")
        raise _internal_problem("reading_generation_artifact") from error


@router.websocket("/api/projects/{project_id}/jobs/{job_id}/events")
async def job_events(websocket: WebSocket, project_id: str, job_id: str) -> None:
    await websocket.accept()
    try:
        parsed_project_id = _parse_project_id(project_id)
        parsed_job_id = _parse_job_id(job_id)
        service = _generation_service_from_socket(websocket)
        broker = _job_events(websocket)
        loaded = service.get_job(parsed_project_id, parsed_job_id)
        last_revision = loaded.state.revision
        await websocket.send_json(_snapshot_payload(loaded))
        while True:
            changed_revision = await asyncio.to_thread(
                broker.wait_for_change,
                parsed_project_id,
                parsed_job_id,
                last_revision,
                _HEARTBEAT_SECONDS,
            )
            if changed_revision is None:
                await websocket.send_json({"type": "heartbeat"})
                continue
            loaded = service.get_job(parsed_project_id, parsed_job_id)
            if loaded.state.revision > last_revision:
                last_revision = loaded.state.revision
                await websocket.send_json(_snapshot_payload(loaded))
    except (WebSocketDisconnect, asyncio.CancelledError):
        return
    except ApiProblem as error:
        await websocket.close(code=4404 if error.status_code == 404 else 4400)
    except GenerationError:
        await websocket.close(code=4400)
    except Exception:
        _LOGGER.exception("Unexpected generation websocket failure")
        await websocket.close(code=1011)


def _snapshot_payload(loaded: LoadedJob) -> dict[str, object]:
    return {
        "type": "snapshot",
        "revision": loaded.state.revision,
        "job": {
            "request": loaded.request.model_dump(mode="json"),
            "state": loaded.state.model_dump(mode="json"),
        },
    }


def _generation_service(request: Request) -> GenerationService:
    service = getattr(request.app.state.services, "generation_service", None)
    if service is None:
        raise ApiProblem(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="GENERATION_UNAVAILABLE",
            stage="generation",
            user_message="生成服务不可用",
            recommended_actions=("重启应用后重试",),
            technical_details=None,
        )
    return service


def _generation_service_from_socket(websocket: WebSocket) -> GenerationService:
    service = getattr(websocket.app.state.services, "generation_service", None)
    if service is None:
        raise ApiProblem(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="GENERATION_UNAVAILABLE",
            stage="generation",
            user_message="生成服务不可用",
            recommended_actions=("重启应用后重试",),
            technical_details=None,
        )
    return service


def _generation_coordinator(request: Request) -> GenerationCoordinator:
    coordinator = getattr(request.app.state.services, "generation_coordinator", None)
    if coordinator is None:
        raise ApiProblem(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="GENERATION_UNAVAILABLE",
            stage="generation",
            user_message="生成协调器不可用",
            recommended_actions=("重启应用后重试",),
            technical_details=None,
        )
    return coordinator


def _job_events(websocket: WebSocket) -> JobEventBroker:
    broker = getattr(websocket.app.state.services, "job_events", None)
    if broker is None:
        raise ApiProblem(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="GENERATION_UNAVAILABLE",
            stage="generation",
            user_message="任务事件代理不可用",
            recommended_actions=("重启应用后重试",),
            technical_details=None,
        )
    return broker


def _load_job_for_dispatch(
    request: Request,
    project_id: UUID,
    job_id: UUID,
) -> LoadedJob:
    service = legacy_jobs_api._job_service(request.app.state.services)
    return service.get_job(project_id, job_id)


def _detail(loaded: LoadedJob) -> JobDetail:
    return JobDetail(request=loaded.request, state=loaded.state)


def _parse_project_id(raw_value: str) -> UUID:
    return _parse_uuid(
        raw_value,
        code="INVALID_PROJECT_ID",
        stage="generation",
        user_message="项目标识无效",
    )


def _parse_job_id(raw_value: str) -> UUID:
    return _parse_uuid(
        raw_value,
        code="INVALID_JOB_ID",
        stage="generation",
        user_message="任务标识无效",
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


def _to_reference_selection(item: ReferenceSelectionModel):
    if item.source == "pack":
        if item.relative_path is None or item.reference_id is not None:
            raise ApiProblem(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="INVALID_REQUEST",
                stage="request_validation",
                user_message="任务请求格式无效",
                recommended_actions=("检查任务参数后重新提交",),
                technical_details=None,
            )
        return PackReferenceSelection(source="pack", relative_path=item.relative_path)
    return _to_upload_selection(item)


def _to_upload_selection(item: ReferenceSelectionModel):
    if item.reference_id is None or item.relative_path is not None:
        raise ApiProblem(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="INVALID_REQUEST",
            stage="request_validation",
            user_message="任务请求格式无效",
            recommended_actions=("检查任务参数后重新提交",),
            technical_details=None,
        )
    try:
        reference_id = UUID(item.reference_id)
    except (ValueError, AttributeError) as error:
        raise ApiProblem(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="INVALID_REQUEST",
            stage="request_validation",
            user_message="任务请求格式无效",
            recommended_actions=("检查任务参数后重新提交",),
            technical_details=None,
        ) from error
    if str(reference_id) != item.reference_id:
        raise ApiProblem(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="INVALID_REQUEST",
            stage="request_validation",
            user_message="任务请求格式无效",
            recommended_actions=("检查任务参数后重新提交",),
            technical_details=None,
        )
    return UploadReferenceSelection(source="upload", reference_id=reference_id)


def _generation_problem(error: GenerationError, stage: str) -> ApiProblem:
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    if error.code in {"GENERATION_JOB_CONFLICT", "ARTIFACT_INTEGRITY_ERROR"}:
        status_code = status.HTTP_409_CONFLICT
    elif error.code == "ARTIFACT_NOT_AVAILABLE":
        status_code = status.HTTP_404_NOT_FOUND
    return ApiProblem(
        status_code=status_code,
        code=error.code,
        stage=stage,
        user_message=error.user_message,
        recommended_actions=error.recommended_actions,
        technical_details=None,
    )


def _invalid_request_problem() -> ApiProblem:
    return ApiProblem(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="INVALID_REQUEST",
        stage="request_validation",
        user_message="任务请求格式无效",
        recommended_actions=("检查任务参数后重新提交",),
        technical_details=None,
    )


def _internal_problem(stage: str) -> ApiProblem:
    return ApiProblem(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_ERROR",
        stage=stage,
        user_message="处理生成请求时发生内部错误",
        recommended_actions=("重试操作；若问题持续，请查看应用日志",),
        technical_details=None,
    )
