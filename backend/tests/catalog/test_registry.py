import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aimctexturegen.catalog.models import CatalogEntry, CatalogProfile
from aimctexturegen.catalog.registry import CatalogRegistry, UnsupportedPackFormat


CATALOG_ROOT = Path(__file__).parents[3] / "catalogs" / "java"


def _write_profile(root: Path, filename: str, catalog_id: str, pack_formats: list[int]) -> None:
    root.joinpath(filename).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "catalog_id": catalog_id,
                "status": "development_fixture",
                "pack_formats": pack_formats,
                "entries": [],
            }
        ),
        encoding="utf-8",
    )


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


def test_loads_multiple_profiles_and_sorts_supported_formats(tmp_path: Path) -> None:
    _write_profile(tmp_path, "z-format-2.json", "format-2", [2])
    _write_profile(tmp_path, "a-format-1.json", "format-1", [1])

    registry = CatalogRegistry(tmp_path)

    assert registry.for_pack_format(1).catalog_id == "format-1"
    assert registry.for_pack_format(2).catalog_id == "format-2"
    with pytest.raises(UnsupportedPackFormat) as raised:
        registry.for_pack_format(999)
    assert raised.value.supported == (1, 2)


def test_rejects_duplicate_pack_format_claims(tmp_path: Path) -> None:
    _write_profile(tmp_path, "first.json", "first", [34])
    _write_profile(tmp_path, "second.json", "second", [34])

    with pytest.raises(
        ValueError,
        match="A pack format is claimed by more than one catalog profile",
    ):
        CatalogRegistry(tmp_path)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("pack_formats", ["34"]),
        ("schema_version", "1"),
    ],
)
def test_catalog_profile_rejects_integer_coercion(
    field: str,
    invalid_value: object,
) -> None:
    values = {
        "schema_version": 1,
        "catalog_id": "java-test",
        "status": "development_fixture",
        "pack_formats": [34],
        "entries": [],
    }
    values[field] = invalid_value

    with pytest.raises(ValidationError):
        CatalogProfile.model_validate_json(json.dumps(values), strict=True)


def test_catalog_entry_rejects_integer_boolean() -> None:
    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(
            {
                "semantic_id": "minecraft:stone",
                "display_name": "Stone",
                "category": "block",
                "texture_role": "all",
                "relative_path": "assets/minecraft/textures/block/stone.png",
                "prompt_terms": ("stone",),
                "mvp_eligible": 1,
            }
        )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("semantic_id", "Minecraft:Stone"),
        ("semantic_id", "stone"),
        ("semantic_id", "minecraft:/stone"),
        ("semantic_id", "minecraft:stone/"),
        ("semantic_id", "minecraft:stone//variant"),
        ("semantic_id", "minecraft:stone/./variant"),
        ("semantic_id", "minecraft:stone/../variant"),
        ("relative_path", "assets\\minecraft\\stone.png"),
        ("relative_path", "assets/minecraft/textures/block/../stone.png"),
        ("relative_path", "Assets/minecraft/textures/block/stone.png"),
    ],
)
def test_catalog_entry_rejects_noncanonical_identifiers_and_paths(
    field: str,
    invalid_value: str,
) -> None:
    values = {
        "semantic_id": "minecraft:stone",
        "display_name": "Stone",
        "category": "block",
        "texture_role": "all",
        "relative_path": "assets/minecraft/textures/block/stone.png",
        "prompt_terms": ("stone",),
        "mvp_eligible": True,
    }
    values[field] = invalid_value

    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(values)


def test_catalog_profile_rejects_duplicate_semantic_ids_and_paths() -> None:
    entry = {
        "semantic_id": "minecraft:stone",
        "display_name": "Stone",
        "category": "block",
        "texture_role": "all",
        "relative_path": "assets/minecraft/textures/block/stone.png",
        "prompt_terms": ["stone"],
        "mvp_eligible": True,
    }

    with pytest.raises(ValidationError):
        CatalogProfile.model_validate_json(
            json.dumps(
                {
                    "schema_version": 1,
                    "catalog_id": "java-test",
                    "status": "development_fixture",
                    "pack_formats": [34],
                    "entries": [entry, entry],
                }
            ),
            strict=True,
        )
