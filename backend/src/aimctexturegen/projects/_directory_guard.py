import ctypes
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType


class DirectoryGuardError(OSError):
    pass


@dataclass(frozen=True)
class FileIdentity:
    device: int
    file_id: int


class GuardedDirectoryTree:
    def __init__(
        self,
        temporary_root: Path,
        temporary_identity: FileIdentity,
        destination_root: Path,
        destination_identity: FileIdentity,
    ) -> None:
        self._temporary_root = temporary_root
        self._temporary_identity = temporary_identity
        self._destination_root = destination_root
        self._destination_identity = destination_identity
        self._stack = ExitStack()
        self._guards: dict[str, _DirectoryGuard] = {}

    def __enter__(self) -> "GuardedDirectoryTree":
        self._stack.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return self._stack.__exit__(exc_type, exc_value, traceback)

    def ensure_parent(self, parent: Path) -> None:
        if not parent.is_relative_to(self._destination_root):
            raise DirectoryGuardError("Destination parent escapes guarded root")

        self._ensure_guard(self._temporary_root, self._temporary_identity)
        self._ensure_guard(self._destination_root, self._destination_identity)
        current = self._destination_root
        for part in parent.relative_to(self._destination_root).parts:
            current /= part
            try:
                current.mkdir()
            except FileExistsError:
                pass
            except OSError as error:
                raise DirectoryGuardError(str(error)) from error
            self._ensure_guard(current)

    def _ensure_guard(
        self,
        path: Path,
        expected_identity: FileIdentity | None = None,
    ) -> None:
        key = _normalized_path(path)
        existing = self._guards.get(key)
        if existing is not None:
            if expected_identity is not None and existing.identity != expected_identity:
                raise DirectoryGuardError("Guarded directory identity changed")
            return

        guard = _open_directory_guard(path)
        if expected_identity is not None and guard.identity != expected_identity:
            guard.close()
            raise DirectoryGuardError("Guarded directory identity changed")
        self._guards[key] = self._stack.enter_context(guard)


class _DirectoryGuard:
    def __init__(
        self,
        path: Path,
        identity: FileIdentity,
        resource: int,
        close_resource,
    ) -> None:
        self.path = path
        self.identity = identity
        self._resource = resource
        self._close_resource = close_resource

    def __enter__(self) -> "_DirectoryGuard":
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> bool:
        self.close()
        return False

    def close(self) -> None:
        if self._resource is None:
            return
        resource = self._resource
        self._resource = None
        self._close_resource(resource)


def capture_directory_identity(path: Path) -> FileIdentity:
    with _open_directory_guard(path) as guard:
        return guard.identity


@contextmanager
def hold_directory_identity(path: Path) -> Iterator[FileIdentity]:
    with _open_directory_guard(path) as guard:
        yield guard.identity


def matches_directory_identity(path: Path, expected: FileIdentity) -> bool:
    try:
        with _open_directory_guard(path) as guard:
            return guard.identity == expected
    except (DirectoryGuardError, OSError):
        return False


def is_reparse_point(path: Path, status: os.stat_result) -> bool:
    attributes = getattr(status, "st_file_attributes", 0)
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return (
        stat.S_ISLNK(status.st_mode)
        or path.is_symlink()
        or path.is_junction()
        or bool(attributes & reparse_attribute)
    )


def _open_directory_guard(path: Path) -> _DirectoryGuard:
    if os.name == "nt":
        return _open_windows_directory_guard(path)
    return _open_portable_directory_guard(path)


def _open_portable_directory_guard(path: Path) -> _DirectoryGuard:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise DirectoryGuardError(str(error)) from error
    try:
        handle_status = os.fstat(descriptor)
        path_status = os.lstat(path)
        if (
            not stat.S_ISDIR(handle_status.st_mode)
            or not stat.S_ISDIR(path_status.st_mode)
            or is_reparse_point(path, path_status)
        ):
            raise DirectoryGuardError("Guarded path is not a plain directory")
        handle_identity = _identity_from_stat(handle_status)
        if _identity_from_stat(path_status) != handle_identity:
            raise DirectoryGuardError("Guarded directory identity changed")
        return _DirectoryGuard(path, handle_identity, descriptor, os.close)
    except BaseException:
        os.close(descriptor)
        raise


def _identity_from_stat(status: os.stat_result) -> FileIdentity:
    return FileIdentity(device=status.st_dev, file_id=status.st_ino)


def _normalized_path(path: Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


if os.name == "nt":
    from ctypes import wintypes

    _FILE_READ_ATTRIBUTES = 0x0080
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _OPEN_EXISTING = 3
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _WINDOWS_REPARSE_ATTRIBUTE = 0x00000400

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _create_file = _kernel32.CreateFileW
    _create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _create_file.restype = wintypes.HANDLE

    _get_file_information = _kernel32.GetFileInformationByHandle
    _get_file_information.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    _get_file_information.restype = wintypes.BOOL

    _get_final_path = _kernel32.GetFinalPathNameByHandleW
    _get_final_path.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _get_final_path.restype = wintypes.DWORD

    _close_handle = _kernel32.CloseHandle
    _close_handle.argtypes = [wintypes.HANDLE]
    _close_handle.restype = wintypes.BOOL


def _open_windows_directory_guard(path: Path) -> _DirectoryGuard:
    if os.name != "nt":
        raise DirectoryGuardError("Windows directory guards are unavailable")

    handle = _create_file(
        str(path),
        _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        error_code = ctypes.get_last_error()
        raise DirectoryGuardError(
            error_code,
            ctypes.FormatError(error_code),
            str(path),
        )

    try:
        information = _ByHandleFileInformation()
        if not _get_file_information(handle, ctypes.byref(information)):
            error_code = ctypes.get_last_error()
            raise DirectoryGuardError(
                error_code,
                ctypes.FormatError(error_code),
                str(path),
            )
        if information.dwFileAttributes & _WINDOWS_REPARSE_ATTRIBUTE:
            raise DirectoryGuardError("Guarded path is a Windows reparse point")

        final_path = _windows_final_path(handle, path)
        if _normalized_path(Path(final_path)) != _normalized_path(path):
            raise DirectoryGuardError("Guarded directory path changed")

        identity = FileIdentity(
            device=information.dwVolumeSerialNumber,
            file_id=(information.nFileIndexHigh << 32) | information.nFileIndexLow,
        )
        return _DirectoryGuard(path, identity, handle, _close_handle)
    except BaseException:
        _close_handle(handle)
        raise


def _windows_final_path(handle: int, path: Path) -> str:
    required = _get_final_path(handle, None, 0, 0)
    if required == 0:
        error_code = ctypes.get_last_error()
        raise DirectoryGuardError(
            error_code,
            ctypes.FormatError(error_code),
            str(path),
        )
    buffer = ctypes.create_unicode_buffer(required + 1)
    written = _get_final_path(handle, buffer, len(buffer), 0)
    if written == 0 or written >= len(buffer):
        error_code = ctypes.get_last_error()
        raise DirectoryGuardError(
            error_code,
            ctypes.FormatError(error_code),
            str(path),
        )
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value
