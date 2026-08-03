"""Generic fixed-workflow binding protocol and profile resolution."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, field_validator
from pydantic import BaseModel

from aimctexturegen.comfy.errors import (
    ProfileBindingError,
    WorkflowBindingError,
)
from aimctexturegen.comfy.manifests import manifest_sha256
from aimctexturegen.comfy.registry import ManifestRegistry
from aimctexturegen.jobs.models import MAX_SAFE_SEED, ModelProfileBinding


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class GenericWorkflowInputs(_StrictModel):
    prompt: str = Field(min_length=1, max_length=4000)
    negative_prompt: str = Field(default="", max_length=4000)
    seed: int = Field(ge=0, le=MAX_SAFE_SEED)
    inference_canvas: Literal[16, 32, 64]
    batch_size: Literal[1, 2, 4] = 1
    style_reference_names: tuple[str, ...] = Field(
        max_length=8,
    )
    structure_reference_name: str | None = None
    advanced: dict[str, str | int | float | bool] = Field(
        default_factory=dict
    )

    @field_validator("prompt")
    @classmethod
    def validate_prompt_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must contain non-whitespace characters")
        return value


class WorkflowBinding:
    """Deep-copy a fixed template and set only semantic slots."""

    def __init__(
        self,
        *,
        kind: str,
        template: dict,
        required_node_classes: tuple[str, ...],
        output_node_id: str | None = None,
    ) -> None:
        _validate_template(template)
        self._kind = kind
        self._template = deepcopy(template)
        self._required = tuple(required_node_classes)
        resolved_output_node_id = (
            output_node_id
            if output_node_id is not None
            else next(reversed(template))
        )
        if resolved_output_node_id not in template:
            raise WorkflowBindingError(
                f"output node {resolved_output_node_id!r} is not present in the workflow template"
            )
        self._output_node_id = resolved_output_node_id

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def template(self) -> dict:
        return deepcopy(self._template)

    @property
    def required_node_classes(self) -> tuple[str, ...]:
        return self._required

    @property
    def output_node_id(self) -> str:
        return self._output_node_id

    def validate_server_nodes(self, available: set[str]) -> None:
        missing = set(self._required) - set(available)
        if missing:
            raise WorkflowBindingError(
                f"server is missing required nodes: {sorted(missing)}"
            )

    def compile(self, inputs: GenericWorkflowInputs) -> dict:
        working = deepcopy(self._template)
        self._apply(inputs, working)
        _validate_template(working)
        return working

    def _apply(
        self,
        inputs: GenericWorkflowInputs,
        working: dict,
    ) -> None:
        raise NotImplementedError


def load_workflow_template(path: Path, expected_sha256: str) -> dict:
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise WorkflowBindingError(
            f"cannot read workflow template {path}"
        ) from exc
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256:
        raise WorkflowBindingError(
            "workflow template digest does not match the tracked manifest"
        )
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise WorkflowBindingError(
            "workflow template is not valid JSON"
        ) from exc
    _validate_template(data)
    return data


def build_model_profile_binding(
    registry: ManifestRegistry,
    profile_id: str,
    *,
    profile_version: str = "1",
    structure_reference_present: bool,
) -> ModelProfileBinding:
    try:
        profile = registry.profile(profile_id, profile_version)
    except Exception as exc:
        raise ProfileBindingError(
            "UNKNOWN_PROFILE",
            f"unknown model profile {(profile_id, profile_version)!r}",
        ) from exc
    if profile.schema_version != 1:
        raise ProfileBindingError(
            "PROFILE_CAPABILITY_MISMATCH",
            "profile version is not compatible with the legacy job binding",
        )
    if profile.support_state != "verified":
        raise ProfileBindingError(
            "PROFILE_CAPABILITY_MISMATCH",
            "profile must be verified before it can back product job bindings",
        )
    kind = "img2img" if structure_reference_present else "text2img"
    capabilities = profile.capabilities
    if kind == "img2img" and not capabilities.structure_reference:
        raise ProfileBindingError(
            "PROFILE_CAPABILITY_MISMATCH",
            "profile does not support structure references",
        )
    if kind == "text2img" and not capabilities.text_to_image:
        raise ProfileBindingError(
            "PROFILE_CAPABILITY_MISMATCH",
            "profile does not support text-to-image",
        )
    workflow = next(
        (item for item in profile.workflows if item.kind == kind),
        None,
    )
    if workflow is None or workflow.sha256 is None:
        raise ProfileBindingError(
            "PROFILE_DIGEST_MISMATCH",
            "profile workflow digest is not locked",
        )
    runtime_id = profile.compatible_runtime_ids[0]
    runtime = registry.runtime(runtime_id)
    return ModelProfileBinding(
        profile_id=profile.profile_id,
        profile_version=profile.profile_version,
        profile_manifest_sha256=manifest_sha256(profile),
        runtime_id=runtime.runtime_id,
        runtime_version=runtime.runtime_version,
        runtime_manifest_sha256=manifest_sha256(runtime),
        workflow_kind=kind,
        workflow_sha256=workflow.sha256,
    )


def _validate_template(template: dict) -> None:
    if not isinstance(template, dict) or not template:
        raise WorkflowBindingError("workflow template must be a non-empty object")
    for node_id, node in template.items():
        if (
            not isinstance(node_id, str)
            or not isinstance(node, dict)
            or not isinstance(node.get("class_type"), str)
            or not isinstance(node.get("inputs"), dict)
        ):
            raise WorkflowBindingError(
                f"workflow node {node_id!r} is malformed"
            )
