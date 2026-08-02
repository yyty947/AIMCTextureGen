"""RED/GREEN tests for strict runtime and model-profile manifest models."""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from aimctexturegen.comfy.manifests import (
    ArtifactManifest,
    LicenseRecord,
    ModelProfileManifest,
    ProfileCapabilities,
    RuntimeManifest,
    WorkflowRecord,
    canonical_manifest_bytes,
    manifest_sha256,
)

from comfy._helpers import (
    CUSTOM_NODE_COMMIT,
    SDXL_REVISION,
    VALID_SHA,
    make_artifact,
    make_capabilities,
    make_license,
    make_profile,
    make_runtime,
    make_workflow,
    reversed_key_order,
)


def test_license_record_accepts_valid_values() -> None:
    license_record = LicenseRecord.model_validate(
        {"name": "Apache-2.0", "source_url": "https://example.com/license"}
    )
    assert license_record.name == "Apache-2.0"
    assert license_record.source_url == "https://example.com/license"


@pytest.mark.parametrize(
    ("builder", "label"),
    [
        (make_runtime, "runtime"),
        (make_profile, "profile"),
        (make_artifact, "artifact"),
        (make_license, "license"),
        (make_capabilities, "capabilities"),
        (make_workflow, "workflow"),
    ],
)
def test_unknown_fields_are_rejected(builder: object, label: str) -> None:
    value = builder()
    value["unexpected_field"] = 1
    with pytest.raises(ValidationError):
        if label == "runtime":
            RuntimeManifest.model_validate(value)
        elif label == "profile":
            ModelProfileManifest.model_validate(value)
        elif label == "artifact":
            ArtifactManifest.model_validate(value)
        elif label == "license":
            LicenseRecord.model_validate(value)
        elif label == "capabilities":
            ProfileCapabilities.model_validate(value)
        else:
            WorkflowRecord.model_validate(value)


@pytest.mark.parametrize(
    "sha256",
    [
        "A" * 64,
        "a" * 63,
        "g" * 64,
        "",
        f"{VALID_SHA}\n",
        f"{VALID_SHA} ",
    ],
)
def test_artifact_sha256_must_be_lowercase_64_hex(sha256: str) -> None:
    with pytest.raises(ValidationError):
        ArtifactManifest.model_validate(make_artifact(sha256=sha256))


@pytest.mark.parametrize(
    "sha256",
    ["A" * 64, "a" * 63, "g" * 64, "", f"{VALID_SHA}\n"],
)
def test_workflow_sha256_is_validated_when_present(sha256: str) -> None:
    workflow = make_workflow()
    workflow["sha256"] = sha256
    with pytest.raises(ValidationError):
        WorkflowRecord.model_validate(workflow)


def test_workflow_sha256_may_be_none_until_workflow_is_locked() -> None:
    workflow = WorkflowRecord.model_validate(make_workflow())
    assert workflow.sha256 is None


@pytest.mark.parametrize("byte_size", [0, -1, -100, 1.5, "1024"])
def test_artifact_byte_size_must_be_a_positive_integer(byte_size: object) -> None:
    with pytest.raises(ValidationError):
        ArtifactManifest.model_validate(make_artifact(byte_size=byte_size))


@pytest.mark.parametrize("revision", ["main", "latest", "master", "HEAD", "develop"])
def test_mutable_revision_identity_is_rejected(revision: str) -> None:
    with pytest.raises(ValidationError):
        ArtifactManifest.model_validate(make_artifact(revision=revision))


@pytest.mark.parametrize(
    "destination",
    [
        "/absolute/path.png",
        "trailing/slash/",
        "double//slash.png",
        "./relative.png",
        "assets/../escape.png",
        "assets/./stone.png",
        r"assets\minecraft\stone.png",
        "C:/drive/path.png",
        r"\\server\share\file.png",
        r"\\?\C:\device\file.png",
        "assets/CON/file.png",
        "assets/com9.bin",
        "assets/stone.png:stream",
        "assets/trailing./file.png",
        "assets/trailing-space /file.png",
        "assets/control\x01/file.png",
        "assets/question?/file.png",
        "assets/star*/file.png",
    ],
)
def test_unsafe_destinations_are_rejected(destination: str) -> None:
    with pytest.raises(ValidationError):
        ArtifactManifest.model_validate(make_artifact(destination=destination))


def test_runtime_archive_destination_must_live_under_downloads() -> None:
    runtime = make_runtime()
    runtime["archive"]["destination"] = "models/checkpoints/not-a-download.7z"
    with pytest.raises(ValidationError):
        RuntimeManifest.model_validate(runtime)


@pytest.mark.parametrize(
    "destination",
    [
        "downloads/out.7z",
        "tmp/model.safetensors",
        "custom_files/model.safetensors",
        "models",
    ],
)
def test_profile_artifact_destination_must_be_in_an_allowlisted_root(
    destination: str,
) -> None:
    profile = make_profile()
    profile["artifacts"][0]["destination"] = destination
    with pytest.raises(ValidationError):
        ModelProfileManifest.model_validate(profile)


def test_case_colliding_destinations_are_rejected() -> None:
    profile = make_profile()
    profile["artifacts"][1]["destination"] = "Models/Checkpoints/MODEL.SAFETENSORS"
    with pytest.raises(ValidationError):
        ModelProfileManifest.model_validate(profile)


def test_duplicate_artifact_ids_are_rejected() -> None:
    profile = make_profile()
    profile["artifacts"][1]["artifact_id"] = "checkpoint"
    with pytest.raises(ValidationError):
        ModelProfileManifest.model_validate(profile)


