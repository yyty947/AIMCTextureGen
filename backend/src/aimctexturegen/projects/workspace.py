import os
import shutil
import stat
import zipfile
import zlib
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from uuid import UUID, uuid4

from aimctexturegen.catalog.models import CatalogProfile
from aimctexturegen.catalog.registry import CatalogRegistry
from aimctexturegen.packs.java_adapter import (
    MAX_ZIP_MEMBER_BYTES,
    MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES,
    JavaPackAdapter,
    PackValidationError,
)
from aimctexturegen.packs.models import InspectedPack
from aimctexturegen.projects._directory_guard import (
    DirectoryGuardError,
    FileIdentity,
    GuardedDirectoryTree,
    capture_directory_identity,
    is_reparse_point,
    matches_directory_identity,
)
from aimctexturegen.projects.models import (
    MAX_PROJECT_MANIFEST_BYTES,
    MAX_PROJECT_NAME_LENGTH,
    ProjectManifest,
)


_COPY_BUFFER_BYTES = 1024 * 1024
_DETERMINISTIC_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_REGULAR_FILE_MODE = 0o100644


class ProjectWorkspace:
    def __init__(
        self,
        root: Path,
        adapter: JavaPackAdapter,
        catalogs: CatalogRegistry,
    ) -> None:
        self._root = root.resolve()
        self._adapter = adapter
        self._catalogs = catalogs

    def import_pack(self, source: Path, project_name: str) -> ProjectManifest:
        normalized_project_name = project_name.strip()
        if not normalized_project_name:
            raise PackValidationError("INVALID_PROJECT_NAME", "项目名称不能为空")
        if len(normalized_project_name) > MAX_PROJECT_NAME_LENGTH:
            raise PackValidationError(
                "INVALID_PROJECT_NAME",
                f"项目名称必须为 1 到 {MAX_PROJECT_NAME_LENGTH} 个字符",
            )
        inspected = self._adapter.inspect(source.resolve())
        profile = self._catalogs.for_pack_format(inspected.metadata.pack_format)
        return self._create_project(inspected, profile, normalized_project_name)

    def _create_project(
        self,
        inspected: InspectedPack,
        profile: CatalogProfile,
        project_name: str,
    ) -> ProjectManifest:
        if not project_name:
            raise PackValidationError("INVALID_PROJECT_NAME", "项目名称不能为空")

        project_id = uuid4()
        temporary_root = self._root / f"{project_id}.tmp"
        final_root = self._root / str(project_id)
        temporary_created = False
        temporary_identity: FileIdentity | None = None

        try:
            temporary_root.mkdir(parents=True, exist_ok=False)
            temporary_created = True
            temporary_identity = capture_directory_identity(temporary_root)
            source_directory = temporary_root / "source"
            working_directory = temporary_root / "pack"
            source_directory.mkdir()
            working_directory.mkdir()
            working_identity = capture_directory_identity(working_directory)

            snapshot = source_directory / "imported-pack.zip"
            source_hash = self._create_snapshot(inspected, snapshot)
            snapshot_identity = _capture_regular_file_identity(snapshot)
            snapshot_inspection = JavaPackAdapter().inspect(snapshot)
            self._validate_snapshot(inspected, snapshot_inspection)
            with snapshot.open("rb") as snapshot_file:
                _verify_snapshot_binding(
                    snapshot,
                    snapshot_file,
                    snapshot_identity,
                    source_hash,
                )
                self._copy_validated_members(
                    snapshot_inspection,
                    snapshot_file,
                    working_directory,
                    temporary_root,
                    temporary_identity,
                    working_identity,
                )

                timestamp = datetime.now(timezone.utc)
                manifest = ProjectManifest(
                    schema_version=1,
                    project_id=project_id,
                    project_name=project_name,
                    edition="java",
                    java_pack_format=inspected.metadata.pack_format,
                    supported_formats=inspected.metadata.supported_formats,
                    catalog_id=profile.catalog_id,
                    source_sha256=source_hash,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                manifest_path = temporary_root / "project.json"
                _require_directory_identity(temporary_root, temporary_identity)
                manifest_payload = manifest.model_dump_json(indent=2).encode("utf-8")
                if len(manifest_payload) > MAX_PROJECT_MANIFEST_BYTES:
                    raise PackValidationError(
                        "INVALID_PROJECT_MANIFEST",
                        "项目清单超过允许大小",
                    )
                manifest_path.write_bytes(manifest_payload)
                validated_manifest = ProjectManifest.model_validate_json(
                    manifest_path.read_text(encoding="utf-8"),
                    strict=True,
                )
                _verify_snapshot_binding(
                    snapshot,
                    snapshot_file,
                    snapshot_identity,
                    source_hash,
                )
            _require_directory_identity(temporary_root, temporary_identity)
            if _tree_contains_reparse_point(temporary_root):
                raise PackValidationError(
                    "UNSAFE_PROJECT_PATH",
                    "项目临时目录不安全",
                )
            _verify_snapshot_path(
                snapshot,
                snapshot_identity,
                source_hash,
            )
            _require_directory_identity(temporary_root, temporary_identity)
            temporary_root.rename(final_root)
            return validated_manifest
        except BaseException:
            if temporary_created and temporary_identity is not None:
                self._remove_verified_temporary_directory(
                    temporary_root,
                    project_id,
                    temporary_identity,
                )
            raise

    def _create_snapshot(self, inspected: InspectedPack, destination: Path) -> str:
        if inspected.source_kind == "zip":
            return _copy_file_and_hash(inspected.source, destination)

        _write_deterministic_directory_zip(inspected, destination)
        return _hash_file(destination)

    @staticmethod
    def _validate_snapshot(
        original: InspectedPack,
        snapshot: InspectedPack,
    ) -> None:
        if (
            snapshot.metadata != original.metadata
            or snapshot.normalized_files != original.normalized_files
        ):
            raise PackValidationError(
                "SOURCE_CHANGED",
                "资源包在导入期间发生变化",
            )

    @staticmethod
    def _copy_validated_members(
        inspected: InspectedPack,
        snapshot_file: BinaryIO,
        destination_root: Path,
        temporary_root: Path,
        temporary_identity: FileIdentity,
        destination_identity: FileIdentity,
    ) -> None:
        try:
            snapshot_file.seek(0)
            with GuardedDirectoryTree(
                temporary_root,
                temporary_identity,
                destination_root,
                destination_identity,
            ) as directory_guards, zipfile.ZipFile(snapshot_file) as archive:
                files = {
                    info.filename.replace("\\", "/"): info
                    for info in archive.infolist()
                    if not info.is_dir()
                    and not info.filename.endswith(("/", "\\"))
                }
                total_copied = 0
                for relative_name in sorted(inspected.normalized_files):
                    archive_name = _under_pack_root(
                        relative_name,
                        inspected.pack_root,
                    )
                    info = files.get(archive_name)
                    if info is None:
                        raise PackValidationError(
                            "SOURCE_CHANGED",
                            "资源包在导入期间发生变化",
                        )
                    destination = _contained_destination(
                        temporary_root,
                        temporary_identity,
                        destination_root,
                        destination_identity,
                        relative_name,
                    )
                    directory_guards.ensure_parent(destination.parent)
                    try:
                        source_file = archive.open(info)
                    except NotImplementedError as error:
                        raise PackValidationError(
                            "UNSUPPORTED_ZIP_COMPRESSION",
                            "ZIP 资源包使用了不支持的压缩方式",
                        ) from error
                    except RuntimeError as error:
                        raise PackValidationError(
                            "ENCRYPTED_ZIP_MEMBER",
                            "ZIP 资源包包含加密文件，无法导入",
                        ) from error
                    except (zipfile.BadZipFile, EOFError, OSError) as error:
                        raise PackValidationError(
                            "CORRUPT_ZIP_MEMBER",
                            "ZIP 资源包中的文件已损坏或截断",
                        ) from error
                    with source_file, destination.open("xb") as destination_file:
                        copied = _copy_zip_member_bounded(
                            source_file,
                            destination_file,
                            info,
                            total_copied,
                        )
                    total_copied += copied
                    _verify_destination_ancestry(
                        temporary_root,
                        temporary_identity,
                        destination_root,
                        destination_identity,
                        destination.parent,
                    )
        except PackValidationError:
            raise
        except DirectoryGuardError as error:
            raise PackValidationError(
                "UNSAFE_PROJECT_PATH",
                "项目工作目录不安全",
            ) from error
        except NotImplementedError as error:
            raise PackValidationError(
                "UNSUPPORTED_ZIP_COMPRESSION",
                "ZIP 资源包使用了不支持的压缩方式",
            ) from error
        except (zipfile.BadZipFile, EOFError, zlib.error) as error:
            raise PackValidationError(
                "CORRUPT_ZIP_MEMBER",
                "ZIP 资源包中的文件已损坏或截断",
            ) from error

    def _remove_verified_temporary_directory(
        self,
        temporary_root: Path,
        project_id: UUID,
        original_identity: FileIdentity,
    ) -> None:
        expected = self._root / f"{project_id}.tmp"
        if temporary_root != expected or temporary_root.parent != self._root:
            return
        if not matches_directory_identity(temporary_root, original_identity):
            return
        if _tree_contains_reparse_point(temporary_root):
            return
        if not matches_directory_identity(temporary_root, original_identity):
            return
        shutil.rmtree(temporary_root)


def _copy_file_and_hash(source: Path, destination: Path) -> str:
    digest = sha256()
    with source.open("rb") as source_file, destination.open("xb") as destination_file:
        while chunk := source_file.read(_COPY_BUFFER_BYTES):
            destination_file.write(chunk)
            digest.update(chunk)
    return digest.hexdigest()


def _copy_zip_member_bounded(
    source_file: BinaryIO,
    destination_file: BinaryIO,
    info: zipfile.ZipInfo,
    total_before: int,
) -> int:
    copied = 0
    while True:
        try:
            chunk = source_file.read(_COPY_BUFFER_BYTES)
        except NotImplementedError as error:
            raise PackValidationError(
                "UNSUPPORTED_ZIP_COMPRESSION",
                "ZIP 资源包使用了不支持的压缩方式",
            ) from error
        except RuntimeError as error:
            raise PackValidationError(
                "ENCRYPTED_ZIP_MEMBER",
                "ZIP 资源包包含加密文件，无法导入",
            ) from error
        except (zipfile.BadZipFile, EOFError, OSError, zlib.error) as error:
            raise PackValidationError(
                "CORRUPT_ZIP_MEMBER",
                "ZIP 资源包中的文件已损坏或截断",
            ) from error
        if not chunk:
            break
        copied += len(chunk)
        if copied > MAX_ZIP_MEMBER_BYTES:
            raise PackValidationError(
                "ZIP_MEMBER_TOO_LARGE",
                "ZIP 资源包中的单个文件超过允许大小",
            )
        if total_before + copied > MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES:
            raise PackValidationError(
                "ZIP_TOTAL_SIZE_EXCEEDED",
                "ZIP 资源包展开后的总大小超过允许范围",
            )
        destination_file.write(chunk)
    if copied != info.file_size:
        raise PackValidationError(
            "CORRUPT_ZIP_MEMBER",
            "ZIP 资源包中的文件大小与目录记录不一致",
        )
    return copied


def _capture_regular_file_identity(path: Path) -> FileIdentity:
    try:
        status = os.lstat(path)
    except OSError as error:
        raise PackValidationError(
            "SOURCE_CHANGED",
            "资源包快照在导入期间发生变化",
        ) from error
    if not stat.S_ISREG(status.st_mode) or is_reparse_point(path, status):
        raise PackValidationError(
            "SOURCE_CHANGED",
            "资源包快照在导入期间发生变化",
        )
    return _identity_from_stat(status)


def _verify_snapshot_binding(
    path: Path,
    snapshot_file: BinaryIO,
    expected_identity: FileIdentity,
    expected_hash: str,
) -> None:
    try:
        path_status = os.lstat(path)
        handle_status = os.fstat(snapshot_file.fileno())
    except OSError as error:
        raise PackValidationError(
            "SOURCE_CHANGED",
            "资源包快照在导入期间发生变化",
        ) from error
    if (
        not stat.S_ISREG(path_status.st_mode)
        or is_reparse_point(path, path_status)
        or _identity_from_stat(path_status) != expected_identity
        or not stat.S_ISREG(handle_status.st_mode)
        or _identity_from_stat(handle_status) != expected_identity
        or _hash_stream(snapshot_file) != expected_hash
    ):
        raise PackValidationError(
            "SOURCE_CHANGED",
            "资源包快照在导入期间发生变化",
        )
    try:
        final_path_status = os.lstat(path)
        final_handle_status = os.fstat(snapshot_file.fileno())
    except OSError as error:
        raise PackValidationError(
            "SOURCE_CHANGED",
            "资源包快照在导入期间发生变化",
        ) from error
    if (
        is_reparse_point(path, final_path_status)
        or _identity_from_stat(final_path_status) != expected_identity
        or _identity_from_stat(final_handle_status) != expected_identity
    ):
        raise PackValidationError(
            "SOURCE_CHANGED",
            "资源包快照在导入期间发生变化",
        )


def _verify_snapshot_path(
    path: Path,
    expected_identity: FileIdentity,
    expected_hash: str,
) -> None:
    if _capture_regular_file_identity(path) != expected_identity:
        raise PackValidationError(
            "SOURCE_CHANGED",
            "资源包快照在导入期间发生变化",
        )
    try:
        with path.open("rb") as snapshot_file:
            _verify_snapshot_binding(
                path,
                snapshot_file,
                expected_identity,
                expected_hash,
            )
    except PackValidationError:
        raise
    except OSError as error:
        raise PackValidationError(
            "SOURCE_CHANGED",
            "资源包快照在导入期间发生变化",
        ) from error


def _hash_stream(source_file: BinaryIO) -> str:
    digest = sha256()
    source_file.seek(0)
    while chunk := source_file.read(_COPY_BUFFER_BYTES):
        digest.update(chunk)
    source_file.seek(0)
    return digest.hexdigest()


def _tree_contains_reparse_point(root: Path) -> bool:
    try:
        for current_root, directory_names, file_names in os.walk(
            root,
            topdown=True,
            followlinks=False,
        ):
            current = Path(current_root)
            for name in [*directory_names, *file_names]:
                path = current / name
                status = os.lstat(path)
                if is_reparse_point(path, status):
                    return True
    except OSError:
        return True
    return False


def _identity_from_stat(status: os.stat_result) -> FileIdentity:
    return FileIdentity(device=status.st_dev, file_id=status.st_ino)


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source_file:
        while chunk := source_file.read(_COPY_BUFFER_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _write_deterministic_directory_zip(
    inspected: InspectedPack,
    destination: Path,
) -> None:
    with zipfile.ZipFile(
        destination,
        mode="x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for relative_name in sorted(inspected.normalized_files):
            source = _directory_source_member(inspected, relative_name)
            info = zipfile.ZipInfo(relative_name, _DETERMINISTIC_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = _REGULAR_FILE_MODE << 16
            with source.open("rb") as source_file, archive.open(
                info,
                mode="w",
            ) as archive_file:
                shutil.copyfileobj(
                    source_file,
                    archive_file,
                    length=_COPY_BUFFER_BYTES,
                )


def _directory_source_member(inspected: InspectedPack, relative_name: str) -> Path:
    source_root = inspected.source.resolve(strict=True)
    parts = PurePosixPath(relative_name).parts
    pack_root_parts = (
        () if inspected.pack_root == PurePosixPath(".") else inspected.pack_root.parts
    )
    source = source_root.joinpath(*pack_root_parts, *parts).resolve(strict=True)
    if not source.is_relative_to(source_root) or not source.is_file():
        raise PackValidationError(
            "SOURCE_CHANGED",
            "资源包在导入期间发生变化",
        )
    return source


def _under_pack_root(relative_name: str, pack_root: PurePosixPath) -> str:
    if pack_root == PurePosixPath("."):
        return relative_name
    return (pack_root / relative_name).as_posix()


def _contained_destination(
    temporary_root: Path,
    temporary_identity: FileIdentity,
    destination_root: Path,
    destination_identity: FileIdentity,
    relative_name: str,
) -> Path:
    destination = destination_root.joinpath(*PurePosixPath(relative_name).parts)
    if (
        not destination.is_relative_to(temporary_root)
        or not destination.parent.is_relative_to(destination_root)
    ):
        raise PackValidationError("UNSAFE_PROJECT_PATH", "项目工作目录不安全")
    _require_directory_identity(temporary_root, temporary_identity)
    _require_directory_identity(destination_root, destination_identity)
    return destination


def _verify_destination_ancestry(
    temporary_root: Path,
    temporary_identity: FileIdentity,
    destination_root: Path,
    destination_identity: FileIdentity,
    parent: Path,
) -> None:
    _require_directory_identity(temporary_root, temporary_identity)
    _require_directory_identity(destination_root, destination_identity)
    current = destination_root
    for part in parent.relative_to(destination_root).parts:
        current /= part
        _require_plain_directory(current)


def _require_directory_identity(path: Path, expected: FileIdentity) -> None:
    if not matches_directory_identity(path, expected):
        raise PackValidationError("UNSAFE_PROJECT_PATH", "项目工作目录不安全")


def _require_plain_directory(path: Path) -> None:
    try:
        status = os.lstat(path)
    except OSError as error:
        raise PackValidationError(
            "UNSAFE_PROJECT_PATH",
            "项目工作目录不安全",
        ) from error
    if not stat.S_ISDIR(status.st_mode) or is_reparse_point(path, status):
        raise PackValidationError("UNSAFE_PROJECT_PATH", "项目工作目录不安全")
