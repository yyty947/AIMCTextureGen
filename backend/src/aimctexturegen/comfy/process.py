"""Owned Windows child-process lifecycle with identity checks."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ConfigDict, Field
from pydantic import BaseModel

from aimctexturegen.comfy.errors import (
    PortInUseError,
    ProcessIdentityError,
    ProcessStartError,
    ProcessStopError,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ProcessIdentity(_StrictModel):
    pid: int = Field(ge=0)
    executable: str = Field(min_length=1)
    creation_time_ns: int = Field(ge=0)


class OwnedProcess:
    def __init__(
        self,
        process: subprocess.Popen,
        identity: ProcessIdentity,
        *,
        identity_provider: Callable[[int], ProcessIdentity],
    ) -> None:
        self._process = process
        self._identity = identity
        self._identity_provider = identity_provider

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def identity(self) -> ProcessIdentity:
        return self._identity

    def poll(self) -> int | None:
        return self._process.poll()

    def stop(self, graceful_timeout: float) -> None:
        current = self._identity_provider(self._process.pid)
        if (
            current.pid != self._identity.pid
            or current.creation_time_ns != self._identity.creation_time_ns
            or current.executable.casefold()
            != self._identity.executable.casefold()
        ):
            raise ProcessIdentityError(
                "live process identity no longer matches the owned record"
            )
        if self._process.poll() is not None:
            return
        try:
            self._process.terminate()
            self._process.wait(timeout=graceful_timeout)
        except subprocess.TimeoutExpired as exc:
            self._process.kill()
            try:
                self._process.wait(timeout=5.0)
            except subprocess.TimeoutExpired as force_exc:
                raise ProcessStopError(
                    "owned child did not exit after force termination"
                ) from force_exc
        except OSError as exc:
            raise ProcessStopError("cannot stop owned child") from exc


def _default_spawner(arguments: list[str], **kwargs: Any) -> subprocess.Popen:
    return subprocess.Popen(arguments, **kwargs)


class ProcessLauncher:
    """Launch loopback children with hidden windows and identity records."""

    def __init__(
        self,
        *,
        spawner: Callable[..., subprocess.Popen] | None = None,
        identity_provider: Callable[[int], ProcessIdentity] | None = None,
    ) -> None:
        self._spawner = spawner or _default_spawner
        self._identity_provider = identity_provider or windows_process_identity

    def identity_for(self, pid: int) -> ProcessIdentity:
        return self._identity_provider(pid)

    def stop_owned(
        self,
        identity: ProcessIdentity,
        *,
        graceful_timeout: float,
    ) -> None:
        """Stop a recorded child after revalidating its process identity."""

        current = self.identity_for(identity.pid)
        if (
            current.pid != identity.pid
            or current.creation_time_ns != identity.creation_time_ns
            or current.executable.casefold() != identity.executable.casefold()
        ):
            raise ProcessIdentityError(
                "live process identity no longer matches the owned record"
            )
        if not _pid_alive(identity.pid):
            return
        try:
            os.kill(identity.pid, signal.SIGTERM)
        except OSError as exc:
            if _pid_alive(identity.pid):
                raise ProcessStopError("cannot stop owned child") from exc
            return
        deadline = time.monotonic() + graceful_timeout
        while _pid_alive(identity.pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        if not _pid_alive(identity.pid):
            return
        current = self.identity_for(identity.pid)
        if (
            current.pid != identity.pid
            or current.creation_time_ns != identity.creation_time_ns
            or current.executable.casefold() != identity.executable.casefold()
        ):
            raise ProcessIdentityError(
                "live process identity no longer matches the owned record"
            )
        try:
            force_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
            os.kill(identity.pid, force_signal)
        except OSError as exc:
            if _pid_alive(identity.pid):
                raise ProcessStopError(
                    "owned child did not exit after force termination"
                ) from exc
        if _pid_alive(identity.pid):
            raise ProcessStopError(
                "owned child did not exit after force termination"
            )

    def start(
        self,
        *,
        executable: str,
        arguments: list[str],
        cwd: Path,
        env: dict[str, str],
        log_path: Path,
        port: int,
        log_max_bytes: int = 16 * 1024 * 1024,
    ) -> OwnedProcess:
        _assert_port_free(port)
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_log(log_path, log_max_bytes)
        creation_flags = 0
        if os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with log_path.open("ab") as log_file:
            try:
                process = self._spawner(
                    [executable, *arguments],
                    cwd=str(cwd),
                    env=env,
                    shell=False,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    creationflags=creation_flags,
                )
            except OSError as exc:
                raise ProcessStartError(
                    f"cannot start child process: {exc}"
                ) from exc
        identity = self._identity_provider(process.pid)
        return OwnedProcess(
            process,
            identity,
            identity_provider=self._identity_provider,
        )


def _assert_port_free(port: int) -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", port))
    except OSError as exc:
        raise PortInUseError(
            f"loopback port {port} is already occupied"
        ) from exc


def _rotate_log(log_path: Path, max_bytes: int) -> None:
    if not log_path.is_file():
        return
    if log_path.stat().st_size <= max_bytes:
        return
    rotated = log_path.with_name(log_path.name + ".1")
    rotated.unlink(missing_ok=True)
    os.replace(log_path, rotated)


def _pid_alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    return _windows_pid_alive(pid)


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _open_process = _kernel32.OpenProcess
    _open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _open_process.restype = wintypes.HANDLE
    _get_exit_code = _kernel32.GetExitCodeProcess
    _get_exit_code.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _get_exit_code.restype = wintypes.BOOL
    _close_handle = _kernel32.CloseHandle
    _close_handle.argtypes = [wintypes.HANDLE]
    _close_handle.restype = wintypes.BOOL
    _query_image_name = _kernel32.QueryFullProcessImageNameW
    _query_image_name.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _query_image_name.restype = wintypes.BOOL
    _get_process_times = _kernel32.GetProcessTimes
    _get_process_times.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    _get_process_times.restype = wintypes.BOOL


def _windows_pid_alive(pid: int) -> bool:
    if os.name != "nt":
        return False
    handle = _open_process(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not _get_exit_code(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == _STILL_ACTIVE
    finally:
        _close_handle(handle)


def windows_process_identity(pid: int) -> ProcessIdentity:
    if os.name != "nt":
        raise ProcessIdentityError(
            "Windows process identity is unavailable on this platform"
        )
    handle = _open_process(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        raise ProcessIdentityError(f"cannot open pid {pid}")
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(32768)
        if not _query_image_name(
            handle,
            0,
            buffer,
            ctypes.byref(size),
        ):
            raise ProcessIdentityError(
                f"cannot resolve executable for pid {pid}"
            )
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        if not _get_process_times(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            raise ProcessIdentityError(
                f"cannot read creation time for pid {pid}"
            )
        creation_ns = (
            (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        ) * 100
        return ProcessIdentity(
            pid=pid,
            executable=buffer.value,
            creation_time_ns=creation_ns,
        )
    finally:
        _close_handle(handle)
