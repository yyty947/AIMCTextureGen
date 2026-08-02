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


def _load_profile(path: Path) -> ModelProfileManifest:
    data = _read_manifest_data(path)
    try:
        return ModelProfileManifest.model_validate(data)
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
        profiles: dict[str, ModelProfileManifest],
    ) -> None:
        self._root = root
        self._runtimes = dict(runtimes)
        self._profiles = dict(profiles)

    @property
    def runtimes(self) -> dict[str, RuntimeManifest]:
        return dict(self._runtimes)

    @property
    def profiles(self) -> dict[str, ModelProfileManifest]:
        return dict(self._profiles)

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

        profiles: dict[str, ModelProfileManifest] = {}
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
            if profile.profile_id in profiles:
                raise ManifestError(
                    f"duplicate profile_id {profile.profile_id!r} "
                    "across manifest files"
                )
            profiles[profile.profile_id] = profile

        return cls(root=root, runtimes=runtimes, profiles=profiles)

    def runtime(self, runtime_id: str) -> RuntimeManifest:
        try:
            return self._runtimes[runtime_id]
        except KeyError as exc:
            raise ManifestNotFoundError(
                f"unknown runtime {runtime_id!r}"
            ) from exc

    def profile(self, profile_id: str) -> ModelProfileManifest:
        try:
            return self._profiles[profile_id]
        except KeyError as exc:
            raise ManifestNotFoundError(
                f"unknown model profile {profile_id!r}"
            ) from exc

    def profiles_for_runtime(
        self, runtime_id: str
    ) -> tuple[ModelProfileManifest, ...]:
        return tuple(
            profile
            for _, profile in sorted(self._profiles.items())
            if runtime_id in profile.compatible_runtime_ids
        )
