import asyncio
import io
import json
import socket
import sys
import zipfile
from hashlib import sha256
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from aimctexturegen.main import create_app


REPOSITORY_ROOT = Path(__file__).parents[3]
CATALOG_ROOT = REPOSITORY_ROOT / "catalogs" / "java"
STONE_PATH = "assets/minecraft/textures/block/stone.png"
FORBIDDEN_RUNTIME_PREFIXES = (
    "comfy",
    "cuda",
    "diffusers",
    "huggingface_hub",
    "nvidia",
    "torch",
    "transformers",
)


def _png_bytes() -> bytes:
    payload = io.BytesIO()
    Image.new("RGB", (2, 2), (64, 64, 64)).save(payload, format="PNG")
    return payload.getvalue()


def _create_synthetic_pack(path: Path) -> tuple[bytes, dict[str, bytes]]:
    metadata = json.dumps(
        {
            "pack": {
                "pack_format": 34,
                "description": "AIMCTextureGen Phase 1 synthetic pack",
            }
        },
        separators=(",", ":"),
    ).encode("utf-8")
    expected_members = {
        "pack.mcmeta": metadata,
        STONE_PATH: _png_bytes(),
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member_name, payload in expected_members.items():
            archive.writestr(member_name, payload)
    return path.read_bytes(), expected_members


def _file_hashes_outside(root: Path, excluded: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_relative_to(excluded)
    }


def _loaded_forbidden_runtime_modules() -> set[str]:
    return {
        module_name
        for module_name in sys.modules
        if module_name.split(".", 1)[0].casefold() in FORBIDDEN_RUNTIME_PREFIXES
    }


async def _call_import_flow(
    app: FastAPI,
    source_name: str,
    source_bytes: bytes,
) -> tuple[httpx.Response, httpx.Response]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        imported = await client.post(
            "/api/projects/import",
            data={"project_name": "Phase 1 Synthetic Pack"},
            files={"pack": (source_name, source_bytes, "application/zip")},
        )
        if imported.status_code != 201:
            return imported, imported
        coverage = await client.get(
            f"/api/projects/{imported.json()['project_id']}/coverage"
        )
        return imported, coverage


def test_phase_1_import_flow_is_isolated_and_preserves_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "projects"
    source = tmp_path / "synthetic-pack.zip"
    sentinel = tmp_path / "outside-project-root.txt"
    sentinel.write_text("must remain unchanged", encoding="utf-8")
    source_bytes, expected_members = _create_synthetic_pack(source)
    source_hash = sha256(source_bytes).hexdigest()
    outside_before = _file_hashes_outside(tmp_path, project_root)
    runtime_modules_before = _loaded_forbidden_runtime_modules()

    def reject_network(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Phase 1 import flow attempted an external network connection")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    app = create_app(project_root=project_root, catalog_root=CATALOG_ROOT)

    imported, coverage = asyncio.run(
        _call_import_flow(app, source.name, source_bytes)
    )
    assert imported.status_code == 201
    imported_body = imported.json()
    assert coverage.status_code == 200
    coverage_body = coverage.json()

    project_directory = project_root / imported_body["project_id"]
    manifest_path = project_directory / "project.json"
    snapshot_path = project_directory / "source" / "imported-pack.zip"
    working_pack = project_directory / "pack"
    persisted_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshot_bytes = snapshot_path.read_bytes()

    assert sha256(source.read_bytes()).hexdigest() == source_hash
    assert imported_body["source_sha256"] == source_hash
    assert persisted_manifest == imported_body
    assert persisted_manifest["source_sha256"] == source_hash
    assert sha256(snapshot_bytes).hexdigest() == source_hash
    assert snapshot_bytes == source_bytes

    with zipfile.ZipFile(snapshot_path) as snapshot:
        assert set(snapshot.namelist()) == set(expected_members)
        assert {
            member: snapshot.read(member) for member in sorted(snapshot.namelist())
        } == expected_members

    working_files = {
        path.relative_to(working_pack).as_posix(): path.read_bytes()
        for path in sorted(working_pack.rglob("*"))
        if path.is_file()
    }
    assert working_files == expected_members
    assert (working_pack / "pack.mcmeta").is_file()
    assert (working_pack / STONE_PATH).is_file()

    assert coverage_body == {
        "catalog_id": "java-dev-format-34",
        "catalog_status": "development_fixture",
        "covered_count": 1,
        "missing_count": 1,
        "unknown_paths": [],
        "items": [
            {
                "semantic_id": "minecraft:stone",
                "display_name": "Stone",
                "relative_path": STONE_PATH,
                "mvp_eligible": True,
                "status": "covered",
            },
            {
                "semantic_id": "minecraft:deepslate",
                "display_name": "Deepslate",
                "relative_path": "assets/minecraft/textures/block/deepslate.png",
                "mvp_eligible": True,
                "status": "missing",
            },
        ],
    }
    assert _file_hashes_outside(tmp_path, project_root) == outside_before
    assert _loaded_forbidden_runtime_modules() == runtime_modules_before
    assert [path.name for path in project_root.iterdir()] == [
        imported_body["project_id"]
    ]
