import asyncio
import io
import os
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from aimctexturegen.catalog.registry import CatalogRegistry
from aimctexturegen.core.errors import ErrorEnvelope
from aimctexturegen.jobs.errors import JobError
from aimctexturegen.main import AppServices, create_app
from aimctexturegen.projects.models import ProjectManifest
from aimctexturegen.projects.repository import ProjectScanResult


CATALOG_ROOT = Path(__file__).parents[3] / "catalogs" / "java"


class FailingWorkspace:
    def __init__(self, secret: str) -> None:
        self._secret = secret

    def import_pack(self, _source: Path, _project_name: str):
        raise RuntimeError(f"injected workspace failure: {self._secret}")


class FailingJobService:
    def __init__(
        self,
        technical_details: str,
        *,
        expose_technical_details: bool,
    ) -> None:
        self._technical_details = technical_details
        self._expose_technical_details = expose_technical_details

    def create_job(self, _project_id, _command):
        raise JobError(
            "INDEX_UNAVAILABLE",
            "任务已保存，但任务索引暂时不可用",
            technical_details=self._technical_details,
            expose_technical_details=self._expose_technical_details,
        )


class CountingProjectRepository:
    def __init__(self, manifest: ProjectManifest) -> None:
        self._manifest = manifest
        self.list_calls = 0

    def list_manifests(self) -> ProjectScanResult:
        self.list_calls += 1
        return ProjectScanResult(
            manifests=(self._manifest,),
            issues=(),
        )


class ReplacingCatalog:
    def __init__(
        self,
        registry: CatalogRegistry,
        project_directory: Path,
        outside: Path,
    ) -> None:
        self._registry = registry
        self._project_directory = project_directory
        self._outside = outside
        self.backup = project_directory.with_name(f"{project_directory.name}.backup")
        self.replacement_error: OSError | None = None

    def for_pack_format(self, pack_format: int):
        try:
            self._project_directory.rename(self.backup)
        except OSError as error:
            self.replacement_error = error
        else:
            create_junction(self._project_directory, self._outside)
        return self._registry.for_pack_format(pack_format)


async def request_app(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.request(method, path, **kwargs)


def zip_payload() -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(
            "pack.mcmeta",
            '{"pack":{"pack_format":34,"description":"synthetic"}}',
        )
    return payload.getvalue()


def write_project(project_root: Path, project_id: UUID) -> None:
    project_directory = project_root / str(project_id)
    (project_directory / "pack").mkdir(parents=True)
    timestamp = datetime.now(timezone.utc)
    manifest = ProjectManifest(
        schema_version=2,
        project_id=project_id,
        project_name="Canonical Project",
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
    (project_directory / "project.json").write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )


def write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = io.BytesIO()
    Image.new("RGB", (1, 1), (64, 64, 64)).save(payload, format="PNG")
    path.write_bytes(payload.getvalue())


def create_junction(link: Path, target: Path) -> None:
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"Unable to create test junction: {result.stdout}{result.stderr}")
    assert link.is_junction()


def remove_junction(link: Path) -> None:
    if os.path.lexists(link):
        os.rmdir(link)


def test_create_app_uses_supplied_services_object_directly(tmp_path: Path) -> None:
    catalogs = CatalogRegistry(CATALOG_ROOT)
    services = AppServices(
        workspace=FailingWorkspace("not called"),
        catalogs=catalogs,
        project_root=tmp_path / "injected-projects",
    )

    app = create_app(services=services)

    assert app.state.services is services


