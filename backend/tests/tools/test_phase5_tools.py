"""Deterministic audits for the Phase 5 PowerShell qualification tools."""

from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
PREPARE_SCRIPT = REPO_ROOT / "tools" / "Prepare-Phase5ManualPack.ps1"
SMOKE_SCRIPT = REPO_ROOT / "tools" / "Invoke-Phase5Smoke.ps1"


def _powershell() -> str:
    command = "Get-Command powershell.exe -ErrorAction SilentlyContinue"
    result = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("Windows PowerShell is unavailable")
    return "powershell.exe"


def _write_pack(path: Path) -> None:
    members = {
        "pack.mcmeta": json.dumps(
            {"pack": {"pack_format": 34, "description": "synthetic"}},
            separators=(",", ":"),
        ).encode("utf-8"),
        "assets/minecraft/textures/block/deepslate.png": b"root-deepslate",
        "assets/minecraft/textures/block/stone.png": b"root-stone",
        "overlays/alt/pack.mcmeta": b'{"pack":{"pack_format":34}}',
        "overlays/alt/assets/minecraft/textures/block/deepslate.png": b"overlay-deepslate",
        "nested/project-owned.txt": b"synthetic-nested",
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, payload in members.items():
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            archive.writestr(info, payload)


def _write_pack_without_primary_format(path: Path) -> None:
    members = {
        "pack.mcmeta": b'{"pack":{"description":"synthetic"}}',
        "assets/minecraft/textures/block/deepslate.png": b"root-deepslate",
        "assets/minecraft/textures/block/stone.png": b"root-stone",
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, payload in members.items():
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            archive.writestr(info, payload)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_prepare_phase5_pack_is_deterministic_and_preserves_source(tmp_path: Path) -> None:
    powershell = _powershell()
    source = tmp_path / "source.zip"
    output = tmp_path / "derived.zip"
    _write_pack(source)
    before = _sha256(source)

    command = [
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(PREPARE_SCRIPT),
        "-SourceZip",
        str(source),
        "-OutputZip",
        str(output),
    ]
    first = subprocess.run(command, capture_output=True, text=True, check=False)
    assert first.returncode == 0, first.stdout + first.stderr
    first_bytes = output.read_bytes()

    second = subprocess.run(command, capture_output=True, text=True, check=False)
    assert second.returncode == 0, second.stdout + second.stderr
    assert output.read_bytes() == first_bytes
    assert _sha256(source) == before

    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
    assert "assets/minecraft/textures/block/deepslate.png" not in names
    assert "assets/minecraft/textures/block/stone.png" in names
    assert "overlays/alt/assets/minecraft/textures/block/deepslate.png" in names
    assert len(names) == 5


def test_prepare_phase5_pack_rejects_overwriting_the_source(tmp_path: Path) -> None:
    powershell = _powershell()
    source = tmp_path / "source.zip"
    _write_pack(source)
    result = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PREPARE_SCRIPT),
            "-SourceZip",
            str(source),
            "-OutputZip",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "source" in (result.stdout + result.stderr).casefold()


def test_prepare_phase5_pack_rejects_missing_primary_format_without_guessing(
    tmp_path: Path,
) -> None:
    powershell = _powershell()
    source = tmp_path / "missing-primary-format.zip"
    output = tmp_path / "derived.zip"
    _write_pack_without_primary_format(source)

    result = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PREPARE_SCRIPT),
            "-SourceZip",
            str(source),
            "-OutputZip",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "missing primary pack_format" in (
        result.stdout + result.stderr
    ).casefold()


def test_phase5_smoke_entry_contract_is_explicit_and_manifest_immutable() -> None:
    script = SMOKE_SCRIPT.read_text(encoding="utf-8")
    assert ".venv\\Scripts\\python.exe" in script
    assert "git check-ignore" in script
    assert "Get-NetTCPConnection" in script
    assert "run_smoke_from_env" in script
    assert "PHASE5_SMOKE_COMPLETED" in script
    assert "PHASE5_SMOKE_FAILED" in script
    assert "support_state" not in script
    assert "sdxl-mapchip-ipadapter-v2.json" not in script
