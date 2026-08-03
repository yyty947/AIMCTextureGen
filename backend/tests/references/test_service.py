import hashlib
import io
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image

from aimctexturegen.catalog.registry import CatalogRegistry
from aimctexturegen.jobs.store import JobInputSnapshot
from aimctexturegen.projects.models import ProjectManifest, dump_project_manifest
from aimctexturegen.projects.repository import ProjectRepository
from aimctexturegen.references.models import (
    PackReferenceSelection,
    ReferenceSelections,
    UploadReferenceSelection,
)
from aimctexturegen.references.service import ReferenceService
from aimctexturegen.references.store import ProjectReferenceStore
from aimctexturegen.references.validation import validate_reference_png


NOW = datetime(2026, 8, 3, 10, 30, tzinfo=timezone.utc)
PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
CATALOG_ROOT = Path(__file__).parents[3] / "catalogs" / "java"


def _png(path: Path, *, mode: str = "RGB", size: tuple[int, int] = (16, 16), color=128) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = io.BytesIO()
    Image.new(mode, size, color).save(payload, format="PNG")
    data = payload.getvalue()
    path.write_bytes(data)
    return data


def _manifest() -> ProjectManifest:
    return ProjectManifest(
        schema_version=2,
        project_id=PROJECT_ID,
        project_name="Reference service project",
        edition="java",
        java_pack_format=34,
        supported_formats=None,
        catalog_id="java-dev-format-34",
        source_sha256="ab" * 32,
        created_at=NOW,
        updated_at=NOW,
        default_resolution=16,
        default_parallelism=1,
        style_references=(),
    )


def _write_project(projects_root: Path) -> Path:
    project_root = projects_root / str(PROJECT_ID)
    (project_root / "source").mkdir(parents=True)
    pack_root = project_root / "pack"
    pack_root.mkdir()
    (project_root / "project.json").write_bytes(dump_project_manifest(_manifest()))
    _png(pack_root / "assets/minecraft/textures/block/stone.png")
    _png(pack_root / "assets/minecraft/textures/block/custom_ref.png")
    _png(pack_root / "assets/example/textures/block/ignored_rect.png", size=(16, 32))
    return project_root


def _service(projects_root: Path) -> ReferenceService:
    return ReferenceService(
        repository=ProjectRepository(projects_root),
        catalogs=CatalogRegistry(CATALOG_ROOT),
        store=ProjectReferenceStore(ProjectRepository(projects_root)),
    )


def _create_junction(link: Path, target: Path) -> None:
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"Unable to create test junction: {result.stdout}{result.stderr}")


def _remove_junction(link: Path) -> None:
    if os.path.lexists(link):
        os.rmdir(link)


def test_list_pack_references_returns_catalog_covered_and_unknown_square_pngs(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)

    references = _service(projects_root).list_pack_references(PROJECT_ID)

    assert [reference.relative_path for reference in references] == [
        "assets/minecraft/textures/block/custom_ref.png",
        "assets/minecraft/textures/block/stone.png",
    ]
    assert all(reference.byte_size > 0 for reference in references)
    assert {reference.source for reference in references} == {"pack"}


def test_list_pack_references_skips_square_pngs_with_invalid_modes(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root)
    _png(
        project_root / "pack" / "assets/example/textures/block/grayscale.png",
        mode="L",
        color=128,
    )

    references = _service(projects_root).list_pack_references(PROJECT_ID)

    assert [reference.relative_path for reference in references] == [
        "assets/minecraft/textures/block/custom_ref.png",
        "assets/minecraft/textures/block/stone.png",
    ]


def test_upload_list_and_freeze_resolve_pack_and_upload_inputs_into_job_snapshot(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root)
    service = _service(projects_root)
    style_upload_payload = _png(tmp_path / "style.png")
    structure_upload_payload = _png(tmp_path / "structure.png", color=64)
    style_upload = service.upload(PROJECT_ID, "style", style_upload_payload)
    structure_upload = service.upload(PROJECT_ID, "structure", structure_upload_payload)

    snapshot = service.freeze(
        PROJECT_ID,
        ReferenceSelections(
            style=(
                PackReferenceSelection(
                    source="pack",
                    relative_path="assets/minecraft/textures/block/stone.png",
                ),
                UploadReferenceSelection(source="upload", reference_id=style_upload.reference_id),
            ),
            structure=UploadReferenceSelection(
                source="upload",
                reference_id=structure_upload.reference_id,
            ),
        ),
    )

    assert isinstance(snapshot, JobInputSnapshot)
    assert [item.relative_path for item in snapshot.files] == [
        "style/00.png",
        "style/01.png",
        "structure.png",
    ]
    payloads = {item.relative_path: item.payload for item in snapshot.files}
    assert payloads["style/00.png"] == (project_root / "pack" / "assets/minecraft/textures/block/stone.png").read_bytes()
    assert payloads["style/01.png"] == style_upload_payload
    assert payloads["structure.png"] == structure_upload_payload
    metadata = json.loads(snapshot.references_json)
    assert [item["reference_id"] for item in metadata["style"]] == ["style-00", "style-01"]
    assert metadata["style"][0]["source"] == "pack"
    assert metadata["style"][1]["source"] == "upload"
    assert metadata["structure"][0]["reference_id"] == "structure-00"
    assert all(not str(value).startswith("C:\\") for item in metadata["style"] + metadata["structure"] for value in item.values() if isinstance(value, str))


def test_freeze_revalidates_selected_bytes_and_rejects_reparse_ancestor(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root)
    service = _service(projects_root)
    outside = tmp_path / "outside"
    outside.mkdir()
    target = project_root / "pack" / "assets" / "minecraft"
    displaced = project_root / "pack" / "assets" / "minecraft-real"
    target.rename(displaced)
    _create_junction(target, outside)

    try:
        with pytest.raises(Exception):
            service.freeze(
                PROJECT_ID,
                ReferenceSelections(
                    style=(
                        PackReferenceSelection(
                            source="pack",
                            relative_path="assets/minecraft/textures/block/stone.png",
                        ),
                    ),
                    structure=None,
                ),
            )
    finally:
        _remove_junction(target)
        displaced.rename(target)


def test_upload_reuses_shared_validator_contract(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    payload = _png(tmp_path / "rgba.png", mode="RGBA", size=(32, 32))
    validated = validate_reference_png(payload)

    stored = _service(projects_root).upload(PROJECT_ID, "style", payload)

    assert stored.sha256 == hashlib.sha256(payload).hexdigest()
    assert stored.width == validated.width
    assert stored.height == validated.height
    assert stored.mode == validated.mode


def test_freeze_preserves_pack_reference_display_label(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    service = _service(projects_root)
    listed = next(
        reference
        for reference in service.list_pack_references(PROJECT_ID)
        if reference.relative_path == "assets/minecraft/textures/block/stone.png"
    )

    snapshot = service.freeze(
        PROJECT_ID,
        ReferenceSelections(
            style=(
                PackReferenceSelection(
                    source="pack",
                    relative_path=listed.relative_path,
                ),
            ),
            structure=None,
        ),
    )

    metadata = json.loads(snapshot.references_json)
    assert metadata["style"][0]["display_label"] == listed.display_name
