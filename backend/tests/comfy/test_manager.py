"""RED/GREEN tests for ComfyUI manager status, start and stop."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from aimctexturegen.comfy.errors import (
    ManagerPortInUseError,
    ManagerStartError,
    PortInUseError,
    ProcessIdentityError,
)
from aimctexturegen.comfy.manager import ComfyUIManager, ReadinessProbe
from aimctexturegen.comfy.manifests import (
    ModelProfileManifest,
    RuntimeManifest,
)
from aimctexturegen.comfy.process import ProcessIdentity

from comfy._helpers import make_profile, make_runtime


class FakeProbe:
    def __init__(
        self,
        *,
        version: str = "0.29.2",
        node_classes: tuple[str, ...] = ("CheckpointLoaderSimple",),
    ) -> None:
        self.version = version
        self.node_classes = node_classes

    def system_stats(self) -> dict:
        return {"system": {"comfyui_version": self.version}}

    def object_info(self) -> dict:
        return {name: {} for name in self.node_classes}


class FakeChild:
    def __init__(self, pid: int, *, alive: bool = True) -> None:
        self.pid = pid
        self.alive = alive
        self.identity = ProcessIdentity(pid=pid, executable="fake.exe", creation_time_ns=7)
        self.stopped = False

    def poll(self) -> int | None:
        return None if self.alive else 3

    def stop(self, graceful_timeout: float) -> None:
        self.stopped = True
        self.alive = False


class FakeLauncher:
    def __init__(self, *, identity: ProcessIdentity | None = None) -> None:
        self.starts: list[dict[str, Any]] = []
        self.port_error: int | None = None
        self.owned_stops: list[int] = []
        self.identity = identity or ProcessIdentity(pid=42, executable="fake.exe", creation_time_ns=7)

    def start(self, **kwargs: Any) -> FakeChild:
        if self.port_error == kwargs.get("port"):
            raise PortInUseError("port in use")
        child = FakeChild(self.identity.pid)
        self.starts.append(kwargs)
        return child

    def identity_for(self, pid: int) -> ProcessIdentity:
        return self.identity

    def stop_owned(self, identity: ProcessIdentity, *, graceful_timeout: float) -> None:
        self.owned_stops.append(identity.pid)


def _manager(
    tmp_path: Path,
    *,
    launcher: FakeLauncher | None = None,
    probe: ReadinessProbe | None = None,
    runtime_check: Any = None,
    profile_ready: Any = None,
    alive_check: Any = None,
    readiness_timeout: float = 2.0,
) -> tuple[ComfyUIManager, FakeLauncher]:
    runtime = RuntimeManifest.model_validate(make_runtime())
    profile = ModelProfileManifest.model_validate(make_profile())
    fake_launcher = launcher or FakeLauncher()
    root = tmp_path / "runtime"
    (root / "state").mkdir(parents=True, exist_ok=True)
    import json

    (root / "state" / "selected-runtime.json").write_text(
        json.dumps({"directory": "0.29.2-abc12345"}),
        encoding="utf-8",
    )
    manager = ComfyUIManager(
        runtime_root=root,
        runtime=runtime,
        profile=profile,
        launcher=fake_launcher,  # type: ignore[arg-type]
        probe=probe or FakeProbe(),
        port=18193,
        stabilization_seconds=0.0,
        readiness_timeout=readiness_timeout,
        runtime_check=runtime_check or (lambda: "ready"),
        profile_ready=profile_ready or (lambda: True),
        alive_check=alive_check or (lambda pid: True),
    )
    return manager, fake_launcher


def test_status_is_stopped_without_a_record(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)
    assert manager.status().state == "stopped"


def test_start_records_process_and_is_single_flight(tmp_path: Path) -> None:
    manager, launcher = _manager(tmp_path)
    first = manager.start()
    second = manager.start()
    assert first.state == "ready"
    assert second.state == "ready"
    assert len(launcher.starts) == 1
    assert manager.status().state == "ready"
    assert (tmp_path / "runtime" / "state" / "process.json").is_file()


def test_readiness_waits_through_a_cold_start_probe_delay(
    tmp_path: Path,
) -> None:
    class DelayedProbe(FakeProbe):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def system_stats(self) -> dict:
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError("ComfyUI is still starting")
            return super().system_stats()

    manager, _ = _manager(
        tmp_path,
        probe=DelayedProbe(),
        readiness_timeout=0.5,
    )

    assert manager.start().state == "ready"


def test_new_manager_instance_stops_persisted_owned_process(
    tmp_path: Path,
) -> None:
    launcher = FakeLauncher()
    manager, _ = _manager(tmp_path, launcher=launcher)
    manager.start()
    restarted, _ = _manager(tmp_path, launcher=launcher)

    stopped = restarted.stop()

    assert stopped.state == "stopped"
    assert launcher.owned_stops == [42]
    assert not (tmp_path / "runtime" / "state" / "process.json").exists()


def test_start_passes_resolved_executable_and_managed_args(
    tmp_path: Path,
) -> None:
    manager, launcher = _manager(tmp_path)
    manager.start()
    arguments = launcher.starts[0]["arguments"]
    assert "--port" in arguments
    assert "18193" in arguments
    assert str(tmp_path / "runtime" / "state" / "extra_model_paths.yaml") in arguments
    assert launcher.starts[0]["port"] == 18193


def test_start_reports_port_in_use(tmp_path: Path) -> None:
    launcher = FakeLauncher()
    launcher.port_error = 18193
    manager, _ = _manager(tmp_path, launcher=launcher)
    with pytest.raises(ManagerPortInUseError, match="8188|18193"):
        manager.start()
    assert not (tmp_path / "runtime" / "state" / "process.json").exists()


def test_start_fails_on_wrong_version_and_cleans_record(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path, probe=FakeProbe(version="9.9.9"))
    with pytest.raises(ManagerStartError):
        manager.start()
    assert not (tmp_path / "runtime" / "state" / "process.json").exists()


def test_start_fails_on_missing_required_node_class(tmp_path: Path) -> None:
    manager, _ = _manager(
        tmp_path,
        probe=FakeProbe(node_classes=("WrongNode",)),
    )
    with pytest.raises(ManagerStartError):
        manager.start()
    assert not (tmp_path / "runtime" / "state" / "process.json").exists()


def test_start_fails_when_profile_is_not_ready(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path, profile_ready=lambda: False)
    with pytest.raises(ManagerStartError):
        manager.start()


def test_stale_record_is_recovered_to_stopped(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    (root / "state").mkdir(parents=True)
    import json

    (root / "state" / "process.json").write_text(
        json.dumps(
            {
                "pid": 999999,
                "executable": "gone.exe",
                "creation_time_ns": 1,
                "started_at": "2026-08-02T00:00:00Z",
                "profile_id": "p",
                "runtime_version": "0.29.2",
                "log_path": str(root / "logs" / "comfyui.log"),
                "port": 18193,
            }
        ),
        encoding="utf-8",
    )

    class DeadLauncher(FakeLauncher):
        def start(self, **kwargs: Any) -> FakeChild:
            raise AssertionError("must not start")

    manager, _ = _manager(
        tmp_path,
        launcher=DeadLauncher(),
        alive_check=lambda pid: False,
    )
    status = manager.status()
    assert status.state == "stopped"
    assert not (root / "state" / "process.json").exists()


def test_stop_only_stops_owned_identity(tmp_path: Path) -> None:
    manager, launcher = _manager(tmp_path)
    manager.start()
    launcher.identity = ProcessIdentity(
        pid=42,
        executable="other.exe",
        creation_time_ns=999,
    )
    with pytest.raises(ProcessIdentityError):
        manager.stop()


def test_status_fails_closed_on_persisted_pid_reuse(tmp_path: Path) -> None:
    manager, launcher = _manager(tmp_path)
    manager.start()
    launcher.identity = ProcessIdentity(
        pid=42,
        executable="other.exe",
        creation_time_ns=999,
    )

    status = manager.status()

    assert status.state == "unhealthy"
    assert status.errors == ("process_identity_mismatch",)


def test_start_refuses_to_adopt_reused_persisted_pid(tmp_path: Path) -> None:
    manager, launcher = _manager(tmp_path)
    manager.start()
    restarted, _ = _manager(tmp_path, launcher=launcher)
    launcher.identity = ProcessIdentity(
        pid=42,
        executable="other.exe",
        creation_time_ns=999,
    )

    with pytest.raises(ManagerStartError, match="identity"):
        restarted.start()


def test_stop_clears_record_and_child_is_stopped(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)
    manager.start()
    status = manager.stop()
    assert status.state == "stopped"
    assert not (tmp_path / "runtime" / "state" / "process.json").exists()
