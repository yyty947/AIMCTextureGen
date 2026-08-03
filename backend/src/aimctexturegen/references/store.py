from __future__ import annotations

import os
import shutil
import stat
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError

from aimctexturegen.core.atomic_files import AtomicWriteError, atomic_replace_bytes
from aimctexturegen.projects._directory_guard import (
    DirectoryGuardError,
    FileIdentity,
    capture_directory_identity,
    hold_directory_identity,
    is_reparse_point,
    matches_directory_identity,
)
from aimctexturegen.projects.repository import OpenedProject, ProjectRepository

from .models import (
    ReferenceKind,
    StoredReference,
    ValidatedReference,
    dump_reference_metadata,
)
from .validation import MAX_REFERENCE_BYTES, validate_reference_png


MAX_REFERENCE_METADATA_BYTES = 16 * 1024
_REFERENCE_WRITE_LOCK = threading.RLock()
_KIND_ROOTS: dict[ReferenceKind, str] = {
    "style": "style-references",
    "structure": "structure-references",
}


class ReferenceStoreError(Exception):
    def __init__(self, code: str, user_message: str) -> None:
        self.code = code
        self.user_message = user_message
        super().__init__(user_message)


class ProjectReferenceStore:
    def __init__(self, repository: ProjectRepository) -> None:
        self._repository = repository

    def create(
        self,
        project_id: UUID,
        kind: ReferenceKind,
        validated: ValidatedReference,
        *,
        now,
    ) -> StoredReference:
        if not isinstance(validated, ValidatedReference):
            raise TypeError("validated must be a ValidatedReference")
        _require_kind(kind)
        with _REFERENCE_WRITE_LOCK:
            with self._repository.open(project_id) as opened:
                with self._hold_kind_root(opened, kind, create=True) as kind_identity:
                    if kind_identity is None:
                        raise _reference_error("UNSAFE_REFERENCE_PATH")
                    reference = StoredReference(
                        reference_id=uuid4(),
                        kind=kind,
                        sha256=validated.sha256,
                        byte_size=validated.byte_size,
                        width=validated.width,
                        height=validated.height,
                        mode=validated.mode,
                        created_at=now,
                    )
                    final_root = self._record_root(opened, kind, reference.reference_id)
                    temporary_root = final_root.with_name(f"{reference.reference_id}.tmp")
                    temporary_identity: FileIdentity | None = None
                    temporary_created = False
                    try:
                        if os.path.lexists(final_root) or os.path.lexists(temporary_root):
                            raise _reference_error("REFERENCE_ALREADY_EXISTS")
                        temporary_root.mkdir()
                        temporary_created = True
                        temporary_identity = capture_directory_identity(temporary_root)
                        with hold_directory_identity(temporary_root) as held_identity:
                            if held_identity != temporary_identity:
                                raise DirectoryGuardError("temporary reference identity changed")
                            payload_path = temporary_root / "original.png"
                            metadata_path = temporary_root / "metadata.json"
                            atomic_replace_bytes(
                                payload_path,
                                validated.payload,
                                lambda readback: _validate_payload_readback(readback, validated),
                            )
                            payload = dump_reference_metadata(reference)
                            atomic_replace_bytes(
                                metadata_path,
                                payload,
                                lambda readback: _validate_metadata_readback(readback, reference),
                            )
                            loaded = self._load_record(
                                opened,
                                kind,
                                temporary_root,
                                temporary_identity,
                                allow_temporary_name=True,
                                reference_id=reference.reference_id,
                            )
                            if loaded != reference:
                                raise ValueError("reference metadata changed during publication")
                        self._require_identity(opened.uploads_root / _KIND_ROOTS[kind], kind_identity)
                        self._require_identity(temporary_root, temporary_identity)
                        temporary_root.rename(final_root)
                        return reference
                    except ReferenceStoreError:
                        raise
                    except (
                        AtomicWriteError,
                        DirectoryGuardError,
                        OSError,
                        ValidationError,
                        ValueError,
                    ) as error:
                        raise _reference_error("REFERENCE_STORAGE_UNAVAILABLE") from error
                    finally:
                        if temporary_created and temporary_identity is not None:
                            _remove_owned_temporary_tree(temporary_root, temporary_identity)

    def list(self, project_id: UUID, kind: ReferenceKind) -> tuple[StoredReference, ...]:
        _require_kind(kind)
        with self._repository.open(project_id) as opened:
            with self._hold_kind_root(opened, kind, create=False) as kind_identity:
                if kind_identity is None:
                    return ()
                kind_root = opened.uploads_root / _KIND_ROOTS[kind]
                try:
                    entries = tuple(os.scandir(kind_root))
                except OSError as error:
                    raise _reference_error("UNSAFE_REFERENCE_PATH") from error
                references: list[StoredReference] = []
                for entry in sorted(entries, key=lambda item: item.name):
                    try:
                        reference_id = UUID(entry.name)
                    except (TypeError, ValueError):
                        continue
                    if str(reference_id) != entry.name:
                        continue
                    record_root = kind_root / entry.name
                    try:
                        with hold_directory_identity(record_root) as record_identity:
                            references.append(
                                self._load_record(
                                    opened,
                                    kind,
                                    record_root,
                                    record_identity,
                                    reference_id=reference_id,
                                )
                            )
                    except ReferenceStoreError as error:
                        raise error
                return tuple(sorted(references, key=lambda item: str(item.reference_id)))

    def read_content(
        self,
        project_id: UUID,
        kind: ReferenceKind,
        reference_id: UUID,
    ) -> bytes:
        _require_kind(kind)
        with self._repository.open(project_id) as opened:
            record_root = self._record_root(opened, kind, reference_id)
            try:
                with hold_directory_identity(record_root) as record_identity:
                    reference = self._load_record(
                        opened,
                        kind,
                        record_root,
                        record_identity,
                        reference_id=reference_id,
                    )
                    payload = _read_reference_payload(record_root / "original.png")
                    validated = validate_reference_png(payload)
                    if validated.sha256 != reference.sha256:
                        raise _reference_error("CORRUPT_REFERENCE_RECORD")
                    return payload
            except ReferenceStoreError:
                raise
            except (DirectoryGuardError, OSError) as error:
                if not os.path.lexists(record_root):
                    raise _reference_error("REFERENCE_NOT_FOUND") from error
                raise _reference_error("UNSAFE_REFERENCE_PATH") from error

    def delete(self, project_id: UUID, kind: ReferenceKind, reference_id: UUID) -> None:
        _require_kind(kind)
        with _REFERENCE_WRITE_LOCK:
            with self._repository.open(project_id) as opened:
                record_root = self._record_root(opened, kind, reference_id)
                try:
                    with hold_directory_identity(record_root) as record_identity:
                        self._load_record(
                            opened,
                            kind,
                            record_root,
                            record_identity,
                            reference_id=reference_id,
                        )
                        if _tree_contains_reparse_point(record_root):
                            raise _reference_error("UNSAFE_REFERENCE_PATH")
                    if os.path.lexists(record_root):
                        shutil.rmtree(record_root)
                except ReferenceStoreError:
                    raise
                except (DirectoryGuardError, OSError) as error:
                    if not os.path.lexists(record_root):
                        raise _reference_error("REFERENCE_NOT_FOUND") from error
                    raise _reference_error("UNSAFE_REFERENCE_PATH") from error

    @staticmethod
    def _record_root(opened: OpenedProject, kind: ReferenceKind, reference_id: UUID) -> Path:
        return opened.uploads_root / _KIND_ROOTS[kind] / str(reference_id)

    @contextmanager
    def _hold_kind_root(self, opened: OpenedProject, kind: ReferenceKind, *, create: bool):
        uploads_root = opened.uploads_root
        if uploads_root.parent != opened.root or uploads_root.name != "uploads":
            raise _reference_error("UNSAFE_REFERENCE_PATH")
        if not os.path.lexists(uploads_root):
            if not create:
                yield None
                return
            try:
                uploads_root.mkdir()
            except FileExistsError:
                pass
            except OSError as error:
                raise _reference_error("REFERENCE_STORAGE_UNAVAILABLE") from error
        try:
            with hold_directory_identity(uploads_root):
                kind_root = uploads_root / _KIND_ROOTS[kind]
                if not os.path.lexists(kind_root):
                    if not create:
                        yield None
                        return
                    kind_root.mkdir()
                with hold_directory_identity(kind_root) as kind_identity:
                    yield kind_identity
                    self._require_identity(kind_root, kind_identity)
        except ReferenceStoreError:
            raise
        except (DirectoryGuardError, OSError) as error:
            raise _reference_error("UNSAFE_REFERENCE_PATH") from error

    def _load_record(
        self,
        opened: OpenedProject,
        kind: ReferenceKind,
        record_root: Path,
        record_identity: FileIdentity,
        *,
        reference_id: UUID,
        allow_temporary_name: bool = False,
    ) -> StoredReference:
        expected_name = f"{reference_id}.tmp" if allow_temporary_name else str(reference_id)
        if (
            record_root.parent != opened.uploads_root / _KIND_ROOTS[kind]
            or record_root.name != expected_name
        ):
            raise _reference_error("UNSAFE_REFERENCE_PATH")
        self._require_identity(record_root, record_identity)
        if _tree_contains_reparse_point(record_root):
            raise _reference_error("UNSAFE_REFERENCE_PATH")
        try:
            children = frozenset(path.name for path in record_root.iterdir())
        except OSError as error:
            raise _reference_error("UNSAFE_REFERENCE_PATH") from error
        if children != {"original.png", "metadata.json"}:
            raise _reference_error("CORRUPT_REFERENCE_RECORD")
        metadata = _read_reference_metadata(record_root / "metadata.json")
        if metadata.reference_id != reference_id or metadata.kind != kind:
            raise _reference_error("CORRUPT_REFERENCE_RECORD")
        payload = _read_reference_payload(record_root / "original.png")
        validated = validate_reference_png(payload)
        if (
            metadata.sha256 != validated.sha256
            or metadata.byte_size != validated.byte_size
            or metadata.width != validated.width
            or metadata.height != validated.height
            or metadata.mode != validated.mode
        ):
            raise _reference_error("CORRUPT_REFERENCE_RECORD")
        return metadata

    @staticmethod
    def _require_identity(path: Path, expected: FileIdentity | None) -> None:
        if expected is None or not matches_directory_identity(path, expected):
            raise _reference_error("UNSAFE_REFERENCE_PATH")


