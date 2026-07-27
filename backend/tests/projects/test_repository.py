import ctypes
import hashlib
import json
import os
import subprocess
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

import aimctexturegen.core.atomic_files as atomic_files_module
import aimctexturegen.projects.repository as repository_module
from aimctexturegen.projects.models import (
    ProjectManifest,
    ProjectManifestV1,
    dump_project_manifest,
    load_project_manifest,
)
from aimctexturegen.projects.repository import (
    ProjectRepository,
    ProjectRepositoryError,
)


def _manifest(
    project_id: UUID,
    *,
    updated_at: datetime | None = None,
) -> ProjectManifest:
    timestamp = updated_at or datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc)
    return ProjectManifest(
        schema_version=2,
        project_id=project_id,
        project_name=f"Project {project_id}",
        edition="java",
        java_pack_format=34,
        supported_formats=None,
        catalog_id="java-dev-format-34",
        source_sha256="ab" * 32,
        created_at=timestamp,
        updated_at=timestamp,
        default_resolution=16,
        default_parallelism=1,
        style_references=(),
    )


def _write_project(
    projects_root: Path,
    manifest: ProjectManifest,
    *,
    schema_1: bool = False,
) -> Path:
    project_root = projects_root / str(manifest.project_id)
    (project_root / "source").mkdir(parents=True)
    (project_root / "pack").mkdir()
    if schema_1:
        old = ProjectManifestV1.model_validate(
            {
                field: 1 if field == "schema_version" else getattr(manifest, field)
                for field in ProjectManifestV1.model_fields
            }
        )
        payload = (old.model_dump_json() + "\n").encode("utf-8")
    else:
        payload = dump_project_manifest(manifest)
    (project_root / "project.json").write_bytes(payload)
    return project_root


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


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


def _replace_with_posix_semantics(destination: Path, payload: bytes) -> None:
    attacker_path = destination.with_name(f"{destination.name}.attacker")
    handle = atomic_files_module._create_windows_file(attacker_path)
    descriptor = atomic_files_module._open_windows_handle_as_descriptor(handle)
    with os.fdopen(descriptor, "w+b") as attacker:
        attacker.write(payload)
        attacker.flush()
        native_handle = atomic_files_module._windows_handle_from_descriptor(
            attacker.fileno()
        )
        target_bytes = os.path.abspath(destination).encode("utf-16-le")
        filename_offset = atomic_files_module._FileRenameInfo.FileName.offset
        buffer = ctypes.create_string_buffer(
            filename_offset
            + len(target_bytes)
            + ctypes.sizeof(wintypes.WCHAR)
        )
        ctypes.c_uint32.from_buffer(buffer).value = 0x00000001 | 0x00000002
        information = ctypes.cast(
            buffer,
            ctypes.POINTER(atomic_files_module._FileRenameInfo),
        ).contents
        information.RootDirectory = None
        information.FileNameLength = len(target_bytes)
        ctypes.memmove(
            ctypes.addressof(buffer) + filename_offset,
            target_bytes,
            len(target_bytes),
        )
        if not atomic_files_module._set_file_information(
            native_handle,
            22,
            buffer,
            len(buffer),
        ):
            atomic_files_module._raise_windows_error(destination)


