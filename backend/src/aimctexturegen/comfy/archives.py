"""Safe 7z inventory, extraction and post-extraction tree audit."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Protocol

from pydantic import ConfigDict, Field
from pydantic import BaseModel

from aimctexturegen.comfy.errors import (
    ArchiveError,
    ArchiveExtractError,
    ArchiveUnsafeError,
)
from aimctexturegen.core.relative_paths import validate_project_relative_path

_REPARSE_POINT = 0x400


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ArchiveMember(_StrictModel):
    name: str
    size: int = Field(ge=0)
    is_directory: bool = False
    is_symlink: bool = False


class ArchiveInventory(_StrictModel):
    root: str
    members: tuple[ArchiveMember, ...]
    total_size: int = Field(ge=0)


class ExtractedTree(_StrictModel):
    root: str
    files: tuple[str, ...]
    total_size: int = Field(ge=0)


class ExtractionPolicy(_StrictModel):
    max_members: int = Field(gt=0)
    max_total_size: int = Field(gt=0)
    max_single_size: int = Field(gt=0)


class SevenZipReader(Protocol):
    def members(self, archive: Path) -> list[ArchiveMember]: ...

    def extract(self, archive: Path, target: Path) -> None: ...


class Py7zReader:
    """Default reader backed by the pinned py7zr dependency."""

    def members(self, archive: Path) -> list[ArchiveMember]:
        import py7zr

        try:
            with py7zr.SevenZipFile(archive, "r") as seven_zip:
                result: list[ArchiveMember] = []
                for file_info in seven_zip.list():
                    raw_name = str(file_info.filename).replace("\\", "/")
                    size = int(
                        getattr(file_info, "uncompressed", 0) or 0
                    )
                    result.append(
                        ArchiveMember(
                            name=raw_name,
                            size=max(0, size),
                            is_directory=bool(file_info.is_directory),
                            is_symlink=bool(getattr(file_info, "is_symlink", False)),
                        )
                    )
                return result
        except OSError as exc:
            raise ArchiveExtractError("cannot list 7z archive") from exc

    def extract(self, archive: Path, target: Path) -> None:
        import py7zr

        try:
            with py7zr.SevenZipFile(archive, "r") as seven_zip:
                seven_zip.extractall(target)
        except OSError as exc:
            raise ArchiveExtractError("cannot extract 7z archive") from exc


class BsdtarReader:
    """Windows bsdtar-backed extractor for BCJ2 archives py7zr cannot read.

    Inventory still uses the pinned py7zr reader; extraction shells to the
    OS-bundled ``tar.exe`` (libarchive) with fixed, shell-free arguments
    after the full safe member preflight has passed.
    """

    def __init__(self, tar_executable: str | None = None) -> None:
        self._tar_executable = tar_executable or _find_bsdtar()

    def members(self, archive: Path) -> list[ArchiveMember]:
        return Py7zReader().members(archive)

    def extract(self, archive: Path, target: Path) -> None:
        if self._tar_executable is None:
            raise ArchiveExtractError(
                "bsdtar (tar.exe) is unavailable; cannot extract a BCJ2 7z"
            )
        creation_flags = 0
        if os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                [
                    self._tar_executable,
                    "-xf",
                    str(Path(archive).resolve()),
                    "-C",
                    str(Path(target).resolve()),
                ],
                capture_output=True,
                text=True,
                timeout=1800,
                check=False,
                shell=False,
                creationflags=creation_flags,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ArchiveExtractError("bsdtar extraction failed") from exc
        if completed.returncode != 0:
            raise ArchiveExtractError(
                f"bsdtar extraction failed: {completed.stderr[-400:]}"
            )


def _find_bsdtar() -> str | None:
    candidates = [
        r"C:\Windows\System32\tar.exe",
        r"C:\Windows\Sysnative\tar.exe",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    return shutil.which("tar")


def inspect_7z(
    archive: Path,
    policy: ExtractionPolicy,
    *,
    reader: SevenZipReader | None = None,
) -> ArchiveInventory:
    reader = reader or Py7zReader()
    try:
        members = reader.members(Path(archive))
    except ArchiveError:
        raise
    except Exception as exc:
        raise ArchiveExtractError("archive listing failed") from exc
    validated = _validate_members(members, policy)
    root = validated[0].name.split("/", maxsplit=1)[0]
    total_size = sum(
        member.size for member in validated if not member.is_directory
    )
    return ArchiveInventory(
        root=root,
        members=validated,
        total_size=total_size,
    )


def extract_and_audit_7z(
    archive: Path,
    staging: Path,
    policy: ExtractionPolicy,
    *,
    reader: SevenZipReader | None = None,
) -> ExtractedTree:
    inventory = inspect_7z(archive, policy, reader=reader)
    staging = Path(staging)
    if staging.exists():
        raise ArchiveExtractError("staging directory already exists")
    staging.mkdir(parents=True)
    try:
        reader = reader or Py7zReader()
        reader.extract(Path(archive), staging)
        return _audit_tree(staging, inventory)
    except ArchiveError:
        remove_staging(staging)
        raise
    except Exception as exc:
        remove_staging(staging)
        raise ArchiveExtractError("extraction or audit failed") from exc


def remove_staging(staging: Path) -> None:
    staging = Path(staging)
    if not staging.exists():
        return
    if not staging.is_dir():
        staging.unlink(missing_ok=True)
        return
    shutil.rmtree(staging)


def _validate_members(
    members: list[ArchiveMember],
    policy: ExtractionPolicy,
) -> tuple[ArchiveMember, ...]:
    if not members:
        raise ArchiveUnsafeError("archive is empty")
    if len(members) > policy.max_members:
        raise ArchiveUnsafeError("archive member count exceeds the limit")
    roots: set[str] = set()
    seen: set[str] = set()
    total_size = 0
    validated: list[ArchiveMember] = []
    for member in members:
        name = member.name.strip()
        if member.is_directory:
            name = name.rstrip("/")
        if not name or name.endswith("/") or name != name.strip():
            raise ArchiveUnsafeError(f"unsafe member name {member.name!r}")
        try:
            validate_project_relative_path(name)
        except ValueError as exc:
            raise ArchiveUnsafeError(
                f"unsafe member name {member.name!r}"
            ) from exc
        if member.is_symlink:
            raise ArchiveUnsafeError(f"symlink member {member.name!r} is rejected")
        if member.size > policy.max_single_size:
            raise ArchiveUnsafeError(
                f"member {member.name!r} exceeds the single-size limit"
            )
        total_size += 0 if member.is_directory else member.size
        key = name.casefold()
        if key in seen:
            raise ArchiveUnsafeError(
                f"case-colliding member {member.name!r}"
            )
        seen.add(key)
        roots.add(name.split("/", maxsplit=1)[0])
        validated.append(member.model_copy(update={"name": name}))
    if len(roots) != 1:
        raise ArchiveUnsafeError("archive must have exactly one root")
    if total_size > policy.max_total_size:
        raise ArchiveUnsafeError("archive total size exceeds the limit")
    return tuple(validated)


def _audit_tree(staging: Path, inventory: ArchiveInventory) -> ExtractedTree:
    expected = {
        member.name: member.size
        for member in inventory.members
        if not member.is_directory
    }
    seen_case: set[str] = set()
    for current_root, directories, files in os.walk(staging, followlinks=False):
        current = Path(current_root)
        for directory in directories:
            path = current / directory
            _assert_plain_path(path)
            _assert_unique_case(path, seen_case)
        for file_name in files:
            path = current / file_name
            _assert_plain_path(path)
            _assert_unique_case(path, seen_case)
            relative = path.relative_to(staging).as_posix()
            if relative not in expected:
                raise ArchiveUnsafeError(
                    f"extracted file {relative!r} is not declared"
                )
            if path.stat().st_size != expected[relative]:
                raise ArchiveUnsafeError(
                    f"extracted file {relative!r} has the wrong size"
                )
    missing = set(expected) - {
        path.relative_to(staging).as_posix()
        for current_root, _, files in os.walk(staging, followlinks=False)
        for file_name in files
        for path in [Path(current_root) / file_name]
    }
    if missing:
        raise ArchiveUnsafeError(
            f"declared files missing after extraction: {sorted(missing)}"
        )
    return ExtractedTree(
        root=inventory.root,
        files=tuple(sorted(expected)),
        total_size=sum(expected.values()),
    )


def _assert_plain_path(path: Path) -> None:
    try:
        status = path.lstat()
    except OSError as exc:
        raise ArchiveUnsafeError(
            f"extracted path {path.name!r} cannot be inspected"
        ) from exc
    if (
        path.is_symlink()
        or bool(getattr(status, "st_file_attributes", 0) & _REPARSE_POINT)
    ):
        raise ArchiveUnsafeError(
            f"extracted path {path.name!r} is a symlink or reparse point"
        )
    if not (stat.S_ISREG(status.st_mode) or stat.S_ISDIR(status.st_mode)):
        raise ArchiveUnsafeError(
            f"extracted path {path.name!r} is not a regular file or directory"
        )


def _assert_unique_case(path: Path, seen: set[str]) -> None:
    key = path.as_posix().casefold()
    if key in seen:
        raise ArchiveUnsafeError(f"case-colliding extracted path {path!s}")
    seen.add(key)
