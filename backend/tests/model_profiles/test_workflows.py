"""RED/GREEN tests for generic workflow binding and profile resolution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aimctexturegen.comfy.errors import (
    ProfileBindingError,
    WorkflowBindingError,
)
from aimctexturegen.comfy.manifests import (
    ModelProfileManifest,
    RuntimeManifest,
)
from aimctexturegen.comfy.registry import ManifestRegistry
from aimctexturegen.model_profiles.workflows import (
    GenericWorkflowInputs,
    WorkflowBinding,
    build_model_profile_binding,
    load_workflow_template,
)

from comfy._helpers import make_capabilities, make_profile, make_runtime


def _template(kind: str = "text2img") -> dict:
    return {
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "<positive_prompt>", "clip": ["5", 0]},
        },
        "12": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["11", 0],
                "positive": ["3", 0],
                "negative": ["17", 0],
                "latent_image": ["13", 0],
                "seed": 0,
                "steps": 20,
                "cfg": 7.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
            },
        },
        "13": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
        },
    }


class _FakeBinding(WorkflowBinding):
    def _apply(self, inputs: GenericWorkflowInputs, working: dict) -> None:
        working["3"]["inputs"]["text"] = inputs.prompt
        working["12"]["inputs"]["seed"] = inputs.seed


def _inputs(**updates: object) -> GenericWorkflowInputs:
    values = dict(
        prompt="stone texture",
        negative_prompt="",
        seed=42,
        inference_canvas=16,
        style_reference_names=("style.png",),
        structure_reference_name=None,
        advanced={},
    )
    values.update(updates)
    return GenericWorkflowInputs.model_validate(values)


def test_workflow_inputs_enforce_js_safe_seed_and_style_bounds() -> None:
    assert _inputs().seed == 42
    assert (
        _inputs(seed=9_007_199_254_740_991).seed
        == 9_007_199_254_740_991
    )
    with pytest.raises(ValidationError):
        _inputs(seed=9_007_199_254_740_992)
    with pytest.raises(ValidationError):
        _inputs(style_reference_names=())
    with pytest.raises(ValidationError):
        _inputs(
            style_reference_names=tuple(
                f"{index}.png" for index in range(9)
            )
        )


def test_load_workflow_template_requires_the_tracked_digest(
    tmp_path: Path,
) -> None:
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps(_template()), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    loaded = load_workflow_template(path, digest)
    assert loaded["12"]["class_type"] == "KSampler"
    with pytest.raises(WorkflowBindingError):
        load_workflow_template(path, "0" * 64)


def test_binding_validates_template_shape() -> None:
    template = _template()
    template["12"] = "not-a-node"
    with pytest.raises(WorkflowBindingError):
        _FakeBinding(
            kind="fake",
            template=template,
            required_node_classes=("CLIPTextEncode", "KSampler", "EmptyLatentImage"),
        )


def test_compile_deep_copies_and_never_mutates_the_template() -> None:
    template = _template()
    binding = _FakeBinding(
        kind="fake",
        template=template,
        required_node_classes=("CLIPTextEncode", "KSampler", "EmptyLatentImage"),
    )
    before = json.dumps(template, sort_keys=True)
    compiled = binding.compile(_inputs())
    compiled["12"]["inputs"]["seed"] = 999
    compiled["evil"] = True
    assert json.dumps(template, sort_keys=True) == before
    assert template["12"]["inputs"]["seed"] == 0


def test_missing_required_server_node_fails_before_submission() -> None:
    binding = _FakeBinding(
        kind="fake",
        template=_template(),
        required_node_classes=("CLIPTextEncode", "KSampler", "EmptyLatentImage"),
    )
    with pytest.raises(WorkflowBindingError):
        binding.validate_server_nodes({"CLIPTextEncode", "KSampler"})
    binding.validate_server_nodes(
        {"CLIPTextEncode", "KSampler", "EmptyLatentImage", "Extra"}
    )


def test_fake_second_profile_uses_the_same_protocol_with_different_nodes() -> None:
    second_template = {
        "1": {"class_type": "FakeNodeA", "inputs": {"text": "<prompt>"}},
        "2": {"class_type": "FakeNodeB", "inputs": {"seed": 0}},
    }

    class SecondBinding(WorkflowBinding):
        def _apply(
            self,
            inputs: GenericWorkflowInputs,
            working: dict,
        ) -> None:
            working["1"]["inputs"]["text"] = inputs.prompt
            working["2"]["inputs"]["seed"] = inputs.seed

    binding = SecondBinding(
        kind="fake-second",
        template=second_template,
        required_node_classes=("FakeNodeA", "FakeNodeB"),
    )
    compiled = binding.compile(_inputs())
    assert compiled["2"]["inputs"]["seed"] == 42
    assert binding.required_node_classes == ("FakeNodeA", "FakeNodeB")
    assert binding.kind == "fake-second"


def _registry(tmp_path: Path, profile_updates: dict | None = None) -> ManifestRegistry:
    runtime = RuntimeManifest.model_validate(make_runtime())
    profile = make_profile()
    if profile_updates:
        profile.update(profile_updates)
    manifest = ModelProfileManifest.model_validate(profile)
    return ManifestRegistry(
        root=tmp_path,
        runtimes={runtime.runtime_id: runtime},
        profiles={manifest.profile_id: manifest},
    )


def _locked_profile(profile_updates: dict | None = None) -> dict:
    updates = {"workflows": None}
    workflow_values = [
        {
            "kind": "text2img",
            "relative_path": "sdxl-mapchip-ipadapter-v1/text2img.api.json",
            "sha256": "a" * 64,
        },
        {
            "kind": "img2img",
            "relative_path": "sdxl-mapchip-ipadapter-v1/img2img.api.json",
            "sha256": "b" * 64,
        },
    ]
    if profile_updates:
        workflow_values = profile_updates.pop("workflows", workflow_values)
    updates["workflows"] = workflow_values
    updates.update(profile_updates or {})
    return updates


def test_build_model_profile_binding_selects_kind_and_freezes_digests(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path, _locked_profile())
    text_binding = build_model_profile_binding(
        registry,
        "sdxl-mapchip-ipadapter",
        structure_reference_present=False,
    )
    image_binding = build_model_profile_binding(
        registry,
        "sdxl-mapchip-ipadapter",
        structure_reference_present=True,
    )
    assert text_binding.workflow_kind == "text2img"
    assert text_binding.workflow_sha256 == "a" * 64
    assert image_binding.workflow_kind == "img2img"
    assert image_binding.runtime_id == "comfyui-windows-nvidia"


def test_build_model_profile_binding_rejects_unknown_profile_and_capabilities(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path, _locked_profile())
    with pytest.raises(ProfileBindingError):
        build_model_profile_binding(
            registry,
            "missing-profile",
            structure_reference_present=False,
        )
    capability_profile = _locked_profile()
    capability_profile["capabilities"] = {
        **make_capabilities(),
        "structure_reference": False,
    }
    restricted = _registry(tmp_path, capability_profile)
    with pytest.raises(ProfileBindingError):
        build_model_profile_binding(
            restricted,
            "sdxl-mapchip-ipadapter",
            structure_reference_present=True,
        )


def test_build_model_profile_binding_requires_locked_workflow_digest(
    tmp_path: Path,
) -> None:
    unlocked = _locked_profile()
    unlocked["workflows"][0]["sha256"] = None
    registry = _registry(tmp_path, unlocked)
    with pytest.raises(ProfileBindingError):
        build_model_profile_binding(
            registry,
            "sdxl-mapchip-ipadapter",
            structure_reference_present=False,
        )
