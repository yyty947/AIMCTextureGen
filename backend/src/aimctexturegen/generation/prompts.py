from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field


PROMPT_TEMPLATE_ID = "java-block-prompt"
PROMPT_TEMPLATE_VERSION = 1
MAX_PROMPT_CODE_POINTS = 4000
DEFAULT_NEGATIVE = (
    "item icon, isolated object, centered composition, empty margin, "
    "white background, border, frame, visible seam, perspective, 3d render, "
    "scene, text, watermark, drop shadow, soft focus, anti-aliasing, "
    "blurry gradient, lighting vignette"
)

_WHITESPACE = re.compile(r"\s+", flags=re.UNICODE)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CompiledPrompt(_StrictModel):
    prompt_version: str = Field(min_length=1)
    compiled_positive: str = Field(min_length=1)
    compiled_negative: str = Field(min_length=1)
    user_prompt: str


def compile_block_prompt(
    *,
    resolution: int,
    display_name: str,
    prompt_terms: tuple[str, ...],
    user_description: str,
    user_negative_prompt: str,
) -> CompiledPrompt:
    prefix = {
        16: "1616",
        32: "3232",
        64: "logical 64x64 pixel grid",
    }.get(resolution)
    if prefix is None:
        raise ValueError("resolution must be 16, 32, or 64")

    positive_parts = [
        prefix,
        "pixel art",
        "seamless tileable square block texture",
        "Minecraft Java Edition resource-pack texture",
        "flat albedo",
        "uniform material covering the full canvas",
        "edge-to-edge continuous texture",
        "crisp hard-edged pixel clusters",
        "no border",
        "no centered subject",
        *_normalize_components((display_name, *prompt_terms, user_description)),
    ]
    negative_parts = [
        *_normalize_components((DEFAULT_NEGATIVE, user_negative_prompt)),
    ]
    positive = ", ".join(positive_parts)
    negative = ", ".join(negative_parts)
    if len(positive) > MAX_PROMPT_CODE_POINTS or len(negative) > MAX_PROMPT_CODE_POINTS:
        raise ValueError("compiled prompt exceeds the code point limit")
    return CompiledPrompt(
        prompt_version=f"{PROMPT_TEMPLATE_ID}-v{PROMPT_TEMPLATE_VERSION}",
        compiled_positive=positive,
        compiled_negative=negative,
        user_prompt=_single_component(user_description),
    )


def _normalize_components(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        for part in value.split(","):
            collapsed = _single_component(part)
            if collapsed:
                normalized.append(collapsed)
    return tuple(normalized)


def _single_component(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return _WHITESPACE.sub(" ", normalized).strip(" ,")

