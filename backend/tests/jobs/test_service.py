import hashlib
import io
import os
import subprocess
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image

from aimctexturegen.catalog.models import CatalogProfile
from aimctexturegen.catalog.registry import CatalogRegistry
from aimctexturegen.jobs.errors import JobError
from aimctexturegen.jobs.models import (
    CreateJobCommand,
    JobSummary,
    MAX_SAFE_SEED,
    ModelProfileBinding,
)
from aimctexturegen.jobs.service import JobService
from aimctexturegen.jobs.store import JobStore
from aimctexturegen.projects.models import ProjectManifest, dump_project_manifest
from aimctexturegen.projects.repository import ProjectRepository


CATALOG_ROOT = Path(__file__).parents[3] / "catalogs" / "java"
CREATED_AT = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)
PROJECT_ID = UUID("44444444-4444-4444-8444-444444444444")
JOB_ID = UUID("55555555-5555-4555-8555-555555555555")
RETRY_ID = UUID("66666666-6666-4666-8666-666666666666")
STYLE_PATH = "assets/minecraft/textures/block/stone.png"
STRUCTURE_PATH = "uploads/structure-references/layout.png"


def _png() -> bytes:
    payload = io.BytesIO()
    Image.new("RGB", (16, 16), (32, 32, 32)).save(payload, format="PNG")
    return payload.getvalue()


def _manifest() -> ProjectManifest:
    return ProjectManifest(
        schema_version=2,
        project_id=PROJECT_ID,
        project_name="Job service project",
        edition="java",
        java_pack_format=34,
        supported_formats=None,
        catalog_id="java-dev-format-34",
        source_sha256="cd" * 32,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
        default_resolution=16,
        default_parallelism=1,
        style_references=(),
    )


def _write_project(
    projects_root: Path,
    *,
    include_style: bool = True,
    include_target: bool = False,
    include_structure: bool = False,
) -> Path:
    project_root = projects_root / str(PROJECT_ID)
    (project_root / "source").mkdir(parents=True)
    (project_root / "pack").mkdir()
    (project_root / "source" / "imported-pack.zip").write_bytes(b"snapshot")
    (project_root / "pack" / "pack.mcmeta").write_bytes(b"metadata")
    if include_style:
        style = project_root / "pack" / Path(*STYLE_PATH.split("/"))
        style.parent.mkdir(parents=True)
        style.write_bytes(_png())
    if include_target:
        target = (
            project_root
            / "pack"
            / "assets"
            / "minecraft"
            / "textures"
            / "block"
            / "deepslate.png"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_png())
    if include_structure:
        structure = project_root / Path(*STRUCTURE_PATH.split("/"))
        structure.parent.mkdir(parents=True)
        structure.write_bytes(_png())
    (project_root / "project.json").write_bytes(dump_project_manifest(_manifest()))
    return project_root


def _command(
    *,
    target: str = "minecraft:deepslate",
    style_references: tuple[str, ...] = (STYLE_PATH,),
    structure_reference: str | None = None,
) -> CreateJobCommand:
    return CreateJobCommand(
        target_semantic_id=target,
        prompt="cold blue-gray stone",
        resolution=16,
        parallelism=1,
        style_references=style_references,
        structure_reference=structure_reference,
    )


class SequenceSource:
    def __init__(self, values: tuple[object, ...]) -> None:
        self._values: Iterator[object] = iter(values)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return next(self._values)


class SequenceIds:
    def __init__(self, values: tuple[UUID, ...]) -> None:
        self._values = iter(values)
        self.calls = 0

    def __call__(self) -> UUID:
        self.calls += 1
        return next(self._values)


class RecordingIndex:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.upserts: list[JobSummary] = []

    def upsert_job(self, summary: JobSummary) -> None:
        self.upserts.append(summary)
        if self.fail:
            raise OSError("injected index failure")


