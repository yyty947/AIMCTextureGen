from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict


class ProjectManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    project_id: UUID
    project_name: str
    edition: Literal["java"]
    java_pack_format: int
    supported_formats: tuple[int, int] | None
    catalog_id: str
    source_sha256: str
    created_at: AwareDatetime
    updated_at: AwareDatetime
