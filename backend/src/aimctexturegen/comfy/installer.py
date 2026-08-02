"""Consent-bound install planning and operation creation."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import zipfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import ConfigDict, Field
from pydantic import BaseModel

from aimctexturegen.comfy.archives import (
    BsdtarReader,
    ExtractionPolicy,
    SevenZipReader,
    extract_and_audit_7z,
    inspect_7z,
    remove_staging,
)
from aimctexturegen.comfy.environment import EnvironmentInspector, EnvironmentReport
from aimctexturegen.comfy.downloads import ArtifactDownloader, DownloadPolicy
from aimctexturegen.comfy.errors import (
    ArchiveError,
    InstallBlockedError,
    InstallError,
    InstallValidationError,
    ProfileInstallError,
    ProfileUnsafeArtifactError,
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
from aimctexturegen.core.relative_paths import validate_project_relative_path
from aimctexturegen.model_profiles.models import (
    ComponentStatus,
    ProfileStatus,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


ComponentState = str

_PROFILE_HASH_CACHE: dict[tuple[str, int, int], str] = {}
_PROFILE_HASH_CACHE_LOCK = threading.Lock()


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
        runtime_root = Path(runtime_root)
        installed_records = _read_installed_records(runtime_root)
        components = tuple(
            _component_from_artifact(
                artifact,
                state=(
                    "ready"
                    if artifact is runtime.archive
                    and RuntimeInstaller().status(runtime, runtime_root).state
                    == "ready"
                    else _classify_profile_artifact(
                        artifact,
                        runtime_root,
                        installed_records,
                    )
                ),
            )
            for artifact in artifacts
        )
        pending_components = tuple(
            component for component in components if component.state != "ready"
        )
        total_download_bytes = sum(
            component.byte_size for component in pending_components
        )
        temporary_headroom_bytes = (
            runtime.extraction_headroom_bytes
            if components[0].state != "ready"
            else 0
        )
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
            can_install=not blockers and bool(pending_components),
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
        if not plan.can_install:
            raise InstallBlockedError(
                "all managed runtime and profile components are already ready"
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


def _classify_profile_artifact(
    artifact: ArtifactManifest,
    root: Path,
    records: dict,
) -> str:
    """Classify a profile artifact using its receipt and content hash.

    A same-sized file is not sufficient evidence of installation.  The plan
    must agree with ``ProfileInstaller.status`` so corrupt or unrecorded bytes
    still count toward the download and disk budget.
    """

    target = Path(root) / artifact.destination
    if not target.is_file():
        return "missing"
    try:
        size = target.stat().st_size
        if size < artifact.byte_size:
            return "partial"
        if size != artifact.byte_size:
            return "corrupt"
        record = records.get(artifact.artifact_id)
        if record is None or record.get("sha256") != artifact.sha256:
            return "corrupt"
        record_size = record.get("file_size")
        record_mtime = record.get("mtime_ns")
        if (
            record.get("content_sha256") == artifact.sha256
            and record_size == size
            and record_mtime == target.stat().st_mtime_ns
        ):
            return "ready"
        if (
            record.get("content_sha256") is None
            and record.get("sha256") == artifact.sha256
            and record.get("byte_size") == size
            and record.get("installed_at")
        ):
            # Legacy receipts were written only after the downloader's exact
            # hash check. Treat them as verified until a future install writes
            # the stronger content/mtime fields; this avoids re-reading
            # multi-GB models on every status poll after an upgrade.
            return "ready"
        if _cached_sha256_file(target) != artifact.sha256:
            return "corrupt"
        if artifact.destination.startswith("custom_nodes/"):
            expected_root = _custom_node_root_name(artifact)
            marker = (
                _managed_runtime_root(root)
                / "ComfyUI"
                / "custom_nodes"
                / expected_root
                / "__init__.py"
            )
            if not marker.is_file():
                return "corrupt"
    except (OSError, ProfileInstallError):
        return "corrupt"
    return "ready"


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
        self._reader = reader if reader is not None else BsdtarReader()
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


def _cached_sha256_file(path: Path) -> str:
    """Hash a profile artifact in bounded memory and reuse unchanged files."""

    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    with _PROFILE_HASH_CACHE_LOCK:
        cached = _PROFILE_HASH_CACHE.get(key)
        if cached is not None:
            return cached
        digest = _sha256_file(path)
        _PROFILE_HASH_CACHE[key] = digest
        for old_key in tuple(_PROFILE_HASH_CACHE):
            if old_key[0] == key[0] and old_key != key:
                _PROFILE_HASH_CACHE.pop(old_key, None)
        return digest


def _file_size(path: Path) -> int:
    return path.stat().st_size


class ProfileInstaller:
    """Install hash-addressed profile artifacts into a managed runtime."""

    def __init__(
        self,
        *,
        downloader: ArtifactDownloader | None = None,
        policy: DownloadPolicy | None = None,
    ) -> None:
        self._downloader = downloader or ArtifactDownloader()
        self._policy = policy or DownloadPolicy()

    def status(
        self,
        profile: ModelProfileManifest,
        root: Path,
    ) -> ProfileStatus:
        root = Path(root)
        records = _read_installed_records(root)
        components = tuple(
            ComponentStatus(
                artifact_id=artifact.artifact_id,
                state=self._artifact_state(artifact, root, records),
                installed_bytes=self._installed_bytes(artifact, root),
            )
            for artifact in profile.artifacts
        )
        return ProfileStatus(
            profile_id=profile.profile_id,
            profile_version=profile.profile_version,
            support_state=profile.support_state,
            components=components,
            ready=all(
                component.state == "ready" for component in components
            ),
        )

    def install(
        self,
        profile: ModelProfileManifest,
        root: Path,
        *,
        cancel: Callable[[], bool] | None = None,
        progress: Callable[[object], None] | None = None,
    ) -> ProfileStatus:
        root = Path(root)
        records = _read_installed_records(root)
        for artifact in profile.artifacts:
            if self._artifact_state(artifact, root, records) == "ready":
                continue
            target = root / artifact.destination
            if (
                target.exists()
                and _cached_sha256_file(target) != artifact.sha256
            ):
                raise ProfileInstallError(
                    f"destination {artifact.destination!r} already holds "
                    "different bytes"
                )
            if not self._reuse_by_hash(artifact, root, records):
                self._downloader.download(
                    artifact,
                    target,
                    policy=self._policy,
                    cancel=cancel,
                    progress=progress,
                )
            if artifact.destination.startswith("custom_nodes/"):
                self._install_custom_node(artifact, target, root)
            records = _record_artifact(records, artifact, target, root)
        return self.status(profile, root)

    def write_extra_model_paths(
        self,
        profile: ModelProfileManifest,
        root: Path,
    ) -> Path:
        root = Path(root)
        models_root = root / "models"
        categories = {
            "checkpoints": models_root / "checkpoints",
            "loras": models_root / "loras",
            "ipadapter": models_root / "ipadapter",
            "clip_vision": models_root / "clip_vision",
        }
        for directory in (models_root, *categories.values()):
            directory.mkdir(parents=True, exist_ok=True)
        import yaml

        payload = yaml.safe_dump(
            {
                "models": {
                    "base_path": str(models_root),
                    **{
                        key: str(path)
                        for key, path in sorted(categories.items())
                    },
                }
            },
            sort_keys=True,
            default_flow_style=False,
            allow_unicode=False,
        ).encode("utf-8")
        path = root / "state" / "extra_model_paths.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_replace_bytes(
            path,
            payload,
            validator=lambda readback: readback.decode("utf-8"),
        )
        return path

    def _artifact_state(
        self,
        artifact: ArtifactManifest,
        root: Path,
        records: dict,
    ) -> str:
        return _classify_profile_artifact(artifact, root, records)

    def _installed_bytes(
        self,
        artifact: ArtifactManifest,
        root: Path,
    ) -> int | None:
        target = root / artifact.destination
        if not target.is_file():
            return None
        return target.stat().st_size

    def _reuse_by_hash(
        self,
        artifact: ArtifactManifest,
        root: Path,
        records: dict,
    ) -> bool:
        target = root / artifact.destination
        for record in records.values():
            if record.get("sha256") != artifact.sha256:
                continue
            source = root / str(record.get("destination", ""))
            if source.is_file() and source != target:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                return True
        return False

    def _install_custom_node(
        self,
        artifact: ArtifactManifest,
        zip_path: Path,
        root: Path,
    ) -> None:
        expected_root = _custom_node_root_name(artifact)
        managed_root = _managed_runtime_root(root)
        target_dir = (
            managed_root / "ComfyUI" / "custom_nodes" / expected_root
        )
        if (target_dir / "__init__.py").is_file():
            return
        staging = (
            managed_root
            / "ComfyUI"
            / "custom_nodes"
            / f".staging-{uuid4()}"
        )
        staging.mkdir(parents=True)
        try:
            with zipfile.ZipFile(zip_path) as archive:
                names = archive.namelist()
                _validate_custom_node_names(names, expected_root)
                for name in names:
                    if name.endswith("/"):
                        continue
                    destination = staging / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.read(name))
            marker = staging / expected_root / "__init__.py"
            if not marker.is_file():
                raise ProfileUnsafeArtifactError(
                    "custom-node archive is missing __init__.py"
                )
            if target_dir.exists():
                raise ProfileUnsafeArtifactError(
                    "custom-node target directory already exists"
                )
            os.replace(staging / expected_root, target_dir)
            shutil.rmtree(staging, ignore_errors=True)
        except ProfileUnsafeArtifactError:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        except Exception as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise ProfileUnsafeArtifactError(
                "custom-node extraction failed"
            ) from exc


def _custom_node_root_name(artifact: ArtifactManifest) -> str:
    if artifact.file_name.endswith(".zip"):
        return artifact.file_name[: -len(".zip")]
    return artifact.file_name


def _managed_runtime_root(root: Path) -> Path:
    selection = _read_json(root / "state" / "selected-runtime.json")
    if selection is None or not selection.get("directory"):
        raise ProfileInstallError("managed runtime is not installed")
    base = root / "comfyui" / str(selection["directory"])
    if not base.is_dir():
        raise ProfileInstallError("managed runtime directory is missing")
    for child in sorted(base.iterdir()):
        if (child / "ComfyUI").is_dir():
            return child
    raise ProfileInstallError("managed runtime layout is invalid")


def _validate_custom_node_names(names: list[str], expected_root: str) -> None:
    roots: set[str] = set()
    seen: set[str] = set()
    for raw_name in names:
        name = raw_name.replace("\\", "/").rstrip("/")
        if not name or not (
            name == expected_root or name.startswith(f"{expected_root}/")
        ):
            raise ProfileUnsafeArtifactError(
                f"custom-node member {raw_name!r} escapes the expected root"
            )
        try:
            validate_project_relative_path(name)
        except ValueError as exc:
            raise ProfileUnsafeArtifactError(
                f"unsafe custom-node member {raw_name!r}"
            ) from exc
        key = name.casefold()
        if key in seen:
            raise ProfileUnsafeArtifactError(
                f"case-colliding custom-node member {raw_name!r}"
            )
        seen.add(key)
        roots.add(name.split("/", maxsplit=1)[0])
    if roots != {expected_root}:
        raise ProfileUnsafeArtifactError(
            "custom-node archive must have a single expected root"
        )


def _read_installed_records(root: Path) -> dict:
    path = root / "state" / "installed-artifacts.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    artifacts = data.get("artifacts") if isinstance(data, dict) else None
    return artifacts if isinstance(artifacts, dict) else {}


def _record_artifact(
    records: dict,
    artifact: ArtifactManifest,
    target: Path,
    root: Path,
) -> dict:
    records = dict(records)
    stat = target.stat()
    records[artifact.artifact_id] = {
        "sha256": artifact.sha256,
        "content_sha256": _cached_sha256_file(target),
        "byte_size": artifact.byte_size,
        "file_size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "destination": artifact.destination,
        "installed_at": datetime.now(UTC).isoformat(),
    }
    path = root / "state" / "installed-artifacts.json"
    _atomic_json(path, {"artifacts": records})
    return records
