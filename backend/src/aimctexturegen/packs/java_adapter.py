import json
import os
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from aimctexturegen.packs.models import InspectedPack, PackMetadata


_WINDOWS_DEVICE_STEMS = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)
_WINDOWS_INVALID_CHARACTERS = frozenset('<>:"|?*')


class PackValidationError(ValueError):
    def __init__(self, code: str, user_message: str) -> None:
        self.code = code
        self.user_message = user_message
        super().__init__(user_message)


class JavaPackAdapter:
    def inspect(self, source: Path) -> InspectedPack:
        if source.is_file() and source.suffix.casefold() == ".zip":
            return self._inspect_zip(source)
        if source.is_dir():
            return self._inspect_directory(source)
        raise PackValidationError(
            "UNSUPPORTED_SOURCE", "请选择 ZIP 文件或资源包目录"
        )

    def _inspect_zip(self, source: Path) -> InspectedPack:
        resolved_source = source.resolve()
        try:
            with zipfile.ZipFile(resolved_source) as archive:
                members = []
                files: dict[str, zipfile.ZipInfo] = {}
                for info in archive.infolist():
                    is_directory = info.is_dir() or info.filename.endswith(("/", "\\"))
                    normalized = _normalize_member_path(
                        info.filename, is_directory=is_directory
                    )
                    members.append((normalized, is_directory))
                    if not is_directory:
                        files[normalized] = info

                _validate_case_folding(members)
                pack_root = _discover_pack_root(files)
                metadata_path = _under_root("pack.mcmeta", pack_root)
                try:
                    metadata_payload = archive.read(files[metadata_path])
                except NotImplementedError as error:
                    raise PackValidationError(
                        "UNSUPPORTED_ZIP_COMPRESSION",
                        "ZIP 资源包使用了不支持的压缩方式",
                    ) from error
                except (KeyError, OSError, RuntimeError, zipfile.BadZipFile) as error:
                    raise PackValidationError(
                        "INVALID_PACK_METADATA", "无法读取 pack.mcmeta"
                    ) from error
                metadata = _parse_metadata(metadata_payload)
        except PackValidationError:
            raise
        except (OSError, zipfile.BadZipFile) as error:
            raise PackValidationError("INVALID_ZIP", "ZIP 资源包无效或已损坏") from error

        return InspectedPack(
            source=resolved_source,
            source_kind="zip",
            pack_root=pack_root,
            metadata=metadata,
            normalized_files=_relative_pack_files(files, pack_root),
        )

    def _inspect_directory(self, source: Path) -> InspectedPack:
        root = source.resolve()
        members: list[tuple[str, bool]] = []
        files: dict[str, Path] = {}

        try:
            for current_root, directory_names, file_names in os.walk(
                root, followlinks=False, onerror=_raise_walk_error
            ):
                current = Path(current_root)
                entries = [(name, True) for name in directory_names]
                entries.extend((name, False) for name in file_names)
                for name, is_directory in entries:
                    entry = current / name
                    resolved_entry = entry.resolve(strict=True)
                    if not resolved_entry.is_relative_to(root):
                        raise PackValidationError(
                            "UNSAFE_PACK_PATH", "资源包目录包含指向目录外部的路径"
                        )
                    relative = entry.relative_to(root).as_posix()
                    normalized = _normalize_member_path(
                        relative, is_directory=is_directory
                    )
                    members.append((normalized, is_directory))
                    if not is_directory:
                        files[normalized] = entry
        except PackValidationError:
            raise
        except OSError as error:
            raise PackValidationError(
                "UNSAFE_PACK_PATH", "无法安全读取资源包目录"
            ) from error

        _validate_case_folding(members)
        pack_root = _discover_pack_root(files)
        metadata_path = _under_root("pack.mcmeta", pack_root)
        try:
            metadata_payload = files[metadata_path].read_bytes()
        except (KeyError, OSError) as error:
            raise PackValidationError(
                "INVALID_PACK_METADATA", "无法读取 pack.mcmeta"
            ) from error

        return InspectedPack(
            source=root,
            source_kind="directory",
            pack_root=pack_root,
            metadata=_parse_metadata(metadata_payload),
            normalized_files=_relative_pack_files(files, pack_root),
        )


