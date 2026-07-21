from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    semantic_id: str
    display_name: str
    category: Literal["block"]
    texture_role: Literal["all"]
    relative_path: str
    prompt_terms: tuple[str, ...]
    mvp_eligible: bool


class CatalogProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    catalog_id: str
    status: Literal["development_fixture", "production"]
    pack_formats: tuple[int, ...] = Field(min_length=1)
    entries: tuple[CatalogEntry, ...]
