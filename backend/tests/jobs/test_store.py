import hashlib
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

import aimctexturegen.jobs.store as store_module
from aimctexturegen.jobs.errors import JobError
from aimctexturegen.jobs.models import (
    CandidateRecord,
    JobFailure,
    JobRequest,
    JobStateRecord,
    dump_job_request,
    dump_job_state,
)
from aimctexturegen.jobs.state_machine import transition_job_state
from aimctexturegen.jobs.store import MAX_JOB_JSON_BYTES, JobStore
from aimctexturegen.projects.models import ProjectManifest, dump_project_manifest
from aimctexturegen.projects.repository import ProjectRepository


CREATED_AT = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
JOB_ID = UUID("22222222-2222-4222-8222-222222222222")
OTHER_JOB_ID = UUID("33333333-3333-4333-8333-333333333333")
SEEDS = (10, 20, 30, 40)


def _manifest(project_id: UUID = PROJECT_ID) -> ProjectManifest:
    return ProjectManifest(
        schema_version=2,
        project_id=project_id,
        project_name="Job store project",
        edition="java",
        java_pack_format=34,
        supported_formats=None,
        catalog_id="java-dev-format-34",
        source_sha256="ab" * 32,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
        default_resolution=16,
        default_parallelism=1,
        style_references=(),
    )


def _write_project(projects_root: Path) -> Path:
    project_root = projects_root / str(PROJECT_ID)
    (project_root / "source").mkdir(parents=True)
    (project_root / "pack").mkdir()
    (project_root / "source" / "imported-pack.zip").write_bytes(b"snapshot")
    (project_root / "pack" / "pack.mcmeta").write_bytes(b"metadata")
    (project_root / "project.json").write_bytes(dump_project_manifest(_manifest()))
    return project_root


def _request(
    *,
    job_id: UUID = JOB_ID,
    created_at: datetime = CREATED_AT,
) -> JobRequest:
    return JobRequest(
        schema_version=1,
        job_id=job_id,
        project_id=PROJECT_ID,
        retry_of_job_id=None,
        catalog_id="java-dev-format-34",
        target_semantic_id="minecraft:deepslate",
        target_display_name="Deepslate",
        target_relative_path="assets/minecraft/textures/block/deepslate.png",
        prompt="cold blue-gray stone",
        resolution=16,
        parallelism=1,
        style_references=("assets/minecraft/textures/block/stone.png",),
        structure_reference=None,
        seeds=SEEDS,
        created_at=created_at,
    )


def _queued_state(request: JobRequest) -> JobStateRecord:
    candidates = tuple(
        CandidateRecord(
            candidate_index=index,
            seed=seed,
            status="pending",
            failure=None,
            started_at=None,
            finished_at=None,
        )
        for index, seed in enumerate(request.seeds)
    )
    return JobStateRecord(
        schema_version=1,
        job_id=request.job_id,
        project_id=request.project_id,
        revision=0,
        status="queued",
        candidates=candidates,
        failure=None,
        created_at=request.created_at,
        updated_at=request.created_at,
        started_at=None,
        finished_at=None,
    )


def _store(projects_root: Path) -> JobStore:
    return JobStore(ProjectRepository(projects_root))


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


def test_create_publishes_exact_layout_and_canonical_queued_records(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root)
    protected_before = {
        "source": _tree_hashes(project_root / "source"),
        "pack": _tree_hashes(project_root / "pack"),
    }
    request = _request()

    loaded = _store(projects_root).create(request)

    job_root = project_root / "jobs" / str(JOB_ID)
    assert loaded.root == job_root
    assert loaded.request == request
    assert loaded.state == _queued_state(request)
    assert {path.name for path in job_root.iterdir()} == {
        "request.json",
        "state.json",
        "raw",
        "processed",
        "previews",
        "reports",
    }
    for name in ("raw", "processed", "previews", "reports"):
        assert (job_root / name).is_dir()
        assert tuple((job_root / name).iterdir()) == ()
    assert (job_root / "request.json").read_bytes() == dump_job_request(request)
    assert (job_root / "state.json").read_bytes() == dump_job_state(
        _queued_state(request)
    )
    assert not (project_root / "jobs" / f"{JOB_ID}.tmp").exists()
    assert protected_before == {
        "source": _tree_hashes(project_root / "source"),
        "pack": _tree_hashes(project_root / "pack"),
    }


