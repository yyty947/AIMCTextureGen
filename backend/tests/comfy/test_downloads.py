"""RED/GREEN tests for the bounded resumable artifact downloader."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from aimctexturegen.comfy.downloads import (
    ArtifactDownloader,
    DownloadPolicy,
    DownloadProgress,
)
from aimctexturegen.comfy.errors import (
    DownloadCanceled,
    DownloadError,
    DownloadHashMismatch,
    DownloadProtocolError,
    DownloadSizeMismatch,
    DownloadUnsafePathError,
)
from aimctexturegen.comfy.manifests import ArtifactManifest

from comfy._helpers import make_artifact
from fakes.artifact_server import ArtifactServer


def _artifact(
    server: ArtifactServer,
    payload: bytes,
    *,
    byte_size: int | None = None,
    sha256: str | None = None,
    allowed_hosts: tuple[str, ...] = ("127.0.0.1",),
) -> ArtifactManifest:
    return ArtifactManifest.model_validate(
        make_artifact(
            artifact_id="local-artifact",
            file_name="artifact.bin",
            source_url=f"{server.base_url}/artifact.bin",
            revision="deadbeefcafebabe" + "0" * 24,
            byte_size=byte_size if byte_size is not None else len(payload),
            sha256=sha256
            if sha256 is not None
            else hashlib.sha256(payload).hexdigest(),
            destination="downloads/artifact.bin",
            allowed_hosts=allowed_hosts,
        )
    )


def _policy(**kwargs) -> DownloadPolicy:
    defaults = dict(require_https=False, max_redirects=5)
    defaults.update(kwargs)
    return DownloadPolicy(**defaults)


def _downloader(
    *,
    client: httpx.Client | None = None,
    chunk_size: int = 1024,
) -> ArtifactDownloader:
    return ArtifactDownloader(client=client, chunk_size=chunk_size)


def _destination(tmp_path: Path) -> Path:
    return tmp_path / "downloads" / "artifact.bin"


def test_happy_path_downloads_and_publishes_exact_bytes(
    tmp_path: Path,
) -> None:
    payload = b"0123456789" * 100
    with ArtifactServer(payload) as server:
        destination = _destination(tmp_path)
        result = _downloader().download(
            _artifact(server, payload),
            destination,
            policy=_policy(),
        )
    assert destination.read_bytes() == payload
    assert result.byte_size == len(payload)
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert not destination.with_name(destination.name + ".part").exists()


def test_progress_is_monotonic_and_reaches_total(tmp_path: Path) -> None:
    payload = b"P" * 5000
    with ArtifactServer(payload) as server:
        events: list[DownloadProgress] = []
        _downloader(chunk_size=1024).download(
            _artifact(server, payload),
            _destination(tmp_path),
            policy=_policy(),
            progress=events.append,
        )
    received = [event.received_bytes for event in events]
    assert received == sorted(received)
    assert received[-1] == len(payload)
    assert all(event.total_bytes == len(payload) for event in events)


def test_existing_correct_final_is_idempotent(tmp_path: Path) -> None:
    payload = b"ready-bytes"
    destination = _destination(tmp_path)
    destination.parent.mkdir(parents=True)
    destination.write_bytes(payload)
    with ArtifactServer(payload) as server:
        result = _downloader().download(
            _artifact(server, payload),
            destination,
            policy=_policy(),
        )
        assert server.request_count == 0
    assert result.byte_size == len(payload)


def test_existing_corrupt_final_is_never_overwritten(tmp_path: Path) -> None:
    payload = b"good-bytes-here"
    destination = _destination(tmp_path)
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"bad-bytes!")
    with ArtifactServer(payload) as server:
        with pytest.raises(DownloadHashMismatch):
            _downloader().download(
                _artifact(server, payload),
                destination,
                policy=_policy(),
            )
        assert destination.read_bytes() == b"bad-bytes!"
        assert server.request_count == 0


def test_canceled_download_leaves_valid_partial_and_resumes(
    tmp_path: Path,
) -> None:
    payload = b"S" * 4096
    destination = _destination(tmp_path)
    canceled = False

    def cancel() -> bool:
        return canceled

    def progress(event: DownloadProgress) -> None:
        nonlocal canceled
        if event.received_bytes >= 1024:
            canceled = True

    with ArtifactServer(payload, mode="slow") as server:
        downloader = _downloader(chunk_size=1024)
        with pytest.raises(DownloadCanceled):
            downloader.download(
                _artifact(server, payload),
                destination,
                policy=_policy(),
                progress=progress,
                cancel=cancel,
            )
        assert not destination.exists()
        part = destination.with_name(destination.name + ".part")
        sidecar = part.with_name(part.name + ".json")
        assert part.exists()
        assert sidecar.exists()
        sidecar_data = json.loads(sidecar.read_text(encoding="utf-8"))
        assert sidecar_data["expected_sha256"] == hashlib.sha256(payload).hexdigest()
        assert sidecar_data["partial_sha256"] == hashlib.sha256(
            part.read_bytes()
        ).hexdigest()

        canceled = False
        result = downloader.download(
            _artifact(server, payload),
            destination,
            policy=_policy(),
            cancel=cancel,
        )
        assert destination.read_bytes() == payload
        assert result.byte_size == len(payload)


def test_sidecar_mismatch_refuses_unsafe_reuse_and_restarts(
    tmp_path: Path,
) -> None:
    payload = b"R" * 2048
    destination = _destination(tmp_path)
    part = destination.with_name(destination.name + ".part")
    sidecar = part.with_name(part.name + ".json")
    destination.parent.mkdir(parents=True)
    part.write_bytes(payload[:1024])
    sidecar.write_text(
        json.dumps(
            {
                "expected_size": len(payload),
                "expected_sha256": hashlib.sha256(payload).hexdigest(),
                "received_bytes": 1024,
                "partial_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    with ArtifactServer(payload, mode="range") as server:
        _downloader().download(
            _artifact(server, payload),
            destination,
            policy=_policy(),
        )
    assert destination.read_bytes() == payload


def test_ignored_range_restarts_from_zero(tmp_path: Path) -> None:
    payload = b"I" * 2048
    destination = _destination(tmp_path)
    part = destination.with_name(destination.name + ".part")
    sidecar = part.with_name(part.name + ".json")
    destination.parent.mkdir(parents=True)
    part.write_bytes(payload[:1024])
    sidecar.write_text(
        json.dumps(
            {
                "expected_size": len(payload),
                "expected_sha256": hashlib.sha256(payload).hexdigest(),
                "received_bytes": 1024,
                "partial_sha256": hashlib.sha256(payload[:1024]).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    with ArtifactServer(payload, mode="ignore-range") as server:
        _downloader().download(
            _artifact(server, payload),
            destination,
            policy=_policy(),
        )
    assert destination.read_bytes() == payload
    assert server.last_range_header is None


def test_redirects_are_followed_within_allowed_hosts(tmp_path: Path) -> None:
    payload = b"redirected"
    with ArtifactServer(payload, mode="redirect") as server:
        server.redirect_target = f"{server.base_url}/final.bin"
        result = _downloader().download(
            _artifact(server, payload),
            _destination(tmp_path),
            policy=_policy(),
        )
    assert result.byte_size == len(payload)


def test_redirect_to_disallowed_host_is_rejected(tmp_path: Path) -> None:
    payload = b"x"
    with ArtifactServer(payload, mode="redirect") as server:
        server.redirect_target = (
            f"http://localhost:{server.base_url.rsplit(':', 1)[1]}/final.bin"
        )
        with pytest.raises(DownloadProtocolError):
            _downloader().download(
                _artifact(server, payload),
                _destination(tmp_path),
                policy=_policy(),
            )


def test_redirect_loop_exceeds_limit(tmp_path: Path) -> None:
    payload = b"loop"
    with ArtifactServer(payload, mode="redirect") as server:
        server.redirect_target = f"{server.base_url}/loop.bin"
        with pytest.raises(DownloadProtocolError):
            _downloader().download(
                _artifact(server, payload),
                _destination(tmp_path),
                policy=_policy(max_redirects=2),
            )


def test_https_only_policy_rejects_http_server(tmp_path: Path) -> None:
    payload = b"secure"
    with ArtifactServer(payload) as server:
        with pytest.raises(DownloadProtocolError):
            _downloader().download(
                _artifact(server, payload),
                _destination(tmp_path),
                policy=_policy(require_https=True),
            )


@pytest.mark.parametrize("mode", ["truncated", "oversized"])
def test_size_mismatches_fail_without_publishing(
    tmp_path: Path,
    mode: str,
) -> None:
    payload = b"Z" * 2048
    with ArtifactServer(payload, mode=mode) as server:
        destination = _destination(tmp_path)
        with pytest.raises(DownloadSizeMismatch):
            _downloader().download(
                _artifact(server, payload),
                destination,
                policy=_policy(),
            )
        assert not destination.exists()


def test_hash_mismatch_fails_without_publishing(tmp_path: Path) -> None:
    payload = b"correct-hash-bytes"
    with ArtifactServer(payload, mode="wrong-hash") as server:
        destination = _destination(tmp_path)
        with pytest.raises(DownloadHashMismatch):
            _downloader().download(
                _artifact(server, payload),
                destination,
                policy=_policy(),
            )
        assert not destination.exists()


def test_wrong_content_range_is_a_protocol_error(tmp_path: Path) -> None:
    payload = b"W" * 2048
    with ArtifactServer(payload, mode="wrong-content-range") as server:
        with pytest.raises(DownloadProtocolError):
            _downloader().download(
                _artifact(server, payload),
                _destination(tmp_path),
                policy=_policy(),
            )


def test_server_error_status_is_a_protocol_error(tmp_path: Path) -> None:
    with ArtifactServer(b"x", mode="status-500") as server:
        with pytest.raises(DownloadProtocolError):
            _downloader().download(
                _artifact(server, b"x"),
                _destination(tmp_path),
                policy=_policy(),
            )


def test_read_timeout_is_a_protocol_error(tmp_path: Path) -> None:
    payload = b"slow"
    client = httpx.Client(timeout=httpx.Timeout(0.2))
    with ArtifactServer(payload, mode="timeout") as server:
        server.delay = 1.0
        with pytest.raises(DownloadProtocolError):
            _downloader(client=client).download(
                _artifact(server, payload),
                _destination(tmp_path),
                policy=_policy(),
            )


def test_unsafe_destination_paths_are_rejected(tmp_path: Path) -> None:
    payload = b"safe"
    with ArtifactServer(payload) as server:
        destination = _destination(tmp_path)
        destination.mkdir(parents=True)  # destination is a directory
        with pytest.raises(DownloadUnsafePathError):
            _downloader().download(
                _artifact(server, payload),
                destination,
                policy=_policy(),
            )


def test_download_error_base_covers_all_failures() -> None:
    assert issubclass(DownloadCanceled, DownloadError)
    assert issubclass(DownloadHashMismatch, DownloadError)
    assert issubclass(DownloadSizeMismatch, DownloadError)
    assert issubclass(DownloadProtocolError, DownloadError)
    assert issubclass(DownloadUnsafePathError, DownloadError)
