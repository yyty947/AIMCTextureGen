"""RED/GREEN tests for the read-only manifest registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aimctexturegen.comfy.errors import ManifestError
from aimctexturegen.comfy.registry import ManifestRegistry

from comfy._helpers import make_profile, make_runtime

REPO_ROOT = Path(__file__).resolve().parents[3]


def _write_manifest_tree(
    tmp_path: Path,
    *,
    runtime_files: dict[str, dict] | None = None,
    profile_files: dict[str, dict] | None = None,
) -> Path:
    runtimes_dir = tmp_path / "manifests" / "runtimes"
    profiles_dir = tmp_path / "manifests" / "model-profiles"
    runtimes_dir.mkdir(parents=True)
    profiles_dir.mkdir(parents=True)
    (tmp_path / "workflows").mkdir(parents=True)
    for file_name, value in (runtime_files or {}).items():
        (runtimes_dir / file_name).write_text(
            json.dumps(value, indent=2), encoding="utf-8"
        )
    for file_name, value in (profile_files or {}).items():
        (profiles_dir / file_name).write_text(
            json.dumps(value, indent=2), encoding="utf-8"
        )
    return tmp_path


def test_load_exposes_runtimes_profiles_and_runtime_compatibility(
    tmp_path: Path,
) -> None:
    root = _write_manifest_tree(
        tmp_path,
        runtime_files={"comfyui.json": make_runtime()},
        profile_files={"sdxl.json": make_profile()},
    )
    registry = ManifestRegistry.load(root)
    assert registry.runtime("comfyui-windows-nvidia").runtime_version == "0.29.2"
    assert registry.profile("sdxl-mapchip-ipadapter").profile_version == "1"
    profiles = registry.profiles_for_runtime("comfyui-windows-nvidia")
    assert [profile.profile_id for profile in profiles] == [
        "sdxl-mapchip-ipadapter"
    ]


def test_load_order_is_deterministic_by_file_name(tmp_path: Path) -> None:
    root = _write_manifest_tree(
        tmp_path,
        runtime_files={
            "z-runtime.json": make_runtime(),
            "a-runtime.json": make_runtime(
                runtime_id="second-runtime", runtime_version="0.1.0"
            ),
        },
        profile_files={},
    )
    registry = ManifestRegistry.load(root)
    assert list(registry.runtimes) == ["second-runtime", "comfyui-windows-nvidia"]


def test_load_requires_manifests_directories(tmp_path: Path) -> None:
    with pytest.raises(ManifestError):
        ManifestRegistry.load(tmp_path)


def test_load_rejects_unknown_compatible_runtime(tmp_path: Path) -> None:
    profile = make_profile(compatible_runtime_ids=("missing-runtime",))
    root = _write_manifest_tree(
        tmp_path,
        runtime_files={"comfyui.json": make_runtime()},
        profile_files={"sdxl.json": profile},
    )
    with pytest.raises(ManifestError):
        ManifestRegistry.load(root)


def test_load_rejects_duplicate_runtime_ids_across_files(tmp_path: Path) -> None:
    root = _write_manifest_tree(
        tmp_path,
        runtime_files={
            "one.json": make_runtime(),
            "two.json": make_runtime(runtime_version="9.9.9"),
        },
        profile_files={},
    )
    with pytest.raises(ManifestError):
        ManifestRegistry.load(root)


def test_load_rejects_workflow_path_escaping_the_workflow_root(
    tmp_path: Path,
) -> None:
    profile = make_profile()
    profile["workflows"][0]["relative_path"] = "sdxl/../../escape.json"
    root = _write_manifest_tree(
        tmp_path,
        runtime_files={"comfyui.json": make_runtime()},
        profile_files={"sdxl.json": profile},
    )
    with pytest.raises(ManifestError):
        ManifestRegistry.load(root)


def test_load_rejects_malformed_json(tmp_path: Path) -> None:
    runtimes_dir = tmp_path / "manifests" / "runtimes"
    runtimes_dir.mkdir(parents=True)
    (tmp_path / "manifests" / "model-profiles").mkdir(parents=True)
    (runtimes_dir / "broken.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ManifestError):
        ManifestRegistry.load(tmp_path)


def test_load_is_read_only_and_creates_nothing(tmp_path: Path) -> None:
    root = _write_manifest_tree(
        tmp_path,
        runtime_files={"comfyui.json": make_runtime()},
        profile_files={"sdxl.json": make_profile()},
    )
    before = sorted(str(path) for path in root.rglob("*"))
    registry = ManifestRegistry.load(root)
    after = sorted(str(path) for path in root.rglob("*"))
    assert before == after
    with pytest.raises(ValidationError):
        registry.runtime("comfyui-windows-nvidia").runtime_version = "9.9.9"


def test_real_repo_manifests_are_locked_to_the_candidate_pins() -> None:
    registry = ManifestRegistry.load(REPO_ROOT)

    runtime = registry.runtime("comfyui-windows-nvidia")
    assert runtime.runtime_version == "0.29.2"
    assert runtime.source_commit == "322122449c9d2ba8b8df1bb517364527dd0615f1"
    archive = runtime.archive
    assert archive.artifact_id == "comfyui-windows-portable-nvidia-v0.29.2"
    assert archive.byte_size == 2_103_175_457
    assert archive.sha256 == (
        "e7a39a817002d85b4fb2d4f6bd176c10d104a0d04031f99b9d8b7b1fd920c6fc"
    )
    assert archive.destination == "downloads/ComfyUI_windows_portable_nvidia.7z"
    assert archive.license.name == "GPL-3.0"

    profile = registry.profile("sdxl-mapchip-ipadapter")
    assert profile.profile_version == "1"
    assert profile.support_state == "verified"
    assert profile.compatible_runtime_ids == ("comfyui-windows-nvidia",)
    assert {workflow.kind for workflow in profile.workflows} == {
        "text2img",
        "img2img",
    }

    artifacts = {artifact.artifact_id: artifact for artifact in profile.artifacts}
    assert artifacts["sdxl-base-1.0"].byte_size == 6_938_078_334
    assert artifacts["sdxl-base-1.0"].sha256 == (
        "31e35c80fc4829d14f90153f4c74cd59c90b779f6afe05a74cd6120b893f7e5b"
    )
    assert artifacts["sdxl-base-1.0"].destination == (
        "models/checkpoints/sd_xl_base_1.0.safetensors"
    )
    assert artifacts["mapchip-lora"].byte_size == 912_555_676
    assert artifacts["mapchip-lora"].sha256 == (
        "9a047fce0fd45e60aaee6bcf6ec465ba34397366b10a34bfb2175f5e129ac1ae"
    )
    assert artifacts["mapchip-lora"].destination == "models/loras/mapchipLora.safetensors"
    assert artifacts["ip-adapter-sdxl-vit-h"].byte_size == 698_391_064
    assert artifacts["ip-adapter-sdxl-vit-h"].sha256 == (
        "ebf05d918348aec7abb02a5e9ecef77e0aaea6914a5c4ea13f50d45eb1681831"
    )
    assert artifacts["ip-adapter-sdxl-vit-h"].destination == (
        "models/ipadapter/ip-adapter_sdxl_vit-h.safetensors"
    )
    assert artifacts["clip-vit-h-image-encoder"].byte_size == 2_528_373_448
    assert artifacts["clip-vit-h-image-encoder"].sha256 == (
        "6ca9667da1ca9e0b0f75e46bb030f7e011f44f86cbfb8d5a36590fcd7507b030"
    )
    assert artifacts["clip-vit-h-image-encoder"].destination == (
        "models/clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"
    )

    custom_node = artifacts["comfyui-ipadapter-plus"]
    assert custom_node.revision == "a0f451a5113cf9becb0847b92884cb10cbdec0ef"
    assert custom_node.byte_size == 306_422
    assert custom_node.sha256 == (
        "c6c49c82aa65cb96b93bdf9f9b547f9c95310a2668a7a9aaa0285cccf4590347"
    )
    assert custom_node.destination == "custom_nodes/ComfyUI_IPAdapter_plus.zip"
    assert custom_node.license.name == "GPL-3.0"