def _validate_payload_readback(readback: bytes, validated: ValidatedReference) -> None:
    if readback != validated.payload:
        raise ValueError("reference payload changed during write")
    if validate_reference_png(readback) != validated:
        raise ValueError("reference payload failed round-trip validation")


def _validate_metadata_readback(readback: bytes, reference: StoredReference) -> None:
    if _read_reference_metadata_bytes(readback) != reference:
        raise ValueError("reference metadata changed during write")


def _read_reference_metadata(path: Path) -> StoredReference:
    return _read_reference_metadata_bytes(_read_bounded_regular_file(path, MAX_REFERENCE_METADATA_BYTES))


def _read_reference_metadata_bytes(payload: bytes) -> StoredReference:
    try:
        return StoredReference.model_validate_json(payload, strict=True)
    except ValidationError as error:
        raise _reference_error("CORRUPT_REFERENCE_RECORD") from error


def _read_reference_payload(path: Path) -> bytes:
    return _read_bounded_regular_file(path, MAX_REFERENCE_BYTES)


def _read_bounded_regular_file(path: Path, maximum_size: int) -> bytes:
    try:
        status = os.lstat(path)
        if not stat.S_ISREG(status.st_mode) or is_reparse_point(path, status):
            raise OSError("reference file is not a safe regular file")
        if status.st_size > maximum_size:
            raise OSError("reference file exceeds its size limit")
        payload = path.read_bytes()
        if len(payload) > maximum_size:
            raise OSError("reference file exceeds its size limit")
        return payload
    except OSError as error:
        raise _reference_error("CORRUPT_REFERENCE_RECORD") from error


