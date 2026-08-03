from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from aimctexturegen.catalog.models import CatalogEntry, CatalogProfile
from aimctexturegen.catalog.registry import CatalogRegistry, UnsupportedPackFormat
from aimctexturegen.comfy.errors import ManifestNotFoundError
from aimctexturegen.comfy.manifests import (
    ModelProfileManifestV2,
    WorkflowVariantRecord,
    manifest_sha256,
)
from aimctexturegen.comfy.registry import ManifestRegistry
from aimctexturegen.jobs.models import MAX_SAFE_SEED
from aimctexturegen.jobs.models_v3 import (
    CompiledPrompt as StoredCompiledPrompt,
    ExecutionBatch,
    FrozenReferences,
    GenerationAdvanced,
    GenerationJobRequest,
    GenerationModelBinding,
    GenerationTarget,
    StoredArtifact,
)
from aimctexturegen.jobs.store import JobStore, LoadedJob
from aimctexturegen.packs.coverage import CoverageValidationError, classify_coverage
from aimctexturegen.projects.repository import ProjectRepository
from aimctexturegen.references.models import ReferenceSelections
from aimctexturegen.references.service import ReferenceService, ReferenceServiceError

from .errors import GenerationError, generation_error
from .prompts import compile_block_prompt


PROFILE_KEY = ("sdxl-mapchip-ipadapter", "2")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CreateGenerationCommand(_StrictModel):
    target_semantic_id: str = Field(min_length=1)
    user_description: str = Field(max_length=4000)
    user_negative_prompt: str = Field(default="", max_length=4000)
    resolution: Literal[16, 32, 64]
    parallelism: Literal[1, 2, 4]
    references: ReferenceSelections
    denoise: float | None = Field(default=None, ge=0.0, le=1.0)
    style_weight: float | None = Field(default=None, ge=0.0, le=2.0)


