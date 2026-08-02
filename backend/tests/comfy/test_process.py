"""RED/GREEN tests for owned child-process lifecycle."""

from __future__ import annotations

import socket
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

import aimctexturegen.comfy.process as process_module
from aimctexturegen.comfy.errors import (
    PortInUseError,
    ProcessIdentityError,
)
from aimctexturegen.comfy.process import (
    ProcessIdentity,
    ProcessLauncher,
)

FAKE_CHILD = (
    Path(__file__).resolve().parents[1] / "fakes" / "fake_child.py"
)


def _launcher(
    *,
    spawner: Any = None,
    identity: ProcessIdentity | None = None,
) -> ProcessLauncher:
    return ProcessLauncher(
        spawner=spawner,
        identity_provider=(
            (lambda pid: identity)
            if identity is not None
            else (lambda pid: ProcessIdentity(pid=pid, executable="fake", creation_time_ns=1))
        ),
    )


def _identity(pid: int, *, creation_time_ns: int = 1) -> ProcessIdentity:
    return ProcessIdentity(pid=pid, executable="fake.exe", creation_time_ns=creation_time_ns)


def test_start_uses_exact_args_shell_false_and_hidden_window(
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    def spawner(args: list[str], **kwargs: Any) -> subprocess.Popen:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.Popen(args, **kwargs)

    launcher = _launcher(spawner=spawner, identity=_identity(1234))
    child = launcher.start(
        executable=sys.executable,
        arguments=[str(FAKE_CHILD), "--sleep", "0"],
        cwd=tmp_path,
        env={"FAKE_ENV": "1"},
        log_path=tmp_path / "child.log",
        port=18188,
    )
    assert captured["args"] == [
        sys.executable,
        str(FAKE_CHILD),
        "--sleep",
        "0",
    ]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert captured["kwargs"]["env"]["FAKE_ENV"] == "1"
    for _ in range(100):
        if child.poll() is not None:
            break
        time.sleep(0.02)
    assert child.poll() is not None
    child.stop(graceful_timeout=2.0)


def test_start_rejects_an_occupied_loopback_port(tmp_path: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        launcher = _launcher(identity=_identity(1))
        with pytest.raises(PortInUseError):
            launcher.start(
                executable=sys.executable,
                arguments=[str(FAKE_CHILD)],
                cwd=tmp_path,
                env={},
                log_path=tmp_path / "child.log",
                port=port,
            )


def test_early_exit_is_observable(tmp_path: Path) -> None:
    launcher = _launcher(identity=_identity(77))
    child = launcher.start(
        executable=sys.executable,
        arguments=[str(FAKE_CHILD), "--exit", "3"],
        cwd=tmp_path,
        env={},
        log_path=tmp_path / "child.log",
        port=18189,
    )
    for _ in range(100):
        if child.poll() is not None:
            break
        time.sleep(0.02)
    assert child.poll() == 3


def test_stop_is_graceful_then_forced_only_for_owned_child(
    tmp_path: Path,
) -> None:
    launcher = _launcher(identity=_identity(555, creation_time_ns=100))
    child = launcher.start(
        executable=sys.executable,
        arguments=[str(FAKE_CHILD), "--sleep", "30"],
        cwd=tmp_path,
        env={},
        log_path=tmp_path / "child.log",
        port=18190,
    )
    assert child.poll() is None
    child.stop(graceful_timeout=0.5)
    for _ in range(100):
        if child.poll() is not None:
            break
        time.sleep(0.02)
    assert child.poll() is not None


def test_pid_reuse_with_different_identity_is_never_stopped(
    tmp_path: Path,
) -> None:
    class Provider:
        def __init__(self) -> None:
            self.creation_time_ns = 10

        def __call__(self, pid: int) -> ProcessIdentity:
            return ProcessIdentity(
                pid=pid,
                executable="fake.exe",
                creation_time_ns=self.creation_time_ns,
            )

    provider = Provider()
    launcher = ProcessLauncher(identity_provider=provider)
    child = launcher.start(
        executable=sys.executable,
        arguments=[str(FAKE_CHILD), "--sleep", "30"],
        cwd=tmp_path,
        env={},
        log_path=tmp_path / "child.log",
        port=18191,
    )
    provider.creation_time_ns = 20
    with pytest.raises(ProcessIdentityError):
        child.stop(graceful_timeout=0.2)
    assert child.poll() is None
    provider.creation_time_ns = 10
    child.stop(graceful_timeout=0.5)


def test_stop_owned_uses_windows_safe_force_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alive_states = iter([True, True, True, False])
    kill_calls: list[tuple[int, int]] = []

    monkeypatch.setattr(
        process_module,
        "_pid_alive",
        lambda pid: next(alive_states),
    )
    monkeypatch.setattr(
        process_module.os,
        "kill",
        lambda pid, sig: kill_calls.append((pid, sig)),
    )

    ProcessLauncher(identity_provider=lambda pid: _identity(pid)).stop_owned(
        _identity(123),
        graceful_timeout=0.0,
    )

    assert kill_calls[0] == (123, signal.SIGTERM)
    assert kill_calls[1][0] == 123
    assert kill_calls[1][1] == getattr(signal, "SIGKILL", signal.SIGTERM)


def test_stop_owned_rechecks_identity_before_force_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identities = iter([_identity(123), _identity(123, creation_time_ns=2)])
    kill_calls: list[tuple[int, int]] = []

    monkeypatch.setattr(process_module, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(
        process_module.os,
        "kill",
        lambda pid, sig: kill_calls.append((pid, sig)),
    )

    with pytest.raises(ProcessIdentityError):
        ProcessLauncher(
            identity_provider=lambda pid: next(identities),
        ).stop_owned(
            _identity(123),
            graceful_timeout=0.0,
        )

    assert kill_calls == [(123, signal.SIGTERM)]


def test_log_rotation_keeps_bounded_history(tmp_path: Path) -> None:
    log_path = tmp_path / "child.log"
    log_path.write_bytes(b"x" * (1024 * 1024 + 1))
    launcher = _launcher(identity=_identity(321))
    child = launcher.start(
        executable=sys.executable,
        arguments=[str(FAKE_CHILD), "--sleep", "0"],
        cwd=tmp_path,
        env={},
        log_path=log_path,
        port=18192,
        log_max_bytes=1024 * 1024,
    )
    child.stop(graceful_timeout=2.0)
    assert (tmp_path / "child.log.1").is_file()
    assert log_path.stat().st_size < 1024 * 1024
