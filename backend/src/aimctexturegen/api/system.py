"""Read-only API for the most recent startup recovery report."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from pydantic import AwareDatetime, BaseModel, ConfigDict

from aimctexturegen.core.errors import ApiProblem
from aimctexturegen.jobs.recovery import RecoveryReport


router = APIRouter(prefix="/api/system", tags=["system"])


class _ResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RecoveryIssueResponse(_ResponseModel):
    project_id: str
    job_id: str | None
    code: str
    user_message: str


class RecoveryReportResponse(_ResponseModel):
    project_count: int
    job_count: int
    recovered_job_count: int
    issues: tuple[RecoveryIssueResponse, ...]
    completed_at: AwareDatetime


@router.get("/recovery", response_model=RecoveryReportResponse)
def get_recovery_report(request: Request) -> RecoveryReportResponse:
    report = getattr(request.app.state, "recovery_report", None)
    if not isinstance(report, RecoveryReport):
        raise ApiProblem(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="RECOVERY_NOT_READY",
            stage="loading_recovery",
            user_message="启动恢复报告尚未就绪",
            recommended_actions=("等待应用启动完成后重试",),
            technical_details=None,
        )
    return RecoveryReportResponse(
        project_count=report.project_count,
        job_count=report.job_count,
        recovered_job_count=report.recovered_job_count,
        issues=tuple(
            RecoveryIssueResponse(
                project_id=str(issue.project_id),
                job_id=None if issue.job_id is None else str(issue.job_id),
                code=issue.code,
                user_message=issue.user_message,
            )
            for issue in report.issues
        ),
        completed_at=report.completed_at,
    )
