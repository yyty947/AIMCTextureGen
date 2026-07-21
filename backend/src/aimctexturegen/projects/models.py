from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ProjectManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    project_id: UUID
    project_name: str
    edition: str
    java_pack_format: int
    supported_formats: tuple[int, int] | None
    catalog_id: str
    source_sha256: str
    created_at: datetime
    updated_at: datetime
