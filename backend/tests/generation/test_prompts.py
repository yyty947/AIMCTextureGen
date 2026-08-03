from __future__ import annotations

import pytest

from aimctexturegen.generation.prompts import (
    DEFAULT_NEGATIVE,
    MAX_PROMPT_CODE_POINTS,
    PROMPT_TEMPLATE_ID,
    PROMPT_TEMPLATE_VERSION,
    compile_block_prompt,
)


def test_java_block_prompt_v1_is_exact_and_normalized() -> None:
    prompt = compile_block_prompt(
        resolution=16,
        display_name="Deepslate",
        prompt_terms=("deep stone", "dense natural texture"),
        user_description="  cold   blue-gray\nstone  ",
        user_negative_prompt="  neon,   glossy ",
    )

    assert PROMPT_TEMPLATE_ID == "java-block-prompt"
    assert PROMPT_TEMPLATE_VERSION == 1
    assert prompt.prompt_version == "java-block-prompt-v1"
    assert prompt.compiled_positive == (
        "1616, pixel art, seamless tileable square block texture, "
        "Minecraft Java Edition resource-pack texture, flat albedo, "
        "uniform material covering the full canvas, edge-to-edge continuous "
        "texture, crisp hard-edged pixel clusters, no border, no centered "
        "subject, Deepslate, deep stone, dense natural texture, cold blue-gray stone"
    )
    assert prompt.compiled_negative == (
        DEFAULT_NEGATIVE + ", neon, glossy"
    )
    assert prompt.user_prompt == "cold blue-gray stone"


@pytest.mark.parametrize(
    ("resolution", "positive_prefix"),
    [
        (
            32,
            "3232, pixel art, seamless tileable square block texture, ",
        ),
        (
            64,
            "logical 64x64 pixel grid, pixel art, seamless tileable square block texture, ",
        ),
    ],
)
def test_prompt_resolution_prefixes_are_versioned_and_never_emit_4848(
    resolution: int,
    positive_prefix: str,
) -> None:
    prompt = compile_block_prompt(
        resolution=resolution,
        display_name="Stone",
        prompt_terms=("natural stone",),
        user_description="granular surface",
        user_negative_prompt="",
    )

    assert prompt.compiled_positive.startswith(positive_prefix)
    assert "4848" not in prompt.compiled_positive
    assert "item icon" not in prompt.compiled_positive
    assert "white background" not in prompt.compiled_positive
    assert "white margin" not in prompt.compiled_positive


def test_default_negative_only_mentions_item_icon_and_white_margin() -> None:
    prompt = compile_block_prompt(
        resolution=32,
        display_name="Andesite",
        prompt_terms=("stone",),
        user_description="",
        user_negative_prompt="",
    )

    assert "item icon" in prompt.compiled_negative
    assert "white background" in prompt.compiled_negative
    assert "item icon" not in prompt.compiled_positive
    assert "white background" not in prompt.compiled_positive


def test_prompt_rejects_compiled_text_past_code_point_limit() -> None:
    repeated = "x" * MAX_PROMPT_CODE_POINTS

    with pytest.raises(ValueError):
        compile_block_prompt(
            resolution=16,
            display_name="Stone",
            prompt_terms=("stone",),
            user_description=repeated,
            user_negative_prompt="",
        )

