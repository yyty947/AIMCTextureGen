"""RED/GREEN tests for atomic managed-runtime publication."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from aimctexturegen.comfy.archives import SevenZipReader
from aimctexturegen.comfy.errors import (
    ArchiveUnsafeError,
    RuntimeInstallError,
)
from aimctexturegen.comfy.install_state import InstallOperationStore
from aimctexturegen.comfy.installer import RuntimeInstaller
from aimctexturegen.comfy.manifests import RuntimeManifest

from comfy._helpers import make_runtime
from comfy.test_archives import FakeReader, make_seven_zip


def _runtime() -> RuntimeManifest:
    return RuntimeManifest.model_validate(make_runtime())


def _installer(reader: SevenZipReader | None = None) -> RuntimeInstaller:
    return RuntimeInstaller(reader=reader)


def _root(tmp_path: Path) -> Path:
    return tmp_path / "runtime"


def test_status_is_missing_without_installation(tmp_path: Path) -> None:
    status = _installer().status(_runtime(), _root(tmp_path))
    assert status.state == "missing"


def test_install_publishes_ready_runtime_and_deletes_archive(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    archive = make_seven_zip(tmp_path)
    runtime = _runtime()
    installer = _installer()
    status = installer.install(runtime, archive, root)
    assert status.state == "ready"
    assert status.selected_version == "0.29.2"
    assert not archive.exists()
    assert (root / "state" / "selected-runtime.json").is_file()
    receipts = list((root / "state" / "installation-receipts").glob("*.json"))
    assert len(receipts) == 1
    installed = list((root / "comfyui").iterdir())
    assert len(installed) == 1
    assert installed[0].is_dir()
    assert installer.status(runtime, root).state == "ready"


def test_install_is_idempotent(tmp_path: Path) -> None:
    root = _root(tmp_path)
    archive = make_seven_zip(tmp_path)
    installer = _installer()
    installer.install(_runtime(), archive, root)
    selection = (root / "state" / "selected-runtime.json").read_bytes()
    installer.install(_runtime(), make_seven_zip(tmp_path / "again"), root)
    assert (root / "state" / "selected-runtime.json").read_bytes() == selection
    assert len(list((root / "comfyui").iterdir())) == 1


def test_unsafe_archive_fails_without_touching_selection(tmp_path: Path) -> None:
    root = _root(tmp_path)
    archive = make_seven_zip(tmp_path)
    installer = _installer()
    installer.install(_runtime(), archive, root)
    selection_before = (root / "state" / "selected-runtime.json").read_bytes()
    selected = json.loads(selection_before)
    import shutil

    shutil.move(
        root / "comfyui" / selected["directory"],
        tmp_path / "kept-aside",
    )
    assert installer.status(_runtime(), root).state == "corrupt"

    from aimctexturegen.comfy.archives import ArchiveMember

    bad_reader = FakeReader(
        [
            ArchiveMember(
                name="../escape.txt",
                size=1,
                is_directory=False,
                is_symlink=False,
            )
        ]
    )
    with pytest.raises(ArchiveUnsafeError):
        _installer(bad_reader).install(
            _runtime(),
            tmp_path / "bad.7z",
            root,
        )
    assert (root / "state" / "selected-runtime.json").read_bytes() == selection_before
    assert installer.status(_runtime(), root).state == "corrupt"


def test_missing_required_paths_fails_and_cleans_staging(tmp_path: Path) -> None:
    root = _root(tmp_path)
    archive = make_seven_zip(
        tmp_path,
        files={"only.txt": b"x"},
    )
    with pytest.raises(RuntimeInstallError):
        _installer().install(_runtime(), archive, root)
    assert not (root / "comfyui").exists()
    assert not root.exists() or not list((root / "comfyui").glob(".staging-*"))


def test_corrupt_selection_is_reported_and_repairable(tmp_path: Path) -> None:
    root = _root(tmp_path)
    archive = make_seven_zip(tmp_path)
    installer = _installer()
    installer.install(_runtime(), archive, root)
    selected = json.loads(
        (root / "state" / "selected-runtime.json").read_text(encoding="utf-8")
    )
    import shutil

    shutil.rmtree(root / "comfyui" / selected["directory"])
    assert installer.status(_runtime(), root).state == "corrupt"
    status = installer.install(_runtime(), make_seven_zip(tmp_path / "repair"), root)
    assert status.state == "ready"


def test_no_writes_escape_the_runtime_root(tmp_path: Path) -> None:
    root = _root(tmp_path)
    archive = make_seven_zip(tmp_path)
    _installer().install(_runtime(), archive, root)
    outside = tmp_path / "outside"
    assert not outside.exists()
    assert not (tmp_path / ".staging").exists()


def test_interrupted_install_recovery_marks_and_cleans(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "comfyui").mkdir(parents=True)
    staging = root / "comfyui" / ".staging-interrupted"
    staging.mkdir()
    (staging / "leftover.bin").write_bytes(b"x")
    store = InstallOperationStore(root / "state")
    operation = store.create(
        runtime_id="comfyui-windows-nvidia",
        profile_id="p",
        plan_digest="a" * 64,
        accepted_component_ids=(),
    )
    store.transition(operation, "extracting")
    installer = _installer()
    installer.recover_interrupted(root, store)
    recovered = store.get(operation.operation_id)
    assert recovered.state == "failed"
    assert recovered.error.code == "INSTALL_INTERRUPTED"
    assert not staging.exists()
