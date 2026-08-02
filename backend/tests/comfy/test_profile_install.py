"""RED/GREEN tests for model, custom-node and profile installation."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from aimctexturegen.comfy.downloads import ArtifactDownloader, DownloadPolicy
from aimctexturegen.comfy.errors import (
    ProfileInstallError,
    ProfileUnsafeArtifactError,
)
from aimctexturegen.comfy.installer import ProfileInstaller
from aimctexturegen.comfy.manifests import ModelProfileManifest

from comfy._helpers import CUSTOM_NODE_COMMIT, make_artifact, make_profile
from fakes.artifact_server import ArtifactServer


def _payload(size: int, seed: int = 0) -> bytes:
    return bytes((index * 31 + seed) % 256 for index in range(size))


def _local_artifact(
    server: ArtifactServer,
    payload: bytes,
    *,
    artifact_id: str,
    destination: str,
) -> dict:
    return make_artifact(
        artifact_id=artifact_id,
        file_name=destination.rsplit("/", 1)[-1],
        source_url=f"{server.base_url}/{artifact_id}.bin",
        revision=CUSTOM_NODE_COMMIT,
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        destination=destination,
        allowed_hosts=("127.0.0.1",),
    )


def _profile_with_artifacts(artifacts: list[dict]) -> ModelProfileManifest:
    profile = make_profile()
    profile["artifacts"] = artifacts
    profile["capabilities"]["requires_custom_nodes"] = any(
        artifact["destination"].startswith("custom_nodes/")
        for artifact in artifacts
    )
    return ModelProfileManifest.model_validate(profile)


def _installer() -> ProfileInstaller:
    return ProfileInstaller(
        downloader=ArtifactDownloader(),
        policy=DownloadPolicy(require_https=False),
    )


def _root(tmp_path: Path) -> Path:
    return tmp_path / "runtime"


def test_install_downloads_each_artifact_once_into_allowlisted_categories(
    tmp_path: Path,
) -> None:
    first = _payload(1024, seed=1)
    second = _payload(2048, seed=2)
    with ArtifactServer(first) as server_a, ArtifactServer(second) as server_b:
        profile = _profile_with_artifacts(
            [
                _local_artifact(
                    server_a,
                    first,
                    artifact_id="checkpoint",
                    destination="models/checkpoints/model.safetensors",
                ),
                _local_artifact(
                    server_b,
                    second,
                    artifact_id="lora",
                    destination="models/loras/style.safetensors",
                ),
            ]
        )
        installer = _installer()
        root = _root(tmp_path)
        status = installer.install(profile, root)
        assert status.ready is True
        assert server_a.request_count == 1
        assert server_b.request_count == 1
    assert (root / "models" / "checkpoints" / "model.safetensors").read_bytes() == first
    assert (root / "models" / "loras" / "style.safetensors").read_bytes() == second
    assert (root / "state" / "installed-artifacts.json").is_file()
    assert installer.status(profile, root).ready is True


def test_identical_hash_is_reused_without_redownload(tmp_path: Path) -> None:
    payload = _payload(512)
    with ArtifactServer(payload) as server:
        shared = _local_artifact(
            server,
            payload,
            artifact_id="shared-model",
            destination="models/checkpoints/shared.safetensors",
        )
        first = _profile_with_artifacts([shared])
        second = _profile_with_artifacts(
            [
                dict(
                    shared,
                    artifact_id="shared-model-copy",
                    destination="models/loras/shared-copy.safetensors",
                )
            ]
        )
        installer = _installer()
        root = _root(tmp_path)
        installer.install(first, root)
        assert server.request_count == 1
        installer.install(second, root)
        assert server.request_count == 1
        assert (root / "models" / "loras" / "shared-copy.safetensors").is_file()


def test_same_destination_with_different_hash_fails(tmp_path: Path) -> None:
    payload = _payload(256)
    with ArtifactServer(payload) as server:
        artifact = _local_artifact(
            server,
            payload,
            artifact_id="checkpoint",
            destination="models/checkpoints/model.safetensors",
        )
        root = _root(tmp_path)
        target = root / "models" / "checkpoints" / "model.safetensors"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"other-bytes")
        with pytest.raises(ProfileInstallError):
            _installer().install(
                _profile_with_artifacts([artifact]),
                root,
            )


def test_status_rechecks_size_and_hash(tmp_path: Path) -> None:
    payload = _payload(1024)
    with ArtifactServer(payload) as server:
        artifact = _local_artifact(
            server,
            payload,
            artifact_id="checkpoint",
            destination="models/checkpoints/model.safetensors",
        )
        profile = _profile_with_artifacts([artifact])
        installer = _installer()
        root = _root(tmp_path)
        installer.install(profile, root)
        target = root / "models" / "checkpoints" / "model.safetensors"
        assert installer.status(profile, root).ready is True

        target.write_bytes(b"x" * len(payload))
        status = installer.status(profile, root)
        assert status.ready is False
        assert status.components[0].state == "corrupt"

        target.write_bytes(payload[:100])
        assert installer.status(profile, root).components[0].state == "partial"

        target.unlink()
        assert installer.status(profile, root).components[0].state == "missing"


def test_custom_node_zip_is_verified_and_extracted_into_managed_runtime(
    tmp_path: Path,
) -> None:
    root_name = f"ComfyUI_IPAdapter_plus-{CUSTOM_NODE_COMMIT}"
    buffer = _custom_node_zip(root_name)
    with ArtifactServer(buffer) as server:
        artifact = make_artifact(
            artifact_id="custom-node",
            file_name=f"{root_name}.zip",
            source_url=f"{server.base_url}/custom-node.zip",
            revision=CUSTOM_NODE_COMMIT,
            byte_size=len(buffer),
            sha256=hashlib.sha256(buffer).hexdigest(),
            destination="custom_nodes/ComfyUI_IPAdapter_plus.zip",
            allowed_hosts=("127.0.0.1",),
            license_name="GPL-3.0",
        )
        profile = _profile_with_artifacts([artifact])
        installer = _installer()
        root = _root(tmp_path)
        status = installer.install(profile, root)
        assert status.ready is True
    assert (root / "custom_nodes" / "ComfyUI_IPAdapter_plus.zip").is_file()
    assert (root / "custom_nodes" / root_name / "__init__.py").is_file()


def test_custom_node_zip_with_wrong_root_is_rejected(tmp_path: Path) -> None:
    buffer = _custom_node_zip("Wrong-Root")
    with ArtifactServer(buffer) as server:
        artifact = make_artifact(
            artifact_id="custom-node",
            file_name=f"ComfyUI_IPAdapter_plus-{CUSTOM_NODE_COMMIT}.zip",
            source_url=f"{server.base_url}/custom-node.zip",
            revision=CUSTOM_NODE_COMMIT,
            byte_size=len(buffer),
            sha256=hashlib.sha256(buffer).hexdigest(),
            destination="custom_nodes/ComfyUI_IPAdapter_plus.zip",
            allowed_hosts=("127.0.0.1",),
            license_name="GPL-3.0",
        )
        with pytest.raises(ProfileUnsafeArtifactError):
            _installer().install(
                _profile_with_artifacts([artifact]),
                _root(tmp_path),
            )


def test_custom_node_zip_with_traversal_member_is_rejected(tmp_path: Path) -> None:
    buffer = _custom_node_zip(
        f"ComfyUI_IPAdapter_plus-{CUSTOM_NODE_COMMIT}",
        extra_member="../escape.txt",
    )
    with ArtifactServer(buffer) as server:
        artifact = make_artifact(
            artifact_id="custom-node",
            file_name=f"ComfyUI_IPAdapter_plus-{CUSTOM_NODE_COMMIT}.zip",
            source_url=f"{server.base_url}/custom-node.zip",
            revision=CUSTOM_NODE_COMMIT,
            byte_size=len(buffer),
            sha256=hashlib.sha256(buffer).hexdigest(),
            destination="custom_nodes/ComfyUI_IPAdapter_plus.zip",
            allowed_hosts=("127.0.0.1",),
            license_name="GPL-3.0",
        )
        with pytest.raises(ProfileUnsafeArtifactError):
            _installer().install(
                _profile_with_artifacts([artifact]),
                _root(tmp_path),
            )


def test_partial_failure_keeps_ready_components_and_never_marks_ready(
    tmp_path: Path,
) -> None:
    first = _payload(512)
    with ArtifactServer(first) as server_a, ArtifactServer(
        b"x", mode="status-500"
    ) as server_b:
        profile = _profile_with_artifacts(
            [
                _local_artifact(
                    server_a,
                    first,
                    artifact_id="checkpoint",
                    destination="models/checkpoints/model.safetensors",
                ),
                _local_artifact(
                    server_b,
                    b"x",
                    artifact_id="lora",
                    destination="models/loras/style.safetensors",
                ),
            ]
        )
        installer = _installer()
        root = _root(tmp_path)
        with pytest.raises(Exception):
            installer.install(profile, root)
    status = installer.status(profile, root)
    assert status.ready is False
    states = {component.artifact_id: component.state for component in status.components}
    assert states["checkpoint"] == "ready"
    assert states["lora"] == "missing"
    assert (root / "models" / "checkpoints" / "model.safetensors").is_file()


def test_extra_model_paths_yaml_is_deterministic_and_managed_only(
    tmp_path: Path,
) -> None:
    payload = _payload(128)
    with ArtifactServer(payload) as server:
        profile = _profile_with_artifacts(
            [
                _local_artifact(
                    server,
                    payload,
                    artifact_id="checkpoint",
                    destination="models/checkpoints/${MANIFEST}-x.safetensors",
                )
            ]
        )
        installer = _installer()
        root = _root(tmp_path)
        path = installer.write_extra_model_paths(profile, root)
        content = path.read_text(encoding="utf-8")
        assert "checkpoints" in content
        assert "${MANIFEST}" not in content
        assert "C:" in content  # absolute managed path
        second = installer.write_extra_model_paths(profile, root)
        assert second.read_bytes() == path.read_bytes()
    assert all(
        line.strip().startswith(("models:", "  ", "- ", "checkpoints:", "loras:", "ipadapter:", "clip_vision:", "base_path:"))
        for line in content.splitlines()
    )


def _custom_node_zip(root_name: str, *, extra_member: str | None = None) -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{root_name}/__init__.py", "NODE_CLASS_MAPPINGS = {}\n")
        archive.writestr(f"{root_name}/nodes.py", "VERSION = 1\n")
        if extra_member:
            archive.writestr(extra_member, "evil")
    return buffer.getvalue()