class StaticCatalogs:
    def __init__(self, profile: CatalogProfile) -> None:
        self.profile = profile

    def for_pack_format(self, _pack_format: int) -> CatalogProfile:
        return self.profile


def _service(
    projects_root: Path,
    *,
    seeds: SequenceSource | None = None,
    ids: SequenceIds | None = None,
    index: RecordingIndex | None = None,
    catalogs=None,
    now: datetime = CREATED_AT,
) -> tuple[JobService, JobStore, SequenceSource, SequenceIds, RecordingIndex]:
    repository = ProjectRepository(projects_root)
    store = JobStore(repository)
    seed_source = seeds or SequenceSource((10, 20, 30, 40))
    id_source = ids or SequenceIds((JOB_ID, RETRY_ID))
    job_index = index or RecordingIndex()
    service = JobService(
        repository=repository,
        catalogs=catalogs or CatalogRegistry(CATALOG_ROOT),
        store=store,
        index=job_index,
        seed_source=seed_source,
        job_id_source=id_source,
        clock=lambda: now,
    )
    return service, store, seed_source, id_source, job_index


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


def test_create_resolves_missing_target_and_persists_four_unique_safe_seeds(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root, include_structure=True)
    protected_before = {
        "source": _tree_hashes(project_root / "source"),
        "pack": _tree_hashes(project_root / "pack"),
    }
    seeds = SequenceSource((10, 10, 20, 30, 40))
    service, store, seed_source, ids, index = _service(
        projects_root,
        seeds=seeds,
    )

    loaded = service.create_job(
        PROJECT_ID,
        _command(structure_reference=STRUCTURE_PATH),
    )

    assert loaded.request.job_id == JOB_ID
    assert loaded.request.project_id == PROJECT_ID
    assert loaded.request.catalog_id == "java-dev-format-34"
    assert loaded.request.target_semantic_id == "minecraft:deepslate"
    assert loaded.request.target_display_name == "Deepslate"
    assert loaded.request.target_relative_path == (
        "assets/minecraft/textures/block/deepslate.png"
    )
    assert loaded.request.seeds == (10, 20, 30, 40)
    assert loaded.request.created_at == CREATED_AT
    assert seed_source.calls == 5
    assert ids.calls == 1
    assert store.load(PROJECT_ID, JOB_ID) == loaded
    assert index.upserts == [_summary(loaded)]
    assert protected_before == {
        "source": _tree_hashes(project_root / "source"),
        "pack": _tree_hashes(project_root / "pack"),
    }


@pytest.mark.parametrize(
    ("command", "include_target", "expected_code"),
    [
        (_command(target="minecraft:unknown"), False, "JOB_TARGET_NOT_FOUND"),
        (_command(), True, "JOB_TARGET_NOT_MISSING"),
    ],
)
def test_create_rejects_unknown_or_already_covered_target_before_persistence(
    tmp_path: Path,
    command: CreateJobCommand,
    include_target: bool,
    expected_code: str,
) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root, include_target=include_target)
    service, store, seeds, ids, index = _service(projects_root)

    with pytest.raises(JobError) as captured:
        service.create_job(PROJECT_ID, command)

    assert captured.value.code == expected_code
    assert store.list(PROJECT_ID) == ()
    assert seeds.calls == 0
    assert ids.calls == 0
    assert index.upserts == []
    assert not (project_root / "jobs").exists()


