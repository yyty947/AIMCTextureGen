from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict


class PackMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    pack_format: int
    supported_formats: tuple[int, int] | None = None


class InspectedPack(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    source: Path
    source_kind: Literal["zip", "directory"]
    pack_root: PurePosixPath
    metadata: PackMetadata
    normalized_files: frozenset[str]
