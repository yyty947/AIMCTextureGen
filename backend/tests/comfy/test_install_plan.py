"""RED/GREEN tests for install plans, consent and operation records."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from aimctexturegen.comfy.environment import (
    EnvironmentInspector,
    EnvironmentProbe,
    NvidiaSmiResult,
)
from aimctexturegen.comfy.errors import (
    InstallBlockedError,
    InstallStateError,
    InstallValidationError,
)
from aimctexturegen.comfy.install_state import (
    InstallOperation,
    InstallOperationStore,
)
from aimctexturegen.comfy.installer import InstallConsent, Installer
from aimctexturegen.comfy.manifests import manifest_sha256
from aimctexturegen.comfy.manifests import (
    ModelProfileManifest,
    RuntimeManifest,
)
from aimctexturegen.comfy.registry import ManifestRegistry

from comfy._helpers import make_profile, make_runtime


class FakeProbe(EnvironmentProbe):
    def __init__(
        self,
        *,
        disk_free: int | None = 2_000_000_000_000,
    ) -> None:
        self._disk_free = disk_free

    def os_name(self) -> str:
        return "windows"

    def machine(self) -> str:
        return "AMD64"

    def nvidia_smi(self) -> NvidiaSmiResult | None:
        return NvidiaSmiResult(gpu_name="RTX 4060", driver_version="552.44", vram_mib=8192)

    def disk_free(self, path: Path) -> int | None:
        return self._disk_free


def _registry(tmp_path: Path) -> ManifestRegistry:
    runtime = RuntimeManifest.model_validate(make_runtime())
    profile = ModelProfileManifest.model_validate(make_profile())
    return ManifestRegistry(
        root=tmp_path,
        runtimes={runtime.runtime_id: runtime},
        profiles={(profile.profile_id, profile.profile_version): profile},
    )


def _installer(tmp_path: Path, *, disk_free: int | None = 2_000_000_000_000) -> Installer:
    return Installer(
        _registry(tmp_path),
        EnvironmentInspector(FakeProbe(disk_free=disk_free)),
    )


def _runtime_root(tmp_path: Path) -> Path:
    return tmp_path / "runtime"


def test_inspect_builds_plan_with_exact_component_totals(tmp_path: Path) -> None:
    plan = _installer(tmp_path).inspect(
        "comfyui-windows-nvidia",
        "sdxl-mapchip-ipadapter",
        "1",
        _runtime_root(tmp_path),
    )
    assert plan.runtime_id == "comfyui-windows-nvidia"
    assert plan.profile_id == "sdxl-mapchip-ipadapter"
    assert len(plan.components) == 3  # runtime archive + two profile artifacts
    assert plan.total_download_bytes == (
        2_103_175_457 + 1024 + 306_422
    )
    assert plan.temporary_headroom_bytes == 8_000_000_000
    assert plan.required_free_bytes == plan.total_download_bytes + 8_000_000_000
    assert plan.can_install is True
    assert plan.blockers == ()
    assert len(plan.plan_digest) == 64
    assert {component.artifact_id for component in plan.components} == {
        "comfyui-portable",
        "checkpoint",
        "custom-node",
    }


def test_ready_runtime_does_not_require_archive_redownload(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    runtime = registry.runtime("comfyui-windows-nvidia")
    selected = tmp_path / "runtime" / "state" / "selected-runtime.json"
    runtime_dir = (
        tmp_path
        / "runtime"
        / "comfyui"
        / "0.29.2-abc12345"
        / runtime.expected_archive_root
    )
    for required in runtime.required_paths:
        (runtime_dir / required).parent.mkdir(parents=True, exist_ok=True)
        (runtime_dir / required).write_bytes(b"ready")
    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.write_text(
        __import__("json").dumps(
            {
                "directory": "0.29.2-abc12345",
                "version": runtime.runtime_version,
                "manifest_sha256": manifest_sha256(runtime),
            }
        ),
        encoding="utf-8",
    )
    receipt = selected.parent / "installation-receipts" / (
        f"{runtime.runtime_id}-{runtime.runtime_version}.json"
    )
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text("{}", encoding="utf-8")

    plan = _installer(tmp_path).inspect(
        runtime.runtime_id,
        "sdxl-mapchip-ipadapter",
        "1",
        tmp_path / "runtime",
    )
    archive = next(
        component
        for component in plan.components
        if component.artifact_id == "comfyui-portable"
    )
    assert archive.state == "ready"
    assert plan.total_download_bytes == 1024 + 306_422
    assert plan.temporary_headroom_bytes == 0
    assert plan.required_free_bytes == plan.total_download_bytes


def test_same_sized_profile_bytes_without_verified_receipt_are_corrupt(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    profile_artifact = registry.profile("sdxl-mapchip-ipadapter", "1").artifacts[0]
    target = tmp_path / "runtime" / profile_artifact.destination
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x" * profile_artifact.byte_size)

    plan = _installer(tmp_path).inspect(
        "comfyui-windows-nvidia",
        "sdxl-mapchip-ipadapter",
        "1",
        tmp_path / "runtime",
    )

    component = next(
        item
        for item in plan.components
        if item.artifact_id == profile_artifact.artifact_id
    )
    assert component.state == "corrupt"
    assert plan.total_download_bytes == (
        2_103_175_457 + 1024 + 306_422
    )


def test_plan_digest_is_deterministic(tmp_path: Path) -> None:
    installer = _installer(tmp_path)
    first = installer.inspect(
        "comfyui-windows-nvidia",
        "sdxl-mapchip-ipadapter",
        "1",
        _runtime_root(tmp_path),
    )
    second = installer.inspect(
        "comfyui-windows-nvidia",
        "sdxl-mapchip-ipadapter",
        "1",
        _runtime_root(tmp_path),
    )
    assert first.plan_digest == second.plan_digest


def test_inspect_reports_unsupported_host_as_blocker(tmp_path: Path) -> None:
    class UnsupportedProbe(FakeProbe):
        def os_name(self) -> str:
            return "posix"

    installer = Installer(
        _registry(tmp_path),
        EnvironmentInspector(UnsupportedProbe()),
    )
    plan = installer.inspect(
        "comfyui-windows-nvidia",
        "sdxl-mapchip-ipadapter",
        "1",
        _runtime_root(tmp_path),
    )
    assert plan.can_install is False
    assert "unsupported_os" in plan.blockers


def test_inspect_reports_insufficient_disk_as_blocker(tmp_path: Path) -> None:
    plan = _installer(tmp_path, disk_free=100).inspect(
        "comfyui-windows-nvidia",
        "sdxl-mapchip-ipadapter",
        "1",
        _runtime_root(tmp_path),
    )
    assert plan.can_install is False
    assert "insufficient_disk_space" in plan.blockers


def test_inspect_never_creates_the_runtime_root(tmp_path: Path) -> None:
    root = _runtime_root(tmp_path)
    _installer(tmp_path).inspect(
        "comfyui-windows-nvidia",
        "sdxl-mapchip-ipadapter",
        "1",
        root,
    )
    assert not root.exists()


def test_consent_binds_to_plan_digest_and_exact_component_ids(tmp_path: Path) -> None:
    installer = _installer(tmp_path)
    plan = installer.inspect(
        "comfyui-windows-nvidia",
        "sdxl-mapchip-ipadapter",
        "1",
        _runtime_root(tmp_path),
    )
    consent = installer.consent(
        plan,
        ["comfyui-portable", "checkpoint", "custom-node"],
    )
    assert isinstance(consent, InstallConsent)
    assert consent.plan_digest == plan.plan_digest
    assert set(consent.accepted_component_ids) == {
        "comfyui-portable",
        "checkpoint",
        "custom-node",
    }


def test_consent_rejects_missing_or_unknown_acceptance(tmp_path: Path) -> None:
    installer = _installer(tmp_path)
    plan = installer.inspect(
        "comfyui-windows-nvidia",
        "sdxl-mapchip-ipadapter",
        "1",
        _runtime_root(tmp_path),
    )
    with pytest.raises(InstallValidationError):
        installer.consent(plan, ["comfyui-portable", "checkpoint"])
    with pytest.raises(InstallValidationError):
        installer.consent(plan, ["comfyui-portable", "checkpoint", "custom-node", "extra"])


def test_consent_rejects_a_blocked_plan(tmp_path: Path) -> None:
    plan = _installer(tmp_path, disk_free=100).inspect(
        "comfyui-windows-nvidia",
        "sdxl-mapchip-ipadapter",
        "1",
        _runtime_root(tmp_path),
    )
    with pytest.raises(InstallBlockedError):
        _installer(tmp_path).consent(plan, ["comfyui-portable", "checkpoint", "custom-node"])


def test_begin_install_creates_planned_operation(tmp_path: Path) -> None:
    installer = _installer(tmp_path)
    root = _runtime_root(tmp_path)
    plan = installer.inspect(
        "comfyui-windows-nvidia",
        "sdxl-mapchip-ipadapter",
        "1",
        root,
    )
    consent = installer.consent(
        plan,
        ["comfyui-portable", "checkpoint", "custom-node"],
    )
    operation = installer.begin_install(consent, root)
    assert operation.state == "planned"
    assert operation.revision == 1
    assert operation.plan_digest == plan.plan_digest
    assert (root / "state" / "install-operations").is_dir()


def test_begin_install_rejects_a_stale_digest_without_creating_root(
    tmp_path: Path,
) -> None:
    installer = _installer(tmp_path)
    root = _runtime_root(tmp_path)
    plan = installer.inspect(
        "comfyui-windows-nvidia",
        "sdxl-mapchip-ipadapter",
        "1",
        root,
    )
    consent = installer.consent(
        plan,
        ["comfyui-portable", "checkpoint", "custom-node"],
    )
    stale_plan = plan.model_copy(update={"plan_digest": "f" * 64})
    stale_consent = consent.model_copy(update={"plan_digest": stale_plan.plan_digest})
    with pytest.raises(InstallValidationError):
        installer.begin_install(stale_consent, root)
    assert not root.exists()


def test_begin_install_rejects_a_now_blocked_plan_without_creating_root(
    tmp_path: Path,
) -> None:
    installer = _installer(tmp_path)
    root = _runtime_root(tmp_path)
    plan = installer.inspect(
        "comfyui-windows-nvidia",
        "sdxl-mapchip-ipadapter",
        "1",
        root,
    )
    consent = installer.consent(
        plan,
        ["comfyui-portable", "checkpoint", "custom-node"],
    )
    blocked = _installer(tmp_path, disk_free=1)
    with pytest.raises(InstallBlockedError):
        blocked.begin_install(consent, root)
    assert not root.exists()


def test_operation_store_persists_and_increments_revision(tmp_path: Path) -> None:
    store = InstallOperationStore(tmp_path / "state")
    operation = store.create(
        runtime_id="comfyui-windows-nvidia",
        profile_id="sdxl-mapchip-ipadapter",
        plan_digest="a" * 64,
        accepted_component_ids=("comfyui-portable",),
    )
    assert operation.state == "planned"
    assert operation.revision == 1
    transitioned = store.transition(operation, "downloading")
    assert transitioned.revision == 2
    assert store.get(operation.operation_id) is not None
    assert store.get(operation.operation_id).state == "downloading"
    assert len(store.list_operations()) == 1


def test_operation_store_rejects_transitions_from_terminal_states(
    tmp_path: Path,
) -> None:
    store = InstallOperationStore(tmp_path / "state")
    operation = store.create(
        runtime_id="r",
        profile_id="p",
        plan_digest="a" * 64,
        accepted_component_ids=(),
    )
    completed = store.transition(
        operation,
        "completed",
    )
    assert completed.state == "completed"
    with pytest.raises(InstallStateError):
        store.transition(completed, "downloading")


def test_operation_store_recovery_marks_interrupted_operations(
    tmp_path: Path,
) -> None:
    store = InstallOperationStore(tmp_path / "state")
    planned = store.create(
        runtime_id="r",
        profile_id="p",
        plan_digest="a" * 64,
        accepted_component_ids=(),
    )
    downloading = store.create(
        runtime_id="r",
        profile_id="p",
        plan_digest="a" * 64,
        accepted_component_ids=(),
    )
    store.transition(downloading, "downloading")
    completed = store.create(
        runtime_id="r",
        profile_id="p",
        plan_digest="a" * 64,
        accepted_component_ids=(),
    )
    store.transition(completed, "completed")

    recovered = store.recover_interrupted()
    assert len(recovered) == 2
    states = {operation.operation_id: operation for operation in store.list_operations()}
    assert states[planned.operation_id].state == "failed"
    assert states[planned.operation_id].error is not None
    assert states[planned.operation_id].error.code == "INSTALL_INTERRUPTED"
    assert states[downloading.operation_id].state == "failed"
    assert states[completed.operation_id].state == "completed"
    assert store.recover_interrupted() == ()


def test_operation_json_is_strict_and_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        InstallOperation.model_validate(
            {
                "operation_id": "00000000-0000-0000-0000-000000000000",
                "runtime_id": "r",
                "profile_id": "p",
                "plan_digest": "a" * 64,
                "accepted_component_ids": [],
                "state": "planned",
                "revision": 1,
                "created_at": "2026-08-02T00:00:00Z",
                "updated_at": "2026-08-02T00:00:00Z",
                "surprise": 1,
            }
        )
