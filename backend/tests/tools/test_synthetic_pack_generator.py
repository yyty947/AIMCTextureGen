from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

from PIL import Image


REPOSITORY_ROOT = Path(__file__).parents[3]
GENERATOR = REPOSITORY_ROOT / "tools" / "Generate-SyntheticPack.ps1"
EXPECTED_MEMBERS = (
    "pack.mcmeta",
    "assets/minecraft/textures/block/stone.png",
)
EXPECTED_DEFAULT_SHA256 = (
    "8ec378c876fe12b17e784c2d03ee59e7ea8a6c1601d7bf00e0a36980e2d24478"
)


def _generate(
    output_path: Path,
    *,
    phase5: bool = False,
) -> subprocess.CompletedProcess[str]:
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    assert powershell is not None, "Phase 3 fixture generation requires PowerShell"
    command = [
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(GENERATOR),
        "-OutputPath",
        str(output_path),
    ]
    if phase5:
        command.append("-Phase5")
    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return completed


def test_powershell_generator_builds_one_deterministic_synthetic_pack(
    tmp_path: Path,
) -> None:
    first = tmp_path / "generated fixtures" / "first.zip"
    second = tmp_path / "generated fixtures" / "second.zip"

    first_run = _generate(first)
    second_run = _generate(second)

    first_bytes = first.read_bytes()
    assert second.read_bytes() == first_bytes
    repeated_run = _generate(first)
    assert first.read_bytes() == first_bytes
    digest = hashlib.sha256(first_bytes).hexdigest()
    assert digest == EXPECTED_DEFAULT_SHA256
    assert f"SHA256={digest}" in first_run.stdout
    assert f"SHA256={digest}" in second_run.stdout
    assert f"SHA256={digest}" in repeated_run.stdout
    assert "COVERAGE=pack_format=34;covered=1;missing=1;unknown=0" in (
        first_run.stdout
    )

    with zipfile.ZipFile(first) as archive:
        assert tuple(info.filename for info in archive.infolist()) == (
            EXPECTED_MEMBERS
        )
        assert all(
            info.date_time == (1980, 1, 1, 0, 0, 0)
            for info in archive.infolist()
        )
        metadata = json.loads(archive.read("pack.mcmeta"))
        assert metadata == {
            "pack": {
                "pack_format": 34,
                "description": "AIMCTextureGen synthetic Phase 3 test pack",
            }
        }
        with archive.open(EXPECTED_MEMBERS[1]) as texture:
            with Image.open(texture) as image:
                assert image.size == (2, 2)
                assert image.mode == "RGB"
                assert set(image.get_flattened_data()) == {(64, 96, 128)}


def test_phase5_mode_is_opt_in_and_contains_only_project_generated_rgb_images(
    tmp_path: Path,
) -> None:
    output = tmp_path / "phase5.zip"
    completed = _generate(output, phase5=True)

    with zipfile.ZipFile(output) as archive:
        members = tuple(info.filename for info in archive.infolist())
        assert members == (
            "pack.mcmeta",
            "assets/minecraft/textures/block/stone.png",
            "assets/minecraft/textures/block/custom_unknown.png",
        )
        for member in members[1:]:
            with archive.open(member) as texture:
                with Image.open(texture) as image:
                    assert image.mode == "RGB"
                    assert image.size == (16, 16)
                    image.load()
        assert "COVERAGE=pack_format=34;covered=1;missing=2;unknown=1" in (
            completed.stdout
        )

    script = GENERATOR.read_text(encoding="utf-8")
    assert "runtime/manual-test-packs" not in script
    assert "Get-Content" not in script
    assert "ReadAllBytes" not in script
