from __future__ import annotations

from uuid import UUID


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

