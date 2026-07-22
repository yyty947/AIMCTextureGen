from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict


class PackMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    pack_format: int
    supported_formats: tuple[int, int] | None = None


class InspectedPack(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
        strict=True,
    )

    source: Path
    source_kind: Literal["zip", "directory"]
    pack_root: PurePosixPath
    metadata: PackMetadata
    normalized_files: frozenset[str]
