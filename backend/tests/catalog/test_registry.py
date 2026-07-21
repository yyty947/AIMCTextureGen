from pathlib import Path

import pytest

from aimctexturegen.catalog.registry import CatalogRegistry, UnsupportedPackFormat


CATALOG_ROOT = Path(__file__).parents[3] / "catalogs" / "java"


def test_loads_profile_by_primary_pack_format() -> None:
    profile = CatalogRegistry(CATALOG_ROOT).for_pack_format(34)

    assert profile.catalog_id == "java-dev-format-34"
    assert profile.pack_formats == (34,)
    assert [entry.semantic_id for entry in profile.entries] == [
        "minecraft:stone",
        "minecraft:deepslate",
    ]


def test_rejects_unsupported_primary_pack_format() -> None:
    registry = CatalogRegistry(CATALOG_ROOT)

    with pytest.raises(UnsupportedPackFormat) as raised:
        registry.for_pack_format(999)

    assert raised.value.pack_format == 999
    assert raised.value.supported == (34,)
