import io
import json
import os
import subprocess
import sys
import zipfile
from hashlib import sha256
from pathlib import Path

from PIL import Image


REPOSITORY_ROOT = Path(__file__).parents[3]
CATALOG_ROOT = REPOSITORY_ROOT / "catalogs" / "java"
CHILD_HELPER = Path(__file__).with_name("import_flow_child.py")
STONE_PATH = "assets/minecraft/textures/block/stone.png"


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
        "audit-spool-regression.bin": bytes(range(256)) * 4097,
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for member_name, payload in expected_members.items():
            archive.writestr(member_name, payload)
    return path.read_bytes(), expected_members


def _file_hashes_outside(root: Path, excluded: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_relative_to(excluded)
    }


def _run_child(
    project_root: Path,
    source: Path,
    *,
    outside_write_probe: Path | None = None,
    network_probe: bool = False,
) -> subprocess.CompletedProcess[str]:
    assert outside_write_probe is None or not network_probe
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    command = [
        sys.executable,
        "-B",
        "-W",
        "error",
        str(CHILD_HELPER),
        str(project_root),
        str(source),
        str(CATALOG_ROOT),
    ]
    if outside_write_probe is not None:
        command.append(str(outside_write_probe))
    elif network_probe:
        command.append("--probe-network")
    return subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def _assert_child_succeeded(
    completed: subprocess.CompletedProcess[str],
) -> dict[str, object]:
    assert completed.returncode == 0, (
        f"audited import child failed with exit {completed.returncode}\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    assert completed.stderr == ""
    return json.loads(completed.stdout)


def test_phase_1_import_flow_is_isolated_and_preserves_source(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "projects"
    project_root.mkdir()
    source = tmp_path / "synthetic-pack.zip"
    sentinel = tmp_path / "outside-project-root.txt"
    sentinel.write_text("must remain unchanged", encoding="utf-8")
    source_bytes, expected_members = _create_synthetic_pack(source)
    assert len(source_bytes) > 1024 * 1024
    source_hash = sha256(source_bytes).hexdigest()
    outside_before = _file_hashes_outside(tmp_path, project_root)

    child_result = _assert_child_succeeded(_run_child(project_root, source))
    assert child_result["isolation_policy_active"] is True
    assert child_result["dont_write_bytecode"] is True
    assert child_result["forbidden_modules_before"] == []
    assert child_result["forbidden_modules_after"] == []
    assert child_result["import_status"] == 201
    imported_body = child_result["import_body"]
    assert isinstance(imported_body, dict)
    assert child_result["coverage_status"] == 200
    coverage_body = child_result["coverage_body"]

    project_directory = project_root / str(imported_body["project_id"])
    manifest_path = project_directory / "project.json"
    snapshot_path = project_directory / "source" / "imported-pack.zip"
    working_pack = project_directory / "pack"
    persisted_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshot_bytes = snapshot_path.read_bytes()

    assert sha256(source.read_bytes()).hexdigest() == source_hash
    assert imported_body["source_sha256"] == source_hash
    assert persisted_manifest == imported_body
    assert persisted_manifest["source_sha256"] == source_hash
    assert persisted_manifest["java_pack_format"] == 34
    assert persisted_manifest["supported_formats"] is None
    assert persisted_manifest["catalog_id"] == "java-dev-format-34"
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
    assert [path.name for path in project_root.iterdir()] == [
        imported_body["project_id"]
    ]


def test_import_flow_audit_policy_blocks_an_outside_write(tmp_path: Path) -> None:
    project_root = tmp_path / "projects"
    project_root.mkdir()
    source = tmp_path / "synthetic-pack.zip"
    _create_synthetic_pack(source)
    outside_write = tmp_path / "transient-outside-write.txt"

    completed = _run_child(
        project_root,
        source,
        outside_write_probe=outside_write,
    )

    assert completed.returncode != 0
    assert "filesystem mutation outside allowed project root: open" in completed.stderr
    assert not outside_write.exists()


def test_import_flow_audit_policy_blocks_network_access(tmp_path: Path) -> None:
    project_root = tmp_path / "projects"
    project_root.mkdir()
    source = tmp_path / "synthetic-pack.zip"
    _create_synthetic_pack(source)

    completed = _run_child(project_root, source, network_probe=True)

    assert completed.returncode != 0
    assert "network audit event blocked: socket.connect" in completed.stderr
