import os
import subprocess
from pathlib import Path

import pytest

import aimctexturegen.core.atomic_files as atomic_files_module
from aimctexturegen.core.atomic_files import AtomicWriteError, atomic_replace_bytes


def _create_junction(link: Path, target: Path) -> None:
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            f"Unable to create test junction: {result.stdout}{result.stderr}"
        )
    assert link.is_junction()


def _remove_junction(link: Path) -> None:
    if os.path.lexists(link):
        os.rmdir(link)


def test_validator_failure_preserves_existing_destination_byte_exactly(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "state.json"
    destination.write_bytes(b"original bytes")

    def reject(_payload: bytes) -> object:
        raise ValueError("invalid replacement")

    with pytest.raises(AtomicWriteError, match="validation"):
        atomic_replace_bytes(destination, b"replacement", reject)

    assert destination.read_bytes() == b"original bytes"
    assert not (tmp_path / "state.json.tmp").exists()


def test_successful_replacement_is_byte_exact_and_leaves_no_temporary_file(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "state.json"
    destination.write_bytes(b"old")
    validated: list[bytes] = []

    atomic_replace_bytes(
        destination,
        b'{"state":"new"}\n',
        lambda payload: validated.append(payload),
    )

    assert destination.read_bytes() == b'{"state":"new"}\n'
    assert validated == [b'{"state":"new"}\n']
    assert not (tmp_path / "state.json.tmp").exists()


def test_fsync_failure_removes_only_the_created_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "state.json"
    destination.write_bytes(b"old")
    unrelated = tmp_path / "unrelated.tmp"
    unrelated.write_bytes(b"keep")

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("forced fsync failure")

    monkeypatch.setattr(atomic_files_module.os, "fsync", fail_fsync)

    with pytest.raises(AtomicWriteError, match="write"):
        atomic_replace_bytes(destination, b"new", lambda _payload: None)

    assert destination.read_bytes() == b"old"
    assert unrelated.read_bytes() == b"keep"
    assert not (tmp_path / "state.json.tmp").exists()


def test_replace_failure_removes_only_the_created_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "state.json"
    destination.write_bytes(b"old")
    unrelated = tmp_path / "unrelated.tmp"
    unrelated.write_bytes(b"keep")

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("forced replace failure")

    monkeypatch.setattr(atomic_files_module.os, "replace", fail_replace)

    with pytest.raises(AtomicWriteError, match="replace"):
        atomic_replace_bytes(destination, b"new", lambda _payload: None)

    assert destination.read_bytes() == b"old"
    assert unrelated.read_bytes() == b"keep"
    assert not (tmp_path / "state.json.tmp").exists()


def test_bounded_stale_regular_temporary_file_is_removed_before_write(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "state.json"
    temporary = tmp_path / "state.json.tmp"
    temporary.write_bytes(b"stale")

    atomic_replace_bytes(destination, b"fresh", lambda _payload: None)

    assert destination.read_bytes() == b"fresh"
    assert not temporary.exists()


def test_existing_temporary_junction_is_rejected_without_following_or_removing_it(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "state.json"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"keep")
    temporary = tmp_path / "state.json.tmp"
    _create_junction(temporary, outside)

    try:
        with pytest.raises(AtomicWriteError, match="unsafe"):
            atomic_replace_bytes(destination, b"new", lambda _payload: None)

        assert temporary.is_junction()
        assert sentinel.read_bytes() == b"keep"
        assert not destination.exists()
    finally:
        _remove_junction(temporary)
