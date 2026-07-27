import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from aimctexturegen.projects.models import (
    ProjectManifest,
    ProjectManifestV1,
    ProjectSummary,
    dump_project_manifest,
    load_project_manifest,
)


def _old_values() -> dict[str, object]:
    created_at = datetime(2026, 7, 20, 8, 30, tzinfo=timezone.utc)
    updated_at = datetime(2026, 7, 22, 9, 45, tzinfo=timezone.utc)
    return {
        "schema_version": 1,
        "project_id": uuid4(),
        "project_name": "Migrated project",
        "edition": "java",
        "java_pack_format": 34,
        "supported_formats": (34, 48),
        "catalog_id": "java-dev-format-34",
        "source_sha256": "ab" * 32,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _manifest(**updates: object) -> ProjectManifest:
    values = {
        **_old_values(),
        "schema_version": 2,
        "default_resolution": 16,
        "default_parallelism": 1,
        "style_references": (),
        **updates,
    }
    return ProjectManifest.model_validate(values)


def _json_bytes(values: dict[str, object]) -> bytes:
    return (
        json.dumps(
            values,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        + "\n"
    ).encode("utf-8")


def test_schema_2_manifest_has_strict_default_contract_fields() -> None:
    manifest = _manifest()

    assert manifest.schema_version == 2
    assert manifest.default_resolution == 16
    assert manifest.default_parallelism == 1
    assert manifest.style_references == ()


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("schema_version", "2"),
        ("schema_version", 1),
        ("project_id", str(uuid4())),
        ("java_pack_format", "34"),
        ("default_resolution", "16"),
        ("default_resolution", 48),
        ("default_parallelism", "1"),
        ("default_parallelism", 3),
        ("style_references", ["assets/minecraft/textures/block/stone.png"]),
    ],
)
def test_schema_2_manifest_rejects_coercion_and_out_of_contract_values(
    field: str,
    invalid: object,
) -> None:
    values = _manifest().model_dump()
    values[field] = invalid

    with pytest.raises(ValidationError):
        ProjectManifest.model_validate(values)


@pytest.mark.parametrize(
    "path",
    [
        "/absolute.png",
        "assets/../escape.png",
        r"assets\minecraft\stone.png",
        "C:/drive.png",
    ],
)
def test_schema_2_manifest_rejects_unsafe_style_reference_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        _manifest(style_references=(path,))


def test_schema_2_manifest_rejects_more_than_eight_style_references() -> None:
    references = tuple(
        f"assets/minecraft/textures/block/{index}.png" for index in range(9)
    )

    with pytest.raises(ValidationError):
        _manifest(style_references=references)


def test_schema_2_manifest_rejects_extra_fields_and_is_frozen() -> None:
    values = _manifest().model_dump()
    values["unexpected"] = True
    with pytest.raises(ValidationError):
        ProjectManifest.model_validate(values)

    manifest = _manifest()
    with pytest.raises(ValidationError):
        manifest.default_resolution = 32


def test_schema_1_load_migrates_every_old_field_and_preserves_timestamps() -> None:
    old = ProjectManifestV1.model_validate(_old_values())
    payload = old.model_dump_json().encode("utf-8")

    manifest, migrated = load_project_manifest(payload)

    assert migrated is True
    assert {
        field: getattr(manifest, field)
        for field in ProjectManifestV1.model_fields
        if field != "schema_version"
    } == {
        field: getattr(old, field)
        for field in ProjectManifestV1.model_fields
        if field != "schema_version"
    }
    assert manifest.schema_version == 2
    assert manifest.created_at == old.created_at
    assert manifest.updated_at == old.updated_at
    assert manifest.default_resolution == 16
    assert manifest.default_parallelism == 1
    assert manifest.style_references == ()


def test_schema_2_load_returns_existing_manifest_without_migration() -> None:
    expected = _manifest(
        default_resolution=64,
        default_parallelism=4,
        style_references=("assets/minecraft/textures/block/stone.png",),
    )

    loaded, migrated = load_project_manifest(dump_project_manifest(expected))

    assert migrated is False
    assert loaded == expected


@pytest.mark.parametrize(
    "document",
    [
        [],
        {},
        {"schema_version": True},
        {"schema_version": "1"},
        {"schema_version": 0},
        {"schema_version": 3},
    ],
)
def test_load_rejects_non_object_missing_typed_or_unsupported_schema_versions(
    document: object,
) -> None:
    payload = json.dumps(document, separators=(",", ":")).encode("utf-8")

    with pytest.raises(ValueError):
        load_project_manifest(payload)


def test_load_rejects_json_coercion_and_extra_fields() -> None:
    document = _manifest().model_dump(mode="json")
    document["default_resolution"] = "16"
    with pytest.raises(ValidationError):
        load_project_manifest(_json_bytes(document))

    document = _manifest().model_dump(mode="json")
    document["unexpected"] = "rejected"
    with pytest.raises(ValidationError):
        load_project_manifest(_json_bytes(document))


def test_dump_is_sorted_compact_utf8_with_exactly_one_trailing_newline() -> None:
    manifest = _manifest(project_name="材质项目")
    payload = dump_project_manifest(manifest)
    text = payload.decode("utf-8")

    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    assert "\n" not in text[:-1]
    assert ": " not in text and ", " not in text
    assert text.index('"catalog_id"') < text.index('"created_at"')
    assert load_project_manifest(payload) == (manifest, False)


def test_project_summary_contains_only_stable_list_fields() -> None:
    manifest = _manifest()
    summary = ProjectSummary(
        project_id=manifest.project_id,
        project_name=manifest.project_name,
        edition=manifest.edition,
        java_pack_format=manifest.java_pack_format,
        catalog_id=manifest.catalog_id,
        created_at=manifest.created_at,
        updated_at=manifest.updated_at,
    )

    assert set(summary.model_dump()) == {
        "project_id",
        "project_name",
        "edition",
        "java_pack_format",
        "catalog_id",
        "created_at",
        "updated_at",
    }
