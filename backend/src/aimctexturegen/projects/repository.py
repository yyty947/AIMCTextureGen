"""Safe project discovery, opening, and manifest migration."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from aimctexturegen.core.atomic_files import AtomicWriteError, atomic_replace_bytes
from aimctexturegen.projects._directory_guard import (
    DirectoryGuardError,
    FileIdentity,
    hold_directory_identity,
    is_reparse_point,
    matches_directory_identity,
)
from aimctexturegen.projects.models import (
    MAX_PROJECT_MANIFEST_BYTES,
    ProjectManifest,
    dump_project_manifest,
    load_project_manifest,
)


_FileIdentity = tuple[int, int]


class ProjectRepositoryError(Exception):
    """A stable, path-free project persistence failure."""

    def __init__(self, code: str, user_message: str) -> None:
        self.code = code
        self.user_message = user_message
        super().__init__(user_message)


@dataclass(frozen=True)
class OpenedProject:
    """A manifest and project-local roots protected by the open context."""

    manifest: ProjectManifest
    root: Path
    pack_root: Path
    jobs_root: Path
    uploads_root: Path


@dataclass(frozen=True)
class ProjectScanIssue:
    """A recoverable issue for one canonical project directory."""

    project_id: UUID
    code: str
    user_message: str


@dataclass(frozen=True)
class ProjectScanResult:
    """Valid manifests and isolated issues found during a direct-root scan."""

    manifests: tuple[ProjectManifest, ...]
    issues: tuple[ProjectScanIssue, ...]


class ProjectRepository:
    """Open and scan project manifests without following unsafe paths."""

    def __init__(self, root: Path) -> None:
        self._root = Path(os.path.abspath(root))

    @property
    def root(self) -> Path:
        return self._root

    @contextmanager
    def open(self, project_id: UUID) -> Iterator[OpenedProject]:
        """Open one canonical project while holding its directory identity."""

        if not isinstance(project_id, UUID):
            raise TypeError("project_id must be a UUID")
        if not self._root.exists():
            raise _repository_error("PROJECT_NOT_FOUND")

        try:
            with hold_directory_identity(self._root) as root_identity:
                project_root = self._root / str(project_id)
                try:
                    try:
                        try:
                            os.lstat(project_root)
                        except FileNotFoundError as error:
                            raise _repository_error("PROJECT_NOT_FOUND") from error
                        with hold_directory_identity(
                            project_root
                        ) as project_identity:
                            manifest = self._load_and_migrate(
                                project_id,
                                project_root,
                                root_identity,
                                project_identity,
                            )
                            opened = OpenedProject(
                                manifest=manifest,
                                root=project_root,
                                pack_root=project_root / "pack",
                                jobs_root=project_root / "jobs",
                                uploads_root=project_root / "uploads",
                            )
                            try:
                                yield opened
                            finally:
                                self._require_identity(
                                    project_root,
                                    project_identity,
                                    "UNSAFE_PROJECT_PATH",
                                )
                    except FileNotFoundError as error:
                        raise _repository_error("PROJECT_NOT_FOUND") from error
                    except DirectoryGuardError as error:
                        if not os.path.lexists(project_root):
                            raise _repository_error("PROJECT_NOT_FOUND") from error
                        raise _repository_error("UNSAFE_PROJECT_PATH") from error
                finally:
                    self._require_identity(
                        self._root,
                        root_identity,
                        "UNSAFE_PROJECT_ROOT",
                    )
        except ProjectRepositoryError:
            raise
        except FileNotFoundError as error:
            raise _repository_error("PROJECT_NOT_FOUND") from error
        except DirectoryGuardError as error:
            raise _repository_error("UNSAFE_PROJECT_ROOT") from error

    def list_manifests(self) -> ProjectScanResult:
        """Scan canonical direct children without hiding valid siblings."""

        if not self._root.exists():
            return ProjectScanResult(manifests=(), issues=())

        manifests: list[ProjectManifest] = []
        issues: list[ProjectScanIssue] = []
        try:
            with hold_directory_identity(self._root) as root_identity:
                try:
                    entries = tuple(os.scandir(self._root))
                except OSError as error:
                    raise _repository_error("UNSAFE_PROJECT_ROOT") from error
                for entry in sorted(entries, key=lambda item: item.name):
                    project_id = _canonical_project_id(entry.name)
                    if project_id is None:
                        continue
                    try:
                        with self.open(project_id) as opened:
                            manifests.append(opened.manifest)
                    except ProjectRepositoryError as error:
                        issues.append(
                            ProjectScanIssue(
                                project_id=project_id,
                                code=error.code,
                                user_message=error.user_message,
                            )
                        )
                self._require_identity(
                    self._root,
                    root_identity,
                    "UNSAFE_PROJECT_ROOT",
                )
        except ProjectRepositoryError:
            raise
        except DirectoryGuardError as error:
            raise _repository_error("UNSAFE_PROJECT_ROOT") from error

        manifests.sort(key=lambda manifest: str(manifest.project_id))
        manifests.sort(key=lambda manifest: manifest.updated_at, reverse=True)
        issues.sort(key=lambda issue: str(issue.project_id))
        return ProjectScanResult(
            manifests=tuple(manifests),
            issues=tuple(issues),
        )

    def _load_and_migrate(
        self,
        project_id: UUID,
        project_root: Path,
        root_identity: FileIdentity,
        project_identity: FileIdentity,
    ) -> ProjectManifest:
        manifest_path = project_root / "project.json"
        payload = self._read_bounded_manifest(
            manifest_path,
            project_root,
            root_identity,
            project_identity,
        )
        try:
            manifest, migrated = load_project_manifest(payload)
        except (OSError, ValueError, ValidationError) as error:
            raise _repository_error("CORRUPT_PROJECT_MANIFEST") from error
        if manifest.project_id != project_id:
            raise _repository_error("CORRUPT_PROJECT_MANIFEST")
        if not migrated:
            return manifest

        replacement = dump_project_manifest(manifest)
        if len(replacement) > MAX_PROJECT_MANIFEST_BYTES:
            raise _repository_error("CORRUPT_PROJECT_MANIFEST")

        def validate_replacement(readback: bytes) -> None:
            loaded, still_migrated = load_project_manifest(readback)
            if still_migrated or loaded != manifest:
                raise ValueError("migrated project manifest changed during write")

        self._require_identity(self._root, root_identity, "UNSAFE_PROJECT_ROOT")
        self._require_identity(
            project_root,
            project_identity,
            "UNSAFE_PROJECT_PATH",
        )
        try:
            atomic_replace_bytes(
                manifest_path,
                replacement,
                validate_replacement,
            )
        except AtomicWriteError as error:
            raise _repository_error("PROJECT_STORAGE_UNAVAILABLE") from error
        self._require_identity(self._root, root_identity, "UNSAFE_PROJECT_ROOT")
        self._require_identity(
            project_root,
            project_identity,
            "UNSAFE_PROJECT_PATH",
        )
        return manifest

    def _read_bounded_manifest(
        self,
        manifest_path: Path,
        project_root: Path,
        root_identity: FileIdentity,
        project_identity: FileIdentity,
    ) -> bytes:
        try:
            path_status = os.lstat(manifest_path)
            if (
                not stat.S_ISREG(path_status.st_mode)
                or is_reparse_point(manifest_path, path_status)
                or path_status.st_size > MAX_PROJECT_MANIFEST_BYTES
            ):
                raise OSError("project manifest is not a bounded regular file")
            expected_identity = _identity(path_status)
            with manifest_path.open("rb") as manifest_file:
                handle_status = os.fstat(manifest_file.fileno())
                if (
                    not stat.S_ISREG(handle_status.st_mode)
                    or _identity(handle_status) != expected_identity
                ):
                    raise OSError("project manifest changed before reading")
                payload = manifest_file.read(MAX_PROJECT_MANIFEST_BYTES + 1)
                if len(payload) > MAX_PROJECT_MANIFEST_BYTES:
                    raise OSError("project manifest exceeds its size limit")
                self._require_identity(
                    self._root,
                    root_identity,
                    "UNSAFE_PROJECT_ROOT",
                )
                self._require_identity(
                    project_root,
                    project_identity,
                    "UNSAFE_PROJECT_PATH",
                )
                final_status = os.lstat(manifest_path)
                final_handle_status = os.fstat(manifest_file.fileno())
                if (
                    _identity(final_status) != expected_identity
                    or _identity(final_handle_status) != expected_identity
                    or is_reparse_point(manifest_path, final_status)
                ):
                    raise OSError("project manifest changed while reading")
                return payload
        except ProjectRepositoryError:
            raise
        except OSError as error:
            raise _repository_error("CORRUPT_PROJECT_MANIFEST") from error

    @staticmethod
    def _require_identity(
        path: Path,
        expected: FileIdentity,
        code: str,
    ) -> None:
        if not matches_directory_identity(path, expected):
            raise _repository_error(code)


def _canonical_project_id(name: str) -> UUID | None:
    try:
        project_id = UUID(name)
    except (ValueError, AttributeError):
        return None
    if str(project_id) != name:
        return None
    return project_id


def _identity(status_result: os.stat_result) -> _FileIdentity:
    return status_result.st_dev, status_result.st_ino


def _repository_error(code: str) -> ProjectRepositoryError:
    messages = {
        "PROJECT_NOT_FOUND": "未找到该项目",
        "CORRUPT_PROJECT_MANIFEST": "项目清单损坏或与项目目录不一致",
        "UNSAFE_PROJECT_ROOT": "项目存储目录不安全",
        "UNSAFE_PROJECT_PATH": "项目目录不安全",
        "PROJECT_STORAGE_UNAVAILABLE": "无法安全更新项目清单",
    }
    return ProjectRepositoryError(code, messages[code])
