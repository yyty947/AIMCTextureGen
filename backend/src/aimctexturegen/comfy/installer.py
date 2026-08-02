"""Consent-bound install planning and operation creation."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import ConfigDict, Field
from pydantic import BaseModel

from aimctexturegen.comfy.archives import (
    ExtractionPolicy,
    SevenZipReader,
    extract_and_audit_7z,
    inspect_7z,
    remove_staging,
)
from aimctexturegen.comfy.environment import EnvironmentInspector, EnvironmentReport
from aimctexturegen.comfy.errors import (
    ArchiveError,
    InstallBlockedError,
    InstallError,
    InstallValidationError,
    RuntimeInstallError,
    RuntimeInstallValidationError,
    RuntimePublicationError,
)
from aimctexturegen.comfy.install_state import (
    InstallOperation,
    InstallOperationStore,
)
from aimctexturegen.comfy.manifests import (
    ArtifactManifest,
    ModelProfileManifest,
    RuntimeManifest,
    manifest_sha256,
)
from aimctexturegen.comfy.registry import ManifestRegistry
from aimctexturegen.core.atomic_files import atomic_replace_bytes


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


ComponentState = str


class InstallComponent(_StrictModel):
    artifact_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    byte_size: int = Field(gt=0)
    sha256: str = Field(min_length=64, max_length=64)
    destination: str = Field(min_length=1)
    license_name: str = Field(min_length=1)
    license_source_url: str = Field(min_length=1)
    state: ComponentState


class InstallPlan(_StrictModel):
    runtime_id: str
    runtime_version: str
    runtime_manifest_sha256: str
    profile_id: str
    profile_version: str
    profile_manifest_sha256: str
    plan_digest: str
    components: tuple[InstallComponent, ...]
    total_download_bytes: int = Field(ge=0)
    temporary_headroom_bytes: int = Field(ge=0)
    required_free_bytes: int = Field(ge=0)
    disk_free_bytes: int | None = None
    environment: EnvironmentReport
    blockers: tuple[str, ...] = ()
    can_install: bool


class InstallConsent(_StrictModel):
    plan_digest: str = Field(min_length=64, max_length=64)
    runtime_id: str
    profile_id: str
    accepted_component_ids: tuple[str, ...]
    created_at: datetime


class Installer:
    """Read-only plan construction plus consent-bound operation creation."""

    def __init__(
        self,
        registry: ManifestRegistry,
        inspector: EnvironmentInspector,
        *,
        store_factory: Callable[[Path], InstallOperationStore] | None = None,
    ) -> None:
        self._registry = registry
        self._inspector = inspector
        self._store_factory = store_factory or (
            lambda root: InstallOperationStore(root / "state")
        )

    def inspect(
        self,
        runtime_id: str,
        profile_id: str,
        runtime_root: Path,
    ) -> InstallPlan:
        runtime = self._registry.runtime(runtime_id)
        profile = self._registry.profile(profile_id)
        environment = self._inspector.inspect(Path(runtime_root))

        artifacts = (runtime.archive, *profile.artifacts)
        components = tuple(
            _component_from_artifact(
                artifact,
                state=_classify_artifact(artifact, Path(runtime_root)),
            )
            for artifact in artifacts
        )
        total_download_bytes = sum(
            component.byte_size for component in components
        )
        temporary_headroom_bytes = runtime.extraction_headroom_bytes
        required_free_bytes = (
            total_download_bytes + temporary_headroom_bytes
        )

        blockers: list[str] = list(environment.blocking_issues)
        if runtime_id not in profile.compatible_runtime_ids:
            blockers.append("incompatible_profile")
        if (
            environment.disk_free_bytes is not None
            and environment.disk_free_bytes < required_free_bytes
        ):
            blockers.append("insufficient_disk_space")

        digest_payload = {
            "runtime_id": runtime.runtime_id,
            "runtime_version": runtime.runtime_version,
            "runtime_manifest_sha256": manifest_sha256(runtime),
            "profile_id": profile.profile_id,
            "profile_version": profile.profile_version,
            "profile_manifest_sha256": manifest_sha256(profile),
            "components": [
                {
                    "artifact_id": component.artifact_id,
                    "revision": component.revision,
                    "byte_size": component.byte_size,
                    "sha256": component.sha256,
                    "license_name": component.license_name,
                }
                for component in components
            ],
            "total_download_bytes": total_download_bytes,
            "temporary_headroom_bytes": temporary_headroom_bytes,
        }
        plan_digest = hashlib.sha256(
            json.dumps(
                digest_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()

        return InstallPlan(
            runtime_id=runtime.runtime_id,
            runtime_version=runtime.runtime_version,
            runtime_manifest_sha256=manifest_sha256(runtime),
            profile_id=profile.profile_id,
            profile_version=profile.profile_version,
            profile_manifest_sha256=manifest_sha256(profile),
            plan_digest=plan_digest,
            components=components,
            total_download_bytes=total_download_bytes,
            temporary_headroom_bytes=temporary_headroom_bytes,
            required_free_bytes=required_free_bytes,
            disk_free_bytes=environment.disk_free_bytes,
            environment=environment,
            blockers=tuple(blockers),
            can_install=not blockers,
        )

    def consent(
        self,
        plan: InstallPlan,
        accepted_component_ids: Sequence[str],
    ) -> InstallConsent:
        expected = {component.artifact_id for component in plan.components}
        accepted = set(accepted_component_ids)
        if accepted != expected:
            raise InstallValidationError(
                "accepted component ids must match the plan exactly"
            )
        if plan.blockers:
            raise InstallBlockedError(
                f"cannot consent to blocked plan: {', '.join(plan.blockers)}"
            )
        return InstallConsent(
            plan_digest=plan.plan_digest,
            runtime_id=plan.runtime_id,
            profile_id=plan.profile_id,
            accepted_component_ids=tuple(sorted(accepted)),
            created_at=datetime.now(UTC),
        )

    def begin_install(
        self,
        consent: InstallConsent,
        runtime_root: Path,
    ) -> InstallOperation:
        runtime_root = Path(runtime_root)
        if not runtime_root.is_absolute():
            raise InstallValidationError("runtime root must be absolute")
        if runtime_root.exists() and not runtime_root.is_dir():
            raise InstallValidationError("runtime root must be a directory")

        fresh = self.inspect(
            consent.runtime_id,
            consent.profile_id,
            runtime_root,
        )
        if fresh.plan_digest != consent.plan_digest:
            raise InstallValidationError("install consent is stale")
        if fresh.blockers:
            raise InstallBlockedError(
                f"install is now blocked: {', '.join(fresh.blockers)}"
            )
        expected = {component.artifact_id for component in fresh.components}
        if set(consent.accepted_component_ids) != expected:
            raise InstallValidationError(
                "accepted component ids no longer match the fresh plan"
            )

        store = self._store_factory(runtime_root)
        return store.create(
            runtime_id=fresh.runtime_id,
            profile_id=fresh.profile_id,
            plan_digest=fresh.plan_digest,
            accepted_component_ids=tuple(sorted(expected)),
        )


def _component_from_artifact(
    artifact: ArtifactManifest,
    *,
    state: str,
) -> InstallComponent:
    return InstallComponent(
        artifact_id=artifact.artifact_id,
        source_url=artifact.source_url,
        revision=artifact.revision,
        byte_size=artifact.byte_size,
        sha256=artifact.sha256,
        destination=artifact.destination,
        license_name=artifact.license.name,
        license_source_url=artifact.license.source_url,
        state=state,
    )


def _classify_artifact(
    artifact: ArtifactManifest,
    runtime_root: Path,
) -> str:
    candidate = Path(runtime_root) / artifact.destination
    if not candidate.exists():
        return "missing"
    try:
        size = candidate.stat().st_size
    except OSError:
        return "corrupt"
    if size == artifact.byte_size:
        return "ready"
    if 0 < size < artifact.byte_size:
        return "partial"
    return "corrupt"


def plan_component_ids(plan: InstallPlan) -> tuple[str, ...]:
    return tuple(
        sorted(component.artifact_id for component in plan.components)
    )


def artifact_state(artifact: ArtifactManifest, runtime_root: Path) -> str:
    return _classify_artifact(artifact, Path(runtime_root))


class RuntimeStatus(_StrictModel):
    state: Literal["missing", "partial", "ready", "corrupt"]
    selected_version: str | None = None
    error: str | None = None


def _default_extraction_policy(runtime: RuntimeManifest) -> ExtractionPolicy:
    return ExtractionPolicy(
        max_members=1_000_000,
        max_total_size=60_000_000_000,
        max_single_size=30_000_000_000,
    )


class RuntimeInstaller:
    """Publish verified runtime trees and a strict selection record."""

    def __init__(
        self,
        *,
        reader: SevenZipReader | None = None,
        policy_factory: Callable[[RuntimeManifest], ExtractionPolicy]
        | None = None,
    ) -> None:
        self._reader = reader
        self._policy_factory = policy_factory or _default_extraction_policy

    def status(self, runtime: RuntimeManifest, root: Path) -> RuntimeStatus:
        root = Path(root)
        selection = _read_json(root / "state" / "selected-runtime.json")
        if selection is None:
            comfyui = root / "comfyui"
            if comfyui.is_dir() and any(comfyui.glob(".staging-*")):
                return RuntimeStatus(state="partial")
            return RuntimeStatus(state="missing")
        directory = root / "comfyui" / str(selection.get("directory", ""))
        if not directory.is_dir():
            return RuntimeStatus(
                state="corrupt",
                selected_version=selection.get("version"),
                error="selected runtime directory is missing",
            )
        receipt = _read_json(
            root
            / "state"
            / "installation-receipts"
            / f"{runtime.runtime_id}-{runtime.runtime_version}.json"
        )
        required_ok = all(
            (directory / runtime.expected_archive_root / path).is_file()
            for path in runtime.required_paths
        )
        digest_ok = (
            selection.get("manifest_sha256") == manifest_sha256(runtime)
        )
        if receipt is None or not required_ok or not digest_ok:
            return RuntimeStatus(
                state="corrupt",
                selected_version=selection.get("version"),
                error="runtime integrity check failed",
            )
        return RuntimeStatus(
            state="ready",
            selected_version=str(selection.get("version", "")),
        )

    def install(
        self,
        runtime: RuntimeManifest,
        archive: Path,
        root: Path,
    ) -> RuntimeStatus:
        root = Path(root)
        current = self.status(runtime, root)
        if current.state == "ready":
            return current

        policy = self._policy_factory(runtime)
        reader = self._reader
        inventory = inspect_7z(archive, policy, reader=reader)
        if inventory.root != runtime.expected_archive_root:
            raise RuntimeInstallValidationError(
                f"archive root {inventory.root!r} does not match "
                f"{runtime.expected_archive_root!r}"
            )
        required = {f"{inventory.root}/{path}" for path in runtime.required_paths}
        member_names = {member.name for member in inventory.members}
        missing = required - member_names
        if missing:
            raise RuntimeInstallValidationError(
                f"archive is missing required paths: {sorted(missing)}"
            )

        comfyui_dir = root / "comfyui"
        comfyui_dir.mkdir(parents=True, exist_ok=True)
        staging = comfyui_dir / f".staging-{uuid4()}"
        try:
            extract_and_audit_7z(
                archive,
                staging,
                policy,
                reader=reader,
            )
        except ArchiveError:
            raise
        except Exception as exc:
            remove_staging(staging)
            raise RuntimeInstallValidationError(
                "runtime extraction failed"
            ) from exc

        final_dir = comfyui_dir / (
            f"{runtime.runtime_version}-{manifest_sha256(runtime)[:8]}"
        )
        if final_dir.exists():
            remove_staging(staging)
            raise RuntimePublicationError(
                "target runtime directory already exists"
            )
        try:
            os.replace(staging, final_dir)
        except OSError as exc:
            remove_staging(staging)
            raise RuntimePublicationError(
                "cannot publish the verified runtime tree"
            ) from exc

        now = datetime.now(UTC).isoformat()
        receipt = {
            "runtime_id": runtime.runtime_id,
            "runtime_version": runtime.runtime_version,
            "manifest_sha256": manifest_sha256(runtime),
            "archive_sha256": _sha256_file(archive),
            "archive_byte_size": _file_size(archive),
            "source_url": runtime.archive.source_url,
            "expected_archive_root": runtime.expected_archive_root,
            "directory": final_dir.name,
            "installed_at": now,
        }
        selection = {
            "runtime_id": runtime.runtime_id,
            "version": runtime.runtime_version,
            "manifest_sha256": manifest_sha256(runtime),
            "directory": final_dir.name,
            "archive_sha256": receipt["archive_sha256"],
            "installed_at": now,
        }
        try:
            _atomic_json(
                root
                / "state"
                / "installation-receipts"
                / f"{runtime.runtime_id}-{runtime.runtime_version}.json",
                receipt,
            )
            _atomic_json(root / "state" / "selected-runtime.json", selection)
        except OSError as exc:
            raise RuntimePublicationError(
                "cannot persist runtime records"
            ) from exc
        try:
            archive.unlink(missing_ok=True)
        except OSError:
            pass
        return RuntimeStatus(
            state="ready",
            selected_version=runtime.runtime_version,
        )

    def recover_interrupted(
        self,
        root: Path,
        store: InstallOperationStore,
    ) -> None:
        store.recover_interrupted()
        comfyui = Path(root) / "comfyui"
        if not comfyui.is_dir():
            return
        for staging in comfyui.glob(".staging-*"):
            remove_staging(staging)


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    atomic_replace_bytes(
        path,
        encoded,
        validator=lambda readback: json.loads(readback),
    )


def _sha256_file(path: Path) -> str:
    import hashlib as _hashlib

    hasher = _hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _file_size(path: Path) -> int:
    return path.stat().st_size