def test_create_removes_owned_temporary_tree_after_injected_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root)
    real_replace = store_module.atomic_replace_bytes
    calls = 0

    def fail_second_write(destination, payload, validator):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected state write failure")
        return real_replace(destination, payload, validator)

    monkeypatch.setattr(store_module, "atomic_replace_bytes", fail_second_write)

    with pytest.raises(JobError) as captured:
        _store(projects_root).create(_request())

    assert captured.value.code == "JOB_STORAGE_UNAVAILABLE"
    assert not (project_root / "jobs" / str(JOB_ID)).exists()
    assert not (project_root / "jobs" / f"{JOB_ID}.tmp").exists()


def test_create_removes_temporary_directory_when_identity_capture_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root)

    def fail_identity_capture(_path: Path):
        raise OSError("injected identity capture failure")

    monkeypatch.setattr(
        store_module,
        "capture_directory_identity",
        fail_identity_capture,
    )

    with pytest.raises(JobError) as captured:
        _store(projects_root).create(_request())

    assert captured.value.code == "JOB_STORAGE_UNAVAILABLE"
    assert not (project_root / "jobs" / str(JOB_ID)).exists()
    assert not (project_root / "jobs" / f"{JOB_ID}.tmp").exists()


def test_create_preserves_substituted_directory_when_identity_capture_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root)
    temporary_root = project_root / "jobs" / f"{JOB_ID}.tmp"
    displaced_root = project_root / "jobs" / "displaced-created-job"

    def substitute_then_fail(path: Path):
        path.rename(displaced_root)
        path.mkdir()
        (path / "sentinel.txt").write_bytes(b"replacement")
        raise OSError("injected failure after directory substitution")

    monkeypatch.setattr(
        store_module,
        "capture_directory_identity",
        substitute_then_fail,
    )

    with pytest.raises(JobError) as captured:
        _store(projects_root).create(_request())

    assert captured.value.code == "JOB_STORAGE_UNAVAILABLE"
    assert displaced_root.is_dir()
    assert temporary_root.is_dir()
    assert (temporary_root / "sentinel.txt").read_bytes() == b"replacement"


def test_create_preserves_substituted_junction_when_identity_capture_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root)
    temporary_root = project_root / "jobs" / f"{JOB_ID}.tmp"
    displaced_root = project_root / "jobs" / "displaced-created-job"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel.txt").write_bytes(b"outside")

    def substitute_then_fail(path: Path):
        path.rename(displaced_root)
        _create_junction(path, outside)
        raise OSError("injected failure after junction substitution")

    monkeypatch.setattr(
        store_module,
        "capture_directory_identity",
        substitute_then_fail,
    )

    try:
        with pytest.raises(JobError) as captured:
            _store(projects_root).create(_request())

        assert captured.value.code == "JOB_STORAGE_UNAVAILABLE"
        assert displaced_root.is_dir()
        assert os.path.lexists(temporary_root)
        assert (outside / "sentinel.txt").read_bytes() == b"outside"
    finally:
        _remove_junction(temporary_root)


def test_create_rejects_existing_job_without_changing_it(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root)
    store = _store(projects_root)
    first = store.create(_request())
    before = _tree_hashes(first.root)

    with pytest.raises(JobError) as captured:
        store.create(_request())

    assert captured.value.code == "JOB_ALREADY_EXISTS"
    assert _tree_hashes(first.root) == before
    assert not (project_root / "jobs" / f"{JOB_ID}.tmp").exists()


def test_create_rejects_reparse_jobs_directory(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root)
    outside = tmp_path / "outside"
    outside.mkdir()
    jobs_root = project_root / "jobs"
    _create_junction(jobs_root, outside)

    try:
        with pytest.raises(JobError) as captured:
            _store(projects_root).create(_request())
    finally:
        _remove_junction(jobs_root)

    assert captured.value.code == "UNSAFE_JOBS_PATH"
    assert tuple(outside.iterdir()) == ()


