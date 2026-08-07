"""Phase 5 profile-v2 qualification matrix and redacted smoke evidence.

The qualification path is deliberately separate from product execution.  It
may bind the candidate profile while it is still ``candidate_unverified``;
the normal generation service keeps its verified-profile gate unchanged.
"""

from __future__ import annotations

import ctypes
import hashlib
import io
import json
import os
import platform
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from PIL import Image
from pydantic import ConfigDict, Field, field_validator, model_validator
from pydantic import BaseModel

from aimctexturegen.comfy.client import ComfyClient, ComfyOutputImage
from aimctexturegen.comfy.environment import EnvironmentInspector
from aimctexturegen.comfy.installer import ProfileInstaller, RuntimeInstaller
from aimctexturegen.comfy.manager import ComfyUIManager, ReadinessProbe
from aimctexturegen.comfy.manifests import (
    ModelProfileManifestV2,
    RuntimeManifest,
    WorkflowVariant,
    manifest_sha256,
)
from aimctexturegen.comfy.process import ProcessLauncher
from aimctexturegen.comfy.registry import ManifestRegistry
from aimctexturegen.generation.prompts import compile_block_prompt
from aimctexturegen.generation.service import build_generation_profile_binding
from aimctexturegen.model_profiles.sdxl_v2 import SDXLV2Binding
from aimctexturegen.model_profiles.smoke import (
    generate_structure_reference,
    generate_style_reference,
)
from aimctexturegen.model_profiles.workflows import GenericWorkflowInputs
from aimctexturegen.processing.pipeline import process_candidate


