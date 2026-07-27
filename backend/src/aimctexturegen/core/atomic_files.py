"""Validated same-directory atomic byte replacement."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path


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
            with temporary.open("xb") as output:
                created_identity = _identity(os.fstat(output.fileno()))
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
        except OSError as error:
            raise AtomicWriteError("atomic write failed") from error

        readback = _read_created_temporary(
            temporary,
            created_identity,
            expected_size=len(payload),
        )
        try:
            validator(readback)
        except Exception as error:
            raise AtomicWriteError("atomic validation failed") from error

        try:
            os.replace(temporary, destination)
        except OSError as error:
            raise AtomicWriteError("atomic replace failed") from error
    except AtomicWriteError:
        raise
    except OSError as error:
        raise AtomicWriteError("atomic path is unsafe or unavailable") from error
    finally:
        if created_identity is not None:
            _remove_exact_regular_file(temporary, created_identity)


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


def _read_created_temporary(
    path: Path,
    expected_identity: _FileIdentity,
    *,
    expected_size: int,
) -> bytes:
    try:
        with path.open("rb") as source:
            status = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(status.st_mode)
                or _identity(status) != expected_identity
            ):
                raise AtomicWriteError("atomic temporary file changed")
            payload = source.read(expected_size + 1)
    except AtomicWriteError:
        raise
    except OSError as error:
        raise AtomicWriteError("atomic readback failed") from error
    if len(payload) != expected_size:
        raise AtomicWriteError("atomic readback size mismatch")
    return payload


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