def test_load_rejects_reparse_point_nested_below_artifact_directory(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    job = _store(projects_root).create(_request())
    outside = tmp_path / "outside-artifact"
    outside.mkdir()
    link = job.root / "raw" / "linked"
    _create_junction(link, outside)

    try:
        with pytest.raises(JobError) as captured:
            _store(projects_root).load(PROJECT_ID, JOB_ID)
    finally:
        _remove_junction(link)

    assert captured.value.code == "UNSAFE_JOB_PATH"


@pytest.mark.parametrize("corruption", ["oversized_request", "mismatched_state"])
def test_load_rejects_oversized_or_cross_identity_json(
    tmp_path: Path,
    corruption: str,
) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root)
    job = _store(projects_root).create(_request())
    if corruption == "oversized_request":
        (job.root / "request.json").write_bytes(b" " * (MAX_JOB_JSON_BYTES + 1))
    else:
        wrong_request = _request(job_id=OTHER_JOB_ID)
        (job.root / "state.json").write_bytes(
            dump_job_state(_queued_state(wrong_request))
        )

    with pytest.raises(JobError) as captured:
        _store(projects_root).load(PROJECT_ID, JOB_ID)

    assert captured.value.code == "CORRUPT_JOB_RECORD"
    assert (project_root / "source" / "imported-pack.zip").read_bytes() == b"snapshot"
    assert (project_root / "pack" / "pack.mcmeta").read_bytes() == b"metadata"


def test_list_is_deterministic_and_ignores_noncanonical_directories(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root)
    store = _store(projects_root)
    newer = _request(job_id=OTHER_JOB_ID, created_at=CREATED_AT + timedelta(minutes=1))
    tied_lower = _request(
        job_id=UUID("00000000-0000-4000-8000-000000000001"),
        created_at=CREATED_AT,
    )
    tied_higher = _request(
        job_id=UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
        created_at=CREATED_AT,
    )
    for request in (tied_higher, newer, tied_lower):
        store.create(request)
    (project_root / "jobs" / "not-a-job").mkdir()
    (project_root / "jobs" / f"{JOB_ID}.tmp").mkdir()

    first = store.list(PROJECT_ID)
    second = store.list(PROJECT_ID)

    expected = (OTHER_JOB_ID, tied_lower.job_id, tied_higher.job_id)
    assert tuple(job.request.job_id for job in first) == expected
    assert tuple(job.request.job_id for job in second) == expected