def test_create_rejects_ineligible_target_before_drawing_seeds(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    profile = CatalogRegistry(CATALOG_ROOT).for_pack_format(34)
    entries = tuple(
        entry.model_copy(update={"mvp_eligible": False})
        if entry.semantic_id == "minecraft:deepslate"
        else entry
        for entry in profile.entries
    )
    service, store, seeds, ids, _index = _service(
        projects_root,
        catalogs=StaticCatalogs(profile.model_copy(update={"entries": entries})),
    )

    with pytest.raises(JobError) as captured:
        service.create_job(PROJECT_ID, _command())

    assert captured.value.code == "JOB_TARGET_NOT_ELIGIBLE"
    assert store.list(PROJECT_ID) == ()
    assert seeds.calls == 0
    assert ids.calls == 0


@pytest.mark.parametrize(
    ("style_references", "structure_reference", "expected_code"),
    [
        (("assets/minecraft/textures/block/missing.png",), None, "INVALID_STYLE_REFERENCE"),
        ((STYLE_PATH,), "layout.png", "INVALID_STRUCTURE_REFERENCE"),
        ((STYLE_PATH,), STRUCTURE_PATH, "INVALID_STRUCTURE_REFERENCE"),
    ],
)
def test_create_rejects_missing_or_out_of_scope_references(
    tmp_path: Path,
    style_references: tuple[str, ...],
    structure_reference: str | None,
    expected_code: str,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    service, store, seeds, ids, _index = _service(projects_root)

    with pytest.raises(JobError) as captured:
        service.create_job(
            PROJECT_ID,
            _command(
                style_references=style_references,
                structure_reference=structure_reference,
            ),
        )

    assert captured.value.code == expected_code
    assert store.list(PROJECT_ID) == ()
    assert seeds.calls == 0
    assert ids.calls == 0


def test_create_rejects_style_reference_below_reparse_ancestor(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "style.png").write_bytes(_png())
    link = project_root / "pack" / "custom"
    _create_junction(link, outside)
    service, store, seeds, ids, _index = _service(projects_root)

    try:
        with pytest.raises(JobError) as captured:
            service.create_job(
                PROJECT_ID,
                _command(style_references=("custom/style.png",)),
            )
    finally:
        _remove_junction(link)

    assert captured.value.code == "INVALID_STYLE_REFERENCE"
    assert store.list(PROJECT_ID) == ()
    assert seeds.calls == 0
    assert ids.calls == 0


@pytest.mark.parametrize("bad_seed", [-1, MAX_SAFE_SEED + 1, True, "10"])
def test_create_rejects_non_js_safe_seed_source_values(
    tmp_path: Path,
    bad_seed: object,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    service, store, seeds, ids, index = _service(
        projects_root,
        seeds=SequenceSource((bad_seed, 10, 20, 30, 40)),
    )

    with pytest.raises(JobError) as captured:
        service.create_job(PROJECT_ID, _command())

    assert captured.value.code == "INVALID_SEED_SOURCE"
    assert store.list(PROJECT_ID) == ()
    assert seeds.calls == 1
    assert ids.calls == 0
    assert index.upserts == []


def test_index_failure_happens_after_canonical_job_commit(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    failing_index = RecordingIndex(fail=True)
    service, store, _seeds, _ids, index = _service(
        projects_root,
        index=failing_index,
    )

    with pytest.raises(JobError) as captured:
        service.create_job(PROJECT_ID, _command())

    assert captured.value.code == "INDEX_UNAVAILABLE"
    persisted = store.load(PROJECT_ID, JOB_ID)
    assert index.upserts == [_summary(persisted)]


def test_service_get_list_cancel_and_retry_update_index_without_new_seeds(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    service, _store, seeds, ids, index = _service(projects_root)
    created = service.create_job(PROJECT_ID, _command())

    assert service.get_job(PROJECT_ID, JOB_ID) == created
    assert service.list_jobs(PROJECT_ID) == (created,)
    canceled = service.cancel_job(
        PROJECT_ID,
        JOB_ID,
        expected_revision=0,
    )
    retried = service.retry_job(PROJECT_ID, JOB_ID)

    assert canceled.state.status == "canceled"
    assert retried.request.job_id == RETRY_ID
    assert retried.request.retry_of_job_id == JOB_ID
    assert retried.request.seeds == created.request.seeds
    assert seeds.calls == 4
    assert ids.calls == 2
    assert index.upserts == [
        _summary(created),
        _summary(canceled),
        _summary(retried),
    ]


def test_public_job_listing_keeps_valid_jobs_visible_beside_malformed_sibling(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    service, store, _seeds, _ids, _index = _service(projects_root)
    valid = service.create_job(PROJECT_ID, _command())
    malformed = store.create(
        valid.request.model_copy(
            update={
                "job_id": RETRY_ID,
                "created_at": CREATED_AT + timedelta(seconds=1),
            }
        )
    )
    malformed_state = b"{malformed state remains for recovery reporting"
    (malformed.root / "state.json").write_bytes(malformed_state)

    assert service.list_jobs(PROJECT_ID) == (valid,)
    scan = store.scan(PROJECT_ID)
    assert tuple(issue.job_id for issue in scan.issues) == (RETRY_ID,)
    assert (malformed.root / "state.json").read_bytes() == malformed_state


def _binding(kind: str = "text2img") -> ModelProfileBinding:
    return ModelProfileBinding(
        profile_id="sdxl-mapchip-ipadapter",
        profile_version="1",
        profile_manifest_sha256="a" * 64,
        runtime_id="comfyui-windows-nvidia",
        runtime_version="0.29.2",
        runtime_manifest_sha256="b" * 64,
        workflow_kind=kind,
        workflow_sha256="c" * 64,
    )


def test_create_with_binding_persists_schema2_frozen_request(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    service, store, _seeds, _ids, _index = _service(projects_root)

    loaded = service.create_job(
        PROJECT_ID,
        _command(),
        model_profile=_binding("text2img"),
    )

    assert loaded.request.schema_version == 2
    assert loaded.request.model_profile == _binding("text2img")
    assert loaded.request.execution_eligibility == "bound"
    raw = (loaded.root / "request.json").read_bytes()
    assert b'"schema_version":2' in raw
    assert b"sdxl-mapchip-ipadapter" in raw
    assert b"C:" not in raw
    assert store.load(PROJECT_ID, JOB_ID) == loaded


def test_create_without_binding_stays_schema1_legacy_unbound(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    service, _store, _seeds, _ids, _index = _service(projects_root)

    loaded = service.create_job(PROJECT_ID, _command())

    assert loaded.request.schema_version == 1
    assert loaded.request.model_profile is None
    assert loaded.request.execution_eligibility == "legacy_unbound"


def test_create_rejects_workflow_kind_mismatch_before_persistence(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    service, store, seeds, ids, index = _service(projects_root)

    with pytest.raises(JobError) as captured:
        service.create_job(
            PROJECT_ID,
            _command(),
            model_profile=_binding("img2img"),
        )

    assert captured.value.code == "PROFILE_WORKFLOW_MISMATCH"
    assert store.list(PROJECT_ID) == ()
    assert seeds.calls == 0
    assert ids.calls == 0
    assert index.upserts == []


def test_retry_preserves_the_frozen_binding(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    service, _store, _seeds, _ids, _index = _service(projects_root)
    created = service.create_job(
        PROJECT_ID,
        _command(),
        model_profile=_binding("text2img"),
    )
    service.cancel_job(PROJECT_ID, JOB_ID, expected_revision=0)

    retried = service.retry_job(PROJECT_ID, JOB_ID)

    assert retried.request.schema_version == 2
    assert retried.request.model_profile == created.request.model_profile
    assert retried.request.execution_eligibility == "bound"


def _summary(loaded) -> JobSummary:
    return JobSummary(
        job_id=loaded.request.job_id,
        project_id=loaded.request.project_id,
        retry_of_job_id=loaded.request.retry_of_job_id,
        target_semantic_id=loaded.request.target_semantic_id,
        target_display_name=loaded.request.target_display_name,
        resolution=loaded.request.resolution,
        parallelism=loaded.request.parallelism,
        status=loaded.state.status,
        revision=loaded.state.revision,
        candidate_statuses=tuple(
            candidate.status for candidate in loaded.state.candidates
        ),
        created_at=loaded.request.created_at,
        updated_at=loaded.state.updated_at,
    )