def test_same_destination_with_different_hash_is_rejected() -> None:
    profile = make_profile()
    profile["artifacts"][1]["destination"] = profile["artifacts"][0]["destination"]
    with pytest.raises(ValidationError):
        ModelProfileManifest.model_validate(profile)


@pytest.mark.parametrize("conflicting_key", ["byte_size", "source_url"])
def test_same_hash_with_conflicting_metadata_is_rejected(conflicting_key: str) -> None:
    profile = make_profile()
    duplicate = make_artifact(
        artifact_id="duplicate",
        file_name="copy.safetensors",
        destination="models/loras/copy.safetensors",
        sha256=profile["artifacts"][0]["sha256"],
        byte_size=profile["artifacts"][0]["byte_size"],
        source_url=profile["artifacts"][0]["source_url"],
    )
    if conflicting_key == "byte_size":
        duplicate["byte_size"] = duplicate["byte_size"] + 1
    else:
        duplicate["source_url"] = "https://huggingface.co/other/model"
    profile["artifacts"] = [profile["artifacts"][0], duplicate]
    with pytest.raises(ValidationError):
        ModelProfileManifest.model_validate(profile)


def test_identical_hash_may_be_reused_by_another_artifact() -> None:
    profile = make_profile()
    duplicate = make_artifact(
        artifact_id="deduplicated-copy",
        file_name="copy.safetensors",
        destination="models/loras/copy.safetensors",
        sha256=profile["artifacts"][0]["sha256"],
        byte_size=profile["artifacts"][0]["byte_size"],
        source_url=profile["artifacts"][0]["source_url"],
    )
    profile["artifacts"] = [profile["artifacts"][0], profile["artifacts"][1], duplicate]
    model = ModelProfileManifest.model_validate(profile)
    assert len(model.artifacts) == 3


def test_workflows_must_cover_text2img_and_img2img() -> None:
    profile = make_profile()
    profile["workflows"] = [profile["workflows"][0]]
    with pytest.raises(ValidationError):
        ModelProfileManifest.model_validate(profile)


def test_duplicate_workflow_kinds_are_rejected() -> None:
    profile = make_profile()
    profile["workflows"][1]["kind"] = "text2img"
    with pytest.raises(ValidationError):
        ModelProfileManifest.model_validate(profile)


@pytest.mark.parametrize(
    "relative_path",
    ["../escape.json", "/absolute.json", "C:/drive.json", r"sub\dir.json", "a//b.json"],
)
def test_unsafe_workflow_relative_paths_are_rejected(relative_path: str) -> None:
    workflow = make_workflow()
    workflow["relative_path"] = relative_path
    with pytest.raises(ValidationError):
        WorkflowRecord.model_validate(workflow)


def test_capabilities_reject_inverted_style_reference_range() -> None:
    with pytest.raises(ValidationError):
        ProfileCapabilities.model_validate(
            make_capabilities(style_reference_min=8, style_reference_max=1)
        )


def test_capabilities_allow_equal_style_reference_bounds() -> None:
    capabilities = ProfileCapabilities.model_validate(
        make_capabilities(style_reference_min=3, style_reference_max=3)
    )
    assert capabilities.style_reference_min == 3
    assert capabilities.style_reference_max == 3


def test_capabilities_require_every_declared_field() -> None:
    capabilities = make_capabilities()
    del capabilities["structure_reference"]
    with pytest.raises(ValidationError):
        ProfileCapabilities.model_validate(capabilities)


def test_custom_nodes_capability_requires_a_custom_node_artifact() -> None:
    profile = make_profile()
    profile["artifacts"] = [profile["artifacts"][0]]
    with pytest.raises(ValidationError):
        ModelProfileManifest.model_validate(profile)


def test_runtime_and_profile_accept_locked_candidate_shapes() -> None:
    runtime = RuntimeManifest.model_validate(make_runtime())
    profile = ModelProfileManifest.model_validate(make_profile())
    assert runtime.runtime_id == "comfyui-windows-nvidia"
    assert runtime.runtime_version == "0.29.2"
    assert runtime.source_commit == "322122449c9d2ba8b8df1bb517364527dd0615f1"
    assert profile.profile_id == "sdxl-mapchip-ipadapter"
    assert profile.profile_version == "1"
    assert profile.support_state == "candidate_unverified"
    assert profile.artifacts[1].revision == CUSTOM_NODE_COMMIT
    assert profile.artifacts[1].sha256 == (
        "c6c49c82aa65cb96b93bdf9f9b547f9c95310a2668a7a9aaa0285cccf4590347"
    )


def test_canonical_bytes_are_sorted_and_independent_of_input_key_order() -> None:
    first = RuntimeManifest.model_validate(make_runtime())
    second = RuntimeManifest.model_validate(
        reversed_key_order(make_runtime())
    )
    expected = json.dumps(
        first.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    assert canonical_manifest_bytes(first) == expected
    assert canonical_manifest_bytes(first) == canonical_manifest_bytes(second)


def test_manifest_sha256_is_the_lowercase_hex_of_canonical_bytes() -> None:
    profile = ModelProfileManifest.model_validate(make_profile())
    expected = hashlib.sha256(canonical_manifest_bytes(profile)).hexdigest()
    assert manifest_sha256(profile) == expected
    assert manifest_sha256(profile) == manifest_sha256(profile).lower()
    assert len(manifest_sha256(profile)) == 64


def test_canonical_bytes_never_contain_local_absolute_paths() -> None:
    runtime = RuntimeManifest.model_validate(make_runtime())
    serialized = canonical_manifest_bytes(runtime).decode("utf-8")
    assert "C:" not in serialized
    assert "\\Users" not in serialized