def test_open_migrates_schema_1_once_without_changing_source_or_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects_root = tmp_path / "projects"
    project_id = UUID("11111111-1111-4111-8111-111111111111")
    project_root = _write_project(
        projects_root,
        _manifest(project_id),
        schema_1=True,
    )
    (project_root / "source" / "imported-pack.zip").write_bytes(b"snapshot")
    (project_root / "pack" / "pack.mcmeta").write_text("metadata", encoding="utf-8")
    protected_before = {
        "source": _tree_hashes(project_root / "source"),
        "pack": _tree_hashes(project_root / "pack"),
    }
    replacements: list[Path] = []
    real_replace = repository_module.atomic_replace_bytes

    def record_replace(destination, payload, validator, *args, **kwargs):
        replacements.append(destination)
        return real_replace(destination, payload, validator, *args, **kwargs)

    monkeypatch.setattr(repository_module, "atomic_replace_bytes", record_replace)
    repository = ProjectRepository(projects_root)

    with repository.open(project_id) as opened:
        assert opened.manifest.schema_version == 2
        assert opened.manifest.default_resolution == 16
        assert opened.root == project_root
        assert opened.pack_root == project_root / "pack"
        assert opened.jobs_root == project_root / "jobs"
        assert opened.uploads_root == project_root / "uploads"
    with repository.open(project_id) as reopened:
        assert reopened.manifest == opened.manifest

    migrated, needs_migration = load_project_manifest(
        (project_root / "project.json").read_bytes()
    )
    assert migrated == opened.manifest
    assert needs_migration is False
    assert replacements == [project_root / "project.json"]
    assert protected_before == {
        "source": _tree_hashes(project_root / "source"),
        "pack": _tree_hashes(project_root / "pack"),
    }


def test_schema_1_migration_preserves_concurrent_newer_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects_root = tmp_path / "projects"
    project_id = UUID("12121212-1212-4212-8212-121212121212")
    project_root = _write_project(
        projects_root,
        _manifest(project_id),
        schema_1=True,
    )
    concurrent_manifest = _manifest(project_id).model_copy(
        update={
            "project_name": "Concurrent",
            "default_resolution": 64,
        }
    )
    concurrent_payload = dump_project_manifest(concurrent_manifest)
    real_replace = repository_module.atomic_replace_bytes

    def replace_destination_before_publication(
        destination,
        payload,
        validator,
        *args,
        **kwargs,
    ):
        destination.write_bytes(concurrent_payload)
        return real_replace(destination, payload, validator, *args, **kwargs)

    monkeypatch.setattr(
        repository_module,
        "atomic_replace_bytes",
        replace_destination_before_publication,
    )

    with pytest.raises(ProjectRepositoryError) as captured:
        with ProjectRepository(projects_root).open(project_id):
            pass

    assert captured.value.code == "PROJECT_MANIFEST_CONFLICT"
    assert (project_root / "project.json").read_bytes() == concurrent_payload


def test_schema_1_migration_protects_after_validation_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects_root = tmp_path / "projects"
    project_id = UUID("13131313-1313-4313-8313-131313131313")
    project_root = _write_project(
        projects_root,
        _manifest(project_id),
        schema_1=True,
    )
    concurrent_manifest = _manifest(project_id).model_copy(
        update={
            "project_name": "Concurrent after validation",
            "default_resolution": 64,
        }
    )
    concurrent_payload = dump_project_manifest(concurrent_manifest)
    real_publish = atomic_files_module._publish_open_file

    def replace_after_validation(handle, destination, *args, **kwargs):
        _replace_with_posix_semantics(destination, concurrent_payload)
        return real_publish(handle, destination, *args, **kwargs)

    monkeypatch.setattr(
        atomic_files_module,
        "_publish_open_file",
        replace_after_validation,
    )

    with pytest.raises(ProjectRepositoryError) as captured:
        with ProjectRepository(projects_root).open(project_id):
            pass

    assert captured.value.code == "PROJECT_MANIFEST_CONFLICT"
    assert (project_root / "project.json").read_bytes() == concurrent_payload


