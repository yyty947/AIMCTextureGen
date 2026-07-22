from pydantic import BaseModel, ConfigDict
from starlette.responses import JSONResponse


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    code: str
    stage: str
    user_message: str
    recommended_actions: tuple[str, ...]
    technical_details: str | None


class ApiProblem(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        stage: str,
        user_message: str,
        recommended_actions: tuple[str, ...] = (),
        technical_details: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.envelope = ErrorEnvelope(
            code=code,
            stage=stage,
            user_message=user_message,
            recommended_actions=recommended_actions,
            technical_details=technical_details,
        )
        super().__init__(user_message)


def problem_response(problem: ApiProblem) -> JSONResponse:
    return JSONResponse(
        status_code=problem.status_code,
        content=problem.envelope.model_dump(mode="json"),
    )
