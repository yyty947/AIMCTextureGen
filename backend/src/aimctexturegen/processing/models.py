"""Versioned processing report models and deterministic serialization.

schema_version governs structural migration; algorithm_version governs
numeric semantics (bump it whenever snap/seam/palette math changes);
pillow_version is provenance only and MAY drift without a version bump.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1
ALGORITHM_VERSION = 1
SCORE_DECIMALS = 6


class _StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class ProcessorInfo(_StrictModel):
    algorithm_version: int = Field(ge=1)
    pillow_version: str = Field(min_length=1)


class ImageRef(_StrictModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class GridSnapInfo(_StrictModel):
    method: Literal["per-channel-median-low"]
    cell_pixels: int = Field(ge=1)


class PaletteInfo(_StrictModel):
    unique_colors: int = Field(ge=1)
    limit: int | None = Field(ge=2)
    method: Literal["median-cut"] | None


class SeamScoreInfo(_StrictModel):
    horizontal: float = Field(ge=0.0, le=1.0)
    vertical: float = Field(ge=0.0, le=1.0)
    average: float = Field(ge=0.0, le=1.0)


class PreviewRefs(_StrictModel):
    nearest_neighbor: ImageRef
    tile_3x3: ImageRef


class ProcessingReport(_StrictModel):
    schema_version: Literal[1]
    processor: ProcessorInfo
    input: ImageRef
    input_mode: Literal["RGB", "RGBA"]
    output: ImageRef
    resolution: Literal[16, 32, 64]
    grid_snap: GridSnapInfo
    palette: PaletteInfo
    seam_score: SeamScoreInfo
    previews: PreviewRefs


def dump_report_json(report: ProcessingReport) -> bytes:
    payload = report.model_dump(mode="json")
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (text + "\n").encode("utf-8")