def test_list_manifests_is_deterministic_and_does_not_follow_unsafe_entries(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    older_id = UUID("22222222-2222-4222-8222-222222222222")
    first_tied_id = UUID("11111111-1111-4111-8111-111111111111")
    second_tied_id = UUID("33333333-3333-4333-8333-333333333333")
    older = datetime(2026, 7, 26, tzinfo=timezone.utc)
    newer = datetime(2026, 7, 27, tzinfo=timezone.utc)
    for manifest in (
        _manifest(older_id, updated_at=older),
        _manifest(second_tied_id, updated_at=newer),
        _manifest(first_tied_id, updated_at=newer),
    ):
        _write_project(projects_root, manifest)

    (projects_root / f"{older_id}.tmp").mkdir()
    (projects_root / ".aimctexturegen").mkdir()
    (projects_root / "not-a-project").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    junction_id = UUID("44444444-4444-4444-8444-444444444444")
    junction = projects_root / str(junction_id)
    _create_junction(junction, outside)

    try:
        result = ProjectRepository(projects_root).list_manifests()
    finally:
        _remove_junction(junction)

    assert tuple(manifest.project_id for manifest in result.manifests) == (
        first_tied_id,
        second_tied_id,
        older_id,
    )
    assert tuple(issue.project_id for issue in result.issues) == (junction_id,)
    assert result.issues[0].code == "UNSAFE_PROJECT_PATH"


def test_corrupt_manifest_is_typed_and_does_not_hide_valid_sibling(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    valid_id = UUID("55555555-5555-4555-8555-555555555555")
    corrupt_id = UUID("66666666-6666-4666-8666-666666666666")
    _write_project(projects_root, _manifest(valid_id))
    corrupt_root = projects_root / str(corrupt_id)
    corrupt_root.mkdir(parents=True)
    (corrupt_root / "project.json").write_text(
        json.dumps({"schema_version": 2}),
        encoding="utf-8",
    )
    repository = ProjectRepository(projects_root)

    result = repository.list_manifests()

    assert tuple(manifest.project_id for manifest in result.manifests) == (valid_id,)
    assert len(result.issues) == 1
    assert result.issues[0].project_id == corrupt_id
    assert result.issues[0].code == "CORRUPT_PROJECT_MANIFEST"
    with pytest.raises(ProjectRepositoryError) as captured:
        with repository.open(corrupt_id):
            pass
    assert captured.value.code == "CORRUPT_PROJECT_MANIFEST"


def test_open_holds_project_directory_identity_for_full_context(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    project_id = UUID("77777777-7777-4777-8777-777777777777")
    project_root = _write_project(projects_root, _manifest(project_id))
    replacement = projects_root / f"{project_id}.replacement"
    repository = ProjectRepository(projects_root)

    with pytest.raises(ProjectRepositoryError) as captured:
        with repository.open(project_id):
            project_root.rename(replacement)
            project_root.mkdir()
    assert captured.value.code == "UNSAFE_PROJECT_PATH"

    project_root.rmdir()
    replacement.rename(project_root)


def test_open_revalidates_identity_when_context_body_raises(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    project_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    project_root = _write_project(projects_root, _manifest(project_id))
    replacement = projects_root / f"{project_id}.replacement"
    repository = ProjectRepository(projects_root)

    with pytest.raises(ProjectRepositoryError) as captured:
        with repository.open(project_id):
            project_root.rename(replacement)
            project_root.mkdir()
            raise RuntimeError("injected body failure")
    assert captured.value.code == "UNSAFE_PROJECT_PATH"

    project_root.rmdir()
    replacement.rename(project_root)


def test_open_rejects_manifest_identity_mismatch_and_missing_project(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    directory_id = UUID("88888888-8888-4888-8888-888888888888")
    other_id = UUID("99999999-9999-4999-8999-999999999999")
    project_root = _write_project(projects_root, _manifest(directory_id))
    (project_root / "project.json").write_bytes(
        dump_project_manifest(_manifest(other_id))
    )
    repository = ProjectRepository(projects_root)

    with pytest.raises(ProjectRepositoryError) as mismatch:
        with repository.open(directory_id):
            pass
    assert mismatch.value.code == "CORRUPT_PROJECT_MANIFEST"

    with pytest.raises(ProjectRepositoryError) as missing:
        with repository.open(other_id):
            pass
    assert missing.value.code == "PROJECT_NOT_FOUND"