PROFILE_ID = "sdxl-mapchip-ipadapter"
PROFILE_VERSION = "2"
RUNTIME_ID = "comfyui-windows-nvidia"
EXPECTED_CANVAS = 1024
EXPECTED_CANDIDATES = 4
VARIANTS: tuple[WorkflowVariant, ...] = (
    "text2img-no-style",
    "text2img-style",
    "img2img-no-style",
    "img2img-style",
)
BATCH_SIZES: tuple[Literal[1, 2, 4], ...] = (1, 2, 4)
_STYLE_VARIANTS = frozenset({"text2img-style", "img2img-style"})
_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_ABSOLUTE_PATH = re.compile(
    r"(?:^[A-Za-z]:[\\/]|^\\\\|^/|\b[A-Za-z]:[\\/])"
)
_SECRET_TEXT = re.compile(
    r"(?i)(?:authorization|bearer\s+|access[_-]?token|api[_-]?key|cookie)"
)
_PRIVATE_FAILURE_DETAIL = re.compile(
    r"(?i)(?:prompt|reference|filename|model|path|token|header|cookie|"
    r"[A-Za-z0-9_.-]+\.(?:png|jpe?g|safetensors|json))"
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SmokePlanItem:
    """One variant/native-batch-size qualification cell."""

    __slots__ = ("variant", "batch_size")

    def __init__(
        self,
        variant: WorkflowVariant,
        batch_size: Literal[1, 2, 4],
    ) -> None:
        self.variant = variant
        self.batch_size = batch_size

    @property
    def expected_output_count(self) -> int:
        return EXPECTED_CANDIDATES

    @property
    def candidate_partitions(self) -> tuple[tuple[int, ...], ...]:
        return native_batch_partitions(self.batch_size)


def _variant_uses_style_reference(variant: WorkflowVariant) -> bool:
    return variant in _STYLE_VARIANTS


def _qualification_binding(
    registry: ManifestRegistry,
    item: SmokePlanItem,
) -> Any:
    """Bind the candidate-only profile to this exact variant and digest."""

    binding = build_generation_profile_binding(
        registry,
        profile_id=PROFILE_ID,
        profile_version=PROFILE_VERSION,
        style_reference_count=(1 if _variant_uses_style_reference(item.variant) else 0),
        structure_reference_present=item.variant.startswith("img2img"),
        require_verified=False,
    )
    profile = registry.profile(PROFILE_ID, PROFILE_VERSION)
    workflow = next(
        workflow for workflow in profile.workflows if workflow.variant == item.variant
    )
    if (
        binding.workflow_variant != item.variant
        or binding.workflow_sha256 != workflow.sha256
        or binding.output_node_id != workflow.output_node_id
    ):
        raise SmokeQualificationError("qualification workflow binding mismatch")
    return binding


def native_batch_partitions(
    batch_size: Literal[1, 2, 4],
) -> tuple[tuple[int, ...], ...]:
    """Return the exact four-candidate partition for a native batch size."""

    return {
        1: ((0,), (1,), (2,), (3,)),
        2: ((0, 1), (2, 3)),
        4: ((0, 1, 2, 3),),
    }[batch_size]


def build_smoke_plan() -> tuple[SmokePlanItem, ...]:
    """Build the complete four-variant × 1/2/4 matrix in stable order."""

    return tuple(
        SmokePlanItem(variant, batch_size)
        for variant in VARIANTS
        for batch_size in BATCH_SIZES
    )


def candidate_order_for(item: SmokePlanItem) -> tuple[int, ...]:
    """Return the candidate order expected from all native batches in a cell."""

    return tuple(
        candidate_index
        for candidate_indices in item.candidate_partitions
        for candidate_index in candidate_indices
    )


def _validate_digest(value: str, *, length: int) -> str:
    pattern = _HEX_40 if length == 40 else _HEX_64
    if pattern.fullmatch(value) is None:
        raise ValueError(f"digest must be {length} lowercase hexadecimal characters")
    return value


def _validate_safe_text(value: str, *, field_name: str) -> str:
    if "\x00" in value or _ABSOLUTE_PATH.search(value):
        raise ValueError(f"{field_name} must not contain an absolute path")
    if _SECRET_TEXT.search(value):
        raise ValueError(f"{field_name} must not contain credentials")
    return value


def _validate_nested_privacy(value: object) -> None:
    if isinstance(value, bytes | bytearray | memoryview):
        raise ValueError("evidence must not contain image bytes")
    if isinstance(value, str):
        _validate_safe_text(value, field_name="evidence text")
    elif isinstance(value, dict):
        for nested in value.values():
            _validate_nested_privacy(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _validate_nested_privacy(nested)


class SmokeMachineV2(_StrictModel):
    os_name: str = Field(min_length=1, max_length=32)
    architecture: str = Field(min_length=1, max_length=32)
    gpu_name: str | None = Field(default=None, max_length=256)
    driver_version: str | None = Field(default=None, max_length=64)
    vram_mib: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_text(self) -> SmokeMachineV2:
        for field_name in ("os_name", "architecture", "gpu_name", "driver_version"):
            value = getattr(self, field_name)
            if value is not None:
                _validate_safe_text(value, field_name=field_name)
        return self


class SmokeCellEvidenceV2(_StrictModel):
    variant: WorkflowVariant
    batch_size: Literal[1, 2, 4]
    status: Literal["completed", "failed"]
    output_count: int = Field(ge=0, le=EXPECTED_CANDIDATES)
    output_order_sha256: str = Field(min_length=64, max_length=64)
    output_hashes: tuple[str, ...] = ()
    postprocess_status: Literal["completed", "failed", "not_run"]
    elapsed_seconds: float = Field(ge=0, le=86_400)
    peak_vram_mib: int | None = Field(default=None, ge=0)
    peak_process_ram_mib: int | None = Field(default=None, ge=0)
    peak_system_ram_mib: int | None = Field(default=None, ge=0)
    failure: str | None = Field(default=None, max_length=160)

    @field_validator("output_hashes", mode="before")
    @classmethod
    def coerce_output_hashes(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("output_order_sha256")
    @classmethod
    def validate_order_digest(cls, value: str) -> str:
        return _validate_digest(value, length=64)

    @field_validator("output_hashes")
    @classmethod
    def validate_output_hashes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > EXPECTED_CANDIDATES:
            raise ValueError("a smoke cell cannot contain more than four outputs")
        for digest in value:
            _validate_digest(digest, length=64)
        return value

    @model_validator(mode="after")
    def validate_result_consistency(self) -> SmokeCellEvidenceV2:
        if self.status == "completed":
            if (
                self.output_count != EXPECTED_CANDIDATES
                or len(self.output_hashes) != EXPECTED_CANDIDATES
                or self.postprocess_status != "completed"
                or self.failure is not None
            ):
                raise ValueError(
                    "completed smoke cells require four outputs and completed postprocess"
                )
        elif not self.failure:
            raise ValueError("failed smoke cells require a redacted failure code")
        if self.failure is not None:
            _validate_safe_text(self.failure, field_name="failure")
        return self


class SmokeEvidenceV2(_StrictModel):
    qualification: Literal["phase-5-profile-v2"]
    qualification_status: Literal["passed", "failed"] = "failed"
    machine: SmokeMachineV2
    runtime_id: str = Field(min_length=1, max_length=128)
    runtime_release: str = Field(min_length=1, max_length=64)
    runtime_commit: str = Field(min_length=40, max_length=40)
    runtime_manifest_sha256: str = Field(min_length=64, max_length=64)
    profile_id: str = Field(min_length=1, max_length=128)
    profile_version: str = Field(min_length=1, max_length=32)
    profile_manifest_sha256: str = Field(min_length=64, max_length=64)
    support_state: Literal["candidate_unverified", "verified"]
    workflow_digests: dict[WorkflowVariant, str]
    runtime_stats: dict[str, str] = Field(default_factory=dict)
    started_at: datetime
    completed_at: datetime
    restart_audit: Literal["passed", "failed", "not_run"] = "not_run"
    cells: tuple[SmokeCellEvidenceV2, ...] = Field(min_length=1, max_length=12)
    preflight_failure: str | None = Field(default=None, max_length=160)

    @field_validator("cells", mode="before")
    @classmethod
    def coerce_cells(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("runtime_commit")
    @classmethod
    def validate_runtime_commit(cls, value: str) -> str:
        return _validate_digest(value, length=40)

    @field_validator("runtime_manifest_sha256", "profile_manifest_sha256")
    @classmethod
    def validate_manifest_digest(cls, value: str) -> str:
        return _validate_digest(value, length=64)

    @field_validator("workflow_digests")
    @classmethod
    def validate_workflow_digests(
        cls, value: dict[WorkflowVariant, str]
    ) -> dict[WorkflowVariant, str]:
        if set(value) != set(VARIANTS):
            raise ValueError("v2 evidence must include all four workflow digests")
        for digest in value.values():
            _validate_digest(digest, length=64)
        return value

    @model_validator(mode="after")
    def reject_private_data(self) -> SmokeEvidenceV2:
        _validate_nested_privacy(self.model_dump(mode="json"))
        if self.completed_at < self.started_at:
            raise ValueError("evidence completed_at must not precede started_at")
        if self.preflight_failure is not None:
            _validate_safe_text(self.preflight_failure, field_name="preflight_failure")
        return self

    @property
    def matrix_complete(self) -> bool:
        return {
            (cell.variant, cell.batch_size)
            for cell in self.cells
        } == set((item.variant, item.batch_size) for item in build_smoke_plan())

    @property
    def qualified(self) -> bool:
        return (
            self.qualification_status == "passed"
            and self.restart_audit == "passed"
            and self.preflight_failure is None
            and self.matrix_complete
            and all(cell.status == "completed" for cell in self.cells)
        )


class SmokeQualificationError(RuntimeError):
    """A real qualification did not satisfy every required gate."""


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
    completion_timeout: float = 1_800.0,
) -> SmokeEvidenceV2:
    """Run the full v2 qualification and write only redacted evidence.

    The function never edits a tracked manifest.  It is safe to run while v2
    is candidate-only and deliberately calls the qualification-only binding
    gate with ``require_verified=False``.
    """

    root = Path(runtime_root)
    output_root = Path(evidence_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    run_root = output_root / f"run-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    run_root.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC)
    runtime = registry.runtime(RUNTIME_ID)
    profile_record = registry.profile(PROFILE_ID, PROFILE_VERSION)
    if not isinstance(profile_record, ModelProfileManifestV2):
        raise SmokeQualificationError("v2 manifest has the wrong schema")
    profile = profile_record

    environment = EnvironmentInspector().inspect(root)
    runtime_status = RuntimeInstaller().status(runtime, root)
    profile_status = ProfileInstaller().status(profile, root)
    preflight_failure = _preflight_failure(
        environment=environment,
        runtime=runtime,
        runtime_status=runtime_status,
        profile=profile,
        profile_status=profile_status,
        registry=registry,
        root=root,
    )
    machine = SmokeMachineV2(
        os_name=environment.platform or platform.system().lower() or "unknown",
        architecture=environment.architecture or platform.machine() or "unknown",
        gpu_name=environment.gpu_name,
        driver_version=environment.driver_version,
        vram_mib=(
            environment.vram_bytes // (1024 * 1024)
            if environment.vram_bytes is not None
            else None
        ),
    )
    workflow_digests = {
        workflow.variant: workflow.sha256 or "0" * 64
        for workflow in profile.workflows
    }
    runtime_stats: dict[str, str] = {}
    cells: list[SmokeCellEvidenceV2] = []
    restart_audit: Literal["passed", "failed", "not_run"] = "not_run"
    manager: ComfyUIManager | None = None
    client: ComfyClient | None = None

    if preflight_failure is not None:
        cells = [_failed_cell(item, preflight_failure) for item in build_smoke_plan()]
    else:
        try:
            # Qualification-only use of the candidate profile.  Product
            # creation and inference continue to use their default gate.
            for item in build_smoke_plan():
                _qualification_binding(registry, item)

            probe = _ClientProbe(port)
            manager = ComfyUIManager(
                runtime_root=root,
                runtime=runtime,
                profile=profile,
                launcher=ProcessLauncher(),
                probe=probe,
                port=port,
                stabilization_seconds=1.0,
                readiness_timeout=readiness_timeout,
            )
            client = ComfyClient(f"http://127.0.0.1:{port}")
            manager.start()
            stats = client.system_stats()
            runtime_stats = _redacted_runtime_stats(stats)
            object_info = client.object_info()
            style_name = client.upload_image(
                generate_style_reference(),
                "phase5-style-reference.png",
            )["name"]
            structure_name = client.upload_image(
                generate_structure_reference(),
                "phase5-structure-reference.png",
            )["name"]

            for item in build_smoke_plan():
                cells.append(
                    _run_cell(
                        item=item,
                        profile=profile,
                        registry=registry,
                        root=root,
                        client=client,
                        object_info=object_info,
                        style_name=str(style_name),
                        structure_name=str(structure_name),
                        run_root=run_root,
                        completion_timeout=completion_timeout,
                    )
                )

            manager.stop()
            manager.start()
            manager.stop()
            restart_audit = "passed"
        except Exception as exc:
            failure_code = _redact_failure(exc)
            if not cells:
                cells = [_failed_cell(item, failure_code) for item in build_smoke_plan()]
            else:
                # Preserve attempted cells and fill only unattempted matrix
                # entries; no output is invented for a failed run.
                attempted = {(cell.variant, cell.batch_size) for cell in cells}
                cells.extend(
                    _failed_cell(item, failure_code)
                    for item in build_smoke_plan()
                    if (item.variant, item.batch_size) not in attempted
                )
            restart_audit = "failed"
        finally:
            if manager is not None:
                try:
                    manager.stop()
                except Exception:
                    pass
            if client is not None:
                client.close()

    cells = sorted(cells, key=lambda cell: (VARIANTS.index(cell.variant), BATCH_SIZES.index(cell.batch_size)))
    qualification_status: Literal["passed", "failed"] = "passed"
    if preflight_failure is not None or restart_audit != "passed" or any(
        cell.status != "completed" for cell in cells
    ):
        qualification_status = "failed"
    evidence = SmokeEvidenceV2(
        qualification="phase-5-profile-v2",
        qualification_status=qualification_status,
        machine=machine,
        runtime_id=runtime.runtime_id,
        runtime_release=runtime.runtime_version,
        runtime_commit=runtime.source_commit,
        runtime_manifest_sha256=manifest_sha256(runtime),
        profile_id=profile.profile_id,
        profile_version=profile.profile_version,
        profile_manifest_sha256=manifest_sha256(profile),
        support_state=profile.support_state,
        workflow_digests=workflow_digests,
        runtime_stats=runtime_stats,
        started_at=started_at,
        completed_at=datetime.now(UTC),
        restart_audit=restart_audit,
        cells=tuple(cells),
        preflight_failure=preflight_failure,
    )
    write_evidence(output_root / "evidence.json", evidence)
    write_evidence(output_root / "full-evidence.json", evidence)
    if not evidence.qualified:
        raise SmokeQualificationError("phase-5 qualification did not pass every gate")
    return evidence


def run_smoke_from_env(*, port: int = 8188) -> SmokeEvidenceV2:
    repo_root = Path(__file__).resolve().parents[4]
    registry = ManifestRegistry.load(repo_root)
    return run_smoke(
        registry=registry,
        runtime_root=repo_root / "runtime",
        evidence_dir=repo_root / "runtime" / "smoke" / "phase-5",
        port=port,
    )


def write_evidence(path: Path, evidence: SmokeEvidenceV2) -> None:
    """Atomically write validated, privacy-safe JSON evidence."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        evidence.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
    SmokeEvidenceV2.model_validate_json(path.read_bytes())


def _preflight_failure(
    *,
    environment: Any,
    runtime: RuntimeManifest,
    runtime_status: Any,
    profile: ModelProfileManifestV2,
    profile_status: Any,
    registry: ManifestRegistry,
    root: Path,
) -> str | None:
    if not environment.supported:
        return "environment_unsupported"
    if runtime_status.state != "ready":
        return "runtime_receipt_not_ready"
    if profile_status.support_state not in {"candidate_unverified", "verified"}:
        return "profile_support_state_invalid"
    if not profile_status.ready:
        return "profile_artifact_receipt_not_ready"
    try:
        _verify_runtime_receipts(runtime, root)
        _verify_profile_receipts(profile, root)
        _verify_workflow_digests(registry, profile)
    except Exception:
        return "managed_receipt_or_digest_mismatch"
    return None


def _verify_runtime_receipts(runtime: RuntimeManifest, root: Path) -> None:
    selection = _read_json(root / "state" / "selected-runtime.json")
    receipt = _read_json(
        root
        / "state"
        / "installation-receipts"
        / f"{runtime.runtime_id}-{runtime.runtime_version}.json"
    )
    expected_manifest = manifest_sha256(runtime)
    if not isinstance(selection, dict) or not isinstance(receipt, dict):
        raise SmokeQualificationError("runtime receipt is missing")
    expected = {
        "runtime_id": runtime.runtime_id,
        "version": runtime.runtime_version,
        "manifest_sha256": expected_manifest,
    }
    if any(selection.get(key) != value for key, value in expected.items()):
        raise SmokeQualificationError("runtime selection receipt mismatch")
    if any(
        receipt.get(key) != value
        for key, value in {
            "runtime_id": runtime.runtime_id,
            "runtime_version": runtime.runtime_version,
            "manifest_sha256": expected_manifest,
            "archive_sha256": runtime.archive.sha256,
            "archive_byte_size": runtime.archive.byte_size,
        }.items()
    ):
        raise SmokeQualificationError("runtime installation receipt mismatch")
    selected_dir = root / "comfyui" / str(selection["directory"])
    if not selected_dir.is_dir():
        raise SmokeQualificationError("selected runtime directory is missing")
    for required in runtime.required_paths:
        if not (selected_dir / runtime.expected_archive_root / required).is_file():
            raise SmokeQualificationError("runtime required file is missing")


def _verify_profile_receipts(profile: ModelProfileManifestV2, root: Path) -> None:
    records = _read_json(root / "state" / "installed-artifacts.json")
    artifacts = records.get("artifacts") if isinstance(records, dict) else None
    if not isinstance(artifacts, dict):
        raise SmokeQualificationError("profile artifact receipt is missing")
    for artifact in profile.artifacts:
        record = artifacts.get(artifact.artifact_id)
        target = root / artifact.destination
        if not isinstance(record, dict) or not target.is_file():
            raise SmokeQualificationError("profile artifact receipt is incomplete")
        if (
            record.get("sha256") != artifact.sha256
            or record.get("byte_size") != artifact.byte_size
            or record.get("destination") != artifact.destination
            or _sha256_file(target) != artifact.sha256
            or target.stat().st_size != artifact.byte_size
        ):
            raise SmokeQualificationError("profile artifact digest mismatch")


def _verify_workflow_digests(
    registry: ManifestRegistry,
    profile: ModelProfileManifestV2,
) -> None:
    root = Path(getattr(registry, "_root"))
    if any(workflow.sha256 is None for workflow in profile.workflows):
        raise SmokeQualificationError("profile workflow digest is not locked")
    for workflow in profile.workflows:
        path = root / "workflows" / workflow.relative_path
        if not path.is_file() or _sha256_file(path) != workflow.sha256:
            raise SmokeQualificationError("profile workflow digest mismatch")


def _run_cell(
    *,
    item: SmokePlanItem,
    profile: ModelProfileManifestV2,
    registry: ManifestRegistry,
    root: Path,
    client: ComfyClient,
    object_info: dict,
    style_name: str,
    structure_name: str,
    run_root: Path,
    completion_timeout: float,
) -> SmokeCellEvidenceV2:
    started = time.monotonic()
    raw_hashes: list[str] = []
    metrics: list[dict[str, int | None]] = []
    postprocess_status: Literal["completed", "failed", "not_run"] = "not_run"
    try:
        workflow = next(workflow for workflow in profile.workflows if workflow.variant == item.variant)
        workflow_path = Path(getattr(registry, "_root")) / "workflows" / workflow.relative_path
        binding = SDXLV2Binding(variant=item.variant, workflow_path=workflow_path)
        binding.validate_server_nodes(set(object_info))
        prompt = compile_block_prompt(
            resolution=16,
            display_name="Synthetic Stone",
            prompt_terms=("granular blue gray stone",),
            user_description="qualification texture",
            user_negative_prompt="",
        )
        cell_root = run_root / item.variant / f"batch-{item.batch_size}"
        raw_root = cell_root / "raw"
        processed_root = cell_root / "processed"
        raw_root.mkdir(parents=True, exist_ok=True)
        uploaded = {
            "style": (style_name,)
            if _variant_uses_style_reference(item.variant)
            else (),
            "structure": structure_name if item.variant.startswith("img2img") else None,
        }
        output_index = 0
        for batch_index, candidate_indices in enumerate(item.candidate_partitions):
            metric_before = _sample_metrics(_managed_pid(root))
            inputs = GenericWorkflowInputs(
                prompt=prompt.compiled_positive,
                negative_prompt=prompt.compiled_negative,
                seed=_smoke_seed(item.variant, item.batch_size, batch_index),
                inference_canvas=16,
                batch_size=len(candidate_indices),
                style_reference_names=uploaded["style"],
                structure_reference_name=uploaded["structure"],
                advanced={},
            )
            prompt_id = client.submit_prompt(binding.compile(inputs))
            history = client.wait_completion(
                prompt_id,
                timeout=completion_timeout,
            )
            declared: tuple[ComfyOutputImage, ...] = client.declared_output_images(
                history,
                output_node_id=workflow.output_node_id,
            )
            if len(declared) != len(candidate_indices):
                raise SmokeQualificationError("native batch output count mismatch")
            for position, image_descriptor in enumerate(declared):
                payload = client.get_output_image(image_descriptor)
                _validate_output(payload)
                candidate_index = candidate_indices[position]
                raw_path = raw_root / f"candidate-{candidate_index}.png"
                raw_path.write_bytes(payload)
                raw_hashes.append(hashlib.sha256(payload).hexdigest())
                output_index += 1
            metric_after = _sample_metrics(_managed_pid(root))
            metrics.extend((metric_before, metric_after))

        if output_index != EXPECTED_CANDIDATES:
            raise SmokeQualificationError("qualification cell did not produce four outputs")
        for candidate_index in range(EXPECTED_CANDIDATES):
            process_candidate(
                raw_root / f"candidate-{candidate_index}.png",
                processed_root / f"candidate-{candidate_index}",
                stem=f"candidate-{candidate_index}",
                resolution=16,
            )
        postprocess_status = "completed"
        return _completed_cell(
            item=item,
            raw_hashes=raw_hashes,
            metrics=metrics,
            postprocess_status=postprocess_status,
            elapsed_seconds=time.monotonic() - started,
        )
    except Exception as exc:
        return SmokeCellEvidenceV2(
            variant=item.variant,
            batch_size=item.batch_size,
            status="failed",
            output_count=len(raw_hashes),
            output_order_sha256=_ordered_hash(raw_hashes),
            output_hashes=tuple(raw_hashes),
            postprocess_status=postprocess_status,
            elapsed_seconds=time.monotonic() - started,
            **_peak_metrics(metrics),
            failure=_redact_failure(exc),
        )


def _completed_cell(
    *,
    item: SmokePlanItem,
    raw_hashes: list[str],
    metrics: list[dict[str, int | None]],
    postprocess_status: Literal["completed", "failed", "not_run"],
    elapsed_seconds: float,
) -> SmokeCellEvidenceV2:
    return SmokeCellEvidenceV2(
        variant=item.variant,
        batch_size=item.batch_size,
        status="completed",
        output_count=len(raw_hashes),
        output_order_sha256=_ordered_hash(raw_hashes),
        output_hashes=tuple(raw_hashes),
        postprocess_status=postprocess_status,
        elapsed_seconds=elapsed_seconds,
        **_peak_metrics(metrics),
    )


def _failed_cell(item: SmokePlanItem, failure: str) -> SmokeCellEvidenceV2:
    return SmokeCellEvidenceV2(
        variant=item.variant,
        batch_size=item.batch_size,
        status="failed",
        output_count=0,
        output_order_sha256="0" * 64,
        output_hashes=(),
        postprocess_status="not_run",
        elapsed_seconds=0.0,
        failure=_redact_failure(failure),
    )


def _ordered_hash(raw_hashes: list[str]) -> str:
    return hashlib.sha256("\n".join(raw_hashes).encode("ascii")).hexdigest()


def _peak_metrics(metrics: list[dict[str, int | None]]) -> dict[str, int | None]:
    return {
        key: max(
            (value for metric in metrics if (value := metric.get(key)) is not None),
            default=None,
        )
        for key in ("peak_vram_mib", "peak_process_ram_mib", "peak_system_ram_mib")
    }


def _validate_output(payload: bytes) -> None:
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.verify()
        with Image.open(io.BytesIO(payload)) as image:
            if image.size != (EXPECTED_CANVAS, EXPECTED_CANVAS) or image.mode != "RGB":
                raise ValueError("output contract mismatch")
    except Exception as exc:
        raise SmokeQualificationError("output contract violation") from exc


def _smoke_seed(variant: WorkflowVariant, batch_size: int, batch_index: int) -> int:
    return 1_700_000_000_000_000 + (
        VARIANTS.index(variant) * 10_000
        + batch_size * 100
        + batch_index
    )


def _redacted_runtime_stats(payload: dict) -> dict[str, str]:
    system = payload.get("system") if isinstance(payload, dict) else None
    if not isinstance(system, dict):
        return {}
    allowed = {"comfyui_version", "python_version", "pytorch_version", "cuda_version"}
    return {
        key: str(value)[:128]
        for key, value in system.items()
        if key in allowed and isinstance(value, (str, int, float))
    }


def _redact_failure(value: object) -> str:
    if isinstance(value, BaseException):
        detail = str(value)
        text = (
            f"{type(value).__name__}: {detail}"
            if type(value).__name__ == "WorkflowBindingError"
            and detail
            and not _PRIVATE_FAILURE_DETAIL.search(detail)
            else type(value).__name__
        )
    else:
        text = str(value)
    if _SECRET_TEXT.search(text):
        return "controlled_failure_redacted"
    text = re.sub(r"[A-Za-z]:[\\/][^\s,;]+", "<redacted-path>", text)
    text = re.sub(r"\\\\[^\s,;]+", "<redacted-path>", text)
    text = text.replace("\n", " ").strip()
    return text[:160] or "controlled_failure"


def _read_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _gpu_memory_used_mib() -> int | None:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    try:
        return max(0, int(completed.stdout.strip().splitlines()[0]))
    except (IndexError, ValueError):
        return None


def _managed_pid(root: Path) -> int | None:
    record = _read_json(Path(root) / "state" / "process.json")
    value = record.get("pid") if isinstance(record, dict) else None
    return int(value) if isinstance(value, int) and value > 0 else None


def _process_memory_mib(pid: int | None) -> int | None:
    if pid is None:
        return None
    if os.name != "nt":
        return None
    try:
        from ctypes import wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_Counters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

        process_query = 0x1000
        handle = kernel32.OpenProcess(process_query, False, pid)
        if not handle:
            return None
        try:
            counters = _Counters()
            counters.cb = ctypes.sizeof(counters)
            ok = psapi.GetProcessMemoryInfo(
                handle,
                ctypes.byref(counters),
                ctypes.sizeof(counters),
            )
            return int(counters.PeakWorkingSetSize // (1024 * 1024)) if ok else None
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, TypeError):
        return None


def _system_memory_used_mib() -> int | None:
    if os.name != "nt":
        return None
    try:
        class _MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return int((status.ullTotalPhys - status.ullAvailPhys) // (1024 * 1024))
    except (AttributeError, OSError, TypeError):
        return None


def _sample_metrics(pid: int | None) -> dict[str, int | None]:
    return {
        "peak_vram_mib": _gpu_memory_used_mib(),
        "peak_process_ram_mib": _process_memory_mib(pid),
        "peak_system_ram_mib": _system_memory_used_mib(),
    }
