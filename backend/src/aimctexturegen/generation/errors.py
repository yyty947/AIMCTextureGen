from __future__ import annotations

from datetime import datetime
from uuid import UUID

from aimctexturegen.comfy.errors import (
    ComfyDisconnectedError,
    ComfyExecutionError,
    ComfyQueueError,
    ComfyTimeoutError,
)
from aimctexturegen.jobs.models_v3 import GenerationFailure
from aimctexturegen.processing.errors import ProcessingError


class GenerationError(Exception):
    def __init__(
        self,
        code: str,
        user_message: str,
        *,
        recommended_actions: tuple[str, ...] = (),
        technical_details: str | None = None,
        current_job: tuple[UUID, UUID] | None = None,
    ) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.recommended_actions = recommended_actions
        self.technical_details = technical_details
        self.current_job = current_job


_ERRORS: dict[str, tuple[str, tuple[str, ...]]] = {
    "JOB_TARGET_NOT_FOUND": (
        "目录中没有该生成目标",
        ("重新选择一个未覆盖的普通方块目标",),
    ),
    "JOB_TARGET_NOT_ELIGIBLE": (
        "该目标不属于 MVP 可生成范围",
        ("选择普通、单贴图、非透明、非动画方块目标",),
    ),
    "JOB_TARGET_NOT_MISSING": (
        "该目标已被资源包覆盖",
        ("改选一个当前缺失的目标后再创建任务",),
    ),
    "REFERENCE_INVALID": (
        "参考图无效或当前不可用",
        ("重新选择包内参考图或重新上传参考图",),
    ),
    "PROFILE_NOT_READY": (
        "当前模型配置尚未就绪",
        ("确认已安装并验证受支持的模型配置",),
    ),
    "PROFILE_WORKFLOW_MISMATCH": (
        "模型配置与当前参考图组合不匹配",
        ("检查风格参考和结构参考的组合后重试",),
    ),
    "INVALID_SEED_SOURCE": (
        "无法安全生成原生批次 seed",
        ("稍后重试；如果持续失败，请检查本地环境",),
    ),
    "INVALID_GENERATION_COMMAND": (
        "生成参数无效",
        ("检查高级参数与参考图是否匹配",),
    ),
    "OUTPUT_CONTRACT_VIOLATION": (
        "生成输出不符合受控契约",
        ("重新运行当前原生批次；如果持续失败，请检查模型配置和 workflow 版本",),
    ),
    "COMFY_QUEUE_REJECTED": (
        "ComfyUI 拒绝了当前原生批次",
        ("稍后重试；如果持续失败，请检查受管 ComfyUI 日志和 workflow 版本",),
    ),
    "COMFY_DISCONNECTED": (
        "与 ComfyUI 的连接意外断开",
        ("稍后重试；如果持续失败，请检查受管 ComfyUI 进程和日志",),
    ),
    "COMFY_TIMEOUT": (
        "等待 ComfyUI 完成当前原生批次超时",
        ("稍后重试；如果持续失败，请检查受管 ComfyUI 日志",),
    ),
    "GPU_OUT_OF_MEMORY": (
        "显存不足，当前原生批次未能完成",
        (
            "用更低并行度重新创建一个新任务",
            "关闭其他占用显存的应用程序",
            "停止其他 ComfyUI 实例",
        ),
    ),
    "COMFY_EXECUTION_FAILED": (
        "ComfyUI 执行当前原生批次失败",
        ("稍后重试；如果持续失败，请检查受管 ComfyUI 日志和 workflow 版本",),
    ),
    "POSTPROCESSING_FAILED": (
        "候选后处理失败",
        ("重新运行该候选后处理；如果持续失败，请检查项目目录权限和磁盘空间",),
    ),
    "JOB_STORAGE_UNAVAILABLE": (
        "无法安全保存生成产物",
        ("稍后重试；如果持续失败，请检查项目目录权限和磁盘空间",),
    ),
}


def generation_error(
    code: str,
    *,
    technical_details: str | None = None,
    current_job: tuple[UUID, UUID] | None = None,
) -> GenerationError:
    user_message, recommended_actions = _ERRORS[code]
    return GenerationError(
        code,
        user_message,
        recommended_actions=recommended_actions,
        technical_details=technical_details,
        current_job=current_job,
    )


def translate_execution_error(error: Exception) -> GenerationError:
    if isinstance(error, GenerationError):
        return error
    if isinstance(error, ProcessingError):
        return generation_error(
            "POSTPROCESSING_FAILED",
            technical_details=f"{error.code}: {error.message}",
        )
    if isinstance(error, ComfyQueueError):
        return generation_error("COMFY_QUEUE_REJECTED", technical_details=str(error))
    if isinstance(error, ComfyDisconnectedError):
        return generation_error("COMFY_DISCONNECTED", technical_details=str(error))
    if isinstance(error, ComfyTimeoutError):
        return generation_error("COMFY_TIMEOUT", technical_details=str(error))
    if isinstance(error, ComfyExecutionError):
        if _looks_like_oom(str(error)):
            return generation_error("GPU_OUT_OF_MEMORY", technical_details=str(error))
        return generation_error("COMFY_EXECUTION_FAILED", technical_details=str(error))
    return GenerationError(
        "COMFY_EXECUTION_FAILED",
        "ComfyUI 执行当前原生批次失败",
        recommended_actions=_ERRORS["COMFY_EXECUTION_FAILED"][1],
        technical_details=str(error) or error.__class__.__name__,
    )


def generation_failure_from_error(
    error: Exception,
    *,
    stage: str,
    occurred_at: datetime,
) -> GenerationFailure:
    mapped = translate_execution_error(error)
    return GenerationFailure(
        error_code=mapped.code,
        stage=stage,
        user_message=mapped.user_message,
        recommended_actions=mapped.recommended_actions,
        technical_details=mapped.technical_details,
        retryable=True,
        occurred_at=occurred_at,
    )


def _looks_like_oom(message: str) -> bool:
    lowered = message.casefold()
    return "out of memory" in lowered or "cuda oom" in lowered or "cuda out of memory" in lowered
