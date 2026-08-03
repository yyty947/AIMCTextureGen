"""RED/GREEN tests for the SDXL v2 native-batch workflow variants."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aimctexturegen.model_profiles.sdxl_v2 import SDXLV2Binding
from aimctexturegen.model_profiles.workflows import GenericWorkflowInputs

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_ROOT = REPO_ROOT / "workflows" / "sdxl-mapchip-ipadapter-v2"
MANIFEST_PATH = (
    REPO_ROOT
    / "manifests"
    / "model-profiles"
    / "sdxl-mapchip-ipadapter-v2.json"
)


def _binding(variant: str) -> SDXLV2Binding:
    return SDXLV2Binding(
        variant=variant,
        workflow_path=WORKFLOW_ROOT / f"{variant}.api.json",
    )


def _inputs(**updates: object) -> GenericWorkflowInputs:
    values = dict(
        prompt="stone texture",
        negative_prompt="blurry",
        seed=123,
        inference_canvas=16,
        batch_size=1,
        style_reference_names=(),
        structure_reference_name=None,
        advanced={},
    )
    values.update(updates)
    return GenericWorkflowInputs.model_validate(values)


@pytest.mark.parametrize(
    ("variant", "styles", "structure", "batch_size"),
    [
        ("text2img-no-style", (), None, 1),
        ("text2img-style", ("style.png",), None, 2),
        ("img2img-no-style", (), "structure.png", 4),
        ("img2img-style", ("a.png", "b.png"), "structure.png", 2),
    ],
)
def test_v2_compiles_only_the_selected_conditioning_graph(
    variant: str,
    styles: tuple[str, ...],
    structure: str | None,
    batch_size: int,
) -> None:
    compiled = _binding(variant).compile(
        _inputs(
            style_reference_names=styles,
            structure_reference_name=structure,
            batch_size=batch_size,
        )
    )
    assert compiled["12"]["inputs"]["seed"] == 123
    assert _binding(variant).output_node_id == "19"


@pytest.mark.parametrize(
    ("variant", "styles", "structure", "forbidden_nodes"),
    [
        (
            "text2img-no-style",
            (),
            None,
            (
                "CLIPVisionLoader",
                "CLIPVisionEncode",
                "IPAdapterUnifiedLoader",
                "IPAdapterAdvanced",
            ),
        ),
        (
            "img2img-no-style",
            (),
            "structure.png",
            (
                "CLIPVisionLoader",
                "CLIPVisionEncode",
                "IPAdapterUnifiedLoader",
                "IPAdapterAdvanced",
            ),
        ),
    ],
)
def test_v2_no_style_variants_exclude_the_style_conditioning_graph(
    variant: str,
    styles: tuple[str, ...],
    structure: str | None,
    forbidden_nodes: tuple[str, ...],
) -> None:
    compiled = _binding(variant).compile(
        _inputs(
            style_reference_names=styles,
            structure_reference_name=structure,
        )
    )
    classes = {node["class_type"] for node in compiled.values()}
    assert classes.isdisjoint(forbidden_nodes)


@pytest.mark.parametrize(
    ("variant", "styles", "structure"),
    [
        ("text2img-style", ("style.png",), None),
        ("img2img-style", ("a.png", "b.png"), "structure.png"),
    ],
)
def test_v2_style_variants_use_average_ipadapter_style_transfer(
    variant: str,
    styles: tuple[str, ...],
    structure: str | None,
) -> None:
    compiled = _binding(variant).compile(
        _inputs(
            style_reference_names=styles,
            structure_reference_name=structure,
            batch_size=2,
        )
    )
    ipadapter_node = compiled["11"]
    assert ipadapter_node["inputs"]["combine_embeds"] == "average"
    assert ipadapter_node["inputs"]["weight_type"] == "style transfer"


def test_v2_text2img_sets_empty_latent_batch_size() -> None:
    compiled = _binding("text2img-style").compile(
        _inputs(
            style_reference_names=("style.png",),
            batch_size=2,
        )
    )
    assert compiled["13"]["class_type"] == "EmptyLatentImage"
    assert compiled["13"]["inputs"]["batch_size"] == 2


def test_v2_img2img_repeats_the_encoded_structure_latent_by_batch_size() -> None:
    compiled = _binding("img2img-no-style").compile(
        _inputs(
            structure_reference_name="structure.png",
            batch_size=4,
        )
    )
    assert compiled["15"]["class_type"] == "RepeatLatentBatch"
    assert compiled["15"]["inputs"]["amount"] == 4


@pytest.mark.parametrize(
    ("variant", "styles", "structure"),
    [
        ("text2img-no-style", ("style.png",), None),
        ("text2img-style", (), None),
        ("img2img-no-style", ("style.png",), "structure.png"),
        ("img2img-style", (), "structure.png"),
        ("img2img-style", ("style.png",), None),
    ],
)
def test_v2_variant_specific_reference_requirements_are_enforced(
    variant: str,
    styles: tuple[str, ...],
    structure: str | None,
) -> None:
    with pytest.raises(Exception):
        _binding(variant).compile(
            _inputs(
                style_reference_names=styles,
                structure_reference_name=structure,
            )
        )


def test_v2_tracked_workflow_digests_match_the_profile_manifest() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    records = {
        record["variant"]: record
        for record in manifest["workflows"]
    }
    for variant, record in records.items():
        path = WORKFLOW_ROOT / f"{variant}.api.json"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == record["sha256"]
