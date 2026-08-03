"""SDXL v2 semantic-slot compiler for four explicit native-batch variants."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aimctexturegen.comfy.errors import WorkflowBindingError
from aimctexturegen.comfy.manifests import WorkflowVariant
from aimctexturegen.model_profiles.sdxl import _validate_upload_name
from aimctexturegen.model_profiles.workflows import (
    GenericWorkflowInputs,
    WorkflowBinding,
    _validate_template,
)

_STYLE_VARIANTS = frozenset({"text2img-style", "img2img-style"})
_IMG2IMG_VARIANTS = frozenset({"img2img-no-style", "img2img-style"})
_ADVANCED_SPEC: dict[str, tuple[type, float, float]] = {
    "denoise": (float, 0.0, 1.0),
    "style_weight": (float, 0.0, 2.0),
}


class SDXLV2Binding(WorkflowBinding):
    """Compile generic product inputs into one of the fixed SDXL v2 workflows."""

    def __init__(
        self,
        *,
        variant: WorkflowVariant,
        workflow_path: Path,
    ) -> None:
        path = Path(workflow_path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WorkflowBindingError(
                f"cannot load SDXL v2 workflow {path}"
            ) from exc
        _validate_template(data)
        required = [
            "CheckpointLoaderSimple",
            "LoraLoader",
            "CLIPTextEncode",
            "KSampler",
            "VAEDecode",
            "SaveImage",
        ]
        if variant in _STYLE_VARIANTS:
            required.extend(
                [
                    "CLIPVisionLoader",
                    "CLIPVisionEncode",
                    "LoadImage",
                    "IPAdapterUnifiedLoader",
                    "IPAdapterAdvanced",
                ]
            )
        if variant in _IMG2IMG_VARIANTS:
            required.extend(
                [
                    "ImageScale",
                    "VAEEncode",
                    "RepeatLatentBatch",
                ]
            )
        else:
            required.append("EmptyLatentImage")
        super().__init__(
            kind=variant,
            template=data,
            required_node_classes=tuple(required),
            output_node_id="19",
        )
        self._variant = variant

    @property
    def variant(self) -> WorkflowVariant:
        return self._variant

    def _apply(
        self,
        inputs: GenericWorkflowInputs,
        working: dict,
    ) -> None:
        wants_style = self.variant in _STYLE_VARIANTS
        style_count = len(inputs.style_reference_names)
        if wants_style:
            if not 1 <= style_count <= 8:
                raise WorkflowBindingError(
                    f"{self.variant} requires 1 to 8 style references"
                )
            for name in inputs.style_reference_names:
                _validate_upload_name(name)
        elif style_count != 0:
            raise WorkflowBindingError(
                f"{self.variant} requires exactly 0 style references"
            )

        wants_structure = self.variant in _IMG2IMG_VARIANTS
        if wants_structure:
            if not inputs.structure_reference_name:
                raise WorkflowBindingError(
                    f"{self.variant} requires exactly one structure reference"
                )
            _validate_upload_name(inputs.structure_reference_name)
        elif inputs.structure_reference_name is not None:
            raise WorkflowBindingError(
                f"{self.variant} cannot accept a structure reference"
            )

        working["3"]["inputs"]["text"] = inputs.prompt
        working["17"]["inputs"]["text"] = inputs.negative_prompt
        working["12"]["inputs"]["seed"] = inputs.seed

        advanced = _validate_advanced(inputs.advanced)
        if "denoise" in advanced and not wants_structure:
            raise WorkflowBindingError(
                f"{self.variant} cannot accept denoise without a structure reference"
            )
        if "denoise" in advanced:
            working["12"]["inputs"]["denoise"] = advanced["denoise"]

        if wants_style:
            image_ref = _set_style_references(
                working,
                inputs.style_reference_names,
            )
            working["11"]["inputs"]["image"] = image_ref
            if "style_weight" in advanced:
                working["11"]["inputs"]["weight"] = advanced["style_weight"]
        elif "style_weight" in advanced:
            raise WorkflowBindingError(
                f"{self.variant} cannot accept style_weight without style references"
            )

        if wants_structure:
            working["14"]["inputs"]["image"] = inputs.structure_reference_name
            working["15"]["inputs"]["amount"] = inputs.batch_size
        else:
            working["13"]["inputs"]["batch_size"] = inputs.batch_size


def _set_style_references(
    working: dict[str, dict[str, Any]],
    style_reference_names: tuple[str, ...],
) -> list[Any]:
    working["8"]["inputs"]["image"] = style_reference_names[0]
    style_node_ids = ["8"]
    for index, reference in enumerate(style_reference_names[1:], start=101):
        node_id = str(index)
        working[node_id] = {
            "class_type": "LoadImage",
            "inputs": {"image": reference},
        }
        style_node_ids.append(node_id)
    image_ref: list[Any] = [style_node_ids[0], 0]
    for index, other in enumerate(style_node_ids[1:], start=201):
        batch_id = str(index)
        working[batch_id] = {
            "class_type": "ImageBatch",
            "inputs": {
                "image1": image_ref,
                "image2": [other, 0],
            },
        }
        image_ref = [batch_id, 0]
    return image_ref


def _validate_advanced(advanced: dict[str, Any]) -> dict[str, float]:
    validated: dict[str, float] = {}
    for key, value in advanced.items():
        spec = _ADVANCED_SPEC.get(key)
        if spec is None:
            raise WorkflowBindingError(f"unknown advanced key {key!r}")
        expected_type, minimum, maximum = spec
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise WorkflowBindingError(
                f"advanced key {key!r} must be numeric"
            )
        numeric = float(value)
        if not (minimum <= numeric <= maximum):
            raise WorkflowBindingError(
                f"advanced key {key!r} is out of range"
            )
        validated[key] = numeric
    return validated
