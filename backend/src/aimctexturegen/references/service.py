from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from uuid import UUID

from aimctexturegen.catalog.registry import CatalogRegistry
from aimctexturegen.jobs.store import JobInputFile, JobInputSnapshot
from aimctexturegen.packs.coverage import classify_coverage
from aimctexturegen.projects._directory_guard import hold_directory_identity, is_reparse_point
from aimctexturegen.projects.repository import ProjectRepository

from .models import (
    PackReference,
    PackReferenceSelection,
    ReferenceKind,
    ReferenceSelections,
    StoredReference,
    UploadReferenceSelection,
)
from .store import ProjectReferenceStore, ReferenceStoreError
from .validation import MAX_REFERENCE_BYTES, validate_reference_png


class ReferenceServiceError(Exception):
    def __init__(self, code: str, user_message: str) -> None:
        self.code = code
        self.user_message = user_message
        super().__init__(user_message)


class ReferenceService:
    def __init__(
        self,
        *,
        repository: ProjectRepository,
        catalogs: CatalogRegistry,
        store: ProjectReferenceStore,
    ) -> None:
        self._repository = repository
        self._catalogs = catalogs
        self._store = store

    def list_pack_references(self, project_id: UUID) -> tuple[PackReference, ...]:
        with self._repository.open(project_id) as opened:
            with hold_directory_identity(opened.pack_root):
                profile = self._catalogs.for_pack_format(opened.manifest.java_pack_format)
                coverage = classify_coverage(opened.pack_root, profile)
                display_names = {
                    item.relative_path: item.display_name
                    for item in coverage.items
                    if item.status == "covered"
                }
                candidates = sorted((*display_names.keys(), *coverage.unknown_paths))
                references: list[PackReference] = []
                for relative_path in candidates:
                    payload = _read_pack_payload(opened.pack_root, relative_path)
                    validated = validate_reference_png(payload)
                    references.append(
                        PackReference(
                            relative_path=relative_path,
                            display_name=display_names.get(relative_path, Path(relative_path).name),
                            sha256=validated.sha256,
                            byte_size=validated.byte_size,
                            width=validated.width,
                            height=validated.height,
                            mode=validated.mode,
                        )
                    )
                return tuple(references)

    def upload(self, project_id: UUID, kind: ReferenceKind, payload: bytes) -> StoredReference:
        validated = validate_reference_png(payload)
        return self._store.create(project_id, kind, validated, now=_now_utc())

    def list_uploads(self, project_id: UUID, kind: ReferenceKind) -> tuple[StoredReference, ...]:
        return self._store.list(project_id, kind)

    def delete(self, project_id: UUID, kind: ReferenceKind, reference_id: UUID) -> None:
        self._store.delete(project_id, kind, reference_id)

    def freeze(self, project_id: UUID, selections: ReferenceSelections) -> JobInputSnapshot:
        metadata = {"style": [], "structure": []}
        files: list[JobInputFile] = []
        with self._repository.open(project_id) as opened:
            with hold_directory_identity(opened.pack_root):
                for index, selection in enumerate(selections.style):
                    relative_path = f"style/{index:02d}.png"
                    frozen_relative_path = f"inputs/{relative_path}"
                    item = self._resolve_selection(opened, "style", selection)
                    files.append(
                        JobInputFile(
                            relative_path=relative_path,
                            payload=item["payload"],
                            sha256=item["sha256"],
                        )
                    )
                    metadata["style"].append(
                        {
                            "reference_id": f"style-{index:02d}",
                            "kind": "style",
                            "source": item["source"],
                            "source_relative_path": item.get("source_relative_path"),
                            "source_upload_reference_id": item.get("source_upload_reference_id"),
                            "display_label": item["display_label"],
                            "relative_path": frozen_relative_path,
                            "sha256": item["sha256"],
                            "byte_size": item["byte_size"],
                            "width": item["width"],
                            "height": item["height"],
                            "mode": item["mode"],
                        }
                    )
                if selections.structure is not None:
                    item = self._resolve_selection(opened, "structure", selections.structure)
                    files.append(
                        JobInputFile(
                            relative_path="structure.png",
                            payload=item["payload"],
                            sha256=item["sha256"],
                        )
                    )
                    metadata["structure"].append(
                        {
                            "reference_id": "structure-00",
                            "kind": "structure",
                            "source": item["source"],
                            "source_relative_path": item.get("source_relative_path"),
                            "source_upload_reference_id": item.get("source_upload_reference_id"),
                            "display_label": item["display_label"],
                            "relative_path": "inputs/structure.png",
                            "sha256": item["sha256"],
                            "byte_size": item["byte_size"],
                            "width": item["width"],
                            "height": item["height"],
                            "mode": item["mode"],
                        }
                    )
        payload = (json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        return JobInputSnapshot(references_json=payload, files=tuple(files))

    def _resolve_selection(self, opened, kind: ReferenceKind, selection):
        if isinstance(selection, PackReferenceSelection):
            payload = _read_pack_payload(opened.pack_root, selection.relative_path)
            validated = validate_reference_png(payload)
            return {
                "source": "pack",
                "source_relative_path": selection.relative_path,
                "display_label": selection.relative_path,
                "payload": payload,
                "sha256": validated.sha256,
                "byte_size": validated.byte_size,
                "width": validated.width,
                "height": validated.height,
                "mode": validated.mode,
            }
        if isinstance(selection, UploadReferenceSelection):
            try:
                stored = {
                    item.reference_id: item
                    for item in self._store.list(opened.manifest.project_id, kind)
                }[selection.reference_id]
                payload = self._store.read_content(opened.manifest.project_id, kind, selection.reference_id)
            except KeyError as error:
                raise _service_error("REFERENCE_NOT_FOUND") from error
            validated = validate_reference_png(payload)
            return {
                "source": "upload",
                "source_upload_reference_id": str(stored.reference_id),
                "display_label": str(stored.reference_id),
                "payload": payload,
                "sha256": validated.sha256,
                "byte_size": validated.byte_size,
                "width": validated.width,
                "height": validated.height,
                "mode": validated.mode,
            }
        raise TypeError("unsupported reference selection")


def _read_pack_payload(pack_root: Path, relative_path: str) -> bytes:
    target = pack_root / Path(*relative_path.split("/"))
    _ensure_plain_descendant(pack_root, target)
    try:
        status = os.lstat(target)
    except OSError as error:
        raise _service_error("REFERENCE_NOT_FOUND") from error
    if not stat.S_ISREG(status.st_mode) or is_reparse_point(target, status):
        raise _service_error("REFERENCE_INVALID")
    if status.st_size > MAX_REFERENCE_BYTES:
        raise _service_error("REFERENCE_INVALID")
    payload = target.read_bytes()
    if hashlib.sha256(payload).hexdigest() != validate_reference_png(payload).sha256:
        raise _service_error("REFERENCE_INVALID")
    return payload


def _ensure_plain_descendant(root: Path, target: Path) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError as error:
        raise _service_error("REFERENCE_INVALID") from error
    current = root
    for part in relative.parts[:-1]:
        current /= part
        try:
            status = os.lstat(current)
        except OSError as error:
            raise _service_error("REFERENCE_NOT_FOUND") from error
        if not stat.S_ISDIR(status.st_mode) or is_reparse_point(current, status):
            raise _service_error("REFERENCE_INVALID")


def _now_utc():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _service_error(code: str) -> ReferenceServiceError:
    messages = {
        "REFERENCE_NOT_FOUND": "未找到该参考图",
        "REFERENCE_INVALID": "参考图不符合格式要求",
    }
    return ReferenceServiceError(code, messages[code])
