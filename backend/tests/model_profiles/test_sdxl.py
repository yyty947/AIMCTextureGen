"""RED/GREEN tests for the SDXL semantic-slot workflow binding."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aimctexturegen.comfy.errors import WorkflowBindingError
from aimctexturegen.model_profiles.sdxl import SDXLBinding
from aimctexturegen.model_profiles.workflows import GenericWorkflowInputs

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_ROOT = REPO_ROOT / "workflows" / "sdxl-mapchip-ipadapter-v1"


def _binding(kind: str = "text2img") -> SDXLBinding:
    return SDXLBinding(
        workflow_path=WORKFLOW_ROOT / f"{kind}.api.json",
    )


def _inputs(**updates: object) -> GenericWorkflowInputs:
    values = dict(
        prompt="stone texture",
        negative_prompt="blurry",
        seed=12345,
        inference_canvas=16,
        style_reference_names=("style.png",),
        structure_reference_name=None,
        advanced={},
    )
    values.update(updates)
    return GenericWorkflowInputs.model_validate(values)


def test_text2img_compile_sets_only_semantic_slots() -> None:
    binding = _binding()
    template = binding.template
    compiled = binding.compile(_inputs())
    assert compiled["3"]["inputs"]["text"] == "stone texture"
    assert compiled["17"]["inputs"]["text"] == "blurry"
    assert compiled["12"]["inputs"]["seed"] == 12345
    assert compiled["8"]["inputs"]["image"] == ["style.png", "", "output"]
    assert compiled["11"]["inputs"]["image"] == [
        ["style.png", "", "output"]
    ]
    assert compiled["13"]["inputs"]["width"] == 1024
    assert template["3"]["inputs"]["text"] == "<positive_prompt>"
    assert template["12"]["inputs"]["seed"] == 0


def test_text2img_rejects_structure_reference() -> None:
    with pytest.raises(WorkflowBindingError):
        _binding().compile(
            _inputs(structure_reference_name="layout.png")
        )


def test_img2img_requires_one_structure_reference() -> None:
    with pytest.raises(WorkflowBindingError):
        _binding("img2img").compile(_inputs())
    compiled = _binding("img2img").compile(
        _inputs(structure_reference_name="layout.png")
    )
    assert compiled["14"]["inputs"]["image"] == ["layout.png", "", "output"]
    assert compiled["15"]["class_type"] == "VAEEncode"
    assert compiled["12"]["inputs"]["latent_image"] == ["15", 0]
    assert compiled["12"]["inputs"]["denoise"] == 0.6


def test_multiple_style_references_use_the_average_combination_contract() -> None:
    compiled = _binding().compile(
        _inputs(
            style_reference_names=("a.png", "b.png", "c.png"),
        )
    )
    assert compiled["11"]["inputs"]["image"] == [
        ["a.png", "", "output"],
        ["b.png", "", "output"],
        ["c.png", "", "output"],
    ]
    assert compiled["11"]["inputs"]["combine_embeds"] == "average"


@pytest.mark.parametrize(
    "name",
    ["../escape.png", "a\\b.png", "/abs.png", "C:/drive.png", "bad\nname.png"],
)
def test_unsafe_style_reference_names_are_rejected(name: str) -> None:
    with pytest.raises(WorkflowBindingError):
        _binding().compile(_inputs(style_reference_names=(name,)))


def test_advanced_allowlist_applies_and_rejects_unknown_keys() -> None:
    compiled = _binding("img2img").compile(
        _inputs(
            structure_reference_name="layout.png",
            advanced={
                "denoise": 0.5,
                "style_weight": 1.2,
                "lora_weight": 0.6,
            },
        )
    )
    assert compiled["12"]["inputs"]["denoise"] == 0.5
    assert compiled["11"]["inputs"]["weight"] == 1.2
    assert compiled["5"]["inputs"]["strength_model"] == 0.6
    assert compiled["5"]["inputs"]["strength_clip"] == 0.6
    with pytest.raises(WorkflowBindingError):
        _binding().compile(_inputs(advanced={"unknown_key": 1}))


@pytest.mark.parametrize("advanced", [{"denoise": 1.5}, {"style_weight": -1}, {"lora_weight": "x"}])
def test_advanced_values_are_type_and_range_checked(advanced: dict) -> None:
    with pytest.raises(WorkflowBindingError):
        _binding("img2img").compile(
            _inputs(
                structure_reference_name="layout.png",
                advanced=advanced,
            )
        )


def test_text2img_denoise_must_stay_at_one() -> None:
    with pytest.raises(WorkflowBindingError):
        _binding().compile(_inputs(advanced={"denoise": 0.5}))


def test_tracked_workflow_digests_match_the_profile_manifest() -> None:
    import json

    manifest = json.loads(
        (
            REPO_ROOT
            / "manifests"
            / "model-profiles"
            / "sdxl-mapchip-ipadapter-v1.json"
        ).read_text(encoding="utf-8")
    )
    records = {record["kind"]: record for record in manifest["workflows"]}
    for kind in ("text2img", "img2img"):
        path = WORKFLOW_ROOT / f"{kind}.api.json"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == records[kind]["sha256"]
