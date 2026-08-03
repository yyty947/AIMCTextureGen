"""Deterministic synthetic smoke inputs and real-profile smoke evidence."""

from __future__ import annotations

import hashlib
import io
import json
import platform
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

from PIL import Image
from pydantic import ConfigDict, Field, model_validator
from pydantic import BaseModel

from aimctexturegen.comfy.client import ComfyClient
from aimctexturegen.comfy.installer import (
    ProfileInstaller,
    RuntimeInstaller,
)
from aimctexturegen.comfy.manager import ComfyUIManager, ReadinessProbe
from aimctexturegen.comfy.manifests import manifest_sha256
from aimctexturegen.comfy.process import ProcessLauncher
from aimctexturegen.comfy.registry import ManifestRegistry
from aimctexturegen.model_profiles.sdxl import SDXLBinding
from aimctexturegen.model_profiles.workflows import GenericWorkflowInputs

STYLE_REFERENCE_SIZE = 512
STRUCTURE_REFERENCE_SIZE = 512
EXPECTED_CANVAS = 1024
SMOKE_PROFILE_ID = "sdxl-mapchip-ipadapter"
SMOKE_PROFILE_VERSION = "1"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SmokeMachine(_StrictModel):
    os_name: str = Field(min_length=1, max_length=32)
    architecture: str = Field(min_length=1, max_length=32)
    gpu_name: str | None = Field(default=None, max_length=256)
    driver_version: str | None = Field(default=None, max_length=64)
    vram_bytes: int | None = None


class SmokeWorkflowResult(_StrictModel):
    kind: Literal["text2img", "img2img"]
    prompt_id: str = Field(default="")
    seed: int = Field(ge=0)
    output_sha256: str = Field(min_length=64, max_length=64)
    output_canvas: int = Field(ge=1)
    duration_seconds: float = Field(ge=0)
    status: Literal["completed", "failed"] = "completed"
    failure_message: str | None = None

    @model_validator(mode="after")
    def validate_failure_consistency(self) -> Self:
        if self.status == "failed" and not self.failure_message:
            raise ValueError("failed smoke requires a failure message")
        if self.status == "completed" and self.failure_message is not None:
            raise ValueError("completed smoke cannot carry a failure message")
        return self


class SmokeEvidence(_StrictModel):
    machine: SmokeMachine
    runtime_release: str = Field(min_length=1)
    runtime_commit: str = Field(min_length=40, max_length=40)
    runtime_manifest_sha256: str = Field(min_length=64, max_length=64)
    profile_id: str
    profile_version: str
    profile_manifest_sha256: str = Field(min_length=64, max_length=64)
    workflow_digests: dict[str, str]
    runtime_stats: dict[str, str] = Field(default_factory=dict)
    started_at: datetime
    completed_at: datetime
    results: tuple[SmokeWorkflowResult, ...]

    @model_validator(mode="after")
    def reject_absolute_paths(self) -> Self:
        for value in self.model_dump(mode="json").values():
            if isinstance(value, str) and (
                "\\" in value or "C:" in value or ":/" in value
            ):
                raise ValueError("evidence must not contain absolute paths")
        return self


class SmokeFailedError(RuntimeError):
    """At least one real workflow smoke did not complete."""


def resolve_smoke_profile(registry: ManifestRegistry):
    """Resolve the explicit verified legacy profile for the v1 smoke path."""

    profile = registry.profile(SMOKE_PROFILE_ID, SMOKE_PROFILE_VERSION)
    if profile.support_state != "verified":
        raise SmokeFailedError("smoke profile must be verified")
    return profile


def generate_style_reference(size: int = STYLE_REFERENCE_SIZE) -> bytes:
    """Return a deterministic non-game geometric style PNG."""

    image = Image.new("RGB", (size, size))
    pixels = image.load()
    for y in range(size):
        for x in range(size):
            pixels[x, y] = (
                (x * 7 + y * 3) % 256,
                (x * 5 + y * 11) % 256,
                (x * 13 + y * 17) % 256,
            )
    for offset in range(0, size, 32):
        for index in range(size):
            pixels[index, offset] = (255, 255, 255)
            pixels[offset, index] = (255, 255, 255)
    return _encode_png(image)


