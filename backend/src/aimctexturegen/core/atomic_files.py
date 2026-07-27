"""Validated same-directory atomic byte replacement."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


_MAX_STALE_TEMP_BYTES = 16 * 1024 * 1024
_FileIdentity = tuple[int, int]


class AtomicWriteError(Exception):
    """Raised when an atomic replacement cannot be completed safely."""


def atomic_replace_bytes(
    destination: Path,
    payload: bytes,
    validator: Callable[[bytes], object],
) -> None:
    """Validate durable temporary bytes before replacing ``destination``."""

    parent = destination.parent
    temporary = parent / f"{destination.name}.tmp"
    created_identity: _FileIdentity | None = None

    try:
        _require_plain_directory(parent)
        _remove_bounded_stale_temporary(temporary)

        try:
            with _open_created_file(temporary) as (output, native_handle):
                created_identity = _identity(os.fstat(output.fileno()))
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
                output.seek(0)
                readback = output.read(len(payload) + 1)
                if len(readback) != len(payload):
                    raise AtomicWriteError("atomic readback size mismatch")

                try:
                    validator(readback)
                except Exception as error:
                    raise AtomicWriteError("atomic validation failed") from error

                try:
                    if os.name == "nt":
                        _publish_open_file(native_handle, destination)
                    else:
                        _publish_portable_file(
                            temporary,
                            destination,
                            created_identity,
                        )
                except OSError as error:
                    raise AtomicWriteError("atomic replace failed") from error
        except AtomicWriteError:
            raise
        except OSError as error:
            raise AtomicWriteError("atomic write failed") from error
    except AtomicWriteError:
        raise
    except OSError as error:
        raise AtomicWriteError("atomic path is unsafe or unavailable") from error
    finally:
        if created_identity is not None:
            _remove_exact_regular_file(temporary, created_identity)


@contextmanager
def _open_created_file(path: Path) -> Iterator[tuple[BinaryIO, int]]:
    if os.name != "nt":
        with path.open("x+b") as output:
            yield output, output.fileno()
        return

    handle = _create_windows_file(path)
    try:
        descriptor = _open_windows_handle_as_descriptor(handle)
    except BaseException:
        _close_windows_handle(handle)
        raise
    with os.fdopen(descriptor, "w+b") as output:
        yield output, _windows_handle_from_descriptor(output.fileno())


def _require_plain_directory(path: Path) -> None:
    status = os.lstat(path)
    if not stat.S_ISDIR(status.st_mode) or _is_reparse_point(status):
        raise AtomicWriteError("atomic parent directory is unsafe")


def _remove_bounded_stale_temporary(path: Path) -> None:
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return
    if (
        not stat.S_ISREG(status.st_mode)
        or _is_reparse_point(status)
        or status.st_size > _MAX_STALE_TEMP_BYTES
    ):
        raise AtomicWriteError("atomic temporary path is unsafe")
    expected_identity = _identity(status)
    current = os.lstat(path)
    if _identity(current) != expected_identity or _is_reparse_point(current):
        raise AtomicWriteError("atomic temporary path changed")
    path.unlink()


def _publish_portable_file(
    temporary: Path,
    destination: Path,
    expected_identity: _FileIdentity,
) -> None:
    status = os.lstat(temporary)
    if (
        not stat.S_ISREG(status.st_mode)
        or _is_reparse_point(status)
        or _identity(status) != expected_identity
    ):
        raise OSError("atomic temporary path changed")
    os.replace(temporary, destination)


def _remove_exact_regular_file(path: Path, expected_identity: _FileIdentity) -> None:
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError:
        return
    if (
        stat.S_ISREG(status.st_mode)
        and not _is_reparse_point(status)
        and _identity(status) == expected_identity
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _identity(status: os.stat_result) -> _FileIdentity:
    return status.st_dev, status.st_ino


def _is_reparse_point(status: os.stat_result) -> bool:
    attributes = getattr(status, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _DELETE = 0x00010000
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _CREATE_NEW = 1
    _FILE_ATTRIBUTE_NORMAL = 0x00000080
    _FILE_RENAME_INFO_CLASS = 3
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class _FileRenameInfo(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", wintypes.BOOLEAN),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * 1),
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

    _set_file_information = _kernel32.SetFileInformationByHandle
    _set_file_information.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _set_file_information.restype = wintypes.BOOL

    _close_handle = _kernel32.CloseHandle
    _close_handle.argtypes = [wintypes.HANDLE]
    _close_handle.restype = wintypes.BOOL


def _create_windows_file(path: Path) -> int:
    if os.name != "nt":
        raise OSError("Windows atomic file handles are unavailable")
    handle = _create_file(
        str(path),
        _GENERIC_READ | _GENERIC_WRITE | _DELETE,
        _FILE_SHARE_READ,
        None,
        _CREATE_NEW,
        _FILE_ATTRIBUTE_NORMAL,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        _raise_windows_error(path)
    return handle


def _open_windows_handle_as_descriptor(handle: int) -> int:
    if os.name != "nt":
        raise OSError("Windows atomic file handles are unavailable")
    return msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)


def _windows_handle_from_descriptor(descriptor: int) -> int:
    if os.name != "nt":
        raise OSError("Windows atomic file handles are unavailable")
    return msvcrt.get_osfhandle(descriptor)


def _close_windows_handle(handle: int) -> None:
    if os.name == "nt":
        _close_handle(handle)


def _publish_open_file(handle: int, destination: Path) -> None:
    """Replace by source handle while its pathname cannot be substituted."""

    if os.name != "nt":
        raise OSError("Handle-bound atomic publication is unavailable")
    target_bytes = os.path.abspath(destination).encode("utf-16-le")
    filename_offset = _FileRenameInfo.FileName.offset
    buffer = ctypes.create_string_buffer(
        filename_offset + len(target_bytes) + ctypes.sizeof(wintypes.WCHAR)
    )
    information = ctypes.cast(
        buffer,
        ctypes.POINTER(_FileRenameInfo),
    ).contents
    information.ReplaceIfExists = True
    information.RootDirectory = None
    information.FileNameLength = len(target_bytes)
    ctypes.memmove(
        ctypes.addressof(buffer) + filename_offset,
        target_bytes,
        len(target_bytes),
    )
    if not _set_file_information(
        handle,
        _FILE_RENAME_INFO_CLASS,
        buffer,
        len(buffer),
    ):
        _raise_windows_error(destination)


def _raise_windows_error(path: Path) -> None:
    error_code = ctypes.get_last_error()
    raise OSError(error_code, ctypes.FormatError(error_code), str(path))
