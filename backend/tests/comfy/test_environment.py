"""RED/GREEN tests for read-only host environment inspection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aimctexturegen.comfy.environment import (
    EnvironmentInspector,
    EnvironmentReport,
    EnvironmentProbe,
    NvidiaSmiResult,
)


class FakeProbe:
    def __init__(
        self,
        *,
        os_name: str = "windows",
        machine: str = "AMD64",
        smi: NvidiaSmiResult | None = NvidiaSmiResult(
            gpu_name="NVIDIA GeForce RTX 4060",
            driver_version="552.44",
            vram_mib=8192,
        ),
        disk_free: int | None = 2_000_000_000_000,
    ) -> None:
        self._os_name = os_name
        self._machine = machine
        self._smi = smi
        self._disk_free = disk_free
        self.calls: list[str] = []

    def os_name(self) -> str:
        self.calls.append("os_name")
        return self._os_name

    def machine(self) -> str:
        self.calls.append("machine")
        return self._machine

    def nvidia_smi(self) -> NvidiaSmiResult | None:
        self.calls.append("nvidia_smi")
        return self._smi

    def disk_free(self, path: Path) -> int | None:
        self.calls.append("disk_free")
        return self._disk_free


def _report(**kwargs) -> EnvironmentReport:
    return EnvironmentInspector(FakeProbe(**kwargs)).inspect(
        Path("C:/runtime")
    )


def test_supported_windows_x64_nvidia_report() -> None:
    report = _report()
    assert report.supported is True
    assert report.platform == "windows"
    assert report.architecture == "x86_64"
    assert report.gpu_vendor == "nvidia"
    assert report.gpu_name == "NVIDIA GeForce RTX 4060"
    assert report.driver_version == "552.44"
    assert report.vram_bytes == 8192 * 1024 * 1024
    assert report.disk_free_bytes == 2_000_000_000_000
    assert report.blocking_issues == ()


def test_windows_os_name_from_python_is_normalized() -> None:
    report = _report(os_name="nt")
    assert report.supported is True
    assert report.platform == "windows"
    assert "unsupported_os" not in report.blocking_issues


def test_non_windows_is_unsupported() -> None:
    report = _report(os_name="posix")
    assert report.supported is False
    assert "unsupported_os" in report.blocking_issues


def test_non_x64_architecture_is_unsupported() -> None:
    report = _report(machine="ARM64")
    assert report.supported is False
    assert "unsupported_architecture" in report.blocking_issues


def test_missing_nvidia_smi_is_unsupported() -> None:
    report = _report(smi=None)
    assert report.supported is False
    assert report.gpu_vendor is None
    assert "nvidia_driver_unavailable" in report.blocking_issues


def test_gpu_strings_are_rejected_when_unbounded() -> None:
    with pytest.raises(ValidationError):
        NvidiaSmiResult(
            gpu_name="X" * 5000,
            driver_version="Y" * 5000,
            vram_mib=4096,
        )


def test_report_never_contains_absolute_paths() -> None:
    serialized = json.dumps(_report().model_dump(mode="json"))
    assert "C:" not in serialized
    assert "\\" not in serialized
    assert serialized.count("/") == 0


def test_inspect_creates_no_directories_and_uses_injected_probe(
    tmp_path: Path,
) -> None:
    probe = FakeProbe()
    runtime_root = tmp_path / "missing" / "runtime"
    inspector = EnvironmentInspector(probe)
    report = inspector.inspect(runtime_root)
    assert report.supported is True
    assert not runtime_root.exists()
    assert probe.calls == ["os_name", "machine", "nvidia_smi", "disk_free"]


def test_disk_free_may_be_unknown() -> None:
    report = _report(disk_free=None)
    assert report.disk_free_bytes is None
