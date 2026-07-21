import shutil
import zipfile
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from aimctexturegen.catalog.models import CatalogProfile
from aimctexturegen.catalog.registry import CatalogRegistry
from aimctexturegen.packs.java_adapter import JavaPackAdapter
from aimctexturegen.packs.java_adapter import PackValidationError
from aimctexturegen.packs.models import InspectedPack
from aimctexturegen.projects.models import ProjectManifest


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
        inspected = self._adapter.inspect(source.resolve())
        profile = self._catalogs.for_pack_format(inspected.metadata.pack_format)
        return self._create_project(inspected, profile, project_name.strip())

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

        try:
            temporary_root.mkdir(parents=True, exist_ok=False)
            temporary_created = True
            source_directory = temporary_root / "source"
            working_directory = temporary_root / "pack"
            source_directory.mkdir()
            working_directory.mkdir()

            snapshot = source_directory / "imported-pack.zip"
            source_hash = self._create_snapshot(inspected, snapshot)
            snapshot_inspection = JavaPackAdapter().inspect(snapshot)
            self._validate_snapshot(inspected, snapshot_inspection)
            self._copy_validated_members(snapshot_inspection, working_directory)

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
            manifest_path.write_text(
                manifest.model_dump_json(indent=2),
                encoding="utf-8",
            )
            validated_manifest = ProjectManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8"),
                strict=True,
            )
            temporary_root.rename(final_root)
            return validated_manifest
        except BaseException:
            if temporary_created:
                self._remove_verified_temporary_directory(
                    temporary_root,
                    project_id,
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
        destination_root: Path,
    ) -> None:
        try:
            with zipfile.ZipFile(inspected.source) as archive:
                files = {
                    info.filename.replace("\\", "/"): info
                    for info in archive.infolist()
                    if not info.is_dir()
                    and not info.filename.endswith(("/", "\\"))
                }
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
                        destination_root,
                        relative_name,
                    )
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source_file, destination.open(
                        "xb"
                    ) as destination_file:
                        shutil.copyfileobj(
                            source_file,
                            destination_file,
                            length=_COPY_BUFFER_BYTES,
                        )
        except PackValidationError:
            raise
        except NotImplementedError as error:
            raise PackValidationError(
                "UNSUPPORTED_ZIP_COMPRESSION",
                "ZIP 资源包使用了不支持的压缩方式",
            ) from error

    def _remove_verified_temporary_directory(
        self,
        temporary_root: Path,
        project_id: UUID,
    ) -> None:
        expected = self._root / f"{project_id}.tmp"
        if temporary_root != expected or temporary_root.parent != self._root:
            return
        if not temporary_root.exists() or temporary_root.is_symlink():
            return
        try:
            resolved = temporary_root.resolve(strict=True)
        except OSError:
            return
        if resolved.parent != self._root or not resolved.is_dir():
            return
        shutil.rmtree(resolved)


def _copy_file_and_hash(source: Path, destination: Path) -> str:
    digest = sha256()
    with source.open("rb") as source_file, destination.open("xb") as destination_file:
        while chunk := source_file.read(_COPY_BUFFER_BYTES):
            destination_file.write(chunk)
            digest.update(chunk)
    return digest.hexdigest()


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


def _contained_destination(root: Path, relative_name: str) -> Path:
    destination = root.joinpath(*PurePosixPath(relative_name).parts)
    if not destination.resolve().is_relative_to(root.resolve()):
        raise PackValidationError("UNSAFE_PACK_PATH", "资源包包含不安全的路径")
    return destination
