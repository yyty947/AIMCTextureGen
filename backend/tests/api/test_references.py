from __future__ import annotations

import asyncio
import io
import warnings
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from PIL import Image
from starlette.exceptions import StarletteDeprecationWarning

from aimctexturegen.main import create_app
from aimctexturegen.projects.models import ProjectManifest

warnings.filterwarnings("ignore", category=StarletteDeprecationWarning)
from starlette.testclient import TestClient


CATALOG_ROOT = Path(__file__).parents[3] / "catalogs" / "java"
PROJECT_ID = UUID("11111111-2222-4333-8444-555555555555")
STYLE_REFERENCE = "assets/minecraft/textures/block/stone.png"


def _png_bytes(
    *,
    size: int = 16,
    color: tuple[int, int, int] = (64, 64, 64),
) -> bytes:
    payload = io.BytesIO()
    Image.new("RGB", (size, size), color).save(payload, format="PNG")
    return payload.getvalue()


def _write_project(projects_root: Path) -> None:
    project_root = projects_root / str(PROJECT_ID)
    pack_root = project_root / "pack"
    (project_root / "source").mkdir(parents=True)
    pack_root.mkdir()
    (project_root / "jobs").mkdir()
    (project_root / "uploads").mkdir()
    (project_root / "source" / "imported-pack.zip").write_bytes(b"snapshot")
    (pack_root / "pack.mcmeta").write_bytes(b"metadata")
    target = pack_root / Path(*STYLE_REFERENCE.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_png_bytes())
    timestamp = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
    manifest = ProjectManifest(
        schema_version=2,
        project_id=PROJECT_ID,
        project_name="Reference API project",
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


def _request(
    app,
    method: str,
    path: str,
    **kwargs,
) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def _assert_error(
    response: httpx.Response,
    *,
    status_code: int,
    code: str,
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
    assert isinstance(body["user_message"], str) and body["user_message"]
    assert isinstance(body["recommended_actions"], list)
    assert body["technical_details"] is None
    assert "C:\\" not in response.text
    return body


def _streaming_content(parts: list[bytes]):
    async def iterator():
        for chunk in parts:
            yield chunk

    return iterator()


def _disconnect_upload(
    app,
    path: str,
    *,
    headers: list[tuple[bytes, bytes]],
    chunks: list[bytes],
) -> tuple[int, list[dict[str, object]]]:
    sent: list[dict[str, object]] = []
    raw_path, _, raw_query = path.partition("?")

    async def receive():
        if chunks:
            chunk = chunks.pop(0)
            return {
                "type": "http.request",
                "body": chunk,
                "more_body": True,
            }
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    async def invoke() -> None:
        await app(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": raw_path,
                "raw_path": raw_path.encode("ascii"),
                "query_string": raw_query.encode("ascii"),
                "headers": headers,
                "client": ("127.0.0.1", 50000),
                "server": ("testserver", 80),
            },
            receive,
            send,
        )

    asyncio.run(invoke())
    start = next(message for message in sent if message["type"] == "http.response.start")
    body_chunks = [
        message
        for message in sent
        if message["type"] == "http.response.body"
    ]
    payload = b"".join(message.get("body", b"") for message in body_chunks)
    if payload:
        sent.append({"decoded_body": payload.decode("utf-8")})
    return start["status"], sent


def test_pack_reference_listing_and_binary_content_are_served_through_api(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    app = create_app(project_root=projects_root, catalog_root=CATALOG_ROOT)

    listed = _request(app, "GET", f"/api/projects/{PROJECT_ID}/references/pack")
    image = _request(
        app,
        "GET",
        f"/api/projects/{PROJECT_ID}/references/pack/image",
        params={"relative_path": STYLE_REFERENCE},
    )

    assert listed.status_code == 200, listed.text
    assert listed.json() == [
        {
            "source": "pack",
            "relative_path": STYLE_REFERENCE,
            "display_name": "Stone",
            "sha256": listed.json()[0]["sha256"],
            "byte_size": len(_png_bytes()),
            "width": 16,
            "height": 16,
            "mode": "RGB",
        }
    ]
    assert image.status_code == 200, image.text
    assert image.headers["content-type"] == "image/png"
    assert image.content == _png_bytes()


def test_pack_reference_cannot_read_a_valid_png_outside_pack_root(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    outside = projects_root / str(PROJECT_ID) / "outside.png"
    outside.write_bytes(_png_bytes(color=(200, 10, 20)))
    app = create_app(project_root=projects_root, catalog_root=CATALOG_ROOT)

    response = _request(
        app,
        "GET",
        f"/api/projects/{PROJECT_ID}/references/pack/image",
        params={"relative_path": "../outside.png"},
    )

    _assert_error(response, status_code=422, code="REFERENCE_INVALID")
    assert response.content != outside.read_bytes()


def test_reference_upload_and_readback_accept_fragmented_png_streams_without_filename(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    app = create_app(project_root=projects_root, catalog_root=CATALOG_ROOT)
    payload = _png_bytes(color=(12, 34, 56))

    created = _request(
        app,
        "POST",
        f"/api/projects/{PROJECT_ID}/references",
        params={"kind": "style"},
        headers={
            "content-type": "image/png",
            "content-length": str(len(payload) - 3),
        },
        content=_streaming_content([payload[:5], payload[5:11], payload[11:]]),
    )

    assert created.status_code == 201, created.text
    reference_id = created.json()["reference_id"]
    assert created.json()["kind"] == "style"
    listed = _request(
        app,
        "GET",
        f"/api/projects/{PROJECT_ID}/references",
        params={"kind": "style"},
    )
    image = _request(
        app,
        "GET",
        f"/api/projects/{PROJECT_ID}/references/style/{reference_id}/image",
    )
    deleted = _request(
        app,
        "DELETE",
        f"/api/projects/{PROJECT_ID}/references/style/{reference_id}",
    )

    assert listed.status_code == 200, listed.text
    assert [item["reference_id"] for item in listed.json()] == [reference_id]
    assert image.status_code == 200, image.text
    assert image.headers["content-type"] == "image/png"
    assert image.content == payload
    assert deleted.status_code == 204, deleted.text
    assert not deleted.content


@pytest.mark.parametrize(
    ("content_type", "payload", "expected_status", "expected_code"),
    [
        ("application/octet-stream", _png_bytes(), 415, "INVALID_REFERENCE_UPLOAD"),
        ("image/png", b"x" * ((16 * 1024 * 1024) + 1), 413, "REFERENCE_TOO_LARGE"),
    ],
    ids=("wrong-mime", "plus-one-byte"),
)
def test_reference_upload_enforces_mime_and_stream_size_before_storage(
    tmp_path: Path,
    content_type: str,
    payload: bytes,
    expected_status: int,
    expected_code: str,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    app = create_app(project_root=projects_root, catalog_root=CATALOG_ROOT)

    response = _request(
        app,
        "POST",
        f"/api/projects/{PROJECT_ID}/references",
        params={"kind": "style"},
        headers={"content-type": content_type},
        content=payload,
    )

    _assert_error(response, status_code=expected_status, code=expected_code)


def test_reference_upload_disconnect_returns_stable_path_free_error(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    app = create_app(project_root=projects_root, catalog_root=CATALOG_ROOT)
    payload = _png_bytes()

    status_code, sent = _disconnect_upload(
        app,
        f"/api/projects/{PROJECT_ID}/references?kind=style",
        headers=[(b"content-type", b"image/png")],
        chunks=[payload[:8], payload[8:16]],
    )

    assert status_code == 400
    decoded = next(item["decoded_body"] for item in sent if "decoded_body" in item)
    assert "REFERENCE_UPLOAD_INCOMPLETE" in decoded
    assert "C:\\" not in decoded


@pytest.mark.parametrize(
    ("path", "status_code", "code"),
    [
        ("/api/projects/11111111222243338444555555555555/references/pack", 400, "INVALID_PROJECT_ID"),
        (f"/api/projects/{PROJECT_ID}/references?kind=other", 422, "INVALID_REQUEST"),
        (
            f"/api/projects/{PROJECT_ID}/references/style/not-a-uuid/image",
            400,
            "INVALID_REFERENCE_ID",
        ),
    ],
)
def test_reference_routes_reject_invalid_ids_and_kinds_without_path_leaks(
    tmp_path: Path,
    path: str,
    status_code: int,
    code: str,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    app = create_app(project_root=projects_root, catalog_root=CATALOG_ROOT)

    response = _request(app, "GET", path)

    _assert_error(response, status_code=status_code, code=code)


def test_reference_web_binary_surface_remains_http_only_not_redirects(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    _write_project(projects_root)
    app = create_app(project_root=projects_root, catalog_root=CATALOG_ROOT)

    with TestClient(app) as client:
        response = client.get(
            f"/api/projects/{PROJECT_ID}/references/pack/image",
            params={"relative_path": STYLE_REFERENCE},
            follow_redirects=False,
        )

    assert response.status_code == 200
    assert "location" not in response.headers
