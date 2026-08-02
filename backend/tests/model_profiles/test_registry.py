"""RED/GREEN tests for the generic profile catalog."""

from __future__ import annotations

import sys
from pathlib import Path

from aimctexturegen.comfy.manifests import (
    ModelProfileManifest,
    RuntimeManifest,
)
from aimctexturegen.comfy.registry import ManifestRegistry
from aimctexturegen.model_profiles.registry import ProfileCatalog

from comfy._helpers import make_profile, make_runtime


def _catalog(tmp_path: Path) -> ProfileCatalog:
    runtime = RuntimeManifest.model_validate(make_runtime())
    profile = ModelProfileManifest.model_validate(make_profile())
    registry = ManifestRegistry(
        root=tmp_path,
        runtimes={runtime.runtime_id: runtime},
        profiles={profile.profile_id: profile},
    )
    return ProfileCatalog.from_registry(registry)


def test_catalog_lists_profiles_sorted_and_exposes_capabilities(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    assert [profile.profile_id for profile in catalog.all()] == [
        "sdxl-mapchip-ipadapter"
    ]
    capabilities = catalog.capabilities("sdxl-mapchip-ipadapter")
    assert capabilities.text_to_image is True
    assert capabilities.style_reference_max == 8
    assert catalog.get("sdxl-mapchip-ipadapter").profile_version == "1"


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
    assert catalog.capabilities("fake-second-profile").native_multi_reference is True


def test_catalog_import_does_not_import_profile_specific_compilers() -> None:
    import aimctexturegen.model_profiles.registry  # noqa: F401

    assert "aimctexturegen.model_profiles.sdxl" not in sys.modules
