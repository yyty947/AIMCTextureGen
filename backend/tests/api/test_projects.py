import asyncio
import io
import json
import os
import subprocess
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from fastapi import FastAPI

import aimctexturegen.main as main_module
from aimctexturegen.catalog.registry import CatalogRegistry
from aimctexturegen.main import create_app
from aimctexturegen.projects.models import ProjectManifest
from aimctexturegen.projects.workspace import ProjectWorkspace


CATALOG_ROOT = Path(__file__).parents[3] / "catalogs" / "java"


@pytest.fixture
def api_pack_zip_factory(
    tmp_path: Path,
) -> Callable[[str, dict[str, bytes], int], Path]:
    def create(
        name: str,
        members: dict[str, bytes],
        pack_format: int = 34,
    ) -> Path:
        path = tmp_path / name
        payload = {
            "pack": {
                "pack_format": pack_format,
                "description": "AIMCTextureGen synthetic test pack",
            }
        }
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("pack.mcmeta", json.dumps(payload))
            for member_name, data in members.items():
                archive.writestr(member_name, data)
        return path

    return create


@pytest.fixture
def api_one_pixel_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1), (64, 64, 64)).save(buffer, format="PNG")
    return buffer.getvalue()


class ApiClient:
    def __init__(
        self,
        app: FastAPI,
        *,
        raise_server_exceptions: bool,
    ) -> None:
        self.app = app
        self._raise_server_exceptions = raise_server_exceptions

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        files = kwargs.get("files")
        if files is not None:
            kwargs["files"] = {
                field: (
                    value[0],
                    value[1].read() if hasattr(value[1], "read") else value[1],
                    *value[2:],
                )
                for field, value in files.items()
            }

        async def send() -> httpx.Response:
            transport = httpx.ASGITransport(
                app=self.app,
                raise_app_exceptions=self._raise_server_exceptions,
            )
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send())

    def post(self, path: str, **kwargs) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self.request("GET", path, **kwargs)


def build_client(
    project_root: Path,
    *,
    raise_server_exceptions: bool = True,
    max_import_bytes: int | None = None,
    max_import_body_bytes: int | None = None,
) -> ApiClient:
    return ApiClient(
        create_app(
            project_root=project_root,
            catalog_root=CATALOG_ROOT,
            max_import_bytes=max_import_bytes,
            max_import_body_bytes=max_import_body_bytes,
        ),
        raise_server_exceptions=raise_server_exceptions,
    )


def assert_stable_error(
    response,
    *,
    status_code: int,
    code: str,
    stage: str,
) -> dict[str, Any]:
    assert response.status_code == status_code
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
    assert isinstance(body["user_message"], str)
    assert body["user_message"]
    assert isinstance(body["recommended_actions"], list)
    assert body["technical_details"] is None or isinstance(
        body["technical_details"], str
    )
    return body


def write_project(
    project_root: Path,
    *,
    project_id,
    stone_png: bytes | None = None,
) -> Path:
    project_directory = project_root / str(project_id)
    pack_root = project_directory / "pack"
    pack_root.mkdir(parents=True)
    if stone_png is not None:
        stone = pack_root / "assets" / "minecraft" / "textures" / "block" / "stone.png"
        stone.parent.mkdir(parents=True)
        stone.write_bytes(stone_png)
    timestamp = datetime.now(timezone.utc)
    manifest = ProjectManifest(
        schema_version=1,
        project_id=project_id,
        project_name="Persisted Pack",
        edition="java",
        java_pack_format=34,
        supported_formats=None,
        catalog_id="java-dev-format-34",
        source_sha256="0" * 64,
        created_at=timestamp,
        updated_at=timestamp,
    )
    (project_directory / "project.json").write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return project_directory


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


def test_app_services_are_explicit_and_runtime_defaults_ignore_shell_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    app = create_app()

    services = app.state.services
    repository_root = Path(main_module.__file__).resolve().parents[3]
    assert isinstance(services.workspace, ProjectWorkspace)
    assert isinstance(services.catalogs, CatalogRegistry)
    assert services.project_root == repository_root / "projects"


