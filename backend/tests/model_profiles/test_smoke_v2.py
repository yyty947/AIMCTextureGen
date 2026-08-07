"""RED/GREEN tests for the Phase 5 profile-v2 qualification contract."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from aimctexturegen.comfy.errors import WorkflowBindingError
from aimctexturegen.comfy.registry import ManifestRegistry
from aimctexturegen.generation.service import (
    _verified_resource_hints,
    build_generation_profile_binding,
)
from aimctexturegen.model_profiles.smoke_v2 import (
    SmokeEvidenceV2,
    _process_memory_mib,
    _qualification_binding,
    _redact_failure,
    _variant_uses_style_reference,
    build_smoke_plan,
    candidate_order_for,
    write_evidence,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


def _valid_evidence_dict() -> dict:
    return {
        "qualification": "phase-5-profile-v2",
        "machine": {
            "os_name": "windows",
            "architecture": "AMD64",
            "gpu_name": "RTX 5080 Laptop GPU",
            "driver_version": "610.88",
            "vram_mib": 16304,
        },
        "runtime_id": "comfyui-windows-nvidia",
        "runtime_release": "0.29.2",
        "runtime_commit": "322122449c9d2ba8b8df1bb517364527dd0615f1",
        "runtime_manifest_sha256": "a" * 64,
        "profile_id": "sdxl-mapchip-ipadapter",
        "profile_version": "2",
        "profile_manifest_sha256": "b" * 64,
        "support_state": "candidate_unverified",
        "workflow_digests": {
            "text2img-no-style": "c" * 64,
            "text2img-style": "d" * 64,
            "img2img-no-style": "e" * 64,
            "img2img-style": "f" * 64,
        },
        "started_at": datetime(2026, 8, 7, tzinfo=UTC),
        "completed_at": datetime(2026, 8, 7, 0, 10, tzinfo=UTC),
        "restart_audit": "passed",
        "cells": [
            {
                "variant": "text2img-no-style",
                "batch_size": 1,
                "status": "completed",
                "output_count": 4,
                "output_order_sha256": "1" * 64,
                "output_hashes": ["2" * 64] * 4,
                "postprocess_status": "completed",
                "elapsed_seconds": 12.5,
                "peak_vram_mib": 12000,
                "peak_process_ram_mib": 6000,
                "peak_system_ram_mib": 9000,
                "failure": None,
            }
        ],
    }


def test_v2_smoke_matrix_covers_four_variants_and_three_batch_sizes() -> None:
    plan = build_smoke_plan()

    assert {item.variant for item in plan} == {
        "text2img-no-style",
        "text2img-style",
        "img2img-no-style",
        "img2img-style",
    }
    assert {item.batch_size for item in plan} == {1, 2, 4}
    assert len(plan) == 12
    assert all(item.expected_output_count == 4 for item in plan)


def test_v2_native_batches_map_declared_outputs_to_all_four_candidates() -> None:
    for item in build_smoke_plan():
        assert candidate_order_for(item) == (0, 1, 2, 3)


def test_no_style_variants_never_receive_style_references() -> None:
    assert not _variant_uses_style_reference("text2img-no-style")
    assert not _variant_uses_style_reference("img2img-no-style")
    assert _variant_uses_style_reference("text2img-style")
    assert _variant_uses_style_reference("img2img-style")


def test_qualification_binds_each_exact_v2_variant_and_digest() -> None:
    registry = ManifestRegistry.load(REPO_ROOT)
    profile = registry.profile("sdxl-mapchip-ipadapter", "2")
    expected = {
        workflow.variant: workflow.sha256
        for workflow in profile.workflows
    }

    for item in build_smoke_plan():
        binding = _qualification_binding(registry, item)
        assert binding.workflow_variant == item.variant
        assert binding.workflow_sha256 == expected[item.variant]


def test_promoted_v2_product_binding_accepts_each_exact_variant() -> None:
    registry = ManifestRegistry.load(REPO_ROOT)

    for item in build_smoke_plan():
        binding = build_generation_profile_binding(
            registry,
            profile_id="sdxl-mapchip-ipadapter",
            profile_version="2",
            style_reference_count=(
                1 if _variant_uses_style_reference(item.variant) else 0
            ),
            structure_reference_present=item.variant.startswith("img2img"),
        )
        assert binding.workflow_variant == item.variant


def test_v1_manifest_and_workflow_bytes_remain_immutable() -> None:
    expected = {
        REPO_ROOT / "manifests/model-profiles/sdxl-mapchip-ipadapter-v1.json":
            "9b909dc2d3b250f03b9a72996f43b6eaa3fa50f5eef0a38900e301a41678ccdd",
        REPO_ROOT / "workflows/sdxl-mapchip-ipadapter-v1/text2img.api.json":
            "80362974e036874262d166389c4099bfc0b85efb1aa6c9f98e58dce6ba8cad27",
        REPO_ROOT / "workflows/sdxl-mapchip-ipadapter-v1/img2img.api.json":
            "ad53c2bc8545b626c56d66de2d4cf481a510939619569faaf1158630d928792e",
    }

    for path, digest in expected.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_evidence_contains_digests_metrics_order_hashes_and_postprocess_status() -> None:
    evidence = SmokeEvidenceV2.model_validate(_valid_evidence_dict())
    cell = evidence.cells[0]

    assert evidence.runtime_manifest_sha256 == "a" * 64
    assert evidence.profile_manifest_sha256 == "b" * 64
    assert set(evidence.workflow_digests) == {
        "text2img-no-style",
        "text2img-style",
        "img2img-no-style",
        "img2img-style",
    }
    assert cell.output_count == 4
    assert len(cell.output_hashes) == 4
    assert cell.postprocess_status == "completed"
    assert cell.peak_vram_mib == 12000


def test_tracked_evidence_aggregates_cells_into_generation_resource_hints() -> None:
    registry = ManifestRegistry.load(REPO_ROOT)
    profile = registry.profile("sdxl-mapchip-ipadapter", "2")
    evidence = SmokeEvidenceV2.model_validate_json(
        (REPO_ROOT / "docs/evidence/phase-5/evidence.json").read_bytes()
    )

    expected = [
        {
            "parallelism": 1,
            "peak_vram_mib": 8516,
            "peak_process_ram_mib": 3644,
            "peak_system_ram_mib": 16271,
            "elapsed_seconds": 26.46799999999712,
        },
        {
            "parallelism": 2,
            "peak_vram_mib": 8535,
            "peak_process_ram_mib": 3644,
            "peak_system_ram_mib": 16355,
            "elapsed_seconds": 22.985000000000582,
        },
        {
            "parallelism": 4,
            "peak_vram_mib": 8491,
            "peak_process_ram_mib": 3661,
            "peak_system_ram_mib": 16356,
            "elapsed_seconds": 24.281000000002678,
        },
    ]

    assert [
        hint.model_dump(mode="json") for hint in evidence.resource_hints
    ] == expected
    assert _verified_resource_hints(profile, evidence) == tuple(expected)


def test_evidence_rejects_absolute_paths_and_image_bytes() -> None:
    payload = _valid_evidence_dict()
    payload["machine"]["gpu_name"] = "C:/private/model"
    with pytest.raises(ValidationError):
        SmokeEvidenceV2.model_validate(payload)

    payload = _valid_evidence_dict()
    payload["cells"][0]["output_image_bytes"] = b"not an allowed evidence field"
    with pytest.raises(ValidationError):
        SmokeEvidenceV2.model_validate(payload)


def test_evidence_rejects_prompt_reference_and_secret_fields() -> None:
    for field in ("prompt", "reference_name", "token", "headers"):
        payload = _valid_evidence_dict()
        payload[field] = "must not be recorded"
        with pytest.raises(ValidationError):
            SmokeEvidenceV2.model_validate(payload)


def test_process_memory_probe_is_safe_for_a_live_process() -> None:
    value = _process_memory_mib(os.getpid())

    assert value is None or isinstance(value, int)


def test_evidence_writer_validates_json_datetime_round_trip(tmp_path) -> None:
    evidence = SmokeEvidenceV2.model_validate(_valid_evidence_dict())
    evidence_path = tmp_path / "evidence.json"

    write_evidence(evidence_path, evidence)

    loaded = SmokeEvidenceV2.model_validate_json(evidence_path.read_bytes())
    assert loaded.started_at == evidence.started_at


def test_evidence_writer_validates_privacy_before_replacing_existing_file(
    tmp_path: Path,
) -> None:
    evidence = SmokeEvidenceV2.model_validate(_valid_evidence_dict())
    evidence_path = tmp_path / "evidence.json"
    previous = b'{"sentinel":"keep"}'
    evidence_path.write_bytes(previous)

    cells = list(evidence.cells)
    cells[0] = cells[0].model_copy(
        update={
            "status": "failed",
            "output_count": 0,
            "output_hashes": (),
            "postprocess_status": "not_run",
            "failure": "reference.png",
        }
    )
    invalid_evidence = (
        evidence.model_copy(update={"runtime_stats": {"api_key": "secret"}}),
        evidence.model_copy(update={"runtime_stats": {"prompt": "private"}}),
        evidence.model_copy(
            update={
                "machine": evidence.machine.model_copy(
                    update={"gpu_name": "C:/private/model"}
                )
            }
        ),
        evidence.model_copy(update={"cells": tuple(cells)}),
    )

    for invalid in invalid_evidence:
        evidence_path.write_bytes(previous)
        with pytest.raises(ValidationError):
            write_evidence(evidence_path, invalid)
        assert evidence_path.read_bytes() == previous
        assert not (tmp_path / ".evidence.json.tmp").exists()


def test_workflow_failure_detail_is_redacted_without_private_inputs() -> None:
    safe = _redact_failure(
        WorkflowBindingError(
            "server is missing required nodes: ['EmptyLatentImage']"
        )
    )
    private = _redact_failure(
        WorkflowBindingError("reference image path C:/private/input.png")
    )

    assert safe == (
        "WorkflowBindingError: server is missing required nodes: "
        "['EmptyLatentImage']"
    )
    assert private == "WorkflowBindingError"
