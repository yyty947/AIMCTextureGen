"""Thin HTTP transport mapping for durable project jobs."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aimctexturegen.core.errors import ApiProblem
from aimctexturegen.comfy.errors import ProfileBindingError
from aimctexturegen.jobs.errors import JobError
from aimctexturegen.jobs.models import (
    CreateJobCommand,
    JobRequest,
    JobStateRecord,
    JobSummary,
)
from aimctexturegen.jobs.models_v3 import GenerationJobRequest, GenerationJobState
from aimctexturegen.jobs.service import JobService
from aimctexturegen.jobs.store import JobStore, LoadedJob
from aimctexturegen.projects.repository import (
    ProjectRepository,
    ProjectRepositoryError,
)
from aimctexturegen.model_profiles.workflows import (
    build_model_profile_binding,
)


router = APIRouter(prefix="/api/projects/{project_id}/jobs", tags=["jobs"])
_LOGGER = logging.getLogger(__name__)


class _TransportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CreateJobRequest(_TransportModel):
    """JSON-friendly request whose arrays are mapped to domain tuples."""

    profile_id: str = Field(min_length=1)
    target_semantic_id: str
    prompt: str
    resolution: int
    parallelism: int
    style_references: list[str] = Field(min_length=1, max_length=8)
    structure_reference: str | None

    def to_command(self) -> CreateJobCommand:
        return CreateJobCommand(
            target_semantic_id=self.target_semantic_id,
            prompt=self.prompt,
            resolution=self.resolution,
            parallelism=self.parallelism,
            style_references=tuple(self.style_references),
            structure_reference=self.structure_reference,
        )


class JobDetail(_TransportModel):
    """Canonical persisted request and its current mutable state."""

    request: JobRequest | GenerationJobRequest
    state: JobStateRecord | GenerationJobState


class CancelJobRequest(_TransportModel):
    """Revision precondition for a conditional cancellation."""

    expected_revision: int = Field(ge=0)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=JobDetail)
def create_job(
    request: Request,
    project_id: str,
    payload: CreateJobRequest,
) -> JobDetail:
    try:
        parsed_project_id = _parse_id(
            project_id,
            code="INVALID_PROJECT_ID",
            user_message="项目标识无效",
        )
        loaded = _job_service(request.app.state.services).create_job(
            parsed_project_id,
            payload.to_command(),
            model_profile=_resolve_profile_binding(
                request.app.state.services,
                payload.profile_id,
                structure_reference_present=(
                    payload.structure_reference is not None
                ),
            ),
        )
        return _detail(loaded)
    except ApiProblem:
        raise
    except ValidationError as error:
        raise _invalid_request_problem() from error
    except ProfileBindingError as error:
        raise _profile_binding_problem(error) from error
    except (JobError, ProjectRepositoryError) as error:
        raise _job_domain_problem(error, "creating_job") from error
    except Exception as error:
        _LOGGER.exception("Unexpected job creation failure")
        raise _internal_problem("creating_job") from error


@router.get("", response_model=tuple[JobSummary, ...])
def list_jobs(request: Request, project_id: str) -> tuple[JobSummary, ...]:
    try:
        parsed_project_id = _parse_id(
            project_id,
            code="INVALID_PROJECT_ID",
            user_message="项目标识无效",
        )
        return tuple(
            _summary(loaded)
            for loaded in _job_service(
                request.app.state.services
            ).list_jobs(parsed_project_id)
        )
    except ApiProblem:
        raise
    except (JobError, ProjectRepositoryError) as error:
        raise _job_domain_problem(error, "listing_jobs") from error
    except Exception as error:
        _LOGGER.exception("Unexpected job list failure")
        raise _internal_problem("listing_jobs") from error


@router.get("/{job_id}", response_model=JobDetail)
def get_job(request: Request, project_id: str, job_id: str) -> JobDetail:
    try:
        parsed_project_id, parsed_job_id = _parse_job_ids(project_id, job_id)
        loaded = _job_service(request.app.state.services).get_job(
            parsed_project_id,
            parsed_job_id,
        )
        return _detail(loaded)
    except ApiProblem:
        raise
    except (JobError, ProjectRepositoryError) as error:
        raise _job_domain_problem(error, "loading_job") from error
    except Exception as error:
        _LOGGER.exception("Unexpected job detail failure")
        raise _internal_problem("loading_job") from error


@router.post("/{job_id}/cancel", response_model=JobDetail)
def cancel_job(
    request: Request,
    project_id: str,
    job_id: str,
    payload: CancelJobRequest,
) -> JobDetail:
    try:
        parsed_project_id, parsed_job_id = _parse_job_ids(project_id, job_id)
        loaded = _job_service(request.app.state.services).cancel_job(
            parsed_project_id,
            parsed_job_id,
            expected_revision=payload.expected_revision,
        )
        return _detail(loaded)
    except ApiProblem:
        raise
    except (JobError, ProjectRepositoryError) as error:
        raise _job_domain_problem(error, "canceling_job") from error
    except Exception as error:
        _LOGGER.exception("Unexpected job cancellation failure")
        raise _internal_problem("canceling_job") from error


@router.post(
    "/{job_id}/retry",
    status_code=status.HTTP_201_CREATED,
    response_model=JobDetail,
)
def retry_job(request: Request, project_id: str, job_id: str) -> JobDetail:
    try:
        parsed_project_id, parsed_job_id = _parse_job_ids(project_id, job_id)
        loaded = _job_service(request.app.state.services).retry_job(
            parsed_project_id,
            parsed_job_id,
        )
        return _detail(loaded)
    except ApiProblem:
        raise
    except (JobError, ProjectRepositoryError) as error:
        raise _job_domain_problem(error, "retrying_job") from error
    except Exception as error:
        _LOGGER.exception("Unexpected job retry failure")
        raise _internal_problem("retrying_job") from error


def _job_service(services) -> JobService:
    if services.job_service is not None:
        return services.job_service
    repository = ProjectRepository(services.project_root)
    return JobService(
        repository=repository,
        catalogs=services.catalogs,
        store=JobStore(repository),
    )


def _resolve_profile_binding(
    services,
    profile_id: str,
    *,
    structure_reference_present: bool,
):
    from aimctexturegen.comfy.registry import ManifestRegistry

    registry = getattr(services, "manifest_registry", None)
    if registry is None:
        from aimctexturegen.main import _REPOSITORY_ROOT

        registry = ManifestRegistry(_REPOSITORY_ROOT)
    return build_model_profile_binding(
        registry,
        profile_id,
        structure_reference_present=structure_reference_present,
    )


def _parse_id(raw_value: str, *, code: str, user_message: str) -> UUID:
    try:
        value = UUID(raw_value)
    except (ValueError, AttributeError) as error:
        raise ApiProblem(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=code,
            stage="loading_job",
            user_message=user_message,
            recommended_actions=("返回项目列表并重新选择",),
            technical_details=None,
        ) from error
    if str(value) != raw_value:
        raise ApiProblem(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=code,
            stage="loading_job",
            user_message=user_message,
            recommended_actions=("返回项目列表并重新选择",),
            technical_details=None,
        )
    return value


def _parse_job_ids(project_id: str, job_id: str) -> tuple[UUID, UUID]:
    return (
        _parse_id(
            project_id,
            code="INVALID_PROJECT_ID",
            user_message="项目标识无效",
        ),
        _parse_id(
            job_id,
            code="INVALID_JOB_ID",
            user_message="任务标识无效",
        ),
    )


def _detail(loaded: LoadedJob) -> JobDetail:
    return JobDetail(request=loaded.request, state=loaded.state)


def _summary(loaded: LoadedJob) -> JobSummary:
    if isinstance(loaded.request, GenerationJobRequest) and isinstance(
        loaded.state,
        GenerationJobState,
    ):
        return JobSummary(
            job_id=loaded.request.job_id,
            project_id=loaded.request.project_id,
            retry_of_job_id=loaded.request.parent_job_id,
            target_semantic_id=loaded.request.target.target_semantic_id,
            target_display_name=loaded.request.target.target_display_name,
            resolution=loaded.request.resolution,
            parallelism=loaded.request.parallelism,
            status=loaded.state.status,
            revision=loaded.state.revision,
            candidate_statuses=tuple(
                candidate.status for candidate in loaded.state.candidates
            ),
            created_at=loaded.request.created_at,
            updated_at=loaded.state.updated_at,
        )
    return JobSummary(
        job_id=loaded.request.job_id,
        project_id=loaded.request.project_id,
        retry_of_job_id=loaded.request.retry_of_job_id,
        target_semantic_id=loaded.request.target_semantic_id,
        target_display_name=loaded.request.target_display_name,
        resolution=loaded.request.resolution,
        parallelism=loaded.request.parallelism,
        status=loaded.state.status,
        revision=loaded.state.revision,
        candidate_statuses=tuple(
            candidate.status for candidate in loaded.state.candidates
        ),
        created_at=loaded.request.created_at,
        updated_at=loaded.state.updated_at,
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


def _job_domain_problem(
    error: JobError | ProjectRepositoryError,
    stage: str,
) -> ApiProblem:
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    actions = ("检查项目与任务存储后重试",)
    if error.code in {"PROJECT_NOT_FOUND", "JOB_NOT_FOUND"}:
        status_code = status.HTTP_404_NOT_FOUND
        actions = ("返回项目或任务列表并重新选择",)
    elif error.code in {
        "JOB_REVISION_CONFLICT",
        "INVALID_JOB_TRANSITION",
        "JOB_ALREADY_EXISTS",
    }:
        status_code = status.HTTP_409_CONFLICT
        actions = ("刷新任务状态后重试",)
    elif error.code in {
        "JOB_TARGET_NOT_FOUND",
        "JOB_TARGET_NOT_ELIGIBLE",
        "JOB_TARGET_NOT_MISSING",
        "INVALID_STYLE_REFERENCE",
        "INVALID_STRUCTURE_REFERENCE",
        "UNSUPPORTED_PACK_FORMAT",
    }:
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
        actions = ("检查生成目标与参考文件后重试",)
    elif error.code == "CORRUPT_JOB_RECORD":
        actions = ("从备份恢复任务记录，或保留该记录并创建新任务",)
    elif error.code == "INDEX_UNAVAILABLE":
        actions = ("任务已保存；请刷新任务列表，或重启应用重建索引",)
    return ApiProblem(
        status_code=status_code,
        code=error.code,
        stage=stage,
        user_message=error.user_message,
        recommended_actions=actions,
        technical_details=(
            error.safe_technical_details
            if isinstance(error, JobError)
            else None
        ),
    )


def _internal_problem(stage: str) -> ApiProblem:
    return ApiProblem(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_ERROR",
        stage=stage,
        user_message="处理任务请求时发生内部错误",
        recommended_actions=("重试操作；若问题持续，请查看应用日志",),
        technical_details=None,
    )


def _profile_binding_problem(error: ProfileBindingError) -> ApiProblem:
    return ApiProblem(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code=error.code,
        stage="creating_job",
        user_message="模型配置不可用",
        recommended_actions=("检查模型配置后重试",),
        technical_details=None,
    )
