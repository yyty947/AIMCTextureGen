"""RED/GREEN tests for deterministic synthetic smoke inputs and evidence."""

from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, datetime

import pytest
from PIL import Image
from pydantic import ValidationError

from aimctexturegen.comfy.manifests import (
    ModelProfileManifest,
    ModelProfileManifestV2,
    RuntimeManifest,
)
from aimctexturegen.comfy.registry import ManifestRegistry
from aimctexturegen.model_profiles.smoke import (
    SmokeEvidence,
    SmokeMachine,
    SmokeWorkflowResult,
    generate_structure_reference,
    resolve_smoke_profile,
    generate_style_reference,
)

from comfy._helpers import make_profile, make_profile_v2, make_runtime


def test_style_and_structure_references_are_deterministic_rgb_pngs() -> None:
    first = generate_style_reference()
    second = generate_style_reference()
    structure = generate_structure_reference()
    assert first == second
    assert structure != first
    for payload in (first, structure):
        image = Image.open(io.BytesIO(payload))
        assert image.format == "PNG"
        assert image.size == (512, 512)
        assert image.mode == "RGB"


def test_reference_hashes_are_stable_for_evidence() -> None:
    style = generate_style_reference()
    structure = generate_structure_reference()
    assert hashlib.sha256(style).hexdigest() == hashlib.sha256(
        generate_style_reference()
    ).hexdigest()
    assert len(hashlib.sha256(structure).hexdigest()) == 64


def test_evidence_model_rejects_absolute_paths_and_unknown_fields() -> None:
    base = {
        "machine": {
            "os_name": "windows",
            "architecture": "x86_64",
            "gpu_name": "RTX 5080",
            "driver_version": "610.88",
            "vram_bytes": 17094311936,
        },
        "runtime_release": "0.29.2",
        "runtime_commit": "322122449c9d2ba8b8df1bb517364527dd0615f1",
        "runtime_manifest_sha256": "a" * 64,
        "profile_id": "sdxl-mapchip-ipadapter",
        "profile_version": "1",
        "profile_manifest_sha256": "b" * 64,
        "workflow_digests": {
            "text2img": "c" * 64,
            "img2img": "d" * 64,
        },
        "runtime_stats": {
            "python_version": "3.13.1",
            "pytorch_version": "2.7.1",
            "cuda": "13.0",
        },
        "started_at": datetime(2026, 8, 2, tzinfo=UTC),
        "completed_at": datetime(2026, 8, 2, 0, 10, tzinfo=UTC),
        "results": (
            {
                "kind": "text2img",
                "prompt_id": "11111111-2222-3333-4444-555555555555",
                "seed": 42,
                "output_sha256": "e" * 64,
                "output_canvas": 1024,
                "duration_seconds": 12.5,
                "status": "completed",
                "failure_message": None,
            },
        ),
    }
    evidence = SmokeEvidence.model_validate(base)
    serialized = json.dumps(evidence.model_dump(mode="json"))
    assert "C:" not in serialized
    assert "\\\\" not in serialized

    with pytest.raises(ValidationError):
        SmokeEvidence.model_validate({**base, "surprise": True})
    with pytest.raises(ValidationError):
        SmokeEvidence.model_validate(
            {**base, "runtime_release": "C:\\leak"}
        )


def test_evidence_accepts_a_documented_failure_record() -> None:
    failure = SmokeWorkflowResult(
        kind="img2img",
        prompt_id="11111111-2222-3333-4444-555555555555",
        seed=7,
        output_sha256="f" * 64,
        output_canvas=1024,
        duration_seconds=0.5,
        status="failed",
        failure_message="OOM",
    )
    assert failure.status == "failed"
    assert failure.failure_message == "OOM"


def test_smoke_machine_round_trips_safely() -> None:
    machine = SmokeMachine(
        os_name="windows",
        architecture="x86_64",
        gpu_name="RTX 5080 Laptop GPU",
        driver_version="610.88",
        vram_bytes=17094311936,
    )
    dumped = machine.model_dump(mode="json")
    assert dumped["gpu_name"] == "RTX 5080 Laptop GPU"
    assert SmokeMachine.model_validate(dumped) == machine


def test_resolve_smoke_profile_uses_explicit_verified_v1_lookup(tmp_path) -> None:
    runtime = RuntimeManifest.model_validate(make_runtime())
    verified_v1 = ModelProfileManifest.model_validate(
        make_profile(support_state="verified")
    )
    candidate_v2 = ModelProfileManifestV2.model_validate(make_profile_v2())
    registry = ManifestRegistry(
        root=tmp_path,
        runtimes={runtime.runtime_id: runtime},
        profiles={
            (verified_v1.profile_id, verified_v1.profile_version): verified_v1,
            (candidate_v2.profile_id, candidate_v2.profile_version): candidate_v2,
        },
    )

    resolved = resolve_smoke_profile(registry)

    assert resolved.profile_id == "sdxl-mapchip-ipadapter"
    assert resolved.profile_version == "1"
    assert resolved.support_state == "verified"
