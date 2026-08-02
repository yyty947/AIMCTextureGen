"""Generic read-only model-profile catalog."""

from __future__ import annotations

from aimctexturegen.comfy.manifests import (
    ModelProfileManifest,
    ProfileCapabilities,
)
from aimctexturegen.comfy.registry import ManifestRegistry


class ProfileCatalog:
    """Expose generic profile metadata without profile-specific imports."""

    def __init__(self, profiles: dict[str, ModelProfileManifest]) -> None:
        self._profiles = dict(profiles)

    @classmethod
    def from_registry(cls, registry: ManifestRegistry) -> "ProfileCatalog":
        return cls(profiles=registry.profiles)

    def register(self, manifest: ModelProfileManifest) -> None:
        if manifest.profile_id in self._profiles:
            raise ValueError(
                f"profile {manifest.profile_id!r} is already registered"
            )
        self._profiles[manifest.profile_id] = manifest

    def all(self) -> tuple[ModelProfileManifest, ...]:
        return tuple(
            self._profiles[profile_id]
            for profile_id in sorted(self._profiles)
        )

    def get(self, profile_id: str) -> ModelProfileManifest:
        try:
            return self._profiles[profile_id]
        except KeyError as exc:
            raise ValueError(f"unknown model profile {profile_id!r}") from exc

    def capabilities(self, profile_id: str) -> ProfileCapabilities:
        return self.get(profile_id).capabilities
