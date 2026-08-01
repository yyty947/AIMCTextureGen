"""Strict project manifest versions, migration, and serialization."""

from __future__ import annotations

import json
from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from aimctexturegen.core.relative_paths import validate_project_relative_path


MAX_PROJECT_NAME_LENGTH = 128
MAX_PROJECT_MANIFEST_BYTES = 1024 * 1024


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _ProjectFields(_StrictModel):
    project_id: UUID
    project_name: str = Field(min_length=1, max_length=MAX_PROJECT_NAME_LENGTH)
    edition: Literal["java"]
    java_pack_format: int
    supported_formats: tuple[int, int] | None
    catalog_id: str
    source_sha256: str
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ProjectManifestV1(_ProjectFields):
    """The immutable schema-1 import manifest contract."""

    schema_version: Literal[1]


class ProjectManifest(_ProjectFields):
    """Current schema-2 project manifest contract."""

    schema_version: Literal[2]
    default_resolution: Literal[16, 32, 64]
    default_parallelism: Literal[1, 2, 4]
    style_references: tuple[str, ...] = Field(max_length=8)

    @field_validator("style_references")
    @classmethod
    def validate_style_references(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(validate_project_relative_path(value) for value in values)


class ProjectSummary(_StrictModel):
    """Stable project fields safe for list and index responses."""

    project_id: UUID
    project_name: str = Field(min_length=1, max_length=MAX_PROJECT_NAME_LENGTH)
    edition: Literal["java"]
    java_pack_format: int
    catalog_id: str
    created_at: AwareDatetime
    updated_at: AwareDatetime


def load_project_manifest(payload: bytes) -> tuple[ProjectManifest, bool]:
    """Strictly load schema 2 or migrate a strictly valid schema-1 payload."""

    document = json.loads(payload)
    if not isinstance(document, dict):
        raise ValueError("project manifest must be a JSON object")
    schema_version = document.get("schema_version")
    if type(schema_version) is not int:
        raise ValueError("project manifest schema_version must be an integer")
    if schema_version == 2:
        return ProjectManifest.model_validate_json(payload, strict=True), False
    if schema_version != 1:
        raise ValueError(f"unsupported project manifest schema: {schema_version}")

    old = ProjectManifestV1.model_validate_json(payload, strict=True)
    migrated = ProjectManifest.model_validate(
        {
            **old.model_dump(),
            "schema_version": 2,
            "default_resolution": 16,
            "default_parallelism": 1,
            "style_references": (),
        }
    )
    return migrated, True


def dump_project_manifest(manifest: ProjectManifest) -> bytes:
    """Serialize a schema-2 manifest as compact, deterministic UTF-8 JSON."""

    document = manifest.model_dump(mode="json")
    text = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (text + "\n").encode("utf-8")
