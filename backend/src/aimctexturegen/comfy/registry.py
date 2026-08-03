"""Read-only registry over tracked runtime and model-profile manifests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Self

from pydantic import ValidationError

from aimctexturegen.comfy.errors import (
    ManifestError,
    ManifestNotFoundError,
    ManifestValidationError,
)
from aimctexturegen.comfy.manifests import (
    ModelProfileManifest,
    ModelProfileManifestRecord,
    ModelProfileManifestV2,
    ProfileKey,
    RuntimeManifest,
)
from aimctexturegen.core.relative_paths import validate_project_relative_path


def _read_manifest_data(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"cannot read manifest {path.name}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestValidationError(
            f"invalid JSON in manifest {path.name}"
        ) from exc
    if not isinstance(data, dict):
        raise ManifestValidationError(
            f"manifest {path.name} root must be a JSON object"
        )
    return data


def _load_runtime(path: Path) -> RuntimeManifest:
    data = _read_manifest_data(path)
    try:
        return RuntimeManifest.model_validate(data)
    except ValidationError as exc:
        raise ManifestValidationError(
            f"invalid runtime manifest {path.name}: {exc}"
        ) from exc


def _load_profile(path: Path) -> ModelProfileManifestRecord:
    data = _read_manifest_data(path)
    try:
        schema_version = data.get("schema_version")
        if schema_version == 1:
            return ModelProfileManifest.model_validate(data)
        if schema_version == 2:
            return ModelProfileManifestV2.model_validate(data)
        raise ManifestValidationError(
            f"invalid model-profile manifest {path.name}: "
            f"unsupported schema_version {schema_version!r}"
        )
    except ValidationError as exc:
        raise ManifestValidationError(
            f"invalid model-profile manifest {path.name}: {exc}"
        ) from exc


class ManifestRegistry:
    """Read-only, deterministically ordered manifest registry."""

    def __init__(
        self,
        *,
        root: Path,
        runtimes: dict[str, RuntimeManifest],
        profiles: dict[ProfileKey, ModelProfileManifestRecord]
        | dict[str, ModelProfileManifestRecord],
        profile_evidence: dict[ProfileKey, object] | None = None,
    ) -> None:
        self._root = root
        self._runtimes = dict(runtimes)
        self._profile_evidence = dict(profile_evidence or {})
        self._profiles: dict[ProfileKey, ModelProfileManifestRecord] = {}
        for key, manifest in profiles.items():
            profile_key = (
                key
                if isinstance(key, tuple)
                else (key, manifest.profile_version)
            )
            self._profiles[profile_key] = manifest

    @property
    def runtimes(self) -> dict[str, RuntimeManifest]:
        return dict(self._runtimes)

    @property
    def profiles(self) -> dict[ProfileKey, ModelProfileManifestRecord]:
        return dict(self._profiles)

    @property
    def profile_evidence(self) -> dict[ProfileKey, object]:
        return dict(self._profile_evidence)

    @classmethod
    def load(cls, root: Path | str) -> Self:
        root = Path(root).resolve()
        runtimes_dir = root / "manifests" / "runtimes"
        profiles_dir = root / "manifests" / "model-profiles"
        workflow_root = root / "workflows"
        if not runtimes_dir.is_dir() or not profiles_dir.is_dir():
            raise ManifestNotFoundError(
                f"missing manifest directories under {root}"
            )

        runtimes: dict[str, RuntimeManifest] = {}
        for path in sorted(runtimes_dir.glob("*.json")):
            runtime = _load_runtime(path)
            if runtime.runtime_id in runtimes:
                raise ManifestError(
                    f"duplicate runtime_id {runtime.runtime_id!r} "
                    "across manifest files"
                )
            runtimes[runtime.runtime_id] = runtime

        profiles: dict[ProfileKey, ModelProfileManifestRecord] = {}
        for path in sorted(profiles_dir.glob("*.json")):
            profile = _load_profile(path)
            for workflow in profile.workflows:
                validate_project_relative_path(workflow.relative_path)
                resolved = (workflow_root / workflow.relative_path).resolve()
                if not resolved.is_relative_to(workflow_root.resolve()):
                    raise ManifestValidationError(
                        f"workflow path escapes workflow root: "
                        f"{workflow.relative_path!r}"
                    )
            for runtime_id in profile.compatible_runtime_ids:
                if runtime_id not in runtimes:
                    raise ManifestValidationError(
                        f"profile {profile.profile_id!r} references unknown "
                        f"compatible runtime {runtime_id!r}"
                    )
            profile_key = (profile.profile_id, profile.profile_version)
            if profile_key in profiles:
                raise ManifestError(
                    f"duplicate profile_id/profile_version {profile_key!r} "
                    "across manifest files"
                )
            profiles[profile_key] = profile

        return cls(
            root=root,
            runtimes=runtimes,
            profiles=profiles,
            profile_evidence=_load_profile_evidence(root),
        )

    def runtime(self, runtime_id: str) -> RuntimeManifest:
        try:
            return self._runtimes[runtime_id]
        except KeyError as exc:
            raise ManifestNotFoundError(
                f"unknown runtime {runtime_id!r}"
            ) from exc

    def profile(
        self, profile_id: str, profile_version: str
    ) -> ModelProfileManifestRecord:
        try:
            return self._profiles[(profile_id, profile_version)]
        except KeyError as exc:
            raise ManifestNotFoundError(
                f"unknown model profile {(profile_id, profile_version)!r}"
            ) from exc

    def profiles_for_runtime(
        self, runtime_id: str
    ) -> tuple[ModelProfileManifestRecord, ...]:
        return tuple(
            profile
            for _, profile in sorted(self._profiles.items())
            if runtime_id in profile.compatible_runtime_ids
        )


def _load_profile_evidence(root: Path) -> dict[ProfileKey, object]:
    path = root / "docs" / "evidence" / "phase-5" / "evidence.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    profile_id = payload.get("profile_id")
    profile_version = payload.get("profile_version")
    if not isinstance(profile_id, str) or not isinstance(profile_version, str):
        return {}
    return {(profile_id, profile_version): payload}
