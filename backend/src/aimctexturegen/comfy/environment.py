"""Read-only Windows/NVIDIA/disk inspection without mutation."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

from pydantic import ConfigDict, Field
from pydantic import BaseModel


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class NvidiaSmiResult(_StrictModel):
    gpu_name: str = Field(max_length=256)
    driver_version: str = Field(max_length=64)
    vram_mib: int = Field(ge=0)


class EnvironmentReport(_StrictModel):
    supported: bool
    platform: str = Field(max_length=32)
    architecture: str = Field(max_length=32)
    gpu_vendor: str | None = None
    gpu_name: str | None = Field(default=None, max_length=256)
    driver_version: str | None = Field(default=None, max_length=64)
    vram_bytes: int | None = None
    disk_free_bytes: int | None = None
    blocking_issues: tuple[str, ...] = ()


class EnvironmentProbe(Protocol):
    def os_name(self) -> str: ...

    def machine(self) -> str: ...

    def nvidia_smi(self) -> NvidiaSmiResult | None: ...

    def disk_free(self, path: Path) -> int | None: ...


class SystemEnvironmentProbe:
    """Default probe using platform, subprocess and disk APIs."""

    def os_name(self) -> str:
        return os.name

    def machine(self) -> str:
        return platform.machine()

    def nvidia_smi(self) -> NvidiaSmiResult | None:
        command = [
            "nvidia-smi",
            "--query-gpu=name,driver_version,memory.total",
            "--format=csv,noheader,nounits",
        ]
        creation_flags = 0
        if os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                shell=False,
                creationflags=creation_flags,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        first_line = (completed.stdout or "").strip().splitlines()
        if completed.returncode != 0 or not first_line:
            return None
        parts = [part.strip() for part in first_line[0].split(",")]
        if len(parts) != 3:
            return None
        name, driver, memory_text = parts
        try:
            vram_mib = int(memory_text)
        except ValueError:
            return None
        return NvidiaSmiResult(
            gpu_name=name[:256],
            driver_version=driver[:64],
            vram_mib=max(0, vram_mib),
        )

    def disk_free(self, path: Path) -> int | None:
        existing = path
        while not existing.exists():
            parent = existing.parent
            if parent == existing:
                return None
            existing = parent
        try:
            return shutil.disk_usage(existing).free
        except OSError:
            return None


class EnvironmentInspector:
    """Inspect host capabilities without creating files or directories."""

    def __init__(self, probe: EnvironmentProbe | None = None) -> None:
        self._probe = probe if probe is not None else SystemEnvironmentProbe()

    def inspect(self, runtime_root: Path) -> EnvironmentReport:
        os_name = self._probe.os_name()
        machine = self._probe.machine()
        architecture = _normalize_architecture(machine)
        smi = self._probe.nvidia_smi()
        disk_free = self._probe.disk_free(Path(runtime_root))

        issues: list[str] = []
        if os_name != "windows":
            issues.append("unsupported_os")
        if architecture != "x86_64":
            issues.append("unsupported_architecture")
        if smi is None:
            issues.append("nvidia_driver_unavailable")

        return EnvironmentReport(
            supported=not issues,
            platform=os_name,
            architecture=architecture,
            gpu_vendor="nvidia" if smi is not None else None,
            gpu_name=smi.gpu_name if smi is not None else None,
            driver_version=smi.driver_version if smi is not None else None,
            vram_bytes=(
                smi.vram_mib * 1024 * 1024 if smi is not None else None
            ),
            disk_free_bytes=disk_free,
            blocking_issues=tuple(issues),
        )


def _normalize_architecture(machine: str) -> str:
    normalized = machine.strip().lower()
    if normalized in {"amd64", "x86_64", "x64"}:
        return "x86_64"
    return normalized[:32] or "unknown"