class GenerationService:
    def __init__(
        self,
        *,
        repository: ProjectRepository,
        catalogs: CatalogRegistry,
        references: ReferenceService,
        store: JobStore,
        manifests: ManifestRegistry,
        seed_source: Callable[[], int] | None = None,
        job_id_source: Callable[[], UUID] = uuid4,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._catalogs = catalogs
        self._references = references
        self._store = store
        self._manifests = manifests
        self._seed_source = (
            (lambda: secrets.randbelow(MAX_SAFE_SEED + 1))
            if seed_source is None
            else seed_source
        )
        self._job_id_source = job_id_source
        self._clock = (
            (lambda: datetime.now(timezone.utc)) if clock is None else clock
        )

    def create_job(self, project_id: UUID, command: CreateGenerationCommand) -> LoadedJob:
        if not isinstance(project_id, UUID):
            raise TypeError("project_id must be a UUID")
        if not isinstance(command, CreateGenerationCommand):
            raise TypeError("command must be a CreateGenerationCommand")
        if command.denoise is not None and command.references.structure is None:
            raise generation_error("INVALID_GENERATION_COMMAND")
        if command.style_weight is not None and not command.references.style:
            raise generation_error("REFERENCE_INVALID")

        with self._repository.open(project_id) as opened:
            profile = _catalog_profile(self._catalogs, opened.manifest.java_pack_format)
            if profile.catalog_id != opened.manifest.catalog_id:
                raise generation_error("JOB_TARGET_NOT_FOUND")
            target = _resolve_target(profile, command.target_semantic_id)
            if target is None:
                raise generation_error("JOB_TARGET_NOT_FOUND")
            if not target.mvp_eligible:
                raise generation_error("JOB_TARGET_NOT_ELIGIBLE")
            coverage = _classify_missing(opened.pack_root, profile, target.semantic_id)
            if coverage != "missing":
                raise generation_error("JOB_TARGET_NOT_MISSING")

            binding = build_generation_profile_binding(
                self._manifests,
                profile_id=PROFILE_KEY[0],
                profile_version=PROFILE_KEY[1],
                style_reference_count=len(command.references.style),
                structure_reference_present=command.references.structure is not None,
            )
            try:
                frozen_snapshot = self._references.freeze(project_id, command.references)
            except ReferenceServiceError as error:
                raise generation_error(
                    "REFERENCE_INVALID",
                    technical_details=f"{error.code}: {error.user_message}",
                ) from error

            compiled = compile_block_prompt(
                resolution=command.resolution,
                display_name=target.display_name,
                prompt_terms=target.prompt_terms,
                user_description=command.user_description,
                user_negative_prompt=command.user_negative_prompt,
            )
            batches = build_execution_batches(
                parallelism=command.parallelism,
                seed_source=self._seed_source,
            )
            request = GenerationJobRequest(
                schema_version=3,
                job_id=self._job_id_source(),
                project_id=opened.manifest.project_id,
                parent_job_id=None,
                target=GenerationTarget(
                    catalog_id=profile.catalog_id,
                    target_semantic_id=target.semantic_id,
                    target_display_name=target.display_name,
                    target_relative_path=target.relative_path,
                ),
                prompt=StoredCompiledPrompt(
                    prompt_version=compiled.prompt_version,
                    positive_prompt=compiled.compiled_positive,
                    negative_prompt=compiled.compiled_negative,
                    user_prompt=compiled.user_prompt,
                ),
                resolution=command.resolution,
                parallelism=command.parallelism,
                execution_batches=batches,
                references=_freeze_references(frozen_snapshot),
                advanced=GenerationAdvanced(
                    style_strength=command.style_weight,
                    denoise_strength=command.denoise,
                    lora_weight=None,
                ),
                model_profile=binding,
                created_at=self._clock(),
            )
        return self._store.create_generation(request, frozen_snapshot)


def build_execution_batches(
    parallelism: Literal[1, 2, 4],
    seed_source: Callable[[], int],
) -> tuple[ExecutionBatch, ...]:
    partitions = {
        1: ((0,), (1,), (2,), (3,)),
        2: ((0, 1), (2, 3)),
        4: ((0, 1, 2, 3),),
    }[parallelism]
    batches: list[ExecutionBatch] = []
    for batch_index, candidate_indices in enumerate(partitions):
        seed = seed_source()
        if type(seed) is not int or seed < 0 or seed > MAX_SAFE_SEED:
            raise generation_error("INVALID_SEED_SOURCE")
        batches.append(
            ExecutionBatch(
                batch_index=batch_index,
                candidate_indices=candidate_indices,
                seed=seed,
            )
        )
    return tuple(batches)


def build_generation_profile_binding(
    registry: ManifestRegistry,
    *,
    profile_id: str,
    profile_version: str,
    style_reference_count: int,
    structure_reference_present: bool,
    require_verified: bool = True,
) -> GenerationModelBinding:
    try:
        profile = registry.profile(profile_id, profile_version)
    except ManifestNotFoundError as error:
        raise generation_error("PROFILE_NOT_READY", technical_details=str(error)) from error
    if not isinstance(profile, ModelProfileManifestV2):
        raise generation_error("PROFILE_WORKFLOW_MISMATCH")
    if require_verified and profile.support_state != "verified":
        raise generation_error("PROFILE_NOT_READY")
    capabilities = profile.capabilities
    if structure_reference_present and not capabilities.structure_reference:
        raise generation_error("PROFILE_WORKFLOW_MISMATCH")
    if not structure_reference_present and not capabilities.text_to_image:
        raise generation_error("PROFILE_WORKFLOW_MISMATCH")
    if not (
        capabilities.style_reference_min
        <= style_reference_count
        <= capabilities.style_reference_max
    ):
        raise generation_error("PROFILE_WORKFLOW_MISMATCH")

    variant = (
        ("img2img" if structure_reference_present else "text2img")
        + "-"
        + ("style" if style_reference_count else "no-style")
    )
    workflow = _workflow_for_variant(profile.workflows, variant)
    if workflow is None or workflow.sha256 is None:
        raise generation_error("PROFILE_WORKFLOW_MISMATCH")
    runtime = registry.runtime(profile.compatible_runtime_ids[0])
    return GenerationModelBinding(
        profile_id=profile.profile_id,
        profile_version=profile.profile_version,
        profile_manifest_sha256=manifest_sha256(profile),
        runtime_id=runtime.runtime_id,
        runtime_version=runtime.runtime_version,
        runtime_manifest_sha256=manifest_sha256(runtime),
        workflow_variant=variant,
        workflow_sha256=workflow.sha256,
        output_node_id=workflow.output_node_id,
    )


def _freeze_references(snapshot) -> FrozenReferences:
    try:
        metadata = json.loads(snapshot.references_json)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise generation_error("REFERENCE_INVALID", technical_details=str(error)) from error
    payloads = {
        item.relative_path: item.payload
        for item in snapshot.files
    }
    style = tuple(
        _stored_reference(item, payloads)
        for item in metadata.get("style", ())
    )
    structure = tuple(
        _stored_reference(item, payloads)
        for item in metadata.get("structure", ())
    )
    return FrozenReferences(style=style, structure=structure)


def _stored_reference(item: dict, payloads: dict[str, bytes]) -> StoredArtifact:
    relative_path = item["relative_path"]
    source_relative = relative_path.removeprefix("inputs/")
    payload = payloads[source_relative]
    if hashlib.sha256(payload).hexdigest() != item["sha256"]:
        raise generation_error("REFERENCE_INVALID")
    return StoredArtifact(
        kind="raw",
        relative_path=relative_path,
        sha256=item["sha256"],
        byte_size=item["byte_size"],
        media_type="image/png",
        width=item["width"],
        height=item["height"],
    )


def _workflow_for_variant(
    workflows: tuple[WorkflowVariantRecord, ...],
    variant: str,
) -> WorkflowVariantRecord | None:
    return next((workflow for workflow in workflows if workflow.variant == variant), None)


def _resolve_target(profile: CatalogProfile, semantic_id: str) -> CatalogEntry | None:
    return next((entry for entry in profile.entries if entry.semantic_id == semantic_id), None)


def _classify_missing(pack_root, profile: CatalogProfile, semantic_id: str) -> str:
    try:
        coverage = classify_coverage(pack_root, profile)
    except CoverageValidationError as error:
        raise generation_error("REFERENCE_INVALID", technical_details=error.user_message) from error
    item = next((entry for entry in coverage.items if entry.semantic_id == semantic_id), None)
    if item is None:
        raise generation_error("JOB_TARGET_NOT_FOUND")
    return item.status


def _catalog_profile(catalogs: CatalogRegistry, pack_format: int) -> CatalogProfile:
    try:
        return catalogs.for_pack_format(pack_format)
    except UnsupportedPackFormat as error:
        raise generation_error("JOB_TARGET_NOT_FOUND", technical_details=str(error)) from error
