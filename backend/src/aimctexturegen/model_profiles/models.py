"""Profile status models independent of process and runtime state."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field
from pydantic import BaseModel


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


ComponentState = Literal["missing", "partial", "ready", "corrupt"]


class ComponentStatus(_StrictModel):
    artifact_id: str = Field(min_length=1)
    state: ComponentState
    installed_bytes: int | None = None


class ProfileStatus(_StrictModel):
    profile_id: str
    profile_version: str
    support_state: Literal["candidate_unverified", "verified"]
    components: tuple[ComponentStatus, ...]
    ready: bool
