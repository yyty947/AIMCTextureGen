"""Owned ComfyUI child-process and readiness orchestration."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import ConfigDict, Field
from pydantic import BaseModel

from aimctexturegen.comfy.errors import (
    ManagerStartError,
    ProcessError,
    ProcessIdentityError,
    ReadinessError,
)
from aimctexturegen.comfy.installer import (
    ProfileInstaller,
    RuntimeInstaller,
)
from aimctexturegen.comfy.manifests import (
    ModelProfileManifest,
    RuntimeManifest,
)
from aimctexturegen.comfy.process import (
    OwnedProcess,
    ProcessLauncher,
    ProcessIdentity,
    _pid_alive,
)
from aimctexturegen.core.atomic_files import atomic_replace_bytes


class ReadinessProbe(Protocol):
    def system_stats(self) -> dict: ...

    def object_info(self) -> dict: ...


_ENV_ALLOWLIST = frozenset(
    {"PATH", "SYSTEMROOT", "TEMP", "TMP", "COMSPEC", "OPENBLAS_NUM_THREADS"}
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ManagerProcessStatus(_StrictModel):
    state: Literal["stopped", "starting", "ready", "unhealthy"]
    pid: int | None = None
    version: str | None = None
    errors: tuple[str, ...] = ()


class ComfyUIManager:
    """Start, verify and stop one managed loopback ComfyUI child."""

    def __init__(
        self,
        *,
        runtime_root: Path,
        runtime: RuntimeManifest,
        profile: ModelProfileManifest,
        launcher: ProcessLauncher,
        probe: ReadinessProbe,
        port: int = 8188,
        stabilization_seconds: float = 0.2,
        readiness_timeout: float = 5.0,
        runtime_check: Callable[[], str] | None = None,
        profile_ready: Callable[[], bool] | None = None,
        alive_check: Callable[[int], bool] | None = None,
    ) -> None:
        self._root = Path(runtime_root)
        self._runtime = runtime
        self._profile = profile
        self._launcher = launcher
        self._probe = probe
        self._port = port
        self._stabilization_seconds = stabilization_seconds
        self._readiness_timeout = readiness_timeout
        self._runtime_check = runtime_check or (
            lambda: RuntimeInstaller().status(self._runtime, self._root).state
        )
        self._profile_ready = profile_ready or (
            lambda: ProfileInstaller().status(self._profile, self._root).ready
        )
        self._alive_check = alive_check or _pid_alive
        self._child: OwnedProcess | None = None
        self._lock = threading.Lock()

    def status(self) -> ManagerProcessStatus:
        record = self._read_record()
        if record is None:
            return ManagerProcessStatus(state="stopped")
        if not self._alive_check(int(record["pid"])):
            self._delete_record()
            return ManagerProcessStatus(state="stopped")
        errors: list[str] = []
        version: str | None = None
        try:
            stats = self._probe.system_stats()
            version = (
                stats.get("system", {}).get("comfyui_version")
                if isinstance(stats.get("system"), dict)
                else None
            )
            if version != self._runtime.expected_runtime_identity:
                errors.append("wrong_runtime_version")
            present = set(self._probe.object_info())
            missing = set(self._profile.required_node_classes) - present
            if missing:
                errors.append("missing_profile_nodes")
        except Exception:
            errors.append("probe_unreachable")
        return ManagerProcessStatus(
            state="ready" if not errors else "unhealthy",
            pid=int(record["pid"]),
            version=version,
            errors=tuple(errors),
        )

    def start(self) -> ManagerProcessStatus:
        with self._lock:
            record = self._read_record()
            if record is not None and self._alive_check(int(record["pid"])):
                return self.status()
            if self._runtime_check() != "ready":
                raise ManagerStartError(
                    "runtime is not installed and verified"
                )
            if not self._profile_ready():
                raise ManagerStartError(
                    "model profile is not fully installed"
                )
            extra_model_paths = self._ensure_extra_model_paths()
            executable, runtime_cwd = self._resolved_executable()
            arguments = [
                str(argument)
                .replace("<port>", str(self._port))
                .replace(
                    "<extra-model-paths-config>",
                    str(extra_model_paths),
                )
                for argument in self._runtime.startup_argument_template[1:]
            ]
            try:
                child = self._launcher.start(
                    executable=executable,
                    arguments=arguments,
                    cwd=runtime_cwd,
                    env=_bounded_environment(),
                    log_path=self._root / "logs" / "comfyui.log",
                    port=self._port,
                )
            except ProcessError as exc:
                raise ManagerStartError(str(exc)) from exc
            self._child = child
            try:
                self._wait_ready(child)
            except ReadinessError as exc:
                try:
                    child.stop(graceful_timeout=2.0)
                except Exception:
                    pass
                self._child = None
                raise ManagerStartError(str(exc)) from exc
            self._write_record(child)
            return self.status()

    def stop(self) -> ManagerProcessStatus:
        with self._lock:
            record = self._read_record()
            if record is None:
                return ManagerProcessStatus(state="stopped")
            child = self._child
            if child is None:
                self._delete_record()
                return ManagerProcessStatus(state="stopped")
            current = self._launcher.identity_for(int(record["pid"]))
            expected = ProcessIdentity(
                pid=int(record["pid"]),
                executable=str(record["executable"]),
                creation_time_ns=int(record["creation_time_ns"]),
            )
            if (
                current.pid != expected.pid
                or current.creation_time_ns != expected.creation_time_ns
                or current.executable.casefold()
                != expected.executable.casefold()
            ):
                raise ProcessIdentityError(
                    "live process identity no longer matches the owned record"
                )
            child.stop(graceful_timeout=5.0)
            self._child = None
            self._delete_record()
            return ManagerProcessStatus(state="stopped")

    def shutdown(self) -> ManagerProcessStatus:
        return self.stop()

    def _wait_ready(self, child: OwnedProcess) -> None:
        deadline = time.monotonic() + self._readiness_timeout
        stable_since: float | None = None
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise ReadinessError("managed child exited during startup")
            try:
                stats = self._probe.system_stats()
                object_info = self._probe.object_info()
            except Exception as exc:
                stable_since = None
                time.sleep(0.05)
                continue
            version = (
                stats.get("system", {}).get("comfyui_version")
                if isinstance(stats.get("system"), dict)
                else None
            )
            required = set(self._profile.required_node_classes)
            if (
                version == self._runtime.expected_runtime_identity
                and required.issubset(set(object_info))
            ):
                now = time.monotonic()
                if stable_since is None:
                    stable_since = now
                elif now - stable_since >= self._stabilization_seconds:
                    return
            else:
                stable_since = None
            time.sleep(0.05)
        raise ReadinessError("managed child did not become ready in time")

    def _resolved_executable(self) -> tuple[str, Path]:
        selection = self._read_json(
            self._root / "state" / "selected-runtime.json"
        )
        if selection is None or not selection.get("directory"):
            raise ManagerStartError("runtime is not installed")
        template_root = self._runtime.startup_argument_template[0]
        runtime_cwd = (
            self._root
            / "comfyui"
            / str(selection["directory"])
            / self._runtime.expected_archive_root
        )
        return str(runtime_cwd / template_root), runtime_cwd

    def _ensure_extra_model_paths(self) -> Path:
        path = self._root / "state" / "extra_model_paths.yaml"
        if path.is_file():
            return path
        return ProfileInstaller().write_extra_model_paths(self._profile, self._root)

    def _write_record(self, child: OwnedProcess) -> None:
        payload = {
            "pid": child.pid,
            "executable": child.identity.executable,
            "creation_time_ns": child.identity.creation_time_ns,
            "started_at": datetime.now(UTC).isoformat(),
            "profile_id": self._profile.profile_id,
            "runtime_version": self._runtime.runtime_version,
            "log_path": str(self._root / "logs" / "comfyui.log"),
            "port": self._port,
        }
        path = self._root / "state" / "process.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        atomic_replace_bytes(
            path,
            encoded,
            validator=lambda readback: json.loads(readback),
        )

    def _read_record(self) -> dict | None:
        return self._read_json(self._root / "state" / "process.json")

    def _delete_record(self) -> None:
        try:
            (self._root / "state" / "process.json").unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None


def _bounded_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key in _ENV_ALLOWLIST
    }
