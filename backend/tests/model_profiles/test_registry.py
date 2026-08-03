"""RED/GREEN tests for the generic profile catalog."""

from __future__ import annotations

import sys
import subprocess
from pathlib import Path

from aimctexturegen.comfy.manifests import (
    ModelProfileManifest,
    ModelProfileManifestV2,
    RuntimeManifest,
)
from aimctexturegen.comfy.registry import ManifestRegistry
from aimctexturegen.model_profiles.registry import ProfileCatalog

from comfy._helpers import make_profile, make_profile_v2, make_runtime


def _catalog(tmp_path: Path) -> ProfileCatalog:
    runtime = RuntimeManifest.model_validate(make_runtime())
    profile = ModelProfileManifest.model_validate(make_profile())
    registry = ManifestRegistry(
        root=tmp_path,
        runtimes={runtime.runtime_id: runtime},
        profiles={(profile.profile_id, profile.profile_version): profile},
    )
    return ProfileCatalog.from_registry(registry)


def test_catalog_lists_profiles_sorted_and_exposes_capabilities(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    assert [profile.profile_id for profile in catalog.all()] == [
        "sdxl-mapchip-ipadapter"
    ]
    capabilities = catalog.capabilities("sdxl-mapchip-ipadapter", "1")
    assert capabilities.text_to_image is True
    assert capabilities.style_reference_max == 8
    assert catalog.get("sdxl-mapchip-ipadapter", "1").profile_version == "1"


def test_registering_a_second_profile_needs_no_installer_changes(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    second = make_profile(
        profile_id="fake-second-profile",
        profile_version="9",
    )
    second["capabilities"]["structure_reference"] = False
    second["capabilities"]["native_multi_reference"] = True
    second["required_node_classes"] = ["FakeNodeA", "FakeNodeB"]
    catalog.register(ModelProfileManifest.model_validate(second))
    assert [profile.profile_id for profile in catalog.all()] == [
        "fake-second-profile",
        "sdxl-mapchip-ipadapter",
    ]
    assert (
        catalog.capabilities("fake-second-profile", "9").native_multi_reference
        is True
    )


def test_catalog_tracks_multiple_versions_of_one_profile_id(
    tmp_path: Path,
) -> None:
    runtime = RuntimeManifest.model_validate(make_runtime())
    v1 = ModelProfileManifest.model_validate(make_profile())
    v2 = ModelProfileManifestV2.model_validate(make_profile_v2())
    registry = ManifestRegistry(
        root=tmp_path,
        runtimes={runtime.runtime_id: runtime},
        profiles={
            (v1.profile_id, v1.profile_version): v1,
            (v2.profile_id, v2.profile_version): v2,
        },
    )
    catalog = ProfileCatalog.from_registry(registry)
    assert catalog.get("sdxl-mapchip-ipadapter", "1").profile_version == "1"
    assert catalog.get("sdxl-mapchip-ipadapter", "2").profile_version == "2"


def test_catalog_import_does_not_import_profile_specific_compilers() -> None:
    probe = (
        "import sys; "
        "import aimctexturegen.model_profiles.registry; "
        "assert 'aimctexturegen.model_profiles.sdxl' not in sys.modules"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
