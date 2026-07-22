from pathlib import Path

from aimctexturegen.catalog.models import CatalogProfile


class UnsupportedPackFormat(ValueError):
    def __init__(self, pack_format: int, supported: tuple[int, ...]) -> None:
        self.pack_format = pack_format
        self.supported = supported
        super().__init__(f"Unsupported Java resource pack format: {pack_format}")


class CatalogRegistry:
    def __init__(self, root: Path) -> None:
        self._profiles = self._load(root)
        self._by_format = {
            pack_format: profile
            for profile in self._profiles
            for pack_format in profile.pack_formats
        }
        if len(self._by_format) != sum(
            len(profile.pack_formats) for profile in self._profiles
        ):
            raise ValueError("A pack format is claimed by more than one catalog profile")

    @staticmethod
    def _load(root: Path) -> tuple[CatalogProfile, ...]:
        profiles = []
        for path in sorted(root.glob("*.json")):
            profiles.append(
                CatalogProfile.model_validate_json(path.read_bytes(), strict=True)
            )
        return tuple(profiles)

    def for_pack_format(self, pack_format: int) -> CatalogProfile:
        profile = self._by_format.get(pack_format)
        if profile is None:
            raise UnsupportedPackFormat(pack_format, tuple(sorted(self._by_format)))
        return profile