def test_pre_lifespan_project_fallback_uses_injected_repository(
    tmp_path: Path,
) -> None:
    project_id = UUID("abcdefab-cdef-4abc-8def-abcdefabcdef")
    timestamp = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)
    manifest = ProjectManifest(
        schema_version=2,
        project_id=project_id,
        project_name="Injected repository project",
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
    repository = CountingProjectRepository(manifest)
    project_root = tmp_path / "must-not-be-read"
    services = AppServices(
        workspace=FailingWorkspace("not called"),
        catalogs=CatalogRegistry(CATALOG_ROOT),
        project_root=project_root,
        repository=repository,
    )
    app = create_app(services=services)

    response = asyncio.run(request_app(app, "GET", "/api/projects"))

    assert response.status_code == 200, response.text
    assert [item["project_id"] for item in response.json()] == [str(project_id)]
    assert repository.list_calls == 1
    assert not project_root.exists()


def test_canonical_uuid_is_accepted_and_noncanonical_uuid_is_rejected(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "projects"
    project_id = UUID("abcdefab-cdef-4abc-8def-abcdefabcdef")
    write_project(project_root, project_id)
    app = create_app(project_root=project_root, catalog_root=CATALOG_ROOT)

    canonical = asyncio.run(
        request_app(app, "GET", f"/api/projects/{project_id}")
    )
    noncanonical = asyncio.run(
        request_app(app, "GET", f"/api/projects/{str(project_id).upper()}")
    )

    assert canonical.status_code == 200
    assert noncanonical.status_code == 400
    assert noncanonical.json()["code"] == "INVALID_PROJECT_ID"


def test_error_envelope_is_strict_forbidden_extra_and_frozen() -> None:
    fields = {
        "code": "TEST_ERROR",
        "stage": "testing",
        "user_message": "test",
        "recommended_actions": ("retry",),
        "technical_details": None,
    }
    envelope = ErrorEnvelope(**fields)

    with pytest.raises(ValidationError):
        ErrorEnvelope(**fields, extra_field="forbidden")
    with pytest.raises(ValidationError):
        ErrorEnvelope(**{**fields, "recommended_actions": ["retry"]})
    with pytest.raises(ValidationError):
        envelope.code = "CHANGED"


def test_injected_unexpected_failure_is_logged_but_not_returned(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = str(tmp_path / "private" / "secret-source.zip")
    project_root = tmp_path / "injected-projects"
    services = AppServices(
        workspace=FailingWorkspace(secret),
        catalogs=CatalogRegistry(CATALOG_ROOT),
        project_root=project_root,
    )
    pack = zip_payload()
    app = create_app(
        services=services,
        max_import_bytes=len(pack),
        max_import_body_bytes=len(pack) + 4096,
    )

    with caplog.at_level("ERROR"):
        response = asyncio.run(
            request_app(
                app,
                "POST",
                "/api/projects/import",
                data={"project_name": "Injected Failure"},
                files={"pack": ("source.zip", pack, "application/zip")},
            )
        )

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert response.json()["technical_details"] is None
    assert secret in caplog.text
    assert secret not in response.text
    assert list(project_root.iterdir()) == []


@pytest.mark.parametrize(
    ("expose_technical_details", "expected_details"),
    [
        (False, None),
        (True, "index rebuild failed after one retry"),
    ],
)
def test_job_domain_details_require_explicit_safe_marker(
    tmp_path: Path,
    expose_technical_details: bool,
    expected_details: str | None,
) -> None:
    unsafe_secret = str(tmp_path / "private" / "index.sqlite3")
    technical_details = (
        "index rebuild failed after one retry"
        if expose_technical_details
        else unsafe_secret
    )
    services = AppServices(
        workspace=FailingWorkspace("not called"),
        catalogs=CatalogRegistry(CATALOG_ROOT),
        project_root=tmp_path / "projects",
        job_service=FailingJobService(
            technical_details,
            expose_technical_details=expose_technical_details,
        ),
    )
    app = create_app(services=services)

    response = asyncio.run(
        request_app(
            app,
            "POST",
            "/api/projects/abcdefab-cdef-4abc-8def-abcdefabcdef/jobs",
            json={
                "target_semantic_id": "minecraft:deepslate",
                "prompt": "cold blue-gray stone",
                "resolution": 16,
                "parallelism": 1,
                "style_references": [
                    "assets/minecraft/textures/block/stone.png"
                ],
                "structure_reference": None,
            },
        )
    )

    assert response.status_code == 500
    assert response.json()["code"] == "INDEX_UNAVAILABLE"
    assert response.json()["stage"] == "creating_job"
    assert response.json()["technical_details"] == expected_details
    if not expose_technical_details:
        assert unsafe_secret not in response.text


def test_coverage_holds_project_identity_against_post_manifest_replacement(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "projects"
    project_id = UUID("12345678-1234-4abc-8def-123456789abc")
    write_project(project_root, project_id)
    project_directory = project_root / str(project_id)
    write_png(
        project_directory
        / "pack"
        / "assets"
        / "minecraft"
        / "textures"
        / "block"
        / "stone.png"
    )
    outside = tmp_path / "outside"
    (outside / "pack").mkdir(parents=True)
    catalogs = ReplacingCatalog(
        CatalogRegistry(CATALOG_ROOT),
        project_directory,
        outside,
    )
    services = AppServices(
        workspace=FailingWorkspace("not called"),
        catalogs=catalogs,
        project_root=project_root,
    )
    app = create_app(services=services)

    try:
        response = asyncio.run(
            request_app(app, "GET", f"/api/projects/{project_id}/coverage")
        )

        assert response.status_code == 200, response.text
        assert catalogs.replacement_error is not None
        assert response.json()["covered_count"] == 1
    finally:
        if project_directory.is_junction():
            remove_junction(project_directory)
        if catalogs.backup.exists():
            catalogs.backup.rename(project_directory)