def _normalize_member_path(raw_name: str, *, is_directory: bool) -> str:
    normalized = raw_name.replace("\\", "/")
    if is_directory:
        normalized = normalized.removesuffix("/")

    if (
        not normalized
        or normalized.startswith("/")
        or PureWindowsPath(normalized).drive
    ):
        raise PackValidationError("UNSAFE_PACK_PATH", "资源包包含不安全的路径")

    parts = normalized.split("/")
    for part in parts:
        if part in {"", ".", ".."}:
            raise PackValidationError("UNSAFE_PACK_PATH", "资源包包含不安全的路径")
        if (
            any(character in _WINDOWS_INVALID_CHARACTERS for character in part)
            or any(ord(character) < 32 for character in part)
            or part.endswith((" ", "."))
        ):
            raise PackValidationError("UNSAFE_PACK_PATH", "资源包包含不安全的路径")
        device_stem = part.split(".", maxsplit=1)[0].casefold()
        if device_stem in _WINDOWS_DEVICE_STEMS:
            raise PackValidationError("UNSAFE_PACK_PATH", "资源包包含 Windows 设备路径")

    return PurePosixPath(*parts).as_posix()


def _validate_case_folding(members: list[tuple[str, bool]]) -> None:
    seen: dict[str, tuple[str, Literal["file", "directory"]]] = {}
    for normalized, is_directory in members:
        parts = PurePosixPath(normalized).parts
        for index in range(1, len(parts) + 1):
            prefix = PurePosixPath(*parts[:index]).as_posix()
            kind: Literal["file", "directory"] = (
                "file" if not is_directory and index == len(parts) else "directory"
            )
            key = prefix.casefold()
            existing = seen.get(key)
            if existing is None:
                seen[key] = (prefix, kind)
                continue
            existing_prefix, existing_kind = existing
            if existing_prefix != prefix or existing_kind != kind or kind == "file":
                raise PackValidationError(
                    "CASE_CONFLICT", "资源包包含在 Windows 上冲突的路径"
                )


def _raise_walk_error(error: OSError) -> None:
    raise error


def _discover_pack_root(files: Mapping[str, object]) -> PurePosixPath:
    candidates = []
    for filename in files:
        path = PurePosixPath(filename)
        if path.name == "pack.mcmeta" and len(path.parts) in {1, 2}:
            candidates.append(path.parent)

    if not candidates:
        raise PackValidationError("PACK_METADATA_NOT_FOUND", "未找到 pack.mcmeta")
    if len(candidates) != 1:
        raise PackValidationError(
            "AMBIGUOUS_PACK_ROOT", "资源包中存在多个可能的 pack.mcmeta"
        )
    return candidates[0]


def _under_root(relative_path: str, pack_root: PurePosixPath) -> str:
    if pack_root == PurePosixPath("."):
        return relative_path
    return (pack_root / relative_path).as_posix()


def _relative_pack_files(
    files: Mapping[str, object], pack_root: PurePosixPath
) -> frozenset[str]:
    if pack_root == PurePosixPath("."):
        return frozenset(files)

    prefix = f"{pack_root.as_posix()}/"
    return frozenset(
        path.removeprefix(prefix) for path in files if path.startswith(prefix)
    )


def _parse_metadata(payload: bytes) -> PackMetadata:
    try:
        document = json.loads(payload)
        pack = document["pack"]
        pack_format = pack["pack_format"]
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError) as error:
        raise PackValidationError("INVALID_PACK_METADATA", "pack.mcmeta 无效") from error

    if (
        type(document) is not dict
        or type(pack) is not dict
        or type(pack_format) is not int
    ):
        raise PackValidationError("INVALID_PACK_METADATA", "pack.mcmeta 无效")

    supported_formats = None
    if "supported_formats" in pack:
        supported = pack["supported_formats"]
        if type(supported) is not dict or set(supported) != {
            "min_inclusive",
            "max_inclusive",
        }:
            raise PackValidationError("INVALID_PACK_METADATA", "pack.mcmeta 无效")
        minimum = supported.get("min_inclusive")
        maximum = supported.get("max_inclusive")
        if (
            type(minimum) is not int
            or type(maximum) is not int
            or minimum > maximum
        ):
            raise PackValidationError("INVALID_PACK_METADATA", "pack.mcmeta 无效")
        supported_formats = (minimum, maximum)

    return PackMetadata(
        pack_format=pack_format,
        supported_formats=supported_formats,
    )
