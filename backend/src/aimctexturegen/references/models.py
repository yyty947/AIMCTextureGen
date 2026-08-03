from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from aimctexturegen.core.relative_paths import validate_project_relative_path


ReferenceKind = Literal["style", "structure"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


@dataclass(frozen=True)
class ValidatedReference:
    payload: bytes
    sha256: str
    byte_size: int
    width: int
    height: int
    mode: Literal["RGB", "RGBA"]


class StoredReference(_StrictModel):
    schema_version: Literal[1] = 1
    reference_id: UUID
    kind: ReferenceKind
    sha256: str = Field(min_length=64, max_length=64)
    byte_size: int = Field(ge=0)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    mode: Literal["RGB", "RGBA"]
    created_at: AwareDatetime


class PackReference(_StrictModel):
    source: Literal["pack"] = "pack"
    relative_path: str
    display_name: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)
    byte_size: int = Field(ge=0)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    mode: Literal["RGB", "RGBA"]

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return validate_project_relative_path(value)


class PackReferenceSelection(_StrictModel):
    source: Literal["pack"]
    relative_path: str

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return validate_project_relative_path(value)


class UploadReferenceSelection(_StrictModel):
    source: Literal["upload"]
    reference_id: UUID


class ReferenceSelections(_StrictModel):
    style: tuple[PackReferenceSelection | UploadReferenceSelection, ...] = Field(
        default=(),
        max_length=8,
    )
    structure: UploadReferenceSelection | None = None


def dump_reference_metadata(reference: StoredReference) -> bytes:
    text = json.dumps(
        reference.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (text + "\n").encode("utf-8")
