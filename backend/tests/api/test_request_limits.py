import asyncio
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
import httpx
from starlette.datastructures import UploadFile

from aimctexturegen.api import projects as projects_api
from aimctexturegen.main import create_app


CATALOG_ROOT = Path(__file__).parents[3] / "catalogs" / "java"


def zip_payload(extra: bytes = b"") -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(
            "pack.mcmeta",
            '{"pack":{"pack_format":34,"description":"synthetic"}}',
        )
        if extra:
            archive.writestr("extra.bin", extra)
    return payload.getvalue()


def multipart_body(
    pack: bytes,
    *,
    boundary: str = "aimc-boundary",
    filename: str = "source.zip",
) -> bytes:
    return b"".join(
        [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="project_name"\r\n\r\n',
            b"Synthetic Pack\r\n",
            f"--{boundary}\r\n".encode(),
            (
                'Content-Disposition: form-data; name="pack"; '
                f'filename="{filename}"\r\n'
            ).encode(),
            b"Content-Type: application/zip\r\n\r\n",
            pack,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )


async def invoke_asgi(
    app,
    *,
    body_chunks: list[bytes],
    headers: list[tuple[bytes, bytes]],
    fail_after_receives: int | None = None,
) -> tuple[int, dict[str, Any], int]:
    messages = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(body_chunks) - 1,
        }
        for index, chunk in enumerate(body_chunks)
    ]
    sent: list[dict[str, Any]] = []
    receive_calls = 0

    async def receive() -> dict[str, Any]:
        nonlocal receive_calls
        receive_calls += 1
        if fail_after_receives is not None and receive_calls > fail_after_receives:
            raise OSError("forced request receive failure")
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/projects/import",
        "raw_path": b"/api/projects/import",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    status_code = next(message["status"] for message in sent if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    return status_code, json.loads(response_body), receive_calls


async def request_app(
    app,
    method: str,
    path: str,
    **kwargs,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.request(method, path, **kwargs)


def assert_stable_413(status_code: int, body: dict[str, Any]) -> None:
    assert status_code == 413
    assert set(body) == {
        "code",
        "stage",
        "user_message",
        "recommended_actions",
        "technical_details",
    }
    assert body["code"] == "IMPORT_TOO_LARGE"
    assert body["stage"] == "uploading"


@pytest.mark.parametrize(
    ("content_length", "expected_receive_calls"),
    [
        ("actual", 0),
        (None, None),
        ("1", None),
    ],
)
def test_total_body_limit_rejects_valid_missing_and_lying_content_length(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content_length: str | None,
    expected_receive_calls: int | None,
) -> None:
    pack = zip_payload(b"x" * 128)
    body = multipart_body(pack)
    body_limit = len(body) - 8
    monkeypatch.setattr(
        projects_api,
        "MAX_IMPORT_BODY_BYTES",
        body_limit,
        raising=False,
    )
    app = create_app(project_root=tmp_path / "projects", catalog_root=CATALOG_ROOT)
    headers = [(b"content-type", b"multipart/form-data; boundary=aimc-boundary")]
    if content_length is not None:
        value = str(len(body)) if content_length == "actual" else content_length
        headers.append((b"content-length", value.encode()))

    status_code, response_body, receive_calls = asyncio.run(
        invoke_asgi(
            app,
            body_chunks=[body[:body_limit], body[body_limit:]],
            headers=headers,
        )
    )

    assert_stable_413(status_code, response_body)
    if expected_receive_calls is not None:
        assert receive_calls == expected_receive_calls


def test_missing_multipart_boundary_uses_exact_stable_envelope(tmp_path: Path) -> None:
    app = create_app(project_root=tmp_path / "projects", catalog_root=CATALOG_ROOT)

    status_code, body, _ = asyncio.run(
        invoke_asgi(
            app,
            body_chunks=[b"broken multipart"],
            headers=[(b"content-type", b"multipart/form-data")],
        )
    )

    assert status_code == 400
    assert set(body) == {
        "code",
        "stage",
        "user_message",
        "recommended_actions",
        "technical_details",
    }
    assert body["code"] == "INVALID_MULTIPART"
    assert body["stage"] == "request_validation"


def test_total_body_limit_accepts_request_at_exact_cap(tmp_path: Path) -> None:
    pack = zip_payload()
    body = multipart_body(pack)
    app = create_app(
        project_root=tmp_path / "projects",
        catalog_root=CATALOG_ROOT,
        max_import_bytes=len(pack),
        max_import_body_bytes=len(body),
    )

    status_code, response_body, _ = asyncio.run(
        invoke_asgi(
            app,
            body_chunks=[body],
            headers=[
                (b"content-type", b"multipart/form-data; boundary=aimc-boundary"),
                (b"content-length", str(len(body)).encode()),
            ],
        )
    )

    assert status_code == 201
    assert response_body["project_name"] == "Synthetic Pack"


def test_exact_file_limit_accepts_exact_bytes_and_rejects_plus_one(
    tmp_path: Path,
) -> None:
    pack = zip_payload(b"file boundary")
    exact_app = create_app(
        project_root=tmp_path / "exact-projects",
        catalog_root=CATALOG_ROOT,
        max_import_bytes=len(pack),
        max_import_body_bytes=len(pack) + 4096,
    )
    plus_one_app = create_app(
        project_root=tmp_path / "plus-one-projects",
        catalog_root=CATALOG_ROOT,
        max_import_bytes=len(pack) - 1,
        max_import_body_bytes=len(pack) + 4096,
    )
    request_kwargs = {
        "data": {"project_name": "Boundary Pack"},
        "files": {"pack": ("source.zip", pack, "application/zip")},
    }

    exact = asyncio.run(
        request_app(exact_app, "POST", "/api/projects/import", **request_kwargs)
    )
    plus_one = asyncio.run(
        request_app(plus_one_app, "POST", "/api/projects/import", **request_kwargs)
    )

    assert exact.status_code == 201
    assert_stable_413(plus_one.status_code, plus_one.json())
    assert list((tmp_path / "plus-one-projects").iterdir()) == []


def test_adversarial_upload_filename_is_ignored(tmp_path: Path) -> None:
    pack = zip_payload()
    project_root = tmp_path / "projects"
    app = create_app(
        project_root=project_root,
        catalog_root=CATALOG_ROOT,
        max_import_bytes=len(pack),
        max_import_body_bytes=len(pack) + 4096,
    )

    response = asyncio.run(
        request_app(
            app,
            "POST",
            "/api/projects/import",
            data={"project_name": "Filename Pack"},
            files={
                "pack": (
                    "../../outside.zip",
                    pack,
                    "application/zip",
                )
            },
        )
    )

    assert response.status_code == 201
    assert not (tmp_path / "outside.zip").exists()
    assert [path.name for path in project_root.iterdir()] == [
        response.json()["project_id"]
    ]


def test_malformed_multipart_body_uses_exact_stable_envelope(tmp_path: Path) -> None:
    boundary = "broken-boundary"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            b"Content-Disposition: form-data\r\n\r\n",
            b"missing name\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    app = create_app(
        project_root=tmp_path / "projects",
        catalog_root=CATALOG_ROOT,
        max_import_body_bytes=len(body),
    )

    status_code, response_body, _ = asyncio.run(
        invoke_asgi(
            app,
            body_chunks=[body],
            headers=[
                (
                    b"content-type",
                    f"multipart/form-data; boundary={boundary}".encode(),
                )
            ],
        )
    )

    assert status_code == 400
    assert set(response_body) == {
        "code",
        "stage",
        "user_message",
        "recommended_actions",
        "technical_details",
    }
    assert response_body["code"] == "INVALID_MULTIPART"


def test_request_receive_failure_closes_parser_files_and_creates_no_project(
    tmp_path: Path,
) -> None:
    pack = zip_payload()
    body = multipart_body(pack)
    split = body.index(pack) + 1
    project_root = tmp_path / "projects"
    app = create_app(
        project_root=project_root,
        catalog_root=CATALOG_ROOT,
        max_import_body_bytes=len(body) + 1,
    )

    status_code, response_body, _ = asyncio.run(
        invoke_asgi(
            app,
            body_chunks=[body[:split], body[split:]],
            headers=[
                (b"content-type", b"multipart/form-data; boundary=aimc-boundary")
            ],
            fail_after_receives=1,
        )
    )

    assert status_code == 400
    assert response_body["code"] == "INVALID_MULTIPART"
    assert not project_root.exists() or list(project_root.iterdir()) == []


def test_upload_read_failure_removes_exact_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = zip_payload()
    project_root = tmp_path / "projects"
    app = create_app(
        project_root=project_root,
        catalog_root=CATALOG_ROOT,
        max_import_body_bytes=len(pack) + 4096,
    )

    async def fail_read(_upload: UploadFile, _size: int = -1) -> bytes:
        raise OSError("forced parsed-upload read failure")

    monkeypatch.setattr(UploadFile, "read", fail_read)

    response = asyncio.run(
        request_app(
            app,
            "POST",
            "/api/projects/import",
            data={"project_name": "Read Failure"},
            files={"pack": ("source.zip", pack, "application/zip")},
        )
    )

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert list(project_root.iterdir()) == []
