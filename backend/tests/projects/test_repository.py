import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

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

    def record_replace(destination, payload, validator):
        replacements.append(destination)
        return real_replace(destination, payload, validator)

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
