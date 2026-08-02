"""RED/GREEN tests for safe 7z inventory and extraction."""

from __future__ import annotations

from pathlib import Path

import py7zr
import pytest

from aimctexturegen.comfy.archives import (
    ArchiveInventory,
    ArchiveMember,
    ExtractionPolicy,
    ExtractedTree,
    SevenZipReader,
    extract_and_audit_7z,
    inspect_7z,
)
from aimctexturegen.comfy.errors import ArchiveUnsafeError


def make_seven_zip(
    tmp_path: Path,
    *,
    root: str = "ComfyUI_windows_portable_nvidia",
    files: dict[str, bytes] | None = None,
) -> Path:
    files = files or {
        "python_embeded/python.exe": b"python",
        "ComfyUI/main.py": b"main",
        "ComfyUI/extra_models_config.yaml": b"models",
    }
    source = tmp_path / "source" / root
    for relative, payload in files.items():
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    archive = tmp_path / "runtime.7z"
    with py7zr.SevenZipFile(archive, "w") as writer:
        writer.writeall(source, arcname=root)
    return archive


class FakeReader:
    def __init__(
        self,
        members: list[ArchiveMember],
        *,
        extracted: dict[str, bytes] | None = None,
    ) -> None:
        self._members = members
        self.extracted = extracted or {
            member.name: b"x" * member.size
            for member in self._members
            if not member.is_directory and member.size > 0
        }

    def members(self, archive: Path) -> list[ArchiveMember]:
        return self._members

    def extract(self, archive: Path, target: Path) -> None:
        for name, payload in self.extracted.items():
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)


def _policy(**kwargs) -> ExtractionPolicy:
    defaults = dict(max_members=100, max_total_size=10_000_000, max_single_size=5_000_000)
    defaults.update(kwargs)
    return ExtractionPolicy(**defaults)


def _safe_members(root: str = "ComfyUI_windows_portable_nvidia") -> list[ArchiveMember]:
    return [
        ArchiveMember(name=f"{root}/", size=0, is_directory=True),
        ArchiveMember(name=f"{root}/python_embeded/", size=0, is_directory=True),
        ArchiveMember(
            name=f"{root}/python_embeded/python.exe",
            size=6,
            is_directory=False,
        ),
        ArchiveMember(name=f"{root}/ComfyUI/main.py", size=4, is_directory=False),
    ]


def test_inspect_7z_reports_root_members_and_total_size(tmp_path: Path) -> None:
    archive = make_seven_zip(tmp_path)
    inventory = inspect_7z(
        archive,
        _policy(),
    )
    assert inventory.root == "ComfyUI_windows_portable_nvidia"
    assert inventory.total_size > 0
    assert any(member.name.endswith("python.exe") for member in inventory.members)


@pytest.mark.parametrize(
    "name",
    [
        "../escape.txt",
        "/absolute.txt",
        "C:/drive.txt",
        r"\\server\share.txt",
        "root/CON.txt",
        "root/trailing./x",
        "root/bad\\slash.txt",
        "root/..",
    ],
)
def test_unsafe_member_names_are_rejected_before_extraction(
    tmp_path: Path,
    name: str,
) -> None:
    reader = FakeReader(
        [
            ArchiveMember(name=name, size=1, is_directory=False),
        ]
    )
    with pytest.raises(ArchiveUnsafeError):
        inspect_7z(tmp_path / "archive.7z", _policy(), reader=reader)


def test_case_colliding_members_are_rejected(tmp_path: Path) -> None:
    reader = FakeReader(
        [
            ArchiveMember(name="root/File.txt", size=1, is_directory=False),
            ArchiveMember(name="root/file.txt", size=1, is_directory=False),
        ]
    )
    with pytest.raises(ArchiveUnsafeError):
        inspect_7z(tmp_path / "archive.7z", _policy(), reader=reader)


def test_symlink_members_are_rejected(tmp_path: Path) -> None:
    reader = FakeReader(
        [
            ArchiveMember(
                name="root/link",
                size=0,
                is_directory=False,
                is_symlink=True,
            )
        ]
    )
    with pytest.raises(ArchiveUnsafeError):
        inspect_7z(tmp_path / "archive.7z", _policy(), reader=reader)


def test_expansion_bombs_are_rejected(tmp_path: Path) -> None:
    reader = FakeReader(_safe_members() + [
        ArchiveMember(name="root/huge.bin", size=6_000_000, is_directory=False)
    ])
    with pytest.raises(ArchiveUnsafeError):
        inspect_7z(tmp_path / "archive.7z", _policy(), reader=reader)


def test_total_size_bombs_are_rejected(tmp_path: Path) -> None:
    reader = FakeReader(_safe_members())
    with pytest.raises(ArchiveUnsafeError):
        inspect_7z(
            tmp_path / "archive.7z",
            _policy(max_total_size=9),
            reader=reader,
        )


def test_member_count_limits_are_enforced(tmp_path: Path) -> None:
    reader = FakeReader(_safe_members())
    with pytest.raises(ArchiveUnsafeError):
        inspect_7z(
            tmp_path / "archive.7z",
            _policy(max_members=2),
            reader=reader,
        )


def test_multiple_roots_are_rejected(tmp_path: Path) -> None:
    reader = FakeReader(
        [
            ArchiveMember(name="one/a.txt", size=1, is_directory=False),
            ArchiveMember(name="two/b.txt", size=1, is_directory=False),
        ]
    )
    with pytest.raises(ArchiveUnsafeError):
        inspect_7z(tmp_path / "archive.7z", _policy(), reader=reader)


def test_extract_and_audit_publishes_exact_tree(tmp_path: Path) -> None:
    archive = make_seven_zip(tmp_path)
    staging = tmp_path / "staging" / "runtime-extract"
    tree = extract_and_audit_7z(
        archive,
        staging,
        _policy(),
    )
    assert tree.root == "ComfyUI_windows_portable_nvidia"
    assert (staging / tree.root / "python_embeded" / "python.exe").is_file()
    assert (staging / tree.root / "ComfyUI" / "main.py").is_file()


def test_audit_rejects_extra_files_and_removes_only_staging(
    tmp_path: Path,
) -> None:
    extracted = {
        member.name: b"x" * member.size
        for member in _safe_members()
        if not member.is_directory and member.size > 0
    }
    extracted["ComfyUI_windows_portable_nvidia/extra.bin"] = b"extra"
    reader = FakeReader(_safe_members(), extracted=extracted)
    staging = tmp_path / "staging" / "runtime-extract"
    with pytest.raises(Exception):
        extract_and_audit_7z(
            tmp_path / "archive.7z",
            staging,
            _policy(),
            reader=reader,
        )
    assert not staging.exists()
    assert (tmp_path / "staging").exists()


def test_archive_inventory_and_extracted_tree_are_strict() -> None:
    with pytest.raises(Exception):
        ArchiveInventory.model_validate(
            {"root": "x", "members": [], "total_size": -1}
        )
    with pytest.raises(Exception):
        ExtractedTree.model_validate(
            {"root": "x", "files": [], "total_size": -1}
        )
