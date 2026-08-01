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


def _generate(output_path: Path) -> subprocess.CompletedProcess[str]:
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    assert powershell is not None, "Phase 3 fixture generation requires PowerShell"
    completed = subprocess.run(
        [
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
        ],
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
    digest = hashlib.sha256(first_bytes).hexdigest()
    assert f"SHA256={digest}" in first_run.stdout
    assert f"SHA256={digest}" in second_run.stdout
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
