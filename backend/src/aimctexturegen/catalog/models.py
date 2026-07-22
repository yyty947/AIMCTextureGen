import re
from pathlib import PurePosixPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_SEMANTIC_ID = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")


class CatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    semantic_id: str
    display_name: str
    category: Literal["block"]
    texture_role: Literal["all"]
    relative_path: str
    prompt_terms: tuple[str, ...]
    mvp_eligible: bool

    @field_validator("semantic_id")
    @classmethod
    def validate_semantic_id(cls, value: str) -> str:
        if not _SEMANTIC_ID.fullmatch(value) or ".." in value.split(":", 1)[1].split("/"):
            raise ValueError("semantic_id must be a canonical namespaced identifier")
        return value

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            "\\" in value
            or value != value.casefold()
            or path.is_absolute()
            or path.as_posix() != value
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or path.suffix != ".png"
        ):
            raise ValueError("relative_path must be a canonical lowercase PNG path")
        return value


class CatalogProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    catalog_id: str
    status: Literal["development_fixture", "production"]
    pack_formats: tuple[int, ...] = Field(min_length=1)
    entries: tuple[CatalogEntry, ...]

    @model_validator(mode="after")
    def validate_unique_entries(self) -> Self:
        semantic_ids = [entry.semantic_id for entry in self.entries]
        relative_paths = [entry.relative_path for entry in self.entries]
        if len(set(semantic_ids)) != len(semantic_ids):
            raise ValueError("Catalog semantic_id values must be unique")
        if len(set(relative_paths)) != len(relative_paths):
            raise ValueError("Catalog relative_path values must be unique")
        return self
