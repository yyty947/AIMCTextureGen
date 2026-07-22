from pathlib import Path, PurePosixPath

import pytest
from pydantic import ValidationError

from aimctexturegen.packs.models import InspectedPack, PackMetadata


@pytest.mark.parametrize(
    "payload",
    [
        {"pack_format": "34"},
        {"pack_format": True},
        {"pack_format": 34, "supported_formats": [34, 48]},
        {"pack_format": 34, "supported_formats": ("34", 48)},
        {"pack_format": 34, "supported_formats": (True, 48)},
        {"pack_format": 34, "unexpected": "value"},
    ],
    ids=[
        "string-format",
        "boolean-format",
        "list-range",
        "string-range-bound",
        "boolean-range-bound",
        "extra-field",
    ],
)
def test_pack_metadata_rejects_coercion_and_extra_fields(payload: dict) -> None:
    with pytest.raises(ValidationError):
        PackMetadata.model_validate(payload)


@pytest.mark.parametrize(
    "field_name, invalid_value",
    [
        ("source", "pack.zip"),
        ("pack_root", "."),
        ("metadata", {"pack_format": "34"}),
        ("normalized_files", ["pack.mcmeta"]),
    ],
)
def test_inspected_pack_rejects_coerced_field_values(
    field_name: str, invalid_value: object
) -> None:
    payload = {
        "source": Path("pack.zip"),
        "source_kind": "zip",
        "pack_root": PurePosixPath("."),
        "metadata": PackMetadata(pack_format=34),
        "normalized_files": frozenset({"pack.mcmeta"}),
    }
    payload[field_name] = invalid_value

    with pytest.raises(ValidationError):
        InspectedPack.model_validate(payload)


def test_inspected_pack_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        InspectedPack(
            source=Path("pack.zip"),
            source_kind="zip",
            pack_root=PurePosixPath("."),
            metadata=PackMetadata(pack_format=34),
            normalized_files=frozenset({"pack.mcmeta"}),
            unexpected="value",
        )
