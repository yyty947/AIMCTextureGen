import asyncio
import io
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from PIL import Image

from aimctexturegen.catalog.registry import CatalogRegistry
from aimctexturegen.jobs.errors import JobError
from aimctexturegen.main import AppServices, create_app
from aimctexturegen.projects.models import ProjectManifest


CATALOG_ROOT = Path(__file__).parents[3] / "catalogs" / "java"
PROJECT_ID = UUID("abcdefab-cdef-4abc-8def-abcdefabcdef")
STYLE_REFERENCE = "assets/minecraft/textures/block/stone.png"


def _write_project(projects_root: Path) -> None:
    project_root = projects_root / str(PROJECT_ID)
    pack_root = project_root / "pack"
    reference = pack_root / Path(*STYLE_REFERENCE.split("/"))
    reference.parent.mkdir(parents=True)
    payload = io.BytesIO()
    Image.new("RGB", (1, 1), (64, 64, 64)).save(payload, format="PNG")
    reference.write_bytes(payload.getvalue())
    timestamp = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
    manifest = ProjectManifest(
        schema_version=2,
        project_id=PROJECT_ID,
        project_name="Job API project",
        edition="java",
        java_pack_format=34,
        supported_formats=None,
        catalog_id="java-dev-format-34",
        source_sha256="0" * 64,
        created_at=timestamp,
        updated_at=timestamp,
        default_resolution=16,
        default_parallelism=1,
        style_references=(),
    )
    (project_root / "project.json").write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )


def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(
            app=app,
            raise_app_exceptions=False,
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def _create_payload() -> dict[str, object]:
    return {
        "target_semantic_id": "minecraft:deepslate",
        "prompt": "cold blue-gray stone",
        "resolution": 16,
        "parallelism": 1,
        "style_references": [STYLE_REFERENCE],
        "structure_reference": None,
    }


def _create_job(app, payload: dict[str, object] | None = None) -> httpx.Response:
    return _request(
        app,
        "POST",
        f"/api/projects/{PROJECT_ID}/jobs",
        json=_create_payload() if payload is None else payload,
    )


def _assert_error(
    response: httpx.Response,
    *,
    status_code: int,
    code: str,
    stage: str,
) -> dict[str, object]:
    assert response.status_code == status_code, response.text
    body = response.json()
    assert set(body) == {
        "code",
        "stage",
        "user_message",
        "recommended_actions",
        "technical_details",
    }
    assert body["code"] == code
    assert body["stage"] == stage
    assert isinstance(body["user_message"], str) and body["user_message"]
    assert isinstance(body["recommended_actions"], list)
    assert body["technical_details"] is None
    return body


def test_create_job_maps_json_arrays_to_strict_domain_tuples(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    app = create_app(project_root=projects_root, catalog_root=CATALOG_ROOT)

    response = _request(
        app,
        "POST",
        f"/api/projects/{PROJECT_ID}/jobs",
        json=_create_payload(),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"request", "state"}
    assert body["request"]["project_id"] == str(PROJECT_ID)
    assert body["request"]["target_semantic_id"] == "minecraft:deepslate"
    assert body["request"]["style_references"] == [STYLE_REFERENCE]
    assert len(body["request"]["seeds"]) == 4
    assert len(set(body["request"]["seeds"])) == 4
    assert body["state"]["status"] == "queued"
    assert body["state"]["revision"] == 0
    assert [candidate["seed"] for candidate in body["state"]["candidates"]] == (
        body["request"]["seeds"]
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("resolution", "16"),
        ("resolution", 48),
        ("parallelism", True),
        ("style_references", []),
        ("style_references", ["../outside.png"]),
        ("prompt", "   "),
        ("seeds", [1, 2, 3, 4]),
    ],
)
def test_create_job_rejects_non_strict_or_server_owned_input(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    app = create_app(project_root=projects_root, catalog_root=CATALOG_ROOT)
    payload = {**_create_payload(), field: value}

    response = _create_job(app, payload)

    body = _assert_error(
        response,
        status_code=422,
        code="INVALID_REQUEST",
        stage="request_validation",
    )
    assert "任务" in body["user_message"]


def test_job_ids_require_canonical_uuid_text(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    app = create_app(project_root=projects_root, catalog_root=CATALOG_ROOT)
    created = _create_job(app)
    assert created.status_code == 201
    job_id = created.json()["request"]["job_id"]

    noncanonical_project = _request(
        app,
        "GET",
        f"/api/projects/{str(PROJECT_ID).upper()}/jobs",
    )
    noncanonical_job = _request(
        app,
        "GET",
        f"/api/projects/{PROJECT_ID}/jobs/{job_id.upper()}",
    )

    _assert_error(
        noncanonical_project,
        status_code=400,
        code="INVALID_PROJECT_ID",
        stage="loading_job",
    )
    _assert_error(
        noncanonical_job,
        status_code=400,
        code="INVALID_JOB_ID",
        stage="loading_job",
    )


def test_list_and_detail_return_deterministic_durable_history(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    app = create_app(project_root=projects_root, catalog_root=CATALOG_ROOT)
    first = _create_job(app)
    second = _create_job(
        app,
        {
            **_create_payload(),
            "prompt": "second deepslate texture",
            "parallelism": 2,
        },
    )
    assert first.status_code == second.status_code == 201
    first_id = first.json()["request"]["job_id"]
    second_id = second.json()["request"]["job_id"]

    listed = _request(app, "GET", f"/api/projects/{PROJECT_ID}/jobs")
    detail = _request(
        app,
        "GET",
        f"/api/projects/{PROJECT_ID}/jobs/{first_id}",
    )

    assert listed.status_code == 200, listed.text
    summaries = listed.json()
    assert [item["job_id"] for item in summaries] == [second_id, first_id]
    assert [item["parallelism"] for item in summaries] == [2, 1]
    assert all(
        set(item)
        == {
            "job_id",
            "project_id",
            "retry_of_job_id",
            "target_semantic_id",
            "target_display_name",
            "resolution",
            "parallelism",
            "status",
            "revision",
            "candidate_statuses",
            "created_at",
            "updated_at",
        }
        for item in summaries
    )
    assert detail.status_code == 200, detail.text
    assert detail.json() == first.json()


def test_cancel_and_retry_preserve_source_and_record_direct_lineage(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    app = create_app(project_root=projects_root, catalog_root=CATALOG_ROOT)
    created = _create_job(app)
    assert created.status_code == 201
    source = created.json()
    source_id = source["request"]["job_id"]

    canceled = _request(
        app,
        "POST",
        f"/api/projects/{PROJECT_ID}/jobs/{source_id}/cancel",
        json={"expected_revision": 0},
    )
    retried = _request(
        app,
        "POST",
        f"/api/projects/{PROJECT_ID}/jobs/{source_id}/retry",
    )
    original = _request(
        app,
        "GET",
        f"/api/projects/{PROJECT_ID}/jobs/{source_id}",
    )

    assert canceled.status_code == 200, canceled.text
    assert canceled.json()["state"]["status"] == "canceled"
    assert canceled.json()["state"]["revision"] == 1
    assert retried.status_code == 201, retried.text
    retry = retried.json()
    assert retry["request"]["job_id"] != source_id
    assert retry["request"]["retry_of_job_id"] == source_id
    assert retry["request"]["seeds"] == source["request"]["seeds"]
    for field in (
        "catalog_id",
        "target_semantic_id",
        "target_display_name",
        "target_relative_path",
        "prompt",
        "resolution",
        "parallelism",
        "style_references",
        "structure_reference",
    ):
        assert retry["request"][field] == source["request"][field]
    assert retry["state"]["status"] == "queued"
    assert original.status_code == 200
    assert original.json() == canceled.json()


def test_revision_conflict_and_invalid_transition_are_stable_conflicts(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    app = create_app(project_root=projects_root, catalog_root=CATALOG_ROOT)
    created = _create_job(app)
    assert created.status_code == 201
    job_id = created.json()["request"]["job_id"]

    revision_conflict = _request(
        app,
        "POST",
        f"/api/projects/{PROJECT_ID}/jobs/{job_id}/cancel",
        json={"expected_revision": 9},
    )
    canceled = _request(
        app,
        "POST",
        f"/api/projects/{PROJECT_ID}/jobs/{job_id}/cancel",
        json={"expected_revision": 0},
    )
    invalid_transition = _request(
        app,
        "POST",
        f"/api/projects/{PROJECT_ID}/jobs/{job_id}/cancel",
        json={"expected_revision": 1},
    )

    _assert_error(
        revision_conflict,
        status_code=409,
        code="JOB_REVISION_CONFLICT",
        stage="canceling_job",
    )
    assert canceled.status_code == 200
    _assert_error(
        invalid_transition,
        status_code=409,
        code="INVALID_JOB_TRANSITION",
        stage="canceling_job",
    )


def test_missing_project_job_and_reference_use_stable_errors(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    app = create_app(project_root=projects_root, catalog_root=CATALOG_ROOT)
    missing_id = "20000000-0000-4000-8000-000000000002"

    missing_project = _request(
        app,
        "GET",
        f"/api/projects/{missing_id}/jobs",
    )
    missing_job = _request(
        app,
        "GET",
        f"/api/projects/{PROJECT_ID}/jobs/{missing_id}",
    )
    invalid_reference = _create_job(
        app,
        {
            **_create_payload(),
            "style_references": [
                "assets/minecraft/textures/block/missing.png"
            ],
        },
    )

    _assert_error(
        missing_project,
        status_code=404,
        code="PROJECT_NOT_FOUND",
        stage="listing_jobs",
    )
    _assert_error(
        missing_job,
        status_code=404,
        code="JOB_NOT_FOUND",
        stage="loading_job",
    )
    _assert_error(
        invalid_reference,
        status_code=422,
        code="INVALID_STYLE_REFERENCE",
        stage="creating_job",
    )


def test_corrupt_job_json_is_reported_without_leaking_bytes(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    app = create_app(project_root=projects_root, catalog_root=CATALOG_ROOT)
    created = _create_job(app)
    assert created.status_code == 201
    job_id = created.json()["request"]["job_id"]
    secret = "C:/private/do-not-return"
    state_path = (
        projects_root
        / str(PROJECT_ID)
        / "jobs"
        / job_id
        / "state.json"
    )
    state_path.write_text(f'{{"secret":"{secret}"', encoding="utf-8")

    response = _request(
        app,
        "GET",
        f"/api/projects/{PROJECT_ID}/jobs/{job_id}",
    )

    _assert_error(
        response,
        status_code=500,
        code="CORRUPT_JOB_RECORD",
        stage="loading_job",
    )
    assert secret not in response.text


def test_list_jobs_keeps_valid_sibling_visible_when_one_record_is_malformed(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    app = create_app(project_root=projects_root, catalog_root=CATALOG_ROOT)
    valid = _create_job(app)
    malformed = _create_job(
        app,
        {
            **_create_payload(),
            "prompt": "malformed sibling",
        },
    )
    assert valid.status_code == malformed.status_code == 201
    valid_id = valid.json()["request"]["job_id"]
    malformed_id = malformed.json()["request"]["job_id"]
    malformed_state = (
        projects_root
        / str(PROJECT_ID)
        / "jobs"
        / malformed_id
        / "state.json"
    )
    malformed_bytes = b"{malformed state remains canonical"
    malformed_state.write_bytes(malformed_bytes)

    response = _request(
        app,
        "GET",
        f"/api/projects/{PROJECT_ID}/jobs",
    )

    assert response.status_code == 200, response.text
    assert [summary["job_id"] for summary in response.json()] == [valid_id]
    assert malformed_state.read_bytes() == malformed_bytes


def test_index_failure_after_job_commit_uses_stable_recovery_guidance(
    tmp_path: Path,
) -> None:
    class IndexFailingJobService:
        def create_job(self, _project_id, _command):
            raise JobError(
                "INDEX_UNAVAILABLE",
                "任务已保存，但任务索引暂时不可用",
            )

    projects_root = tmp_path / "projects"
    services = AppServices(
        workspace=object(),
        catalogs=CatalogRegistry(CATALOG_ROOT),
        project_root=projects_root,
        job_service=IndexFailingJobService(),
    )
    app = create_app(services=services)

    response = _create_job(app)

    body = _assert_error(
        response,
        status_code=500,
        code="INDEX_UNAVAILABLE",
        stage="creating_job",
    )
    assert body["recommended_actions"] == [
        "任务已保存；请刷新任务列表，或重启应用重建索引"
    ]