def _tree_contains_reparse_point(root: Path) -> bool:
    try:
        for current_root, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
            current = Path(current_root)
            for name in (*directory_names, *file_names):
                path = current / name
                status = os.lstat(path)
                if is_reparse_point(path, status):
                    return True
    except OSError:
        return True
    return False


def _remove_owned_temporary_tree(temporary_root: Path, expected_identity: FileIdentity) -> None:
    if (
        temporary_root.name.endswith(".tmp")
        and matches_directory_identity(temporary_root, expected_identity)
        and not _tree_contains_reparse_point(temporary_root)
    ):
        shutil.rmtree(temporary_root)


def _require_kind(kind: ReferenceKind) -> None:
    if kind not in _KIND_ROOTS:
        raise TypeError("kind must be 'style' or 'structure'")


def _reference_error(code: str) -> ReferenceStoreError:
    messages = {
        "REFERENCE_NOT_FOUND": "未找到该参考图",
        "REFERENCE_ALREADY_EXISTS": "参考图已存在",
        "CORRUPT_REFERENCE_RECORD": "参考图库记录损坏或不一致",
        "REFERENCE_STORAGE_UNAVAILABLE": "无法安全保存参考图",
        "UNSAFE_REFERENCE_PATH": "参考图库目录不安全",
    }
    return ReferenceStoreError(code, messages[code])
