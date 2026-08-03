import hashlib
import io
import json
import os
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image

import aimctexturegen.references.store as store_module
from aimctexturegen.projects.models import ProjectManifest, dump_project_manifest
from aimctexturegen.projects.repository import ProjectRepository
from aimctexturegen.references.models import ValidatedReference
from aimctexturegen.references.store import ProjectReferenceStore


NOW = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")


def _manifest() -> ProjectManifest:
    return ProjectManifest(
        schema_version=2,
        project_id=PROJECT_ID,
        project_name="Reference store project",
        edition="java",
        java_pack_format=34,
        supported_formats=None,
        catalog_id="java-dev-format-34",
        source_sha256="ab" * 32,
        created_at=NOW,
        updated_at=NOW,
        default_resolution=16,
        default_parallelism=1,
        style_references=(),
    )


def _write_project(projects_root: Path) -> Path:
    project_root = projects_root / str(PROJECT_ID)
    (project_root / "source").mkdir(parents=True)
    (project_root / "pack").mkdir()
    (project_root / "project.json").write_bytes(dump_project_manifest(_manifest()))
    return project_root


def _store(projects_root: Path) -> ProjectReferenceStore:
    return ProjectReferenceStore(ProjectRepository(projects_root))


def _validated(payload: bytes = b"\x89PNG\r\n\x1a\nfake-png") -> ValidatedReference:
    image_bytes = payload
    if payload in {b"\x89PNG\r\n\x1a\nfake-png", b"struct"}:
        buffer = io.BytesIO()
        Image.new("RGB", (16, 16), 96).save(buffer, format="PNG")
        image_bytes = buffer.getvalue()
    return ValidatedReference(
        payload=image_bytes,
        sha256=hashlib.sha256(image_bytes).hexdigest(),
        byte_size=len(image_bytes),
        width=16,
        height=16,
        mode="RGB",
    )


def _create_junction(link: Path, target: Path) -> None:
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"Unable to create test junction: {result.stdout}{result.stderr}")


def _remove_junction(link: Path) -> None:
    if os.path.lexists(link):
        os.rmdir(link)


def test_create_read_list_and_delete_publish_only_server_owned_records(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root)
    store = _store(projects_root)
    validated = _validated()

    stored = store.create(PROJECT_ID, "style", validated, now=NOW)

    assert stored.kind == "style"
    assert store.read_content(PROJECT_ID, "style", stored.reference_id) == validated.payload
    listed = store.list(PROJECT_ID, "style")
    assert listed == (stored,)
    record_root = project_root / "uploads" / "style-references" / str(stored.reference_id)
    assert record_root.name != "client-name.png"
    assert (record_root / "original.png").read_bytes() == validated.payload
    metadata = json.loads((record_root / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["schema_version"] == 1
    assert metadata["reference_id"] == str(stored.reference_id)
    assert metadata["kind"] == "style"
    assert metadata["sha256"] == validated.sha256

    store.delete(PROJECT_ID, "style", stored.reference_id)

    assert store.list(PROJECT_ID, "style") == ()
    assert not record_root.exists()


def test_create_rejects_junction_below_upload_kind_root(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root)
    outside = tmp_path / "outside"
    outside.mkdir()
    kind_root = project_root / "uploads" / "style-references"
    kind_root.parent.mkdir(parents=True, exist_ok=True)
    _create_junction(kind_root, outside)

    try:
        with pytest.raises(store_module.ReferenceStoreError) as captured:
            _store(projects_root).create(PROJECT_ID, "style", _validated(), now=NOW)
    finally:
        _remove_junction(kind_root)

    assert captured.value.code == "UNSAFE_REFERENCE_PATH"


def test_create_cleans_temporary_record_when_metadata_publication_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root)
    store = _store(projects_root)
    real_replace = store_module.atomic_replace_bytes
    calls = 0

    def fail_second_write(destination, payload, validator):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("inject metadata failure")
        return real_replace(destination, payload, validator)

    monkeypatch.setattr(store_module, "atomic_replace_bytes", fail_second_write)

    with pytest.raises(store_module.ReferenceStoreError) as captured:
        store.create(PROJECT_ID, "style", _validated(), now=NOW)

    assert captured.value.code == "REFERENCE_STORAGE_UNAVAILABLE"
    uploads_root = project_root / "uploads" / "style-references"
    assert not any(path.name.endswith(".tmp") for path in uploads_root.iterdir())
    assert tuple(path.name for path in uploads_root.iterdir()) == ()


def test_delete_removes_only_library_record_not_job_local_copy(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root)
    store = _store(projects_root)
    stored = store.create(PROJECT_ID, "structure", _validated(b"struct"), now=NOW)
    frozen = project_root / "jobs" / "job-1" / "inputs"
    frozen.mkdir(parents=True)
    snapshot = frozen / "structure.png"
    snapshot.write_bytes(b"struct")

    store.delete(PROJECT_ID, "structure", stored.reference_id)

    assert snapshot.read_bytes() == b"struct"
    assert store.list(PROJECT_ID, "structure") == ()


def test_delete_keeps_record_directory_guarded_during_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root)
    store = _store(projects_root)
    stored = store.create(PROJECT_ID, "style", _validated(), now=NOW)
    record_root = project_root / "uploads" / "style-references" / str(stored.reference_id)
    real_hold = store_module.hold_directory_identity
    real_rmtree = store_module.shutil.rmtree
    active_record_guards = 0

    @contextmanager
    def tracking_hold(path: Path):
        nonlocal active_record_guards
        with real_hold(path) as identity:
            is_record = path == record_root
            if is_record:
                active_record_guards += 1
            try:
                yield identity
            finally:
                if is_record:
                    active_record_guards -= 1

    def checked_rmtree(path: Path, *args, **kwargs) -> None:
        assert path == record_root
        assert active_record_guards == 1
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(store_module, "hold_directory_identity", tracking_hold)
    monkeypatch.setattr(store_module.shutil, "rmtree", checked_rmtree)

    store.delete(PROJECT_ID, "style", stored.reference_id)

    assert not record_root.exists()
