"""Shared dict builders for strict manifest RED/GREEN tests."""

from __future__ import annotations

from typing import Any

VALID_SHA = "a" * 64
SDXL_REVISION = "462165984030d82259a11f4367a4eed129e94a7b"
CUSTOM_NODE_COMMIT = "a0f451a5113cf9becb0847b92884cb10cbdec0ef"


def make_license(
    *,
    name: str = "GPL-3.0",
    source_url: str = "https://example.com/license",
) -> dict[str, Any]:
    return {"name": name, "source_url": source_url}


def make_artifact(
    *,
    artifact_id: str = "checkpoint",
    file_name: str = "model.safetensors",
    source_url: str = "https://huggingface.co/example/model",
    revision: str = SDXL_REVISION,
    byte_size: int = 1024,
    sha256: str = VALID_SHA,
    destination: str = "models/checkpoints/model.safetensors",
    allowed_hosts: tuple[str, ...] = ("huggingface.co",),
    license_name: str = "Apache-2.0",
) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "file_name": file_name,
        "source_url": source_url,
        "revision": revision,
        "byte_size": byte_size,
        "sha256": sha256,
        "destination": destination,
        "allowed_hosts": list(allowed_hosts),
        "license": make_license(name=license_name),
    }


def make_capabilities(
    *,
    text_to_image: bool = True,
    structure_reference: bool = True,
    style_reference_min: int = 1,
    style_reference_max: int = 8,
    native_multi_reference: bool = False,
    requires_custom_nodes: bool = True,
) -> dict[str, Any]:
    return {
        "text_to_image": text_to_image,
        "structure_reference": structure_reference,
        "style_reference_min": style_reference_min,
        "style_reference_max": style_reference_max,
        "native_multi_reference": native_multi_reference,
        "requires_custom_nodes": requires_custom_nodes,
    }


def make_workflow(
    *,
    kind: str = "text2img",
    relative_path: str = "sdxl-mapchip-ipadapter-v1/text2img.api.json",
    sha256: str | None = None,
) -> dict[str, Any]:
    return {"kind": kind, "relative_path": relative_path, "sha256": sha256}


def make_runtime(**overrides: Any) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "runtime_id": "comfyui-windows-nvidia",
        "runtime_version": "0.29.2",
        "platform": "windows",
        "architecture": "x86_64",
        "gpu_vendor": "nvidia",
        "host_requirements": "Windows x64 with NVIDIA CUDA",
        "release_url": "https://github.com/Comfy-Org/ComfyUI/releases/tag/v0.29.2",
        "source_commit": "322122449c9d2ba8b8df1bb517364527dd0615f1",
        "licenses": [make_license()],
        "archive": make_artifact(
            artifact_id="comfyui-portable",
            file_name="ComfyUI_windows_portable_nvidia.7z",
            source_url="https://github.com/Comfy-Org/ComfyUI/releases/download/v0.29.2/ComfyUI_windows_portable_nvidia.7z",
            revision="v0.29.2",
            byte_size=2_103_175_457,
            sha256="e7a39a817002d85b4fb2d4f6bd176c10d104a0d04031f99b9d8b7b1fd920c6fc",
            destination="downloads/ComfyUI_windows_portable_nvidia.7z",
            allowed_hosts=("github.com", "objects.githubusercontent.com"),
            license_name="GPL-3.0",
        ),
        "expected_archive_root": "ComfyUI_windows_portable_nvidia",
        "required_paths": ("python_embeded/python.exe", "ComfyUI/main.py"),
        "startup_argument_template": (
            "python_embeded/python.exe",
            "-s",
            "ComfyUI/main.py",
        ),
        "health_endpoint": "/system_stats",
        "expected_runtime_identity": "0.29.2",
        "extraction_headroom_bytes": 8_000_000_000,
        "headroom_is_estimate": True,
        "revision_notes": "candidate lock",
    }
    manifest.update(overrides)
    return manifest


def make_profile(**overrides: Any) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "profile_id": "sdxl-mapchip-ipadapter",
        "profile_version": "1",
        "support_state": "candidate_unverified",
        "compatible_runtime_ids": ("comfyui-windows-nvidia",),
        "compatible_runtime_versions": ("0.29.2",),
        "capabilities": make_capabilities(),
        "artifacts": [
            make_artifact(
                artifact_id="checkpoint",
                file_name="model.safetensors",
                destination="models/checkpoints/model.safetensors",
            ),
            make_artifact(
                artifact_id="custom-node",
                file_name=f"ComfyUI_IPAdapter_plus-{CUSTOM_NODE_COMMIT}.zip",
                source_url=(
                    "https://codeload.github.com/cubiq/ComfyUI_IPAdapter_plus/"
                    f"zip/{CUSTOM_NODE_COMMIT}"
                ),
                revision=CUSTOM_NODE_COMMIT,
                byte_size=306_422,
                sha256="c6c49c82aa65cb96b93bdf9f9b547f9c95310a2668a7a9aaa0285cccf4590347",
                destination="custom_nodes/ComfyUI_IPAdapter_plus.zip",
                allowed_hosts=("codeload.github.com", "github.com"),
                license_name="GPL-3.0",
            ),
        ],
        "workflows": [
            make_workflow(),
            make_workflow(
                kind="img2img",
                relative_path="sdxl-mapchip-ipadapter-v1/img2img.api.json",
            ),
        ],
        "required_node_classes": ("CheckpointLoaderSimple",),
        "output_contract": {
            "format": "png",
            "color_mode": "rgb",
            "canvas_size": 1024,
        },
        "profile_defaults": {},
        "user_limitations": "candidate profile",
        "revision_notes": None,
    }
    manifest.update(overrides)
    return manifest


def reversed_key_order(value: dict[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in reversed(list(value))}
