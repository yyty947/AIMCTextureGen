"""Strict, versioned runtime and model-profile manifest contracts."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aimctexturegen.core.relative_paths import validate_project_relative_path

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]*$"
_HOST_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")
_MUTABLE_REVISIONS = frozenset(
    {"main", "latest", "master", "head", "develop", "trunk", "dev"}
)
_ALLOWED_PROFILE_ROOTS = ("models/", "custom_nodes/")
_RUNTIME_ARCHIVE_ROOT = "downloads/"
_WORKFLOW_KINDS = frozenset({"text2img", "img2img"})
_WORKFLOW_VARIANTS = (
    "text2img-no-style",
    "text2img-style",
    "img2img-no-style",
    "img2img-style",
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _coerce_tuple(value: object) -> object:
    return tuple(value) if isinstance(value, list) else value


def _validate_http_url(value: str) -> str:
    if not value.startswith(("http://", "https://")) or any(
        character.isspace() for character in value
    ):
        raise ValueError("source URL must be an http(s) URL without whitespace")
    return value


def _validate_sha256(value: str) -> str:
    if not _HEX_64.fullmatch(value):
        raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
    return value


def _validate_relative_path(value: str) -> str:
    return validate_project_relative_path(value)


def _validate_no_whitespace(value: str, field_name: str) -> str:
    stripped = value.strip()
    if not stripped or any(character.isspace() for character in stripped):
        raise ValueError(f"{field_name} must not be empty or contain whitespace")
    return stripped


class LicenseRecord(_StrictModel):
    name: str = Field(min_length=1)
    source_url: str = Field(min_length=1)

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        return _validate_http_url(value)


class ArtifactManifest(_StrictModel):
    artifact_id: str = Field(min_length=1, pattern=_ID_PATTERN)
    file_name: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    byte_size: int = Field(gt=0)
    sha256: str = Field(min_length=64, max_length=64)
    destination: str = Field(min_length=1)
    allowed_hosts: tuple[str, ...] = Field(min_length=1)
    license: LicenseRecord

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        return _validate_http_url(value)

    @field_validator("file_name")
    @classmethod
    def validate_file_name(cls, value: str) -> str:
        if (
            value in {"", ".", ".."}
            or "/" in value
            or "\\" in value
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("file_name must be a plain file name")
        return value

    @field_validator("revision")
    @classmethod
    def validate_revision(cls, value: str) -> str:
        stripped = _validate_no_whitespace(value, "revision")
        if stripped.casefold() in _MUTABLE_REVISIONS:
            raise ValueError("mutable revision identity is not allowed")
        return stripped

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        return _validate_sha256(value)

    @field_validator("destination")
    @classmethod
    def validate_destination(cls, value: str) -> str:
        return _validate_relative_path(value)

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def coerce_allowed_hosts(cls, value: object) -> object:
        return _coerce_tuple(value)

    @field_validator("allowed_hosts")
    @classmethod
    def validate_allowed_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for host in value:
            if not _HOST_PATTERN.fullmatch(host):
                raise ValueError(f"unsafe allowed host {host!r}")
        return value


class WorkflowRecord(_StrictModel):
    kind: Literal["text2img", "img2img"]
    relative_path: str = Field(min_length=1)
    sha256: str | None = None

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _validate_relative_path(value)

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_sha256(value)


WorkflowVariant = Literal[
    "text2img-no-style",
    "text2img-style",
    "img2img-no-style",
    "img2img-style",
]


class WorkflowVariantRecord(_StrictModel):
    variant: WorkflowVariant
    relative_path: str = Field(min_length=1)
    sha256: str | None = None
    output_node_id: str = Field(min_length=1)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _validate_relative_path(value)

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_sha256(value)

    @field_validator("output_node_id")
    @classmethod
    def validate_output_node_id(cls, value: str) -> str:
        if not value.isdecimal():
            raise ValueError("output_node_id must be a non-empty decimal string")
        return value


class ProfileCapabilities(_StrictModel):
    text_to_image: bool
    structure_reference: bool
    style_reference_min: int = Field(ge=0)
    style_reference_max: int = Field(ge=0)
    native_multi_reference: bool
    requires_custom_nodes: bool

    @model_validator(mode="after")
    def validate_style_reference_range(self) -> Self:
        if self.style_reference_max < self.style_reference_min:
            raise ValueError(
                "style_reference_max must be >= style_reference_min"
            )
        return self


class OutputContract(_StrictModel):
    format: Literal["png"]
    color_mode: Literal["rgb"]
    canvas_size: int = Field(gt=0)


class RuntimeManifest(_StrictModel):
    schema_version: Literal[1]
    runtime_id: str = Field(min_length=1, pattern=_ID_PATTERN)
    runtime_version: str = Field(min_length=1)
    platform: Literal["windows"]
    architecture: Literal["x86_64"]
    gpu_vendor: Literal["nvidia"]
    host_requirements: str = Field(min_length=1)
    release_url: str = Field(min_length=1)
    source_commit: str = Field(min_length=40, max_length=40)
    licenses: tuple[LicenseRecord, ...] = Field(min_length=1)
    archive: ArtifactManifest
    expected_archive_root: str = Field(min_length=1)
    required_paths: tuple[str, ...] = Field(min_length=1)
    startup_argument_template: tuple[str, ...] = Field(min_length=1)
    health_endpoint: str = Field(min_length=1)
    expected_runtime_identity: str = Field(min_length=1)
    extraction_headroom_bytes: int = Field(gt=0)
    headroom_is_estimate: bool = True
    revision_notes: str | None = None

    @field_validator("runtime_version")
    @classmethod
    def validate_runtime_version(cls, value: str) -> str:
        return _validate_no_whitespace(value, "runtime_version")

    @field_validator("release_url")
    @classmethod
    def validate_release_url(cls, value: str) -> str:
        return _validate_http_url(value)

    @field_validator("source_commit")
    @classmethod
    def validate_source_commit(cls, value: str) -> str:
        if not _HEX_40.fullmatch(value):
            raise ValueError("source_commit must be 40 lowercase hex characters")
        return value

    @field_validator("licenses", mode="before")
    @classmethod
    def coerce_licenses(cls, value: object) -> object:
        return _coerce_tuple(value)

    @field_validator("expected_archive_root")
    @classmethod
    def validate_expected_archive_root(cls, value: str) -> str:
        validated = _validate_relative_path(value)
        if "/" in validated:
            raise ValueError("expected_archive_root must be a single directory name")
        return validated

    @field_validator("required_paths", mode="before")
    @classmethod
    def coerce_required_paths(cls, value: object) -> object:
        return _coerce_tuple(value)

    @field_validator("required_paths")
    @classmethod
    def validate_required_paths(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        return tuple(_validate_relative_path(path) for path in value)

    @field_validator("startup_argument_template", mode="before")
    @classmethod
    def coerce_startup_arguments(cls, value: object) -> object:
        return _coerce_tuple(value)

    @field_validator("startup_argument_template")
    @classmethod
    def validate_startup_arguments(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        for argument in value:
            if not argument or "\x00" in argument or "\n" in argument:
                raise ValueError("unsafe startup argument")
        return value

    @field_validator("health_endpoint")
    @classmethod
    def validate_health_endpoint(cls, value: str) -> str:
        if not value.startswith("/") or "\x00" in value:
            raise ValueError("health_endpoint must be an absolute path")
        return value

    @model_validator(mode="after")
    def validate_archive_destination(self) -> Self:
        if not self.archive.destination.startswith(_RUNTIME_ARCHIVE_ROOT):
            raise ValueError(
                "runtime archive destination must be under downloads/"
            )
        return self


class _ProfileFields(_StrictModel):
    profile_id: str = Field(min_length=1, pattern=_ID_PATTERN)
    profile_version: str = Field(min_length=1)
    support_state: Literal["candidate_unverified", "verified"]
    compatible_runtime_ids: tuple[str, ...] = Field(min_length=1)
    compatible_runtime_versions: tuple[str, ...] = Field(min_length=1)
    capabilities: ProfileCapabilities
    artifacts: tuple[ArtifactManifest, ...] = Field(min_length=1)
    workflows: tuple[WorkflowRecord, ...] = Field(min_length=2)
    required_node_classes: tuple[str, ...] = Field(min_length=1)
    output_contract: OutputContract
    profile_defaults: dict[str, str | int | float | bool] = Field(
        default_factory=dict
    )
    user_limitations: str = Field(min_length=1)
    revision_notes: str | None = None

    @field_validator("profile_version")
    @classmethod
    def validate_profile_version(cls, value: str) -> str:
        return _validate_no_whitespace(value, "profile_version")

    @field_validator(
        "compatible_runtime_ids",
        "compatible_runtime_versions",
        "required_node_classes",
        mode="before",
    )
    @classmethod
    def coerce_sequences(cls, value: object) -> object:
        return _coerce_tuple(value)

    @field_validator("compatible_runtime_ids")
    @classmethod
    def validate_compatible_runtime_ids(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        for runtime_id in value:
            if re.fullmatch(_ID_PATTERN, runtime_id) is None:
                raise ValueError(f"unsafe compatible runtime id {runtime_id!r}")
        return value

    @field_validator("compatible_runtime_versions")
    @classmethod
    def validate_compatible_runtime_versions(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        return tuple(
            _validate_no_whitespace(version, "compatible_runtime_versions")
            for version in value
        )

    @field_validator("required_node_classes")
    @classmethod
    def validate_required_node_classes(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        for node_class in value:
            if (
                not node_class
                or any(character.isspace() for character in node_class)
                or any(ord(character) < 32 for character in node_class)
            ):
                raise ValueError(f"unsafe node class name {node_class!r}")
        return value

    @field_validator("artifacts", "workflows", mode="before")
    @classmethod
    def coerce_artifact_sequences(cls, value: object) -> object:
        return _coerce_tuple(value)

    @model_validator(mode="after")
    def validate_artifacts(self) -> Self:
        artifact_ids: set[str] = set()
        destinations: set[str] = set()
        by_hash: dict[str, ArtifactManifest] = {}
        has_custom_node = False
        for artifact in self.artifacts:
            if artifact.artifact_id in artifact_ids:
                raise ValueError(
                    f"duplicate artifact_id {artifact.artifact_id!r}"
                )
            artifact_ids.add(artifact.artifact_id)
            if not artifact.destination.startswith(_ALLOWED_PROFILE_ROOTS):
                raise ValueError(
                    "artifact destination outside allowlisted roots: "
                    f"{artifact.destination!r}"
                )
            destination_key = artifact.destination.casefold()
            if destination_key in destinations:
                raise ValueError(
                    f"case-colliding artifact destination "
                    f"{artifact.destination!r}"
                )
            destinations.add(destination_key)
            prior = by_hash.get(artifact.sha256)
            if prior is not None and (
                prior.byte_size != artifact.byte_size
                or prior.source_url != artifact.source_url
            ):
                raise ValueError(
                    f"artifact {artifact.artifact_id!r} reuses sha256 "
                    "with conflicting metadata"
                )
            by_hash.setdefault(artifact.sha256, artifact)
            if artifact.destination.startswith("custom_nodes/"):
                has_custom_node = True
        if self.capabilities.requires_custom_nodes and not has_custom_node:
            raise ValueError(
                "requires_custom_nodes needs a custom_nodes/ artifact"
            )
        return self


class ModelProfileManifest(_ProfileFields):
    schema_version: Literal[1]
    workflows: tuple[WorkflowRecord, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_workflows(self) -> Self:
        kinds: set[str] = set()
        for workflow in self.workflows:
            if workflow.kind in kinds:
                raise ValueError(f"duplicate workflow kind {workflow.kind!r}")
            kinds.add(workflow.kind)
        if kinds != _WORKFLOW_KINDS:
            raise ValueError(
                "profile must declare exactly text2img and img2img workflows"
            )
        return self


class ModelProfileManifestV2(_ProfileFields):
    schema_version: Literal[2]
    workflows: tuple[WorkflowVariantRecord, ...] = Field(
        min_length=4,
        max_length=4,
    )

    @model_validator(mode="after")
    def validate_workflows(self) -> Self:
        variants = tuple(workflow.variant for workflow in self.workflows)
        if variants != _WORKFLOW_VARIANTS:
            raise ValueError(
                "profile must declare the four phase-5 workflow variants in order"
            )
        return self


ModelProfileManifestRecord = ModelProfileManifest | ModelProfileManifestV2
ProfileKey = tuple[str, str]


def canonical_manifest_bytes(model: BaseModel) -> bytes:
    """Return deterministic UTF-8 JSON with sorted keys and stable separators."""

    return json.dumps(
        model.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def manifest_sha256(model: BaseModel) -> str:
    """Return the lowercase SHA-256 of the canonical manifest bytes."""

    return hashlib.sha256(canonical_manifest_bytes(model)).hexdigest()
