"""Bounded streaming and resumable artifact downloader."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import ConfigDict, Field
from pydantic import BaseModel

from aimctexturegen.comfy.errors import (
    DownloadCanceled,
    DownloadError,
    DownloadHashMismatch,
    DownloadProtocolError,
    DownloadSizeMismatch,
    DownloadUnsafePathError,
)
from aimctexturegen.comfy.manifests import ArtifactManifest
from aimctexturegen.core.atomic_files import atomic_replace_bytes

_DEFAULT_CHUNK_SIZE = 64 * 1024
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
_CONTENT_RANGE = re.compile(r"^bytes (?P<start>\d+)-(?P<end>\d+)/(?P<total>\d+)$")
_REPARSE_POINT = 0x400


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class DownloadPolicy(_StrictModel):
    require_https: bool = True
    max_redirects: int = Field(default=5, ge=0, le=20)
    allowed_hosts: tuple[str, ...] | None = None


class DownloadProgress(_StrictModel):
    received_bytes: int = Field(ge=0)
    total_bytes: int = Field(ge=0)


class DownloadResult(_StrictModel):
    destination: str
    byte_size: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)


class ArtifactDownloader:
    """Stream, verify and atomically publish one manifest artifact."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
    ) -> None:
        self._client = client or httpx.Client(
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=False,
        )
        self._chunk_size = chunk_size

    def download(
        self,
        artifact: ArtifactManifest,
        destination: Path,
        *,
        policy: DownloadPolicy | None = None,
        progress: Callable[[DownloadProgress], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> DownloadResult:
        policy = policy or DownloadPolicy()
        destination = Path(destination)
        _ensure_safe_path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        part = destination.with_name(destination.name + ".part")
        sidecar = part.with_name(part.name + ".json")
        _ensure_safe_path(part)
        _ensure_safe_path(sidecar)

        if _file_matches(destination, artifact):
            return DownloadResult(
                destination=str(destination),
                byte_size=artifact.byte_size,
                sha256=artifact.sha256,
            )
        if destination.exists():
            raise DownloadHashMismatch(
                "existing destination is corrupt and will not be overwritten"
            )

        offset = _validated_resume_offset(part, sidecar, artifact)
        url = artifact.source_url
        redirects = 0
        while True:
            _validate_request_url(url, artifact, policy)
            _ensure_safe_path(part)
            _ensure_safe_path(sidecar)
            try:
                with self._client.stream(
                    "GET",
                    url,
                    headers={"Range": f"bytes={offset}-"} if offset else None,
                ) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        redirects += 1
                        if redirects > policy.max_redirects:
                            raise DownloadProtocolError("redirect limit exceeded")
                        location = response.headers.get("location")
                        if not location:
                            raise DownloadProtocolError(
                                "redirect without a Location header"
                            )
                        url = _validated_redirect(
                            url,
                            location,
                            artifact,
                            policy,
                        )
                        continue
                    if response.status_code == 200:
                        if offset:
                            _restart_part(part)
                            offset = 0
                            _write_sidecar(
                                sidecar,
                                artifact,
                                received_bytes=0,
                                partial_sha256=hashlib.sha256().hexdigest(),
                            )
                            url = artifact.source_url
                            redirects = 0
                            continue
                        _validate_content_length(response, artifact)
                    elif response.status_code == 206:
                        offset = _validate_partial_response(
                            response,
                            artifact,
                            offset,
                        )
                    else:
                        raise DownloadProtocolError(
                            f"unexpected HTTP status {response.status_code}"
                        )
                    result = self._stream_body(
                        response,
                        artifact=artifact,
                        destination=destination,
                        part=part,
                        sidecar=sidecar,
                        offset=offset,
                        progress=progress,
                        cancel=cancel,
                    )
                    return result
            except httpx.TimeoutException as exc:
                raise DownloadProtocolError("download timed out") from exc
            except httpx.HTTPError as exc:
                raise DownloadProtocolError("download transport failed") from exc
            except OSError as exc:
                raise DownloadProtocolError("connection failed") from exc

    def _stream_body(
        self,
        response: httpx.Response,
        *,
        artifact: ArtifactManifest,
        destination: Path,
        part: Path,
        sidecar: Path,
        offset: int,
        progress: Callable[[DownloadProgress], None] | None,
        cancel: Callable[[], bool] | None,
    ) -> DownloadResult:
        hasher = hashlib.sha256()
        if offset:
            hasher.update(part.read_bytes())
        received = offset
        mode = "r+b" if offset else "w+b"
        with part.open(mode) as output:
            if not offset:
                output.truncate(0)
            for chunk in response.iter_bytes(self._chunk_size):
                if cancel is not None and cancel():
                    _write_sidecar(
                        sidecar,
                        artifact,
                        received_bytes=received,
                        partial_sha256=hasher.hexdigest(),
                    )
                    raise DownloadCanceled("download canceled by caller")
                if not chunk:
                    continue
                output.write(chunk)
                hasher.update(chunk)
                received += len(chunk)
                if progress is not None:
                    progress(
                        DownloadProgress(
                            received_bytes=received,
                            total_bytes=artifact.byte_size,
                        )
                    )
            output.flush()
            os.fsync(output.fileno())
        if received != artifact.byte_size:
            _remove_managed_partial(part, sidecar)
            raise DownloadSizeMismatch(
                f"expected {artifact.byte_size} bytes, received {received}"
            )
        digest = hasher.hexdigest()
        if digest != artifact.sha256:
            _remove_managed_partial(part, sidecar)
            raise DownloadHashMismatch(
                f"expected sha256 {artifact.sha256}, received {digest}"
            )
        os.replace(part, destination)
        sidecar.unlink(missing_ok=True)
        return DownloadResult(
            destination=str(destination),
            byte_size=artifact.byte_size,
            sha256=artifact.sha256,
        )


def _file_matches(path: Path, artifact: ArtifactManifest) -> bool:
    try:
        status = path.stat()
    except OSError:
        return False
    if not stat.S_ISREG(status.st_mode) or _is_reparse_point(status):
        return False
    if status.st_size != artifact.byte_size:
        return False
    return hashlib.sha256(path.read_bytes()).hexdigest() == artifact.sha256


def _validated_resume_offset(
    part: Path,
    sidecar: Path,
    artifact: ArtifactManifest,
) -> int:
    if not part.is_file() or not sidecar.is_file():
        return 0
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        if (
            data.get("expected_size") != artifact.byte_size
            or data.get("expected_sha256") != artifact.sha256
            or not isinstance(data.get("received_bytes"), int)
            or not isinstance(data.get("partial_sha256"), str)
        ):
            raise ValueError("invalid sidecar")
        received = data["received_bytes"]
        if received != part.stat().st_size or received < 0:
            raise ValueError("partial size mismatch")
        if received == 0:
            return 0
        if hashlib.sha256(part.read_bytes()).hexdigest() != data["partial_sha256"]:
            raise ValueError("partial hash mismatch")
        return received
    except (OSError, ValueError, json.JSONDecodeError):
        _restart_part(part)
        sidecar.unlink(missing_ok=True)
        return 0


def _restart_part(part: Path) -> None:
    _ensure_safe_path(part)
    if part.exists():
        part.unlink()
    part.touch()


def _remove_managed_partial(part: Path, sidecar: Path) -> None:
    try:
        part.unlink(missing_ok=True)
    finally:
        sidecar.unlink(missing_ok=True)


def _write_sidecar(
    sidecar: Path,
    artifact: ArtifactManifest,
    *,
    received_bytes: int,
    partial_sha256: str,
) -> None:
    payload = json.dumps(
        {
            "expected_size": artifact.byte_size,
            "expected_sha256": artifact.sha256,
            "received_bytes": received_bytes,
            "partial_sha256": partial_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    atomic_replace_bytes(
        sidecar,
        payload,
        validator=lambda readback: json.loads(readback),
    )


def _validate_content_length(
    response: httpx.Response,
    artifact: ArtifactManifest,
) -> None:
    value = response.headers.get("content-length")
    if value is not None:
        try:
            length = int(value)
        except ValueError as exc:
            raise DownloadProtocolError("invalid Content-Length") from exc
        if length != artifact.byte_size:
            raise DownloadSizeMismatch(
                f"expected {artifact.byte_size} bytes, "
                f"Content-Length is {length}"
            )


def _validate_partial_response(
    response: httpx.Response,
    artifact: ArtifactManifest,
    offset: int,
) -> int:
    value = response.headers.get("content-range")
    match = _CONTENT_RANGE.fullmatch(value or "")
    if match is None:
        raise DownloadProtocolError("invalid Content-Range")
    start = int(match.group("start"))
    end = int(match.group("end"))
    total = int(match.group("total"))
    if start != offset or total != artifact.byte_size or end < start:
        raise DownloadProtocolError("Content-Range does not match the request")
    return start


def _validated_redirect(
    current_url: str,
    location: str,
    artifact: ArtifactManifest,
    policy: DownloadPolicy,
) -> str:
    resolved = urljoin(current_url, location)
    parts = urlsplit(resolved)
    host = parts.hostname
    if not host:
        raise DownloadProtocolError("redirect has no host")
    if parts.scheme != "https" and policy.require_https:
        raise DownloadProtocolError("non-HTTPS redirect rejected")
    host = host.casefold()
    if host not in artifact.allowed_hosts:
        raise DownloadProtocolError(f"redirect host {host!r} is not allowed")
    if policy.allowed_hosts is not None and host not in policy.allowed_hosts:
        raise DownloadProtocolError(f"redirect host {host!r} is not allowed")
    return resolved


def _validate_request_url(
    url: str,
    artifact: ArtifactManifest,
    policy: DownloadPolicy,
) -> None:
    parts = urlsplit(url)
    host = parts.hostname
    if not host:
        raise DownloadProtocolError("request URL has no host")
    if parts.scheme != "https" and policy.require_https:
        raise DownloadProtocolError("non-HTTPS request rejected")
    host = host.casefold()
    if host not in artifact.allowed_hosts:
        raise DownloadProtocolError(f"request host {host!r} is not allowed")
    if policy.allowed_hosts is not None and host not in policy.allowed_hosts:
        raise DownloadProtocolError(f"request host {host!r} is not allowed")


def _ensure_safe_path(path: Path) -> None:
    parent = path.parent
    if parent.exists() and not parent.is_dir():
        raise DownloadUnsafePathError("destination parent is not a directory")
    if path.exists():
        try:
            status = path.lstat()
        except OSError as exc:
            raise DownloadUnsafePathError("destination cannot be inspected") from exc
        if (
            not stat.S_ISREG(status.st_mode)
            or path.is_symlink()
            or _is_reparse_point(status)
        ):
            raise DownloadUnsafePathError("destination is not a plain file")


def _is_reparse_point(status: os.stat_result) -> bool:
    return bool(getattr(status, "st_file_attributes", 0) & _REPARSE_POINT)
