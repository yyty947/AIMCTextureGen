"""SDXL Base + mapchipLora + IP-Adapter semantic-slot compiler.

Numeric ComfyUI node IDs live only here and in the tracked workflow JSON:
  "3"  positive CLIPTextEncode
  "4"  CheckpointLoaderSimple
  "5"  LoraLoader (mapchipLora)
  "6"  CLIPVisionLoader
  "7"  CLIPVisionEncode
  "8"  LoadImage (first style reference)
  "10" IPAdapterUnifiedLoader
  "11" IPAdapterAdvanced (style references, average combination)
  "12" KSampler
  "13" EmptyLatentImage (text2img only)
  "14" LoadImage (structure reference, img2img only)
  "15" VAEEncode (img2img only)
  "17" negative CLIPTextEncode
  "18" VAEDecode
  "19" SaveImage
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aimctexturegen.comfy.errors import WorkflowBindingError
from aimctexturegen.model_profiles.workflows import (
    GenericWorkflowInputs,
    WorkflowBinding,
    _validate_template,
)

_ADVANCED_SPEC: dict[str, tuple[type, float, float]] = {
    "denoise": (float, 0.0, 1.0),
    "style_weight": (float, 0.0, 2.0),
    "lora_weight": (float, 0.0, 2.0),
}


class SDXLBinding(WorkflowBinding):
    """Compile generic product inputs into the fixed SDXL workflow."""

    def __init__(self, *, workflow_path: Path) -> None:
        path = Path(workflow_path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WorkflowBindingError(
                f"cannot load SDXL workflow {path}"
            ) from exc
        _validate_template(data)
        kind = "img2img" if "14" in data else "text2img"
        required = [
            "CheckpointLoaderSimple",
            "LoraLoader",
            "CLIPVisionLoader",
            "CLIPVisionEncode",
            "LoadImage",
            "IPAdapterUnifiedLoader",
            "IPAdapterAdvanced",
            "KSampler",
            "CLIPTextEncode",
            "VAEDecode",
            "SaveImage",
        ]
        if kind == "text2img":
            required.append("EmptyLatentImage")
        else:
            required.append("VAEEncode")
        super().__init__(
            kind=kind,
            template=data,
            required_node_classes=tuple(required),
        )

    def _apply(
        self,
        inputs: GenericWorkflowInputs,
        working: dict,
    ) -> None:
        for name in inputs.style_reference_names:
            _validate_upload_name(name)
        style_refs = [
            [name, "", "output"] for name in inputs.style_reference_names
        ]
        working["3"]["inputs"]["text"] = inputs.prompt
        working["17"]["inputs"]["text"] = inputs.negative_prompt
        working["12"]["inputs"]["seed"] = inputs.seed
        working["8"]["inputs"]["image"] = style_refs[0]
        working["11"]["inputs"]["image"] = style_refs

        advanced = _validate_advanced(inputs.advanced)
        if "denoise" in advanced:
            if self.kind == "text2img" and advanced["denoise"] != 1.0:
                raise WorkflowBindingError(
                    "text2img denoise must stay at 1.0"
                )
            working["12"]["inputs"]["denoise"] = advanced["denoise"]
        if "style_weight" in advanced:
            working["11"]["inputs"]["weight"] = advanced["style_weight"]
        if "lora_weight" in advanced:
            working["5"]["inputs"]["strength_model"] = advanced["lora_weight"]
            working["5"]["inputs"]["strength_clip"] = advanced["lora_weight"]

        if self.kind == "text2img":
            if inputs.structure_reference_name is not None:
                raise WorkflowBindingError(
                    "text2img cannot accept a structure reference"
                )
            return
        if not inputs.structure_reference_name:
            raise WorkflowBindingError(
                "img2img requires exactly one structure reference"
            )
        _validate_upload_name(inputs.structure_reference_name)
        working["14"]["inputs"]["image"] = [
            inputs.structure_reference_name,
            "",
            "output",
        ]


def _validate_upload_name(name: str) -> None:
    if not isinstance(name, str):
        raise WorkflowBindingError("reference name must be a string")
    normalized = name.replace("\\", "/")
    if "/" in normalized:
        raise WorkflowBindingError(
            f"reference name {name!r} must be a plain file name"
        )
    segments = normalized.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise WorkflowBindingError(
            f"reference name {name!r} is unsafe"
        )
    basename = segments[-1]
    if (
        not basename
        or basename.startswith(".")
        or len(basename) > 255
        or any(ord(character) < 32 for character in basename)
    ):
        raise WorkflowBindingError(f"reference name {name!r} is unsafe")


def _validate_advanced(advanced: dict[str, Any]) -> dict[str, float]:
    validated: dict[str, float] = {}
    for key, value in advanced.items():
        spec = _ADVANCED_SPEC.get(key)
        if spec is None:
            raise WorkflowBindingError(
                f"unknown advanced key {key!r}"
            )
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
