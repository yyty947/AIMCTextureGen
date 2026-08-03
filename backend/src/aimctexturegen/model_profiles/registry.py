"""Generic read-only model-profile catalog."""

from __future__ import annotations

from aimctexturegen.comfy.manifests import (
    ModelProfileManifestRecord,
    ProfileCapabilities,
    ProfileKey,
)
from aimctexturegen.comfy.registry import ManifestRegistry


class ProfileCatalog:
    """Expose generic profile metadata without profile-specific imports."""

    def __init__(
        self, profiles: dict[ProfileKey, ModelProfileManifestRecord]
    ) -> None:
        self._profiles = dict(profiles)

    @classmethod
    def from_registry(cls, registry: ManifestRegistry) -> "ProfileCatalog":
        return cls(profiles=registry.profiles)

    def register(self, manifest: ModelProfileManifestRecord) -> None:
        key = (manifest.profile_id, manifest.profile_version)
        if key in self._profiles:
            raise ValueError(
                f"profile {key!r} is already registered"
            )
        self._profiles[key] = manifest

    def all(self) -> tuple[ModelProfileManifestRecord, ...]:
        return tuple(
            self._profiles[profile_key]
            for profile_key in sorted(self._profiles)
        )

    def get(
        self, profile_id: str, profile_version: str
    ) -> ModelProfileManifestRecord:
        try:
            return self._profiles[(profile_id, profile_version)]
        except KeyError as exc:
            raise ValueError(
                f"unknown model profile {(profile_id, profile_version)!r}"
            ) from exc

    def capabilities(
        self, profile_id: str, profile_version: str
    ) -> ProfileCapabilities:
        return self.get(profile_id, profile_version).capabilities