def generate_structure_reference(size: int = STRUCTURE_REFERENCE_SIZE) -> bytes:
    """Return a deterministic checkerboard structure PNG."""

    image = Image.new("RGB", (size, size))
    pixels = image.load()
    for y in range(size):
        for x in range(size):
            value = 32 if ((x // 64) + (y // 64)) % 2 == 0 else 224
            pixels[x, y] = (value, value, value)
    return _encode_png(image)


def _encode_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _ClientProbe(ReadinessProbe):
    def __init__(self, port: int) -> None:
        self._client = ComfyClient(f"http://127.0.0.1:{port}")

    def system_stats(self) -> dict:
        return self._client.system_stats()

    def object_info(self) -> dict:
        return self._client.object_info()


def run_smoke(
    *,
    registry: ManifestRegistry,
    runtime_root: Path,
    evidence_dir: Path,
    port: int = 8188,
    readiness_timeout: float = 900.0,
) -> SmokeEvidence:
    """Install the pinned runtime/profile and run both real workflows."""

    root = Path(runtime_root)
    evidence_dir = Path(evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC)

    runtime = registry.runtime("comfyui-windows-nvidia")
    profile = resolve_smoke_profile(registry)
    archive = root / "downloads" / "ComfyUI_windows_portable_nvidia.7z"
    runtime_installer = RuntimeInstaller()
    if runtime_installer.status(runtime, root).state != "ready":
        if not archive.is_file():
            raise SmokeFailedError(
                f"runtime archive is missing: {archive.name}"
            )
        runtime_installer.install(runtime, archive, root)
    profile_installer = ProfileInstaller()
    profile_installer.install(profile, root)
    profile_installer.write_extra_model_paths(profile, root)

    manager = ComfyUIManager(
        runtime_root=root,
        runtime=runtime,
        profile=profile,
        launcher=ProcessLauncher(),
        probe=_ClientProbe(port),
        port=port,
        stabilization_seconds=1.0,
        readiness_timeout=readiness_timeout,
    )
    client = ComfyClient(f"http://127.0.0.1:{port}")
    results: list[SmokeWorkflowResult] = []
    try:
        manager.start()
        stats = client.system_stats()
        object_info = client.object_info()
        style_bytes = generate_style_reference()
        style_name = client.upload_image(style_bytes, "style-reference.png")["name"]
        structure_bytes = generate_structure_reference()
        structure_name = client.upload_image(
            structure_bytes,
            "structure-reference.png",
        )["name"]

        for kind, workflow_path in (
            ("text2img", "text2img.api.json"),
            ("img2img", "img2img.api.json"),
        ):
            seed = 1000 + len(results)
            started = time.monotonic()
            try:
                binding = SDXLBinding(
                    workflow_path=(
                        Path(__file__).resolve().parents[4]
                        / "workflows"
                        / "sdxl-mapchip-ipadapter-v1"
                        / workflow_path
                    )
                )
                binding.validate_server_nodes(set(object_info))
                inputs = GenericWorkflowInputs(
                    prompt="flat game-style stone block texture, no text",
                    negative_prompt="blurry, watermark",
                    seed=seed,
                    inference_canvas=16,
                    style_reference_names=(style_name,),
                    structure_reference_name=(
                        structure_name if kind == "img2img" else None
                    ),
                    advanced={},
                )
                compiled = binding.compile(inputs)
                prompt_id = client.submit_prompt(compiled)
                history = client.wait_completion(prompt_id, timeout=1800.0)
                output_name = _first_output_name(history)
                output = client.get_output(history, output_name)
                canvas = _verify_output(output, evidence_dir, kind, seed)
                results.append(
                    SmokeWorkflowResult(
                        kind=kind,
                        prompt_id=prompt_id,
                        seed=seed,
                        output_sha256=hashlib.sha256(output).hexdigest(),
                        output_canvas=canvas,
                        duration_seconds=time.monotonic() - started,
                        status="completed",
                    )
                )
            except Exception as exc:  # record one failed smoke honestly
                results.append(
                    SmokeWorkflowResult(
                        kind=kind,
                        prompt_id="",
                        seed=seed,
                        output_sha256="0" * 64,
                        output_canvas=EXPECTED_CANVAS,
                        duration_seconds=time.monotonic() - started,
                        status="failed",
                        failure_message=str(exc)[:400],
                    )
                )

        manager.stop()
        manager.start()
        manager.stop()
    finally:
        try:
            manager.stop()
        except Exception:
            pass
        client.close()

    workflow_digests = {
        workflow.kind: workflow.sha256
        for workflow in profile.workflows
        if workflow.sha256 is not None
    }
    device = None
    devices = stats.get("devices")
    if isinstance(devices, list):
        for candidate in devices:
            if isinstance(candidate, dict) and candidate.get("type") == "cuda":
                device = candidate
                break
    evidence = SmokeEvidence(
        machine=SmokeMachine(
            os_name=platform.system().lower() or "windows",
            architecture=platform.machine() or "unknown",
            gpu_name=device.get("name") if device is not None else None,
            driver_version=(
                device.get("driver") if device is not None else None
            ),
            vram_bytes=(
                device.get("vram_total") if device is not None else None
            ),
        ),
        runtime_release=runtime.runtime_version,
        runtime_commit=runtime.source_commit,
        runtime_manifest_sha256=manifest_sha256(runtime),
        profile_id=profile.profile_id,
        profile_version=profile.profile_version,
        profile_manifest_sha256=manifest_sha256(profile),
        workflow_digests=workflow_digests,
        runtime_stats={
            key: str(value)
            for key, value in stats.get("system", {}).items()
            if isinstance(value, (str, int, float))
        },
        started_at=started_at,
        completed_at=datetime.now(UTC),
        results=tuple(results),
    )
    (evidence_dir / "evidence.json").write_text(
        json.dumps(
            evidence.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if any(result.status == "failed" for result in results):
        raise SmokeFailedError("at least one real workflow smoke failed")
    return evidence


def run_smoke_from_env() -> SmokeEvidence:
    repo_root = Path(__file__).resolve().parents[4]
    registry = ManifestRegistry.load(repo_root)
    return run_smoke(
        registry=registry,
        runtime_root=repo_root / "runtime",
        evidence_dir=repo_root / "runtime" / "smoke",
        port=8188,
    )


def _first_output_name(history: dict) -> str:
    outputs = history.get("outputs", {})
    for node_output in outputs.values():
        if not isinstance(node_output, dict):
            continue
        for image in node_output.get("images", []):
            if isinstance(image, dict) and isinstance(
                image.get("filename"),
                str,
            ):
                return image["filename"]
    raise SmokeFailedError("history contains no declared output image")


def _verify_output(
    payload: bytes,
    evidence_dir: Path,
    kind: str,
    seed: int,
) -> int:
    try:
        image = Image.open(io.BytesIO(payload))
        image.verify()
        image = Image.open(io.BytesIO(payload))
    except Exception as exc:
        raise SmokeFailedError("output is not a decodable image") from exc
    width, height = image.size
    if width != height or width != EXPECTED_CANVAS:
        raise SmokeFailedError(
            f"output canvas is {width}x{height}, expected {EXPECTED_CANVAS}"
        )
    (evidence_dir / f"{kind}-{seed}.png").write_bytes(payload)
    return width