def test_rejects_configured_project_root_junction_before_resolving_it(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    project_root = tmp_path / "projects"
    create_junction(project_root, outside)

    try:
        with pytest.raises(ValueError, match="project root.*reparse", check=None):
            create_app(project_root=project_root, catalog_root=CATALOG_ROOT)
    finally:
        remove_junction(project_root)


def test_imports_uploaded_zip_and_returns_project_manifest(
    tmp_path: Path,
    api_pack_zip_factory: Callable[[str, dict[str, bytes], int], Path],
    api_one_pixel_png: bytes,
) -> None:
    source = api_pack_zip_factory(
        "source.zip",
        {"assets/minecraft/textures/block/stone.png": api_one_pixel_png},
    )
    client = build_client(tmp_path / "projects")

    with source.open("rb") as upload:
        response = client.post(
            "/api/projects/import",
            data={"project_name": "Synthetic Pack"},
            files={"pack": ("source.zip", upload, "application/zip")},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["project_name"] == "Synthetic Pack"
    assert body["java_pack_format"] == 34
    assert body["catalog_id"] == "java-dev-format-34"
    assert list((tmp_path / "projects").iterdir()) == [
        tmp_path / "projects" / body["project_id"]
    ]


def test_rejects_unsafe_uploaded_zip_with_stable_error(
    tmp_path: Path,
) -> None:
    source = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(
            "pack.mcmeta",
            '{"pack":{"pack_format":34,"description":"synthetic"}}',
        )
        archive.writestr("../escape.txt", b"unsafe")
    client = build_client(tmp_path / "projects")

    with source.open("rb") as upload:
        response = client.post(
            "/api/projects/import",
            data={"project_name": "Unsafe Pack"},
            files={"pack": ("unsafe.zip", upload, "application/zip")},
        )

    assert_stable_error(
        response,
        status_code=400,
        code="UNSAFE_PACK_PATH",
        stage="importing",
    )
    assert list((tmp_path / "projects").iterdir()) == []


def test_unknown_project_returns_stable_not_found_error(tmp_path: Path) -> None:
    client = build_client(tmp_path / "projects")

    response = client.get(f"/api/projects/{uuid4()}")

    assert_stable_error(
        response,
        status_code=404,
        code="PROJECT_NOT_FOUND",
        stage="loading_project",
    )


def test_gets_manifest_and_recomputes_coverage_from_working_copy(
    tmp_path: Path,
    api_pack_zip_factory: Callable[[str, dict[str, bytes], int], Path],
    api_one_pixel_png: bytes,
) -> None:
    source = api_pack_zip_factory(
        "coverage.zip",
        {"assets/minecraft/textures/block/stone.png": api_one_pixel_png},
    )
    client = build_client(tmp_path / "projects")
    with source.open("rb") as upload:
        imported = client.post(
            "/api/projects/import",
            data={"project_name": "Coverage Pack"},
            files={"pack": ("coverage.zip", upload, "application/zip")},
        )
    assert imported.status_code == 201
    project_id = imported.json()["project_id"]

    manifest = client.get(f"/api/projects/{project_id}")
    coverage = client.get(f"/api/projects/{project_id}/coverage")

    assert manifest.status_code == 200
    assert manifest.json()["project_name"] == "Coverage Pack"
    assert coverage.status_code == 200
    assert coverage.json()["covered_count"] == 1
    assert coverage.json()["missing_count"] == 1


def test_rejects_oversize_upload_and_removes_temporary_file(
    tmp_path: Path,
    api_pack_zip_factory: Callable[[str, dict[str, bytes], int], Path],
) -> None:
    source = api_pack_zip_factory("oversize.zip", {"large.bin": b"x" * 256})
    project_root = tmp_path / "projects"
    client = build_client(project_root, max_import_bytes=128)

    with source.open("rb") as upload:
        response = client.post(
            "/api/projects/import",
            data={"project_name": "Oversize Pack"},
            files={"pack": ("oversize.zip", upload, "application/zip")},
        )

    assert_stable_error(
        response,
        status_code=413,
        code="IMPORT_TOO_LARGE",
        stage="uploading",
    )
    assert list(project_root.iterdir()) == []


def test_invalid_project_uuid_uses_stable_error_envelope(tmp_path: Path) -> None:
    client = build_client(tmp_path / "projects")

    response = client.get("/api/projects/not-a-uuid")

    assert_stable_error(
        response,
        status_code=400,
        code="INVALID_PROJECT_ID",
        stage="loading_project",
    )


def test_non_multipart_import_uses_stable_request_error(tmp_path: Path) -> None:
    client = build_client(tmp_path / "projects")

    response = client.post(
        "/api/projects/import",
        json={"project_name": "No path fields", "source_path": "C:/secret.zip"},
    )

    assert_stable_error(
        response,
        status_code=422,
        code="INVALID_REQUEST",
        stage="request_validation",
    )


def test_corrupt_project_manifest_uses_stable_non_leaking_error(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "projects"
    project_id = uuid4()
    project_directory = project_root / str(project_id)
    project_directory.mkdir(parents=True)
    (project_directory / "project.json").write_text("not json", encoding="utf-8")
    client = build_client(project_root)

    response = client.get(f"/api/projects/{project_id}")

    body = assert_stable_error(
        response,
        status_code=500,
        code="CORRUPT_PROJECT_MANIFEST",
        stage="loading_project",
    )
    assert body["technical_details"] is None
    assert str(project_directory) not in body["user_message"]


def test_rejects_uuid_project_directory_junction_without_reading_target(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "projects"
    project_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    project_id = uuid4()
    project_directory = project_root / str(project_id)
    create_junction(project_directory, outside)
    client = build_client(project_root)

    try:
        response = client.get(f"/api/projects/{project_id}")
    finally:
        remove_junction(project_directory)

    assert_stable_error(
        response,
        status_code=500,
        code="UNSAFE_PROJECT_PATH",
        stage="loading_project",
    )


def test_corrupt_working_copy_returns_stable_coverage_error(tmp_path: Path) -> None:
    project_root = tmp_path / "projects"
    project_id = uuid4()
    write_project(project_root, project_id=project_id, stone_png=b"not a png")
    client = build_client(project_root)

    response = client.get(f"/api/projects/{project_id}/coverage")

    assert_stable_error(
        response,
        status_code=422,
        code="INVALID_TEXTURE_PNG",
        stage="classifying_coverage",
    )


def test_unexpected_import_error_is_logged_but_not_leaked_and_temp_is_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    api_pack_zip_factory: Callable[[str, dict[str, bytes], int], Path],
) -> None:
    source = api_pack_zip_factory("unexpected.zip", {})
    project_root = tmp_path / "projects"
    client = build_client(project_root, raise_server_exceptions=False)
    secret = str(tmp_path / "private" / "secret.zip")

    def fail_import(*_args, **_kwargs):
        raise RuntimeError(f"unexpected failure at {secret}")

    monkeypatch.setattr(
        client.app.state.services.workspace,
        "import_pack",
        fail_import,
    )

    with source.open("rb") as upload:
        response = client.post(
            "/api/projects/import",
            data={"project_name": "Unexpected Pack"},
            files={"pack": ("unexpected.zip", upload, "application/zip")},
        )

    body = assert_stable_error(
        response,
        status_code=500,
        code="INTERNAL_ERROR",
        stage="importing",
    )
    assert body["technical_details"] is None
    assert secret not in response.text
    assert list(project_root.iterdir()) == []


def test_open_upload_handle_blocks_replacement_and_cleanup_stays_scoped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    api_pack_zip_factory: Callable[[str, dict[str, bytes], int], Path],
) -> None:
    source = api_pack_zip_factory("replace-upload.zip", {})
    project_root = tmp_path / "projects"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    client = build_client(project_root, raise_server_exceptions=False)
    replaced_upload: Path | None = None

    def replace_upload(upload_path: Path, _project_name: str):
        nonlocal replaced_upload
        replaced_upload = upload_path
        upload_path.unlink()
        create_junction(upload_path, outside)
        raise RuntimeError("forced upload replacement")

    monkeypatch.setattr(
        client.app.state.services.workspace,
        "import_pack",
        replace_upload,
    )

    try:
        with source.open("rb") as upload:
            response = client.post(
                "/api/projects/import",
                data={"project_name": "Replace Upload"},
                files={"pack": ("replace-upload.zip", upload, "application/zip")},
            )

        assert response.status_code == 500
        assert sentinel.read_text(encoding="utf-8") == "keep"
        assert replaced_upload is not None
        assert not os.path.lexists(replaced_upload)
    finally:
        if replaced_upload is not None and replaced_upload.is_junction():
            remove_junction(replaced_upload)


def test_unsupported_pack_format_maps_to_stable_import_error(
    tmp_path: Path,
    api_pack_zip_factory: Callable[[str, dict[str, bytes], int], Path],
) -> None:
    source = api_pack_zip_factory("unsupported.zip", {}, 999)
    client = build_client(tmp_path / "projects")

    with source.open("rb") as upload:
        response = client.post(
            "/api/projects/import",
            data={"project_name": "Unsupported Pack"},
            files={"pack": ("unsupported.zip", upload, "application/zip")},
        )

    assert_stable_error(
        response,
        status_code=400,
        code="UNSUPPORTED_PACK_FORMAT",
        stage="importing",
    )


def test_project_name_length_is_enforced_before_project_staging(
    tmp_path: Path,
    api_pack_zip_factory: Callable[[str, dict[str, bytes], int], Path],
) -> None:
    source = api_pack_zip_factory("name-limit.zip", {})
    project_root = tmp_path / "projects"
    client = build_client(project_root)
    with source.open("rb") as upload:
        response = client.post(
            "/api/projects/import",
            data={"project_name": "x" * 129},
            files={"pack": ("name-limit.zip", upload, "application/zip")},
        )

    assert_stable_error(
        response,
        status_code=400,
        code="INVALID_PROJECT_NAME",
        stage="importing",
    )
    assert list(project_root.iterdir()) == []


def test_corrupt_crc_in_normal_member_maps_to_stable_import_error(
    tmp_path: Path,
    api_pack_zip_factory: Callable[[str, dict[str, bytes], int], Path],
) -> None:
    source = api_pack_zip_factory("corrupt-member.zip", {"assets/data.bin": b"payload"})
    with zipfile.ZipFile(source) as archive:
        info = archive.getinfo("assets/data.bin")
    payload = bytearray(source.read_bytes())
    name_length = int.from_bytes(
        payload[info.header_offset + 26 : info.header_offset + 28], "little"
    )
    extra_length = int.from_bytes(
        payload[info.header_offset + 28 : info.header_offset + 30], "little"
    )
    payload[info.header_offset + 30 + name_length + extra_length] ^= 1
    source.write_bytes(payload)
    project_root = tmp_path / "projects"
    client = build_client(project_root)

    with source.open("rb") as upload:
        response = client.post(
            "/api/projects/import",
            data={"project_name": "Corrupt member"},
            files={"pack": ("corrupt-member.zip", upload, "application/zip")},
        )

    assert_stable_error(
        response,
        status_code=400,
        code="CORRUPT_ZIP_MEMBER",
        stage="importing",
    )
    assert list(project_root.iterdir()) == []


@pytest.mark.parametrize(
    ("project_name", "expected_status"),
    [
        ("x" * 128, 201),
        ("x" * 129, 400),
        ("😀" * 128, 201),
        ("😀" * 129, 400),
    ],
    ids=[
        "128-ascii-code-points",
        "129-ascii-code-points",
        "128-non-bmp-code-points",
        "129-non-bmp-code-points",
    ],
)
def test_project_name_api_counts_code_points(
    tmp_path: Path,
    api_pack_zip_factory: Callable[[str, dict[str, bytes], int], Path],
    project_name: str,
    expected_status: int,
) -> None:
    source = api_pack_zip_factory("unicode-name.zip", {})
    project_root = tmp_path / "projects"
    client = build_client(project_root)

    with source.open("rb") as upload:
        response = client.post(
            "/api/projects/import",
            data={"project_name": project_name},
            files={"pack": ("unicode-name.zip", upload, "application/zip")},
        )

    assert response.status_code == expected_status
    if expected_status == 201:
        assert response.json()["project_name"] == project_name
    else:
        assert response.json()["code"] == "INVALID_PROJECT_NAME"
        assert list(project_root.iterdir()) == []