def test_scan_returns_valid_jobs_and_typed_issues_for_corrupt_siblings(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    store = _store(projects_root)
    valid = store.create(_request(job_id=OTHER_JOB_ID))
    corrupt = store.create(_request(job_id=JOB_ID))
    corrupt_state = b"{not valid job state json"
    (corrupt.root / "state.json").write_bytes(corrupt_state)

    result = store.scan(PROJECT_ID)

    assert tuple(job.request.job_id for job in result.jobs) == (
        valid.request.job_id,
    )
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert isinstance(issue, store_module.JobScanIssue)
    assert issue.job_id == corrupt.request.job_id
    assert issue.code == "CORRUPT_JOB_RECORD"
    assert issue.user_message == "任务记录损坏或不一致"
    assert not hasattr(issue, "path")
    assert (corrupt.root / "state.json").read_bytes() == corrupt_state


def test_replace_state_atomically_updates_only_the_expected_revision(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    store = _store(projects_root)
    original = store.create(_request())
    request_before = (original.root / "request.json").read_bytes()
    next_state = transition_job_state(
        original.state,
        "generating",
        now=CREATED_AT + timedelta(seconds=1),
    )

    updated = store.replace_state(
        PROJECT_ID,
        JOB_ID,
        next_state,
        expected_revision=0,
    )

    assert updated.state == next_state
    assert (original.root / "request.json").read_bytes() == request_before
    assert (original.root / "state.json").read_bytes() == dump_job_state(next_state)
    assert not (original.root / "state.json.tmp").exists()

    before_conflict = (original.root / "state.json").read_bytes()
    with pytest.raises(JobError) as captured:
        store.replace_state(
            PROJECT_ID,
            JOB_ID,
            next_state,
            expected_revision=0,
        )
    assert captured.value.code == "JOB_REVISION_CONFLICT"
    assert (original.root / "state.json").read_bytes() == before_conflict


def test_replace_state_cleans_bounded_stale_state_temporary(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    store = _store(projects_root)
    queued = store.create(_request())
    (queued.root / "state.json.tmp").write_bytes(b"stale")
    next_state = transition_job_state(
        queued.state,
        "generating",
        now=CREATED_AT + timedelta(seconds=1),
    )

    updated = store.replace_state(
        PROJECT_ID,
        JOB_ID,
        next_state,
        expected_revision=0,
    )

    assert updated.state == next_state
    assert not (queued.root / "state.json.tmp").exists()


def test_load_accepts_bounded_regular_stale_state_temporary(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    store = _store(projects_root)
    created = store.create(_request())
    temporary = created.root / "state.json.tmp"
    temporary.write_bytes(b"stale")

    loaded = store.load(PROJECT_ID, JOB_ID)

    assert loaded == created
    assert temporary.read_bytes() == b"stale"


def test_list_keeps_valid_siblings_visible_with_bounded_stale_state_temporary(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    store = _store(projects_root)
    stale = store.create(_request())
    sibling = store.create(_request(job_id=OTHER_JOB_ID))
    (stale.root / "state.json.tmp").write_bytes(b"stale")

    jobs = store.list(PROJECT_ID)

    assert {job.request.job_id for job in jobs} == {JOB_ID, OTHER_JOB_ID}
    assert sibling in jobs


def test_recover_interrupted_accepts_and_cleans_bounded_stale_state_temporary(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    store = _store(projects_root)
    queued = store.create(_request())
    generating = store.replace_state(
        PROJECT_ID,
        JOB_ID,
        transition_job_state(
            queued.state,
            "generating",
            now=CREATED_AT + timedelta(seconds=1),
        ),
        expected_revision=0,
    )
    temporary = generating.root / "state.json.tmp"
    temporary.write_bytes(b"stale")

    recovered = store.recover_interrupted(
        PROJECT_ID,
        JOB_ID,
        expected_revision=1,
        now=CREATED_AT + timedelta(seconds=2),
    )

    assert recovered.state.status == "failed"
    assert recovered.state.failure is not None
    assert recovered.state.failure.code == "JOB_INTERRUPTED"
    assert not temporary.exists()


@pytest.mark.parametrize("unsafe_kind", ["directory", "oversized", "junction"])
def test_load_rejects_unsafe_stale_state_temporary(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    store = _store(projects_root)
    created = store.create(_request())
    temporary = created.root / "state.json.tmp"
    outside = tmp_path / "outside-state-temp"
    if unsafe_kind == "directory":
        temporary.mkdir()
    elif unsafe_kind == "oversized":
        temporary.write_bytes(b"x" * (MAX_JOB_JSON_BYTES + 1))
    else:
        outside.mkdir()
        _create_junction(temporary, outside)

    try:
        with pytest.raises(JobError):
            store.load(PROJECT_ID, JOB_ID)
    finally:
        if unsafe_kind == "junction":
            _remove_junction(temporary)


@pytest.mark.parametrize("status", ["queued", "generating", "postprocessing"])
def test_cancel_persists_one_terminal_revision_for_each_cancelable_status(
    tmp_path: Path,
    status: str,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    store = _store(projects_root)
    loaded = store.create(_request())
    now = CREATED_AT
    if status in {"generating", "postprocessing"}:
        now += timedelta(seconds=1)
        loaded = store.replace_state(
            PROJECT_ID,
            JOB_ID,
            transition_job_state(loaded.state, "generating", now=now),
            expected_revision=loaded.state.revision,
        )
    if status == "postprocessing":
        now += timedelta(seconds=1)
        loaded = store.replace_state(
            PROJECT_ID,
            JOB_ID,
            transition_job_state(loaded.state, "postprocessing", now=now),
            expected_revision=loaded.state.revision,
        )

    canceled_at = now + timedelta(seconds=1)
    canceled = store.cancel(
        PROJECT_ID,
        JOB_ID,
        expected_revision=loaded.state.revision,
        now=canceled_at,
    )

    assert canceled.state.status == "canceled"
    assert canceled.state.revision == loaded.state.revision + 1
    assert canceled.state.updated_at == canceled_at
    assert canceled.state.finished_at == canceled_at
    assert all(
        candidate.status in {"completed", "failed", "canceled"}
        for candidate in canceled.state.candidates
    )
    assert store.load(PROJECT_ID, JOB_ID) == canceled


def test_cancel_terminal_job_fails_without_disk_changes(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    store = _store(projects_root)
    queued = store.create(_request())
    canceled = store.cancel(
        PROJECT_ID,
        JOB_ID,
        expected_revision=queued.state.revision,
        now=CREATED_AT + timedelta(seconds=1),
    )
    before = _tree_hashes(canceled.root)

    with pytest.raises(JobError) as captured:
        store.cancel(
            PROJECT_ID,
            JOB_ID,
            expected_revision=canceled.state.revision,
            now=CREATED_AT + timedelta(seconds=2),
        )

    assert captured.value.code == "INVALID_JOB_TRANSITION"
    assert _tree_hashes(canceled.root) == before


@pytest.mark.parametrize("source_status", ["failed", "canceled"])
def test_retry_publishes_new_lineage_and_preserves_source_bytes_and_seeds(
    tmp_path: Path,
    source_status: str,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    store = _store(projects_root)
    source = store.create(_request())
    now = CREATED_AT + timedelta(seconds=1)
    if source_status == "failed":
        source = store.replace_state(
            PROJECT_ID,
            JOB_ID,
            transition_job_state(source.state, "generating", now=now),
            expected_revision=source.state.revision,
        )
        now += timedelta(seconds=1)
        failure = JobFailure(
            code="INJECTED_FAILURE",
            stage="generating",
            user_message="injected",
            recommended_actions=(),
            technical_details=None,
            log_reference=None,
        )
        source = store.replace_state(
            PROJECT_ID,
            JOB_ID,
            transition_job_state(
                source.state,
                "failed",
                now=now,
                failure=failure,
            ),
            expected_revision=source.state.revision,
        )
    else:
        source = store.cancel(
            PROJECT_ID,
            JOB_ID,
            expected_revision=source.state.revision,
            now=now,
        )
    source_before = _tree_hashes(source.root)
    retried_at = now + timedelta(seconds=1)

    retried = store.retry(
        PROJECT_ID,
        JOB_ID,
        new_job_id=OTHER_JOB_ID,
        created_at=retried_at,
    )

    assert retried.request.job_id == OTHER_JOB_ID
    assert retried.request.retry_of_job_id == JOB_ID
    assert retried.request.created_at == retried_at
    preserved = set(JobRequest.model_fields) - {
        "job_id",
        "retry_of_job_id",
        "created_at",
    }
    for field in preserved:
        assert getattr(retried.request, field) == getattr(source.request, field)
    assert retried.state.status == "queued"
    assert retried.state.revision == 0
    assert tuple(candidate.seed for candidate in retried.state.candidates) == SEEDS
    assert _tree_hashes(source.root) == source_before


def test_retry_rejects_nonterminal_source_without_disk_changes(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    project_root = _write_project(projects_root)
    store = _store(projects_root)
    source = store.create(_request())
    source_before = _tree_hashes(source.root)

    with pytest.raises(JobError) as captured:
        store.retry(
            PROJECT_ID,
            JOB_ID,
            new_job_id=OTHER_JOB_ID,
            created_at=CREATED_AT + timedelta(seconds=1),
        )

    assert captured.value.code == "INVALID_JOB_TRANSITION"
    assert _tree_hashes(source.root) == source_before
    assert not (project_root / "jobs" / str(OTHER_JOB_ID)).exists()


def test_same_revision_updates_have_one_success_and_one_conflict(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    store = _store(projects_root)
    queued = store.create(_request())
    next_state = transition_job_state(
        queued.state,
        "generating",
        now=CREATED_AT + timedelta(seconds=1),
    )

    def update() -> str:
        try:
            store.replace_state(
                PROJECT_ID,
                JOB_ID,
                next_state,
                expected_revision=0,
            )
        except JobError as error:
            return error.code
        return "success"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _index: update(), range(2)))

    assert sorted(results) == ["JOB_REVISION_CONFLICT", "success"]
    assert store.load(PROJECT_ID, JOB_ID).state == next_state


def test_recover_interrupted_persists_active_failure_and_leaves_queued_bytes(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    store = _store(projects_root)
    queued = store.create(_request())
    queued_before = (queued.root / "state.json").read_bytes()

    unchanged = store.recover_interrupted(
        PROJECT_ID,
        JOB_ID,
        expected_revision=0,
        now=CREATED_AT + timedelta(seconds=1),
    )

    assert unchanged == queued
    assert (queued.root / "state.json").read_bytes() == queued_before

    generating = store.replace_state(
        PROJECT_ID,
        JOB_ID,
        transition_job_state(
            queued.state,
            "generating",
            now=CREATED_AT + timedelta(seconds=1),
        ),
        expected_revision=0,
    )
    recovered = store.recover_interrupted(
        PROJECT_ID,
        JOB_ID,
        expected_revision=generating.state.revision,
        now=CREATED_AT + timedelta(seconds=2),
    )

    assert recovered.state.status == "failed"
    assert recovered.state.failure is not None
    assert recovered.state.failure.code == "JOB_INTERRUPTED"
    assert recovered.state.revision == generating.state.revision + 1
    assert store.load(PROJECT_ID, JOB_ID) == recovered
