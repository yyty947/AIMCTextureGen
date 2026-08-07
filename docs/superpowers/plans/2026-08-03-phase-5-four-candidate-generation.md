# Phase 5 Four-Candidate Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the complete Java opaque single-block Phase 5 flow from target/reference selection through four persisted, postprocessed candidates, including managed-ComfyUI execution, cancellation, retry, restart recovery, WebSocket snapshots, and the guided WebUI.

**Architecture:** FastAPI owns a durable `GenerationService` and one process-local `GenerationCoordinator`; project/job JSON and atomically published artifacts remain the source of truth. A version-aware profile registry selects immutable SDXL profile version 2 and one of four explicit workflow variants, while the existing `ComfyClient` remains a product-agnostic transport. React implements steps 2–5 of the already-approved guided flow and talks only to FastAPI over HTTP commands plus read-only WebSocket snapshots.

**Tech Stack:** Python 3.12, FastAPI 0.139.2, Pydantic 2.13.4, Pillow 12.3.0, httpx 0.28.1, websockets 16.1.1, pytest 9.1.1, React 19.2.7, TypeScript 7.0.2, Vitest 4.1.10, Vite 8.1.5, PowerShell, managed ComfyUI v0.29.2.

## Global Constraints

- Phase 5 supports Windows, NVIDIA CUDA, and Java Edition ordinary opaque, static, single-texture blocks only.
- Phase 5 creates and displays candidates; it does not adopt a candidate, modify `pack/`, export a ZIP, merge overlays, support format 32, or add Java/Bedrock conversion.
- `source/`, the imported ZIP, and the project `pack/` working copy must remain byte-for-byte unchanged throughout Phase 5 generation.
- The WebUI calls FastAPI only; it never receives an absolute local path and never calls ComfyUI directly.
- FastAPI is the product boundary; ComfyUI performs controlled GPU inference only.
- Four candidates are fixed. Parallelism is exactly `1`, `2`, or `4`, producing native batch plans `4×1`, `2×2`, or `1×4`.
- Each native batch has one automatically generated, persisted, read-only base seed. A retry preserves the parent batch plan and seeds; a new job generates new seeds.
- The application permits exactly one nonterminal generation job globally. `queued`, `generating`, and `postprocessing` all occupy the slot until cancellation or failure/completion is durably confirmed.
- The user may cancel current work. Completed candidates remain; an active Comfy prompt is interrupted and confirmed stopped before the job becomes `canceled` and releases the slot.
- A restart leaves `queued` jobs queued without starting the GPU. Interrupted active jobs become `failed` with `JOB_INTERRUPTED`; completed candidates and verified raw artifacts remain.
- Style references total `0–8`; a structure reference is optional and singular. Inputs come only from eligible project pack PNGs or browser uploads to the controlled project library.
- Reference PNG limits are: encoded size at most `16 MiB`, each dimension `16–4096`, total pixels at most `16,777,216`, square, static, fully decodable RGB/RGBA.
- Profile `sdxl-mapchip-ipadapter` version 1, its workflows, manifest digest, and Phase 4 evidence are byte-immutable. Phase 5 adds version 2 and does not overwrite version 1.
- Product generation requires an installed, hash-valid, `verified` profile. It may auto-start a ready managed ComfyUI process, but it must not auto-download, silently repair, or silently update anything.
- OOM and all other failures must not change the user's resolution, parallelism, prompt, references, workflow, seeds, working copy, or adoption state.
- Comfy native inference batches are atomic: a missing, extra, corrupt, unordered, or contract-invalid output rejects the whole raw batch. Deterministic CPU postprocessing publishes one candidate atomically at a time.
- Ordinary automated tests use only generated synthetic images, temporary projects, and fake ComfyUI. They do not require a GPU, download models, or contain third-party/Mojang assets.
- Real packs remain only under ignored `runtime/manual-test-packs/phase-5/`. Never commit their ZIPs, PNGs, product/author names, hashes, screenshots, previews, or generated outputs.
- Manual testing is risk-based. Do not repeat the old `400/600/900 px` matrix unless this phase actually changes the relevant responsive rules; final visual polish remains Phase 6.
- Every write under a project goes through a service/store boundary, rejects reparse points and unsafe paths, validates before publication, and uses same-directory atomic replacement.

---

## Authoritative inputs

Read these before implementing any task:

1. `ONBOARDING.md`
2. `AGENTS.md`
3. This plan
4. `docs/superpowers/specs/2026-08-03-phase-5-four-candidate-generation-design.md`
5. `docs/adr/0003-native-batch-seeds-and-generation-coordinator.md`
6. `docs/superpowers/specs/2026-07-18-aimc-texturegen-mvp-design.md`
7. The files and tests named by the task

If code or a repeatable test contradicts a document, preserve the verified behavior and update the affected document in the same task. Do not use this plan to rewrite user-owned `temp/` or any ignored real asset.

## Planned file map

### Durable jobs

- `backend/src/aimctexturegen/jobs/models.py` — preserve the existing schema-1/2 request and schema-1 state classes without changing their serialized bytes.
- `backend/src/aimctexturegen/jobs/models_v3.py` — strict schema-3 request, schema-2 generation state, native batch, candidate, failure, artifact, and lineage models.
- `backend/src/aimctexturegen/jobs/codec.py` — discriminated parsing/dumping and request/state pair validation for legacy and generation records.
- `backend/src/aimctexturegen/jobs/generation_state.py` — pure schema-3 state transitions, cancel intent, progress throttling inputs, failure, completion, and interrupted recovery.
- `backend/src/aimctexturegen/jobs/store.py` — atomic schema-aware job/input creation, conditional state replacement, artifact publication/resolution, and legacy layout compatibility.
- `backend/src/aimctexturegen/jobs/recovery.py` — recover schema-3 active work without auto-running queued jobs.
- `backend/src/aimctexturegen/index/service.py` — summarize either legacy or schema-3 records without making SQLite authoritative.

### References and prompts

- `backend/src/aimctexturegen/references/models.py` — pack/upload selection, stored metadata, validated image, frozen input, and listing response models.
- `backend/src/aimctexturegen/references/validation.py` — one bounded static-PNG validator shared by pack and uploaded references.
- `backend/src/aimctexturegen/references/store.py` — guarded atomic project reference library and content retrieval by server ID.
- `backend/src/aimctexturegen/references/service.py` — eligible pack listing, upload/list/delete, selection resolution, and immutable job input snapshots.
- `backend/src/aimctexturegen/generation/prompts.py` — pure versioned `java-block-prompt` compiler.

### Profiles and ComfyUI

- `backend/src/aimctexturegen/comfy/manifests.py` — profile-manifest schema 1 plus schema 2 with four workflow variants.
- `backend/src/aimctexturegen/comfy/registry.py` — version-aware `(profile_id, profile_version)` lookup.
- `backend/src/aimctexturegen/comfy/client.py` — ordered declared output descriptors, queue snapshots, prompt-scoped interrupt, and cancel-aware waiting.
- `backend/src/aimctexturegen/comfy/errors.py` — typed cancellation/queue/output transport errors only.
- `backend/src/aimctexturegen/inference/service.py` — exact version-2 readiness/start/client access for generation while preserving setup controls.
- `backend/src/aimctexturegen/model_profiles/workflows.py` — common batch-capable product inputs and exact versioned binding identity.
- `backend/src/aimctexturegen/model_profiles/sdxl.py` — keep version-1 compiler behavior explicit.
- `backend/src/aimctexturegen/model_profiles/sdxl_v2.py` — all version-2 SDXL node IDs and four variant compilers.
- `manifests/model-profiles/sdxl-mapchip-ipadapter-v2.json` — new candidate, later verified, profile manifest reusing version-1 artifact hashes.
- `workflows/sdxl-mapchip-ipadapter-v2/*.api.json` — four immutable API workflows.

### Generation orchestration

- `backend/src/aimctexturegen/generation/errors.py` — stable generation error codes and safe error translation.
- `backend/src/aimctexturegen/generation/artifacts.py` — atomic raw-batch and per-candidate processed artifact publisher/resolver.
- `backend/src/aimctexturegen/generation/service.py` — schema-3 creation, prompt/batch/workflow compilation, execution, output validation, postprocessing, and lineage retry.
- `backend/src/aimctexturegen/generation/events.py` — revision notification condition; durable state remains authoritative.
- `backend/src/aimctexturegen/generation/coordinator.py` — global nonterminal gate, background start/continue, cancel confirmation, active prompt ownership, shutdown, and orphan cleanup.
- `backend/src/aimctexturegen/main.py` — explicit service graph and lifespan ordering.

### API and WebUI

- `backend/src/aimctexturegen/api/references.py` — reference listing/upload/delete/content routes.
- `backend/src/aimctexturegen/api/generation.py` — generation options, schema-3 create/start/cancel/retry, artifact, and WebSocket routes.
- `frontend/src/generation/types.ts` — strict TypeScript generation contracts.
- `frontend/src/generation/api.ts` — generation/reference HTTP and WebSocket client with runtime validation.
- `frontend/src/generation/GenerationWizard.tsx` — guided state owner for steps 2–5.
- `frontend/src/generation/TargetStep.tsx` — eligible missing-block search and target choice.
- `frontend/src/generation/ReferenceStep.tsx` — `0–8` style references, optional structure reference, uploads, and description.
- `frontend/src/generation/GenerationStep.tsx` — simple defaults, conditional advanced controls, fixed-four/resource hints, and create/start sequence.
- `frontend/src/generation/CandidateStep.tsx` — incremental candidates, previews, seam score, seed, errors, continue/cancel/retry.
- `frontend/src/generation/useJobEvents.ts` — reconnecting revision-monotonic WebSocket hook with HTTP fallback.
- `frontend/src/App.tsx` — mount the wizard for the selected loaded project.
- `frontend/src/JobHistory.tsx` — display legacy/schema-3 history without pretending legacy jobs are executable.
- `frontend/src/styles.css` — only functional layout/accessibility additions; final visual polish remains Phase 6.

### Qualification and documentation

- `backend/src/aimctexturegen/model_profiles/smoke_v2.py` — synthetic four-variant/batch GPU qualification and redacted metrics.
- `tools/Invoke-Phase5Smoke.ps1` — Windows entry point using only ignored runtime outputs.
- `tools/Prepare-Phase5ManualPack.ps1` — ignored derived-pack preparation that removes exactly one root texture.
- `docs/evidence/phase-5/evidence.json` — redacted v2 qualification identity/metrics/status/hash evidence only.
- `docs/TESTING.md`, `docs/MODEL_PROFILES.md`, `ONBOARDING.md`, and the roadmap — commands and handoff only after they exist and have run.

## Cross-task interfaces

The following names are fixed for the implementation. A later task must use these exact names rather than inventing parallel abstractions:

```python
ProfileKey = tuple[str, str]
WorkflowVariant = Literal[
    "text2img-no-style",
    "text2img-style",
    "img2img-no-style",
    "img2img-style",
]
ArtifactKind = Literal["raw", "final", "nearest", "tile", "report"]

DurableJobRequest = JobRequest | GenerationJobRequest
DurableJobState = JobStateRecord | GenerationJobState

class ReferenceService:
    def list_pack_references(self, project_id: UUID) -> tuple[PackReference, ...]: ...
    def upload(self, project_id: UUID, kind: ReferenceKind, payload: bytes) -> StoredReference: ...
    def list_uploads(self, project_id: UUID, kind: ReferenceKind) -> tuple[StoredReference, ...]: ...
    def delete(self, project_id: UUID, kind: ReferenceKind, reference_id: UUID) -> None: ...
    def freeze(self, project_id: UUID, selections: ReferenceSelections) -> JobInputSnapshot: ...

class GenerationService:
    def create_job(self, project_id: UUID, command: CreateGenerationCommand) -> LoadedJob: ...
    def retry_job(self, project_id: UUID, parent_job_id: UUID) -> LoadedJob: ...
    def run_job(self, project_id: UUID, job_id: UUID, context: ExecutionContext) -> LoadedJob: ...

class GenerationCoordinator:
    def create_job(self, project_id: UUID, command: CreateGenerationCommand) -> LoadedJob: ...
    def start(self, project_id: UUID, job_id: UUID) -> LoadedJob: ...
    def cancel(self, project_id: UUID, job_id: UUID) -> LoadedJob: ...
    def retry(self, project_id: UUID, job_id: UUID) -> LoadedJob: ...
    def shutdown(self) -> None: ...
```

`GenerationCoordinator.create_job` owns the application-wide lock around “scan canonical jobs → reject an existing nonterminal job → atomically create the new queued job.” The SQLite index is never used to decide whether the slot is free.

---

### Task 1: Make model-profile manifests and lookup version-aware

**Files:**
- Modify: `backend/src/aimctexturegen/comfy/manifests.py`
- Modify: `backend/src/aimctexturegen/comfy/registry.py`
- Modify: `backend/src/aimctexturegen/comfy/installer.py`
- Modify: `backend/src/aimctexturegen/model_profiles/registry.py`
- Modify: `backend/src/aimctexturegen/model_profiles/workflows.py`
- Modify: `backend/src/aimctexturegen/inference/service.py`
- Create: `manifests/model-profiles/sdxl-mapchip-ipadapter-v2.json`
- Test: `backend/tests/comfy/test_manifests.py`
- Test: `backend/tests/comfy/test_registry.py`
- Test: `backend/tests/comfy/test_install_plan.py`
- Test: `backend/tests/model_profiles/test_registry.py`
- Test: `backend/tests/model_profiles/test_workflows.py`

**Interfaces:**
- Consumes: immutable version-1 manifest and current artifact installation receipts.
- Produces: `ProfileKey`, `ModelProfileManifestV2`, `ModelProfileManifestRecord`, and `ManifestRegistry.profile(profile_id, profile_version)`. Task 1 keeps the legacy profile-binding path on exact version 1; Task 4 adds the schema-3 version-2 binding after its durable type exists.

- [ ] **Step 1: Record version-1 byte immutability and RED versioned-registry tests**

```python
def test_registry_keeps_two_versions_of_one_profile_id(tmp_path: Path) -> None:
    root = write_registry(tmp_path, profiles=[profile_v1(), profile_v2()])
    registry = ManifestRegistry.load(root)
    assert registry.profile("sdxl-mapchip-ipadapter", "1").profile_version == "1"
    assert registry.profile("sdxl-mapchip-ipadapter", "2").profile_version == "2"
    with pytest.raises(ManifestNotFoundError):
        registry.profile("sdxl-mapchip-ipadapter", "3")

def test_phase5_never_changes_verified_v1_bytes() -> None:
    path = REPO_ROOT / "manifests/model-profiles/sdxl-mapchip-ipadapter-v1.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == (
        "9b909dc2d3b250f03b9a72996f43b6eaa3fa50f5eef0a38900e301a41678ccdd"
    )
```

Before editing, independently confirm the locked value:

```powershell
(Get-FileHash .\manifests\model-profiles\sdxl-mapchip-ipadapter-v1.json -Algorithm SHA256).Hash.ToLowerInvariant()
```

- [ ] **Step 2: Run the registry tests and verify RED**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\comfy\test_manifests.py backend\tests\comfy\test_registry.py backend\tests\model_profiles\test_registry.py -q
```

Expected: failures because profile schema 2 and the version parameter do not exist and duplicate profile IDs are still rejected.

- [ ] **Step 3: Add separate v1/v2 manifest records and tuple-keyed lookup**

```python
WorkflowVariant = Literal[
    "text2img-no-style",
    "text2img-style",
    "img2img-no-style",
    "img2img-style",
]

class WorkflowVariantRecord(_StrictModel):
    variant: WorkflowVariant
    relative_path: str = Field(min_length=1)
    sha256: str | None = None
    output_node_id: str = Field(min_length=1)

class ModelProfileManifestV2(_ProfileFields):
    schema_version: Literal[2]
    workflows: tuple[WorkflowVariantRecord, ...] = Field(min_length=4, max_length=4)

ModelProfileManifestRecord = ModelProfileManifest | ModelProfileManifestV2
ProfileKey = tuple[str, str]
```

Validate that the v2 workflow variants are exactly the four names above, paths are safe, output node IDs are nonempty decimal strings, and every digest is either `None` or 64 lowercase hex characters. Change the registry internal key to `(profile_id, profile_version)` and require both values in every lookup; sort by that tuple for deterministic iteration.

- [ ] **Step 4: Add the candidate v2 manifest without copying or editing v1**

Create `sdxl-mapchip-ipadapter-v2.json` with `schema_version: 2`, the same profile ID, version `"2"`, `support_state: "candidate_unverified"`, the exact runtime compatibility/artifact/license/hash records copied from v1, style range `0–8`, and four workflow records:

```json
[
  {"variant":"text2img-no-style","relative_path":"sdxl-mapchip-ipadapter-v2/text2img-no-style.api.json","sha256":null,"output_node_id":"19"},
  {"variant":"text2img-style","relative_path":"sdxl-mapchip-ipadapter-v2/text2img-style.api.json","sha256":null,"output_node_id":"19"},
  {"variant":"img2img-no-style","relative_path":"sdxl-mapchip-ipadapter-v2/img2img-no-style.api.json","sha256":null,"output_node_id":"19"},
  {"variant":"img2img-style","relative_path":"sdxl-mapchip-ipadapter-v2/img2img-style.api.json","sha256":null,"output_node_id":"19"}
]
```

Do not create workflow files in this task; registry tests must inject temporary workflow files or permit locked paths to be absent only for `candidate_unverified`. Product binding must reject `candidate_unverified`.

- [ ] **Step 5: Update every profile consumer to request an exact version**

Use constants rather than silent “latest” selection:

```python
SETUP_PROFILE_KEY: ProfileKey = ("sdxl-mapchip-ipadapter", "2")

profile = registry.profile(*SETUP_PROFILE_KEY)
```

`Installer.inspect` and `ProfileCatalog.get` accept both profile ID and version. The legacy `build_model_profile_binding` receives `profile_version="1"` explicitly and keeps returning the existing schema-2 `ModelProfileBinding`. It rejects a version-2 profile with `PROFILE_CAPABILITY_MISMATCH` until Task 4 adds `build_generation_profile_binding(profile_version="2", style_reference_count=..., structure_reference_present=..., require_verified=...)` and the schema-3 binding type.

- [ ] **Step 6: Run focused and regression tests**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\comfy\test_manifests.py backend\tests\comfy\test_registry.py backend\tests\comfy\test_install_plan.py backend\tests\model_profiles\test_registry.py backend\tests\model_profiles\test_workflows.py -q
.\.venv\Scripts\python -W error -m pytest backend\tests\api\test_inference.py backend\tests\jobs -q
git diff --check
```

Expected: all pass; v1 file hash is unchanged; v2 artifacts are classified `ready` when their identical v1 hashes are installed.

- [ ] **Step 7: Commit**

```powershell
git add backend/src/aimctexturegen/comfy backend/src/aimctexturegen/model_profiles backend/src/aimctexturegen/inference backend/tests/comfy backend/tests/model_profiles backend/tests/api/test_inference.py manifests/model-profiles/sdxl-mapchip-ipadapter-v2.json
git commit -m "feat: version model profile manifests"
```

---

### Task 2: Add strict schema-3 jobs and schema-aware persistence

**Files:**
- Create: `backend/src/aimctexturegen/jobs/models_v3.py`
- Create: `backend/src/aimctexturegen/jobs/codec.py`
- Create: `backend/src/aimctexturegen/jobs/generation_state.py`
- Modify: `backend/src/aimctexturegen/jobs/store.py`
- Modify: `backend/src/aimctexturegen/index/service.py`
- Test: `backend/tests/jobs/test_models_v3.py`
- Test: `backend/tests/jobs/test_codec.py`
- Test: `backend/tests/jobs/test_generation_state.py`
- Modify: `backend/tests/jobs/test_store.py`
- Modify: `backend/tests/index/test_rebuild.py`

**Interfaces:**
- Consumes: legacy `JobRequest`, `JobStateRecord`, safe seed limit, atomic-file and guarded-directory primitives.
- Produces: `GenerationJobRequest`, `GenerationJobState`, `DurableJobRequest`, `DurableJobState`, `JobInputSnapshot`, schema-aware codec functions, and a store that accepts either exact layout.

- [ ] **Step 1: Write strict schema and native-batch RED tests**

```python
@pytest.mark.parametrize(
    ("parallelism", "groups"),
    [
        (1, ((0,), (1,), (2,), (3,))),
        (2, ((0, 1), (2, 3))),
        (4, ((0, 1, 2, 3),)),
    ],
)
def test_schema3_accepts_only_exact_native_batch_partition(parallelism, groups):
    request = generation_request(
        parallelism=parallelism,
        execution_batches=tuple(
            batch(index, candidates) for index, candidates in enumerate(groups)
        ),
    )
    assert tuple(item.candidate_indices for item in request.execution_batches) == groups

def test_schema3_rejects_duplicate_or_missing_candidates():
    with pytest.raises(ValidationError):
        generation_request(
            parallelism=2,
            execution_batches=(batch(0, (0, 1)), batch(1, (1, 3))),
        )
```

Also test: unknown fields, seed above `MAX_SAFE_SEED`, mismatched workflow variant/references, more than eight styles, structure count above one, wrong batch positions, artifact path escape, completed candidate without complete artifacts, and inherited candidate without lineage.

- [ ] **Step 2: Run schema tests and verify RED**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\jobs\test_models_v3.py backend\tests\jobs\test_codec.py backend\tests\jobs\test_generation_state.py -q
```

Expected: import failures because the new files do not exist.

- [ ] **Step 3: Implement focused schema-3 models**

Use frozen strict Pydantic models with these durable fields:

```python
class ExecutionBatch(_StrictModel):
    batch_index: int = Field(ge=0, le=3)
    candidate_indices: tuple[CandidateIndex, ...]
    seed: Seed

class GenerationFailure(_StrictModel):
    error_code: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    user_message: str = Field(min_length=1)
    recommended_actions: tuple[str, ...]
    technical_details: str | None
    retryable: bool
    occurred_at: AwareDatetime

class GenerationCandidateRecord(_StrictModel):
    candidate_index: CandidateIndex
    batch_index: int = Field(ge=0, le=3)
    position_in_batch: int = Field(ge=0, le=3)
    batch_seed: Seed
    status: Literal[
        "pending", "generating", "raw_ready", "postprocessing",
        "completed", "failed", "canceled", "inherited",
    ]
    artifacts: CandidateArtifacts
    lineage: CandidateLineage | None
    failure: GenerationFailure | None
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None

class GenerationJobRequest(_StrictModel):
    schema_version: Literal[3]
    job_id: UUID
    project_id: UUID
    parent_job_id: UUID | None
    target: GenerationTarget
    prompt: CompiledPrompt
    resolution: Literal[16, 32, 64]
    parallelism: Literal[1, 2, 4]
    execution_batches: tuple[ExecutionBatch, ...]
    references: FrozenReferences
    advanced: GenerationAdvanced
    model_profile: GenerationModelBinding
    created_at: AwareDatetime

class GenerationJobState(_StrictModel):
    schema_version: Literal[2]
    job_id: UUID
    project_id: UUID
    revision: int = Field(ge=0)
    status: JobStatus
    batches: tuple[GenerationBatchRecord, ...]
    candidates: FourGenerationCandidates
    failure: GenerationFailure | None
    cancel_requested_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
```

`GenerationModelBinding` contains `profile_id`, `profile_version`,
`profile_manifest_sha256`, `runtime_id`, `runtime_version`,
`runtime_manifest_sha256`, `workflow_variant`, `workflow_sha256`, and
`output_node_id`. `GenerationBatchRecord` contains its batch identity/status,
nullable prompt ID, nullable sampling progress, raw artifact tuple, failure,
and lifecycle timestamps.

`StoredArtifact` records `kind`, project-relative job artifact path, SHA-256, byte size, media type, and nullable width/height. `CandidateArtifacts` has nullable `raw`, `final`, `nearest`, `tile`, and `report`.
The state-level failure is normally present only for terminal `failed`.
One explicit exception is an active state with `cancel_requested_at` and
`CANCEL_CONFIRMATION_FAILED`; it stays nonterminal and keeps the global slot.

- [ ] **Step 4: Implement discriminated codecs without rewriting legacy bytes**

```python
DurableJobRequest = JobRequest | GenerationJobRequest
DurableJobState = JobStateRecord | GenerationJobState

def load_job_request(payload: bytes) -> DurableJobRequest: ...
def load_job_state(payload: bytes) -> DurableJobState: ...
def dump_durable_request(request: DurableJobRequest) -> bytes: ...
def dump_durable_state(state: DurableJobState) -> bytes: ...
def validate_durable_pair(
    request: DurableJobRequest,
    state: DurableJobState,
) -> None: ...
```

Inspect `schema_version` from bounded JSON, then call the exact model. Never parse a legacy record through the schema-3 model. Add a byte-for-byte test using existing schema-1 and schema-2 request fixtures.

- [ ] **Step 5: Implement pure generation state transitions**

Provide exact functions:

```python
def start_generation(state: GenerationJobState, *, now: datetime) -> GenerationJobState: ...
def request_cancel(state: GenerationJobState, *, now: datetime) -> GenerationJobState: ...
def start_batch(state: GenerationJobState, batch_index: int, *, now: datetime) -> GenerationJobState: ...
def record_progress(state: GenerationJobState, batch_index: int, value: int, maximum: int, *, now: datetime) -> GenerationJobState: ...
def mark_batch_raw_ready(state: GenerationJobState, batch_index: int, artifacts: tuple[StoredArtifact, ...], *, now: datetime) -> GenerationJobState: ...
def complete_candidate(state: GenerationJobState, candidate_index: int, artifacts: CandidateArtifacts, *, now: datetime) -> GenerationJobState: ...
def complete_generation(state: GenerationJobState, *, now: datetime) -> GenerationJobState: ...
def fail_generation(state: GenerationJobState, failure: GenerationFailure, *, now: datetime) -> GenerationJobState: ...
def confirm_canceled(state: GenerationJobState, *, now: datetime) -> GenerationJobState: ...
def recover_generation_interruption(state: GenerationJobState, *, now: datetime) -> GenerationJobState: ...
```

Each function increments revision exactly once. `request_cancel` is idempotent after the first durable timestamp. `confirm_canceled` preserves `completed`/`inherited`, cancels all other nonterminal work, and is illegal until `cancel_requested_at` exists.

- [ ] **Step 6: Make `JobStore` schema-aware and atomically create frozen inputs**

Add:

```python
@dataclass(frozen=True)
class JobInputFile:
    relative_path: str
    payload: bytes
    sha256: str

@dataclass(frozen=True)
class JobInputSnapshot:
    references_json: bytes
    files: tuple[JobInputFile, ...]

def create_generation(
    self,
    request: GenerationJobRequest,
    inputs: JobInputSnapshot,
) -> LoadedJob: ...
```

Schema 3 exact top-level children are `request.json`, `state.json`, `inputs/`, `raw/`, `processed/`, `previews/`, and `reports/`. Legacy exact children remain unchanged. Validate all input paths against the fixed grammar `style/NN.png`, `structure.png`, and `references.json`; recalculate every hash during staging; publish the whole job directory by one rename.

- [ ] **Step 7: Run focused, legacy, and index tests**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\jobs backend\tests\index -q
.\.venv\Scripts\python -W error -m pytest backend\tests\integration\test_restart_recovery.py -q
git diff --check
```

Expected: all pass; existing schema-1/2 test fixtures remain readable and unchanged; index rebuild summarizes both record families.

- [ ] **Step 8: Commit**

```powershell
git add backend/src/aimctexturegen/jobs backend/src/aimctexturegen/index backend/tests/jobs backend/tests/index backend/tests/integration/test_restart_recovery.py
git commit -m "feat: persist schema 3 generation jobs"
```

---

### Task 3: Build the controlled reference library and immutable job snapshots

**Files:**
- Create: `backend/src/aimctexturegen/references/__init__.py`
- Create: `backend/src/aimctexturegen/references/models.py`
- Create: `backend/src/aimctexturegen/references/validation.py`
- Create: `backend/src/aimctexturegen/references/store.py`
- Create: `backend/src/aimctexturegen/references/service.py`
- Test: `backend/tests/references/test_validation.py`
- Test: `backend/tests/references/test_store.py`
- Test: `backend/tests/references/test_service.py`

**Interfaces:**
- Consumes: `ProjectRepository`, `CatalogRegistry`, `classify_coverage`, `JobInputSnapshot`, guarded directories, and atomic writes.
- Produces: the fixed `ReferenceService` interface, server-generated reference IDs, safe content retrieval, and task-local frozen copies.

- [ ] **Step 1: Write synthetic PNG validation RED tests**

```python
def test_accepts_static_square_rgb_and_rgba_png():
    assert validate_reference_png(png("RGB", 16, 16)).mode == "RGB"
    assert validate_reference_png(png("RGBA", 4096, 4096)).mode == "RGBA"

@pytest.mark.parametrize(
    "payload",
    [
        animated_png(),
        truncated_png(),
        png("L", 16, 16),
        png("RGB", 15, 15),
        png("RGB", 16, 17),
        b"x" * (16 * 1024 * 1024 + 1),
    ],
)
def test_rejects_out_of_contract_reference(payload):
    with pytest.raises(ReferenceValidationError) as captured:
        validate_reference_png(payload)
    assert captured.value.code == "REFERENCE_INVALID"
```

Use synthetic colors only. Inside the validator, use
`warnings.catch_warnings()` with `simplefilter("error",
Image.DecompressionBombWarning)` plus the explicit pixel cap; do not modify
Pillow's process-global warning policy.

- [ ] **Step 2: Run reference tests and verify RED**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\references -q
```

Expected: import failures because the reference package does not exist.

- [ ] **Step 3: Implement the single bounded validator**

```python
MAX_REFERENCE_BYTES = 16 * 1024 * 1024
MAX_REFERENCE_PIXELS = 16_777_216
MIN_REFERENCE_SIDE = 16
MAX_REFERENCE_SIDE = 4096

@dataclass(frozen=True)
class ValidatedReference:
    payload: bytes
    sha256: str
    byte_size: int
    width: int
    height: int
    mode: Literal["RGB", "RGBA"]

def validate_reference_png(payload: bytes) -> ValidatedReference: ...
```

Require the PNG signature, `Image.open(...).format == "PNG"`, `n_frames == 1`, `is_animated is not True`, exact square bounds, full `load()`, mode RGB/RGBA after decode, and no bytes beyond the caller's cap.

- [ ] **Step 4: Write library publication/deletion RED tests**

```python
stored = store.create(project_id, "style", validated, now=NOW)
assert stored.reference_id == FIXED_ID
assert store.read_content(project_id, "style", FIXED_ID) == validated.payload
store.delete(project_id, "style", FIXED_ID)
assert store.list(project_id, "style") == ()
```

Also prove IDs, not uploaded filenames, form paths; a junction/reparse ancestor is rejected; metadata publication failure leaves no half-record; deleting an upload does not remove a job-local copy.

- [ ] **Step 5: Implement the guarded project reference store**

Store only:

```text
uploads/<kind>-references/<uuid>/original.png
uploads/<kind>-references/<uuid>/metadata.json
```

`metadata.json` is strict schema 1 and records ID, kind, SHA-256, byte size, width, height, mode, and created time. Create into `<uuid>.tmp`, read back and validate both files, then rename. Deletion opens the exact UUID directory through `ProjectRepository`, rejects reparse points, and removes only the server-owned record.

- [ ] **Step 6: Implement pack listing, selection resolution, and freeze**

Selection transport models are:

```python
class PackReferenceSelection(_StrictModel):
    source: Literal["pack"]
    relative_path: str

class UploadReferenceSelection(_StrictModel):
    source: Literal["upload"]
    reference_id: UUID

class ReferenceSelections(_StrictModel):
    style: tuple[PackReferenceSelection | UploadReferenceSelection, ...] = Field(max_length=8)
    structure: UploadReferenceSelection | None
```

`list_pack_references` returns only covered or unknown/custom square PNGs that pass the shared validator. `freeze` resolves all selections under a held project root, validates bytes again, assigns stable task IDs `style-00`…`style-07` and `structure-00`, and returns `JobInputSnapshot` paths `style/00.png`… plus optional `structure.png`. It never returns an absolute path.

- [ ] **Step 7: Run focused and project safety regressions**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\references backend\tests\packs\test_coverage.py backend\tests\projects -q
git diff --check
```

Expected: all pass; no test fixture contains a real game texture.

- [ ] **Step 8: Commit**

```powershell
git add backend/src/aimctexturegen/references backend/tests/references backend/tests/packs/test_coverage.py backend/tests/projects
git commit -m "feat: manage safe generation references"
```

---

### Task 4: Compile block prompts and create immutable native-batch jobs

**Files:**
- Create: `backend/src/aimctexturegen/generation/__init__.py`
- Create: `backend/src/aimctexturegen/generation/errors.py`
- Create: `backend/src/aimctexturegen/generation/prompts.py`
- Create: `backend/src/aimctexturegen/generation/service.py`
- Test: `backend/tests/generation/test_prompts.py`
- Test: `backend/tests/generation/test_creation.py`

**Interfaces:**
- Consumes: exact profile-v2 binding from Task 1, schema-3/store from Task 2, and `ReferenceService.freeze` from Task 3.
- Produces: `compile_block_prompt`, `CreateGenerationCommand`, `build_execution_batches`, and `GenerationService.create_job`.

- [ ] **Step 1: Write exact prompt RED tests**

```python
def test_java_block_prompt_v1_is_exact_and_normalized():
    prompt = compile_block_prompt(
        resolution=16,
        display_name="Deepslate",
        prompt_terms=("deep stone", "dense natural texture"),
        user_description="  cold   blue-gray\nstone  ",
        user_negative_prompt="  neon,   glossy ",
    )
    assert prompt.compiled_positive == (
        "1616, pixel art, seamless tileable square block texture, "
        "Minecraft Java Edition resource-pack texture, flat albedo, "
        "uniform material covering the full canvas, edge-to-edge continuous "
        "texture, crisp hard-edged pixel clusters, no border, no centered "
        "subject, Deepslate, deep stone, dense natural texture, cold blue-gray stone"
    )
    assert prompt.compiled_negative.endswith(", neon, glossy")
```

Assert `3232` for 32 and the exact prefix `"logical 64x64 pixel grid"` for 64; assert `"4848"` never appears. Assert item-icon/white-margin text appears only in the default negative prompt, never positive.

- [ ] **Step 2: Run prompt tests and verify RED**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\generation\test_prompts.py -q
```

Expected: import failure because the compiler does not exist.

- [ ] **Step 3: Implement the versioned pure compiler**

```python
PROMPT_TEMPLATE_ID = "java-block-prompt"
PROMPT_TEMPLATE_VERSION = 1
DEFAULT_NEGATIVE = (
    "item icon, isolated object, centered composition, empty margin, "
    "white background, border, frame, visible seam, perspective, 3d render, "
    "scene, text, watermark, drop shadow, soft focus, anti-aliasing, "
    "blurry gradient, lighting vignette"
)

def compile_block_prompt(
    *,
    resolution: Literal[16, 32, 64],
    display_name: str,
    prompt_terms: tuple[str, ...],
    user_description: str,
    user_negative_prompt: str,
) -> CompiledPrompt: ...
```

Collapse all Unicode whitespace to one ASCII space, trim comma components, preserve component order, and reject either compiled string above `MAX_PROMPT_CODE_POINTS`.

- [ ] **Step 4: Write batch-plan and atomic creation RED tests**

```python
@pytest.mark.parametrize(
    ("parallelism", "expected"),
    [
        (1, ((0,), (1,), (2,), (3,))),
        (2, ((0, 1), (2, 3))),
        (4, ((0, 1, 2, 3),)),
    ],
)
def test_create_freezes_exact_native_batches_and_inputs(parallelism, expected):
    loaded = service.create_job(PROJECT_ID, command(parallelism=parallelism))
    assert tuple(batch.candidate_indices for batch in loaded.request.execution_batches) == expected
    assert [batch.seed for batch in loaded.request.execution_batches] == SEED_SOURCE[:len(expected)]
    assert (loaded.root / "inputs/references.json").is_file()
```

Also prove: target must be currently missing and `mvp_eligible`; 0 styles select no-style; structure selects img2img; failed target/reference/profile validation creates no job directory and consumes no seed; a new job uses new seeds.

- [ ] **Step 5: Implement command, batch builder, and creation service**

```python
class CreateGenerationCommand(_StrictModel):
    target_semantic_id: str = Field(min_length=1)
    user_description: str = Field(max_length=4000)
    user_negative_prompt: str = Field(default="", max_length=4000)
    resolution: Literal[16, 32, 64]
    parallelism: Literal[1, 2, 4]
    references: ReferenceSelections
    denoise: float | None = Field(default=None, ge=0.0, le=1.0)
    style_weight: float | None = Field(default=None, ge=0.0, le=2.0)

def build_execution_batches(
    parallelism: Literal[1, 2, 4],
    seed_source: Callable[[], int],
) -> tuple[ExecutionBatch, ...]: ...
```

Reject `denoise` without structure and `style_weight` without styles. Bind exact profile key `("sdxl-mapchip-ipadapter", "2")`. Call `ReferenceService.freeze` before constructing the request, but publish inputs and request only through `JobStore.create_generation` so a failure leaves no visible job.

Add the exact profile resolver:

```python
def build_generation_profile_binding(
    registry: ManifestRegistry,
    *,
    profile_id: str,
    profile_version: str,
    style_reference_count: int,
    structure_reference_present: bool,
    require_verified: bool = True,
) -> GenerationModelBinding: ...
```

It derives one of the four `WorkflowVariant` values from the two reference
conditions, verifies capabilities and all profile/runtime/workflow digests,
and persists the selected output node ID.

- [ ] **Step 6: Add stable creation errors**

Map service errors to explicit codes including `JOB_TARGET_NOT_FOUND`, `JOB_TARGET_NOT_ELIGIBLE`, `JOB_TARGET_NOT_MISSING`, `REFERENCE_INVALID`, `PROFILE_NOT_READY`, and `PROFILE_WORKFLOW_MISMATCH`. Each error carries a Chinese user message and actions; do not persist an absolute path or arbitrary model log.

```python
class GenerationError(Exception):
    def __init__(
        self,
        code: str,
        user_message: str,
        *,
        recommended_actions: tuple[str, ...] = (),
        technical_details: str | None = None,
        current_job: tuple[UUID, UUID] | None = None,
    ) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.recommended_actions = recommended_actions
        self.technical_details = technical_details
        self.current_job = current_job
```

Only `GENERATION_JOB_CONFLICT` uses `current_job`; API Task 10 exposes that
identity through `GET /api/generation/current` rather than adding local paths
or arbitrary fields to the common error envelope.

- [ ] **Step 7: Run focused tests**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\generation\test_prompts.py backend\tests\generation\test_creation.py backend\tests\jobs -q
git diff --check
```

Expected: all pass; direct legacy `JobService` tests continue to pass.

- [ ] **Step 8: Commit**

```powershell
git add backend/src/aimctexturegen/generation backend/tests/generation
git commit -m "feat: create immutable generation jobs"
```

---

### Task 5: Add four SDXL v2 native-batch workflows and compiler

**Files:**
- Modify: `backend/src/aimctexturegen/model_profiles/workflows.py`
- Modify: `backend/src/aimctexturegen/model_profiles/sdxl.py`
- Create: `backend/src/aimctexturegen/model_profiles/sdxl_v2.py`
- Create: `workflows/sdxl-mapchip-ipadapter-v2/text2img-no-style.api.json`
- Create: `workflows/sdxl-mapchip-ipadapter-v2/text2img-style.api.json`
- Create: `workflows/sdxl-mapchip-ipadapter-v2/img2img-no-style.api.json`
- Create: `workflows/sdxl-mapchip-ipadapter-v2/img2img-style.api.json`
- Modify: `manifests/model-profiles/sdxl-mapchip-ipadapter-v2.json`
- Test: `backend/tests/model_profiles/test_sdxl_v2.py`
- Modify: `backend/tests/model_profiles/test_sdxl.py`
- Modify: `backend/tests/model_profiles/test_workflows.py`

**Interfaces:**
- Consumes: candidate v2 manifest and immutable generic input/profile binding.
- Produces: `SDXLV2Binding`, locked workflow digests, native batch compilation, and fixed output-node identity.

- [ ] **Step 1: Relax generic style cardinality to 0–8 while pinning v1 to 1–8**

Add RED tests that `GenericWorkflowInputs(style_reference_names=())` is valid for v2, but `SDXLBinding` v1 raises `WorkflowBindingError` rather than indexing an empty tuple. Add `batch_size: Literal[1, 2, 4] = 1` and `output_node_id` to generic inputs/bindings without changing v1 compiled output at default batch 1.

- [ ] **Step 2: Write the four-variant compiler RED matrix**

```python
@pytest.mark.parametrize(
    ("variant", "styles", "structure", "batch_size"),
    [
        ("text2img-no-style", (), None, 1),
        ("text2img-style", ("style.png",), None, 2),
        ("img2img-no-style", (), "structure.png", 4),
        ("img2img-style", ("a.png", "b.png"), "structure.png", 2),
    ],
)
def test_v2_compiles_only_the_selected_conditioning_graph(
    variant, styles, structure, batch_size
):
    compiled = binding(variant).compile(
        inputs(styles=styles, structure=structure, batch_size=batch_size)
    )
    assert compiled["12"]["inputs"]["seed"] == 123
    assert binding(variant).output_node_id == "19"
```

Assert no-style graphs contain none of `CLIPVisionLoader`, `CLIPVisionEncode`, `IPAdapterUnifiedLoader`, or `IPAdapterAdvanced`. Assert style graphs use `combine_embeds="average"` and `weight_type="style transfer"`. Assert text uses `EmptyLatentImage.batch_size`; img2img uses `RepeatLatentBatch.amount`.

- [ ] **Step 3: Run compiler tests and verify RED**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\model_profiles\test_sdxl_v2.py backend\tests\model_profiles\test_sdxl.py backend\tests\model_profiles\test_workflows.py -q
```

Expected: failures because v2 binding/workflows do not exist and generic inputs still require a style.

- [ ] **Step 4: Derive and validate four tracked API workflows**

Use the verified v1 node contracts as the source, not the ComfyUI UI export:

- text/no-style: checkpoint → LoRA → text encoders → `EmptyLatentImage` → sampler → VAE decode → output node.
- text/style: the same plus verified CLIP Vision/IP-Adapter average graph.
- image/no-style: LoadImage → ImageScale 1024 → VAEEncode → `RepeatLatentBatch` → sampler using LoRA model.
- image/style: the same image graph plus verified CLIP Vision/IP-Adapter average graph.

All four use output node `"19"` and the existing locked model filenames/preset. Do not expose sampler, steps, CFG, scheduler, LoRA strength, or node IDs outside `sdxl_v2.py`.

- [ ] **Step 5: Implement `SDXLV2Binding`**

```python
class SDXLV2Binding(WorkflowBinding):
    def __init__(self, *, variant: WorkflowVariant, workflow_path: Path) -> None: ...
    def _apply(self, inputs: GenericWorkflowInputs, working: dict) -> None: ...
```

Require style count `0` for no-style and `1–8` for style; require structure exactly when variant starts `img2img`; set only semantic slots for prompts, seed, batch size, safe uploaded names, denoise, and style weight. Keep v1 compiler in `sdxl.py` and add an explicit empty-style guard.

- [ ] **Step 6: Lock exact workflow SHA-256 values**

```powershell
Get-ChildItem .\workflows\sdxl-mapchip-ipadapter-v2\*.api.json |
  Sort-Object Name |
  ForEach-Object {
    '{0} {1}' -f $_.Name, (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
  }
```

Write those four values into the v2 manifest and add a test that hashes the raw tracked bytes. Keep `support_state` as `candidate_unverified`.

- [ ] **Step 7: Run profile and manifest gates**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\model_profiles backend\tests\comfy\test_manifests.py backend\tests\comfy\test_registry.py -q
git diff --check
```

Expected: all pass; v1 digest test remains unchanged.

- [ ] **Step 8: Commit**

```powershell
git add backend/src/aimctexturegen/model_profiles backend/tests/model_profiles workflows/sdxl-mapchip-ipadapter-v2 manifests/model-profiles/sdxl-mapchip-ipadapter-v2.json backend/tests/comfy
git commit -m "feat: add sdxl profile v2 workflows"
```

---

### Task 6: Extend the generic Comfy transport for ordered batches and prompt-scoped cancellation

**Files:**
- Modify: `backend/src/aimctexturegen/comfy/client.py`
- Modify: `backend/src/aimctexturegen/comfy/errors.py`
- Modify: `backend/tests/fakes/comfy_server.py`
- Modify: `backend/tests/comfy/test_client_http.py`
- Modify: `backend/tests/comfy/test_client_websocket.py`

**Interfaces:**
- Consumes: ComfyUI v0.29.2 `/prompt`, `/history/{id}`, `/view`, `/queue`, `/interrupt`, and WebSocket contracts.
- Produces: `ComfyOutputImage`, `QueueSnapshot`, ordered output extraction, targeted interrupt, and cancel-aware wait.

- [ ] **Step 1: Make fake ComfyUI protocol-faithful for multiple outputs and queue ownership**

Extend `FakeComfyServer` with `view_bytes_by_name`, `queue_running`, `queue_pending`, `last_interrupt_prompt_id`, and per-prompt history. `GET /queue` returns rows with prompt ID at index 1. `POST /interrupt` records optional JSON `{"prompt_id": "..."}`.

- [ ] **Step 2: Write ordered-output and queue RED tests**

```python
def test_declared_output_images_preserve_selected_node_order():
    images = client.declared_output_images(history, output_node_id="19")
    assert [image.filename for image in images] == ["a_00001_.png", "a_00002_.png"]

def test_queue_and_targeted_interrupt_are_prompt_scoped():
    snapshot = client.queue_snapshot()
    assert snapshot.running_prompt_ids == (PROMPT_ID,)
    client.interrupt(PROMPT_ID)
    assert server.last_interrupt_prompt_id == PROMPT_ID
```

Also reject a missing output node, non-list `images`, unsafe filename/subfolder/type, duplicate descriptors, oversized downloads, and prompt IDs not represented as strings.

- [ ] **Step 3: Write cancel-aware WebSocket RED tests**

Use a held fake WebSocket and an event:

```python
cancel = threading.Event()
cancel.set()
with pytest.raises(ComfyCanceledError):
    client.wait_completion(PROMPT_ID, timeout=2.0, cancel_requested=cancel.is_set)
```

The test must complete in under one second rather than waiting for the full timeout.

- [ ] **Step 4: Implement typed descriptors and queue methods**

```python
@dataclass(frozen=True)
class ComfyOutputImage:
    filename: str
    subfolder: str
    type: Literal["output"]

@dataclass(frozen=True)
class QueueSnapshot:
    running_prompt_ids: tuple[str, ...]
    pending_prompt_ids: tuple[str, ...]

def declared_output_images(
    self, history_entry: dict, *, output_node_id: str
) -> tuple[ComfyOutputImage, ...]: ...
def get_output_image(self, image: ComfyOutputImage) -> bytes: ...
def queue_snapshot(self) -> QueueSnapshot: ...
def interrupt(self, prompt_id: str | None = None) -> None: ...
```

Do not import generation/jobs/projects into `comfy/client.py`.

- [ ] **Step 5: Make `wait_completion` observe cancellation promptly**

Add `cancel_requested: Callable[[], bool] | None = None`. Bound each WebSocket receive wait to `min(remaining, 0.25)`; a short receive timeout loops until the overall deadline unless cancellation is true. Raise `ComfyCanceledError`, not `ComfyTimeoutError`, on cancellation.

- [ ] **Step 6: Run all Comfy transport tests**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\comfy\test_client_http.py backend\tests\comfy\test_client_websocket.py backend\tests\comfy\test_client_errors.py -q
git diff --check
```

Expected: all pass and the isolation test still proves the client imports no product layer.

- [ ] **Step 7: Commit**

```powershell
git add backend/src/aimctexturegen/comfy backend/tests/comfy backend/tests/fakes/comfy_server.py
git commit -m "feat: support ordered comfy batches"
```

---

### Task 7: Publish atomic raw batches and deterministic candidate artifacts

**Files:**
- Create: `backend/src/aimctexturegen/generation/artifacts.py`
- Modify: `backend/src/aimctexturegen/jobs/store.py`
- Test: `backend/tests/generation/test_artifacts.py`
- Modify: `backend/tests/processing/test_pipeline.py`

**Interfaces:**
- Consumes: `ComfyOutputImage`, schema-3 artifact models, `process_candidate`, atomic files, and guarded job roots.
- Produces: `CandidateArtifactStore.publish_raw_batch`, `process_and_publish`, and `resolve`.

- [ ] **Step 1: Write whole-batch raw publication RED tests**

```python
def test_raw_batch_is_all_or_nothing():
    with pytest.raises(GenerationError) as captured:
        artifacts.publish_raw_batch(
            loaded,
            batch,
            (valid_1024_png(), corrupt_png()),
        )
    assert captured.value.code == "OUTPUT_CONTRACT_VIOLATION"
    assert list((loaded.root / "raw").iterdir()) == []
```

Also test missing/extra outputs, non-square, wrong 1024 canvas, RGBA when profile contract is RGB, publication failure, and exact mapping to candidate indices in output order.

- [ ] **Step 2: Run artifact tests and verify RED**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\generation\test_artifacts.py -q
```

Expected: import failure because `generation/artifacts.py` does not exist.

- [ ] **Step 3: Implement raw-batch staging and atomic publication**

```python
class CandidateArtifactStore:
    def publish_raw_batch(
        self,
        loaded: LoadedJob,
        batch: ExecutionBatch,
        payloads: tuple[bytes, ...],
        *,
        canvas_size: int,
    ) -> tuple[StoredArtifact, ...]: ...
```

Validate every payload in memory and stage all files under
`raw/.batch-<index>.tmp/`. Reopen, rehash, and revalidate all staged files.
Then publish the complete native batch with one directory rename to
`raw/batch-<index>/`; predictable members are
`candidate-<candidate-index>.png`. A pre-existing final batch directory is an
integrity conflict, never an overwrite target. Do not record state until the
renamed directory and every member are reopened and match. This single
directory publication is the raw-batch atomicity boundary.

- [ ] **Step 4: Write processed artifact and resolver RED tests**

```python
artifacts = store.process_and_publish(loaded, candidate_index=0, resolution=16)
assert artifacts.final.width == 16
assert artifacts.nearest.width > 16
assert artifacts.tile.width == 48
report_path = store.resolve(
    project_id=PROJECT_ID,
    job_id=JOB_ID,
    candidate_index=0,
    kind="report",
)
assert json.loads(report_path.read_bytes())["resolution"] == 16
```

Prove a failure leaves the previous complete artifact set intact, report references match published hashes, and resolver rejects cross-project lineage, path traversal, hash mismatch, and an unavailable kind.

- [ ] **Step 5: Implement per-candidate postprocessing publication**

Run `process_candidate` in `processed/.candidate-<index>.tmp/`, validate its report and four outputs, then atomically rename that one directory to:

```text
processed/candidate-<index>/final.png
processed/candidate-<index>/nearest.png
processed/candidate-<index>/tile.png
processed/candidate-<index>/report.json
```

Return a complete `CandidateArtifacts` containing the existing raw reference. The
schema-3 `previews/` and `reports/` top-level directories remain empty reserved
compatibility roots; clients use artifact records rather than assuming a disk
path. The store resolves inherited artifacts by source job/candidate only after
reopening that same project's canonical parent and verifying recorded SHA-256.

- [ ] **Step 6: Run processing, artifact, and store gates**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\generation\test_artifacts.py backend\tests\processing backend\tests\jobs\test_store.py -q
git diff --check
```

Expected: all pass; `processing` remains independent from FastAPI and ComfyUI.

- [ ] **Step 7: Commit**

```powershell
git add backend/src/aimctexturegen/generation/artifacts.py backend/src/aimctexturegen/jobs/store.py backend/tests/generation/test_artifacts.py backend/tests/processing backend/tests/jobs/test_store.py
git commit -m "feat: publish generation artifacts safely"
```

---

### Task 8: Execute batches, translate failures, and create lineage retries

**Files:**
- Modify: `backend/src/aimctexturegen/generation/errors.py`
- Modify: `backend/src/aimctexturegen/generation/service.py`
- Modify: `backend/src/aimctexturegen/inference/service.py`
- Test: `backend/tests/generation/test_execution.py`
- Test: `backend/tests/generation/test_failures.py`
- Test: `backend/tests/generation/test_retry.py`

**Interfaces:**
- Consumes: version-2 compiler, `ComfyClient`, artifact store, state transitions, exact managed inference status, and frozen job inputs.
- Produces: `ExecutionContext`, `ManagedInferenceService.ensure_generation_ready`, `GenerationService.run_job`, stable failure mapping, and `retry_job`.

- [ ] **Step 1: Write a fake-runtime success RED test**

```python
completed = service.run_job(
    PROJECT_ID,
    JOB_ID,
    ExecutionContext(
        client=fake_client,
        cancel_requested=lambda: False,
        prompt_registered=registered.append,
        state_committed=committed.append,
    ),
)
assert completed.state.status == "completed"
assert fake_client.submitted_batch_sizes == [2, 2]
assert [c.status for c in completed.state.candidates] == ["completed"] * 4
```

Assert references are uploaded from `jobs/<id>/inputs`, safe remote names include job/reference IDs, one Comfy prompt is submitted per native batch, each candidate revision becomes observable as it completes, and batch 0 finishes before batch 1 starts.

- [ ] **Step 2: Write failure translation RED tests**

Use typed fake errors and histories to assert exact persisted codes:

```python
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ComfyQueueError("x"), "COMFY_QUEUE_REJECTED"),
        (ComfyDisconnectedError("x"), "COMFY_DISCONNECTED"),
        (ComfyTimeoutError("x"), "COMFY_TIMEOUT"),
        (oom_execution_error(), "GPU_OUT_OF_MEMORY"),
        (ComfyExecutionError("x"), "COMFY_EXECUTION_FAILED"),
        (ProcessingError("x", "x"), "POSTPROCESSING_FAILED"),
    ],
)
def test_execution_failure_is_persisted_without_parameter_mutation(error, code):
    before = loaded.request
    fake_client.fail_with(error)
    failed = service.run_job(PROJECT_ID, JOB_ID, execution_context(fake_client))
    assert failed.state.status == "failed"
    assert failed.state.failure.error_code == code
    assert failed.request == before
    assert hash_tree(project.pack_root) == PACK_HASHES_BEFORE
```

OOM recommendations must be exactly: lower parallelism on a new job, close VRAM-consuming applications, and stop other ComfyUI instances. Do not automatically perform any recommendation.

- [ ] **Step 3: Run execution tests and verify RED**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\generation\test_execution.py backend\tests\generation\test_failures.py backend\tests\generation\test_retry.py -q
```

Expected: failures because execution and retry do not exist.

- [ ] **Step 4: Expose exact managed-inference readiness**

```python
def ensure_generation_ready(
    self,
    binding: GenerationModelBinding,
) -> ComfyClient:
    """Require exact installed/verified identities; start stopped managed child."""
```

Compare profile/runtime IDs, versions, manifest hashes, workflow digest, installed receipts, process readiness, and server node classes. If stopped and all records are ready, call the existing manager start. If missing/corrupt/unverified, raise `PROFILE_NOT_READY`; do not invoke installation.

- [ ] **Step 5: Implement `run_job` batch loop**

For each persisted batch:

1. commit batch/candidate generating state;
2. upload frozen references;
3. compile the exact persisted workflow variant and batch seed;
4. submit once and immediately call `prompt_registered(prompt_id)`;
5. wait with progress throttled to at most one durable update per 250 ms and always commit the final progress;
6. extract exactly the declared output-node array in order;
7. fetch and atomically publish the whole raw batch;
8. process/publish each candidate and commit it immediately;
9. check cancellation at every durable boundary;
10. complete only after four candidates are `completed` or `inherited`.

Never continue later batches after a failure.

- [ ] **Step 6: Implement lineage retry**

`retry_job` accepts only `failed` or `canceled` schema-3 parents, preserves prompt/profile/workflow/parallelism/batches/seeds/advanced/references, creates a new job ID and `parent_job_id`, and copies the parent's frozen input bytes into the new job.

For each parent candidate:

- complete artifacts all revalidate: new candidate status is `inherited` with lineage;
- complete raw batch but missing processed artifacts: preserve verified raw lineage and schedule CPU postprocessing only;
- any missing raw in a batch: schedule the entire batch for rerun with the same seed.

Do not mutate the parent state or artifacts.

- [ ] **Step 7: Run execution/retry and regression gates**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\generation backend\tests\comfy backend\tests\model_profiles backend\tests\processing -q
git diff --check
```

Expected: all pass using fake Comfy only.

- [ ] **Step 8: Commit**

```powershell
git add backend/src/aimctexturegen/generation backend/src/aimctexturegen/inference/service.py backend/tests/generation
git commit -m "feat: execute generation batches"
```

---

### Task 9: Coordinate one global job, confirmed cancellation, events, and restart

**Files:**
- Create: `backend/src/aimctexturegen/generation/events.py`
- Create: `backend/src/aimctexturegen/generation/coordinator.py`
- Modify: `backend/src/aimctexturegen/jobs/recovery.py`
- Modify: `backend/src/aimctexturegen/main.py`
- Test: `backend/tests/generation/test_events.py`
- Test: `backend/tests/generation/test_coordinator.py`
- Modify: `backend/tests/jobs/test_recovery.py`
- Modify: `backend/tests/api/test_recovery.py`

**Interfaces:**
- Consumes: `GenerationService`, repository/store canonical scans, managed inference, `ComfyClient.queue_snapshot/interrupt`, and schema-3 transitions.
- Produces: fixed `GenerationCoordinator`, `JobEventBroker`, correct lifespan ordering, and orphan prompt cleanup.

- [x] **Step 1: Write application-wide slot RED tests**

```python
first = coordinator.create_job(PROJECT_A, command())
with pytest.raises(GenerationError) as captured:
    coordinator.create_job(PROJECT_B, command())
assert captured.value.code == "GENERATION_JOB_CONFLICT"
assert captured.value.current_job == (PROJECT_A, first.request.job_id)
```

Run two threads through a barrier and prove exactly one creates a job. The scan must include queued jobs from all projects and must not trust a stale/empty SQLite index.

- [x] **Step 2: Write start/continue/background RED tests**

Assert `create_job` returns queued without GPU work; `start` returns promptly after scheduling one daemon worker; a second `start` is idempotent for the same job; an unrelated job start conflicts; worker commits are published through the broker.

- [x] **Step 3: Write confirmed-cancel RED tests**

```python
requested = coordinator.cancel(PROJECT_ID, JOB_ID)
assert requested.state.cancel_requested_at is not None
assert requested.state.status in {"generating", "postprocessing"}
assert fake_client.last_interrupt_prompt_id == PROMPT_ID
wait_until(lambda: store.load(PROJECT_ID, JOB_ID).state.status == "canceled")
```

Prove the slot stays occupied until the prompt disappears from both running and pending queue lists. If confirmation times out, stop only the identity-matching managed child; if safe stop fails, persist `CANCEL_CONFIRMATION_FAILED` and do not report a successful cancellation.

- [x] **Step 4: Run coordinator tests and verify RED**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\generation\test_events.py backend\tests\generation\test_coordinator.py backend\tests\jobs\test_recovery.py -q
```

Expected: import failures because coordinator/events do not exist.

- [x] **Step 5: Implement the revision broker**

```python
class JobEventBroker:
    def publish(self, project_id: UUID, job_id: UUID, revision: int) -> None: ...
    def wait_for_change(
        self,
        project_id: UUID,
        job_id: UUID,
        after_revision: int,
        timeout: float,
    ) -> int | None: ...
```

Use `threading.Condition`. The broker is only a wake-up hint; subscribers always reload the committed job from `JobStore`.

- [x] **Step 6: Implement coordinator ownership and cancellation**

Use one `threading.RLock`, one optional worker thread, one cancellation event, and one active prompt ID. The worker is the only caller of `GenerationService.run_job`. The cancel request persists intent before interrupt. A queued job with no owned prompt may be confirmed canceled immediately. For active work, confirm absence from Comfy queue; after timeout call managed safe stop and recheck. Only then call `confirm_canceled`.

If both prompt confirmation and safe owned-process stop fail, persist
`CANCEL_CONFIRMATION_FAILED` on the active state while retaining its
nonterminal top-level status and occupied slot. The schema-3 validator permits
this one state-level failure only when `cancel_requested_at` is present. A
later idempotent cancel can confirm stop and transition to `canceled`; do not
release the slot merely because cancellation confirmation failed.

`shutdown()` stops accepting commands and signals the worker, but it does not translate application shutdown into user cancellation. Leave active state nonterminal so startup recovery records `JOB_INTERRUPTED`.

- [x] **Step 7: Update recovery and lifespan order**

Startup order:

1. guard project root;
2. recover active schema-3 jobs to `JOB_INTERRUPTED`;
3. rebuild index;
4. construct/activate coordinator;
5. do not start queued work.

Before a new job starts, inspect the recovered job's persisted prompt ID. If it remains in the managed Comfy queue, targeted interrupt and confirmation are required; fall back to safe managed stop. Shutdown coordinator before inference manager and index close.

- [x] **Step 8: Run coordinator, recovery, and full backend tests**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\generation\test_coordinator.py backend\tests\generation\test_events.py backend\tests\jobs\test_recovery.py backend\tests\api\test_recovery.py backend\tests\integration\test_restart_recovery.py -q
.\.venv\Scripts\python -W error -m pytest backend\tests -q
git diff --check
```

Expected: all pass; queued jobs remain queued across app restart; active jobs fail without losing completed artifacts.

- [x] **Step 9: Commit**

```powershell
git add backend/src/aimctexturegen/generation backend/src/aimctexturegen/jobs/recovery.py backend/src/aimctexturegen/main.py backend/tests/generation backend/tests/jobs/test_recovery.py backend/tests/api/test_recovery.py
git commit -m "feat: coordinate one generation job"
```

---

### Task 10: Expose references, generation commands, artifacts, and WebSocket snapshots

**Files:**
- Create: `backend/src/aimctexturegen/api/references.py`
- Create: `backend/src/aimctexturegen/api/generation.py`
- Modify: `backend/src/aimctexturegen/main.py`
- Test: `backend/tests/api/test_references.py`
- Test: `backend/tests/api/test_generation.py`
- Test: `backend/tests/api/test_generation_websocket.py`
- Modify: `backend/tests/api/test_jobs.py`
- Modify: `backend/tests/api/test_services_and_errors.py`

**Interfaces:**
- Consumes: `ReferenceService`, `GenerationCoordinator`, artifact resolver, broker, and the stable API error envelope.
- Produces: the exact Phase 5 HTTP/WebSocket surface below.

- [ ] **Step 1: Write reference API RED tests**

Lock these endpoints:

```text
GET    /api/projects/{project_id}/references/pack
GET    /api/projects/{project_id}/references/pack/image?relative_path=...
GET    /api/projects/{project_id}/references?kind=style|structure
POST   /api/projects/{project_id}/references?kind=style|structure
GET    /api/projects/{project_id}/references/{kind}/{reference_id}/image
DELETE /api/projects/{project_id}/references/{kind}/{reference_id}
```

`POST` accepts raw `image/png` bytes, not a local path or filename, and reads at most `16 MiB + 1` from `request.stream()`. Test lying/missing Content-Length, fragmented body, disconnect, wrong MIME type, plus-one byte, invalid UUID/kind, and stable path-free errors.

- [ ] **Step 2: Write generation command/artifact RED tests**

Lock:

```text
GET  /api/projects/{project_id}/generation-options
GET  /api/generation/current
POST /api/projects/{project_id}/jobs
POST /api/projects/{project_id}/jobs/{job_id}/start
POST /api/projects/{project_id}/jobs/{job_id}/cancel
POST /api/projects/{project_id}/jobs/{job_id}/retry
GET  /api/projects/{project_id}/jobs/{job_id}
GET  /api/projects/{project_id}/jobs/{job_id}/candidates/{candidate_index}/artifacts/{artifact_kind}
```

The create body contains only target, description, negative prompt, resolution, parallelism, reference selections, denoise, and style weight. It cannot supply seeds, compiled prompts, workflow IDs, sampler, steps, CFG, LoRA weight, artifact paths, job ID, or timestamps. `GET /api/generation/current` returns either `null` or `{project_id, job_id, status}` from the coordinator's canonical scan; the UI uses it after `GENERATION_JOB_CONFLICT`.

- [ ] **Step 3: Write WebSocket RED tests**

Lock:

```text
WS /api/projects/{project_id}/jobs/{job_id}/events
```

The first business message is `{"type":"snapshot","revision":N,"job":...}` loaded from disk. Later snapshots have strictly greater revision. Heartbeats use `{"type":"heartbeat"}` and never mutate state. A disconnect does not cancel. Test missing/corrupt job closes with a controlled code and no path details.

- [ ] **Step 4: Run API tests and verify RED**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\api\test_references.py backend\tests\api\test_generation.py backend\tests\api\test_generation_websocket.py -q
```

Expected: route/import failures.

- [ ] **Step 5: Implement thin reference and generation routes**

Transport models use `extra="forbid", strict=True`. Route handlers parse canonical UUIDs, map arrays to tuples, call one service method, and convert typed errors. They do not open project paths, decode images, compile workflows, or update state directly.

`generation-options` returns missing eligible targets, verified profile identity, defaults, fixed candidate count 4, allowed parallelism, and measured resource hints from the verified profile evidence. Before Task 14 promotion, automated tests inject a verified test profile/evidence.

- [ ] **Step 6: Implement controlled artifact responses**

Use the artifact resolver and return:

- PNG kinds with `Content-Type: image/png`, immutable ETag equal to SHA-256, and exact bounded bytes;
- report with `Content-Type: application/json`;
- `404 ARTIFACT_NOT_AVAILABLE` for a valid but absent kind;
- `409 ARTIFACT_INTEGRITY_ERROR` for lineage/hash mismatch.

Never redirect or expose a filesystem path.

- [ ] **Step 7: Implement durable-snapshot WebSocket**

On connect, load/send current job. Then call:

```python
changed_revision = await asyncio.to_thread(
    broker.wait_for_change,
    project_id,
    job_id,
    last_revision,
    1.0,
)
```

On a higher hint, reload and send the committed detail. Send heartbeat on a bounded idle interval. If revisions skip, sending the latest full snapshot is correct.

- [ ] **Step 8: Run API and backend regression gates**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\api -q
.\.venv\Scripts\python -W error -m pytest backend\tests -q
git diff --check
```

Expected: all pass. Existing legacy list/detail remains readable, while new create calls produce schema 3.

- [ ] **Step 9: Commit**

```powershell
git add backend/src/aimctexturegen/api backend/src/aimctexturegen/main.py backend/tests/api
git commit -m "feat: expose generation api"
```

---

### Task 11: Implement strict frontend generation contracts and steps 2–4

**Files:**
- Create: `frontend/src/generation/types.ts`
- Create: `frontend/src/generation/api.ts`
- Create: `frontend/src/generation/GenerationWizard.tsx`
- Create: `frontend/src/generation/TargetStep.tsx`
- Create: `frontend/src/generation/ReferenceStep.tsx`
- Create: `frontend/src/generation/GenerationStep.tsx`
- Create: `frontend/src/generation/api.test.ts`
- Create: `frontend/src/generation/GenerationWizard.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: Phase 5 HTTP response contracts and current loaded `ProjectManifest`/`CoverageReport`.
- Produces: a strict client and guided target/reference/configuration flow that creates then starts one schema-3 job.

- [ ] **Step 1: Write strict parser/request RED tests**

```typescript
it("parses schema 3 batches and rejects a missing candidate index", async () => {
  respondWith(validGenerationJob());
  const job = await getGenerationJob(projectId, jobId);
  expect(job.request.executionBatches[0].candidateIndices).toEqual([0, 1]);
  respondWith(invalidGenerationJobWithDuplicateCandidate());
  await expect(getGenerationJob(projectId, jobId)).rejects.toMatchObject({
    code: "INVALID_API_RESPONSE",
  });
});
```

Test 0-style create body, pack/upload discriminated references, optional structure, strict four candidates, artifact URL encoding, create→start order, and no start request if create fails.

- [ ] **Step 2: Run frontend API tests and verify RED**

```powershell
Push-Location frontend
try {
  npm test -- src/generation/api.test.ts
}
finally {
  Pop-Location
}
```

Expected: test/import failure because generation client files do not exist.

- [ ] **Step 3: Implement strict generation types and client**

Use discriminated TypeScript unions:

```typescript
export type ReferenceSelection =
  | { readonly source: "pack"; readonly relativePath: string }
  | { readonly source: "upload"; readonly referenceId: string };

export interface CreateGenerationInput {
  readonly targetSemanticId: string;
  readonly userDescription: string;
  readonly userNegativePrompt: string;
  readonly resolution: 16 | 32 | 64;
  readonly parallelism: 1 | 2 | 4;
  readonly styleReferences: readonly ReferenceSelection[];
  readonly structureReference: ReferenceSelection | null;
  readonly denoise: number | null;
  readonly styleWeight: number | null;
}
```

All server payloads are runtime-validated. Keep legacy root `api.ts` parsing supported; update its job union instead of treating schema 2/3 as schema 1.

- [ ] **Step 4: Write guided-flow RED tests**

```typescript
it("defaults to missing eligible targets and keeps advanced controls conditional", async () => {
  renderWizard();
  expect(screen.getByRole("option", { name: /Deepslate/ })).toBeVisible();
  expect(screen.queryByLabelText("风格强度")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("重绘强度")).not.toBeInTheDocument();
  await user.click(screen.getByLabelText("Stone 风格参考"));
  expect(screen.getByLabelText("风格强度")).toBeVisible();
});
```

Cover search, back navigation before creation, 0–8 enforcement, style upload/list/delete, optional structure, fixed-four copy, 1/2/4 resource hint copy, read-only profile, negative prompt advanced area, and create/start sequence.

- [ ] **Step 5: Implement steps 2–4 as focused components**

`GenerationWizard` owns only form step/current job state. `TargetStep` receives missing eligible targets. `ReferenceStep` receives/upload references and form values. `GenerationStep` exposes resolution/parallelism and a `<details>` advanced region; denoise renders only with structure, style weight only with at least one style. Seed is not editable and is shown only after creation.

No component reads `File.path`, filesystem APIs, or ComfyUI URLs. Do not add adoption/export buttons.

- [ ] **Step 6: Mount the wizard without expanding `App.tsx` business logic**

Pass selected project ID, manifest, coverage, and a job-list refresh callback. Existing import/recovery/project history stays operational. Use functional CSS sufficient for keyboard access and normal desktop layout; do not perform final checkbox/spacing/visual-system polish.

- [ ] **Step 7: Run focused and full frontend gates**

```powershell
Push-Location frontend
try {
  npm test -- src/generation/api.test.ts src/generation/GenerationWizard.test.tsx
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  npm test
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  npm run build
}
finally {
  Pop-Location
}
```

Expected: all tests and TypeScript/Vite build pass.

- [ ] **Step 8: Commit**

```powershell
git add frontend/src/generation frontend/src/App.tsx frontend/src/api.ts frontend/src/styles.css
git commit -m "feat: add generation configuration wizard"
```

---

### Task 12: Stream live candidates and expose continue, cancel, retry, and legacy states

**Files:**
- Create: `frontend/src/generation/useJobEvents.ts`
- Create: `frontend/src/generation/CandidateStep.tsx`
- Create: `frontend/src/generation/useJobEvents.test.tsx`
- Create: `frontend/src/generation/CandidateStep.test.tsx`
- Modify: `frontend/src/generation/GenerationWizard.tsx`
- Modify: `frontend/src/generation/GenerationWizard.test.tsx`
- Modify: `frontend/src/JobHistory.tsx`
- Modify: `frontend/src/JobHistory.test.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: generation client from Task 11 and HTTP/WebSocket surface from Task 10.
- Produces: monotonic snapshot subscription, four incremental candidate cards, artifact previews/reports, and user commands.

- [x] **Step 1: Write WebSocket reconnection/revision RED tests**

```typescript
it("accepts only higher revisions and falls back to HTTP after a gap", async () => {
  const { result } = renderHook(() => useJobEvents(projectId, jobId));
  emitSnapshot(3);
  emitSnapshot(2);
  expect(result.current.job?.state.revision).toBe(3);
  disconnectSocket();
  await waitFor(() => expect(getJobMock).toHaveBeenCalled());
});
```

Test first snapshot, heartbeat ignore, higher revisions only, malformed message, disconnect/reconnect backoff, component unmount cleanup, and browser disconnect not calling cancel.

- [x] **Step 2: Write candidate/action RED tests**

Cover:

- completed candidates appear while later candidates are pending/generating;
- final/nearest/tile tabs and seam score/report;
- read-only batch seed/position;
- queued job shows “继续任务”;
- active/cancel-requested job shows cancel state and retains completed cards;
- failed/canceled job shows retry when retryable;
- OOM/conflict/interrupted messages and recommended actions;
- legacy schema-1/2 history is labeled read-only and has no start command;
- no adoption/export control exists.

- [x] **Step 3: Run candidate tests and verify RED**

```powershell
Push-Location frontend
try {
  npm test -- src/generation/useJobEvents.test.tsx src/generation/CandidateStep.test.tsx src/JobHistory.test.tsx
}
finally {
  Pop-Location
}
```

Expected: import/render failures because live components do not exist.

- [x] **Step 4: Implement monotonic subscription**

```typescript
export function useJobEvents(
  projectId: string,
  jobId: string | null,
): {
  readonly job: GenerationJobDetail | null;
  readonly connected: boolean;
  readonly error: ApiError | null;
  readonly refresh: () => Promise<void>;
}
```

Create the WebSocket only for a schema-3 current job. Parse every message through the strict parser. On invalid data, gap suspicion, or disconnect, HTTP-refresh the durable job before reconnecting with bounded exponential backoff. Abort timers/socket/fetch on project/job change and unmount.

- [x] **Step 5: Implement four stable candidate cards**

Render four cards keyed by candidate index from job creation onward. For available artifacts, use controlled API URLs. Fetch report JSON through the strict client, not by embedding a local path. Preserve a completed/inherited card during cancellation or later failure.

- [x] **Step 6: Wire user actions and conflict recovery**

Create then start; if start fails, keep the queued/failed job visible. `continue` calls only start. `cancel` shows durable request state until terminal confirmation. `retry` creates a new lineage job and switches the wizard to it. On `GENERATION_JOB_CONFLICT`, load the current job reference returned by the API and offer view/cancel, never submit a second hidden job.

- [x] **Step 7: Run full frontend gate**

```powershell
Push-Location frontend
try {
  npm test
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  npm run build
}
finally {
  Pop-Location
}
git diff --check
```

Expected: all pass. Do one targeted normal-desktop screenshot inspection only; do not run the full Phase 6 size/polish matrix.

- [x] **Step 8: Commit**

```powershell
git add frontend/src/generation frontend/src/JobHistory.tsx frontend/src/JobHistory.test.tsx frontend/src/styles.css
git commit -m "feat: display live generation candidates"
```

---

### Task 13: Prove the synthetic end-to-end, failure, cancellation, and restart contracts

**Status: complete.** RED/GREEN evidence and final gate results are recorded in
`.superpowers/sdd/2026-08-03-phase-5-four-candidate-generation/task-13-report.md`.

**Files:**
- Create: `backend/tests/integration/test_generation_flow.py`
- Create: `backend/tests/integration/test_generation_cancel.py`
- Create: `backend/tests/integration/test_generation_restart.py`
- Modify: `backend/tests/fakes/comfy_server.py`
- Modify: `tools/Generate-SyntheticPack.ps1`
- Modify: `backend/tests/tools/test_synthetic_pack_generator.py`

**Interfaces:**
- Consumes: full FastAPI service graph with fake ComfyUI and generated project-owned assets.
- Produces: repeatable no-GPU evidence that Phase 5 invariants hold across real HTTP/WS/service boundaries.

- [x] **Step 1: Extend the synthetic pack generator with opt-in Phase 5 reference variety**

Keep the existing default ZIP bytes/hash unchanged. Add `-Phase5` to generate a separate ignored ZIP containing only project-created flat/checker RGB PNGs: covered `stone.png`, an unknown square custom PNG, and missing eligible `deepslate`. Add a deterministic script test and explicitly assert no real asset file is read.

- [x] **Step 2: Write complete success-flow RED integration**

The test imports the synthetic ZIP through the real API, lists targets/references, uploads optional synthetic references, creates/starts a schema-3 job, observes WebSocket revisions, lets fake Comfy return four generated PNGs, reads all artifact kinds, and asserts completion.

Record complete path→SHA-256 maps for `source/` and `pack/` before create and after completion; assert exact equality.

- [x] **Step 3: Write atomic failure/cancel RED integration**

Parameterized fake behaviors prove:

- output count `0`, `3`, or `5` rejects the whole batch;
- one corrupt or wrong-size output publishes no raw member of that batch;
- disconnect, timeout, queue rejection, execution failure, and OOM persist exact codes;
- cancellation after candidate 0 completion retains candidate 0, interrupts the current prompt, confirms queue absence, cancels the remainder, and leaves `pack/` unchanged;
- a second project cannot create a queued job until the first is terminal.

- [x] **Step 4: Write restart/retry RED integration**

Use two application service graphs over one temporary project root:

- queued remains queued and blocks a new create;
- generating/postprocessing becomes failed `JOB_INTERRUPTED`;
- completed artifacts survive;
- an orphan prompt is interrupted before a new start;
- retry inherits completed candidates, postprocesses complete raw, and reruns only batches with incomplete raw;
- SQLite deletion/rebuild changes none of the canonical records.

- [x] **Step 5: Run integration tests and verify RED**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\integration\test_generation_flow.py backend\tests\integration\test_generation_cancel.py backend\tests\integration\test_generation_restart.py -q
```

Expected: failures expose any missing full-stack wiring; do not weaken the assertions to fit implementation.

- [x] **Step 6: Make the smallest wiring/fake corrections required by the tests**

Corrections may touch only already-owned Phase 5 modules and `FakeComfyServer`. Preserve route/service boundaries. Every fake response must remain protocol-shaped; do not add test-only branches to production code.

- [x] **Step 7: Run full automated gates**

```powershell
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .\tools\Generate-SyntheticPack.ps1 -Phase5
.\.venv\Scripts\python -W error -m pytest backend\tests --cov=aimctexturegen --cov-report=term-missing
Push-Location frontend
try {
  npm test
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  npm run build
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
  Pop-Location
}
git diff --check
git status --short
```

Expected: all pass; only intended tracked changes plus preserved user-owned `temp/` appear.

- [x] **Step 8: Commit**

```powershell
git add backend/tests/integration backend/tests/fakes/comfy_server.py tools/Generate-SyntheticPack.ps1 backend/tests/tools/test_synthetic_pack_generator.py
git commit -m "test: cover generation end to end"
```

---

### Task 14: Qualify profile v2 on the real GPU, test ignored real packs, and close Phase 5

**Files:**
- Create: `backend/src/aimctexturegen/model_profiles/smoke_v2.py`
- Create: `backend/tests/model_profiles/test_smoke_v2.py`
- Create: `tools/Invoke-Phase5Smoke.ps1`
- Create: `tools/Prepare-Phase5ManualPack.ps1`
- Create: `docs/evidence/phase-5/evidence.json`
- Modify: `manifests/model-profiles/sdxl-mapchip-ipadapter-v2.json`
- Modify: `docs/MODEL_PROFILES.md`
- Modify: `docs/TESTING.md`
- Modify: `docs/superpowers/plans/2026-08-03-phase-5-four-candidate-generation.md`
- Modify: `docs/superpowers/plans/2026-07-21-aimc-texturegen-mvp-roadmap.md`
- Modify: `ONBOARDING.md`
- Modify if a repeatable new pitfall exists: `AGENTS.md`

**Interfaces:**
- Consumes: complete Phase 5, ignored managed runtime/models, and ignored user-provided packs.
- Produces: verified profile-v2 manifest/evidence, exact manual test procedure/results, complete gates, and a truthful Phase 6 handoff.

- [ ] **Step 1: Write qualification-tool RED tests**

```python
def test_v2_smoke_matrix_covers_four_variants_and_three_batch_sizes():
    plan = build_smoke_plan()
    assert {item.variant for item in plan} == {
        "text2img-no-style", "text2img-style",
        "img2img-no-style", "img2img-style",
    }
    assert {item.batch_size for item in plan} == {1, 2, 4}

def test_evidence_rejects_absolute_paths_and_image_bytes():
    payload = valid_evidence_dict()
    payload["machine"]["gpu_name"] = "C:/private/model"
    with pytest.raises(ValidationError):
        SmokeEvidenceV2.model_validate(payload)
```

Evidence records runtime/profile/workflow digests, redacted machine/GPU/driver identity, batch/variant, output count/order hashes, postprocess status, elapsed seconds, peak VRAM MiB, peak process/system RAM MiB, and success/failure. It contains no prompt text, reference name/content, output image, token/header, or absolute path.

- [ ] **Step 2: Implement the real smoke entry without changing product verification rules**

`Invoke-Phase5Smoke.ps1` calls the repository `.venv`, reuses the managed installation, verifies all receipts/hashes, starts managed ComfyUI, runs all four variants and batch sizes `1/2/4`, postprocesses every output into `runtime/smoke/phase-5/`, records bounded metrics, performs stop→start→stop audit, and writes ignored full evidence plus redacted candidate evidence.

Qualification may load `candidate_unverified` with `require_verified=False`; normal product APIs must still reject it until promotion.

- [ ] **Step 3: Run the real GPU qualification**

Preconditions:

```powershell
Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 -LocalPort 8188 -ErrorAction SilentlyContinue
git check-ignore .\runtime\smoke\phase-5\
```

Then:

```powershell
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .\tools\Invoke-Phase5Smoke.ps1
```

Expected: four condition combinations succeed, batch 1/2/4 produce exact ordered counts, every output postprocesses, restart audit passes, and the script prints a final `PHASE5_SMOKE_COMPLETED` summary. If any matrix cell fails, leave v2 `candidate_unverified`, record the controlled failure locally, fix through RED/GREEN tests, and rerun the entire matrix.

- [ ] **Step 4: Promote v2 only after all evidence passes**

Change only `support_state` to `verified` and any profile defaults/64-prefix wording proven by the smoke. Recompute the canonical profile digest, rerun exact workflow hashes, and generate `docs/evidence/phase-5/evidence.json` from the redacted model. Add a test that product binding now accepts v2 and v1 bytes are still unchanged.

- [ ] **Step 5: Prepare and hash-audit the ignored real packs**

```powershell
git check-ignore .\runtime\manual-test-packs\phase-5\legacy-converted.zip
git check-ignore .\runtime\manual-test-packs\phase-5\third-party.zip
git check-ignore .\runtime\manual-test-packs\phase-5\vanilla-latest.zip
$Before = Get-ChildItem .\runtime\manual-test-packs\phase-5\*.zip |
  Get-FileHash -Algorithm SHA256
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .\tools\Prepare-Phase5ManualPack.ps1
```

The preparation script creates an ignored derived ZIP, copies every member except root `assets/minecraft/textures/block/deepslate.png`, verifies `stone.png` remains, verifies overlays/member count except the one removal, and never overwrites any original ZIP.

- [ ] **Step 6: Perform the real-pack API/UI acceptance**

Use the derived format-34 pack as the positive project:

1. import it and confirm format 34, overlay files preserved, deepslate missing, stone eligible as pack style;
2. execute prompt-only, style-only, structure-only, and style+structure jobs;
3. cover parallelism 1, 2, and 4 across those jobs, canceling/finishing the current job before creating the next;
4. confirm four incremental candidates, all artifact views, seam reports, read-only batch seeds, and no `pack/` mutation;
5. cancel one run after at least one completed candidate and confirm that candidate remains;
6. restart with a queued job and confirm explicit Continue; restart one active test and confirm `JOB_INTERRUPTED`;
7. force or use a controlled fake OOM path to judge the Chinese message/actions;
8. import the format-32 pack and confirm readable unsupported-format rejection;
9. import the missing-primary-format pack and confirm no guessing from min/max.

Before and after, compare `$Before` with new original ZIP hashes. Do not capture or commit screenshots containing real textures.

- [ ] **Step 7: Give the user the focused manual browser procedure**

The implementing agent performs automated and non-dense checks, then asks the user to verify only:

- the five-step flow is operable at normal Windows desktop width;
- real progress/candidates appear incrementally;
- completed candidates survive cancel;
- OOM/conflict/interrupted messages are understandable;
- four candidates and reference influence are visually plausible;
- DevTools has no application-origin error.

Provide exact click order, expected text/status after each click, how to distinguish browser-extension errors, and what log/API response to save on failure. Do not require the `400/600/900 px` matrix unless Task 11/12 changed the relevant responsive breakpoint behavior and an observed defect warrants it.

- [ ] **Step 8: Update stable documentation with actual results**

`docs/MODEL_PROFILES.md` receives only verified measured v2 facts. `docs/TESTING.md` receives commands that were actually run, including fake CI, Phase 5 real smoke, real-pack privacy rules, and focused manual steps. Check all completed task boxes in this plan. Set the roadmap Phase 5 state to implementation/qualification complete and `ONBOARDING.md` to the exact next Phase 6 entry. Add an `AGENTS.md` pitfall only if it is general, repeatable, and not already captured.

- [ ] **Step 9: Run final gates and inspect tracked scope**

```powershell
.\.venv\Scripts\python -m pip check
.\.venv\Scripts\python -W error -m pytest backend\tests --cov=aimctexturegen --cov-report=term-missing
Push-Location frontend
try {
  npm test
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  npm run build
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
  Pop-Location
}
git diff --check
git status --short
git ls-files runtime
git ls-files | Select-String -Pattern '\.(zip|png|safetensors|7z)$'
```

Expected: dependency check, all backend/frontend tests, and build pass; no runtime/real pack/model/generated PNG is tracked; only intentional project-owned UI/static PNGs, if any already existed, appear in the extension audit.

- [ ] **Step 10: Review, commit, and stop before integration**

Use `superpowers:requesting-code-review`, address findings through `superpowers:receiving-code-review`, then run `superpowers:verification-before-completion`.

```powershell
git add backend/src/aimctexturegen/model_profiles/smoke_v2.py backend/tests/model_profiles/test_smoke_v2.py tools/Invoke-Phase5Smoke.ps1 tools/Prepare-Phase5ManualPack.ps1 docs/evidence/phase-5/evidence.json manifests/model-profiles/sdxl-mapchip-ipadapter-v2.json docs/MODEL_PROFILES.md docs/TESTING.md docs/superpowers/plans/2026-08-03-phase-5-four-candidate-generation.md docs/superpowers/plans/2026-07-21-aimc-texturegen-mvp-roadmap.md ONBOARDING.md AGENTS.md
git commit -m "docs: complete phase 5 qualification"
```

Do not merge or push until the user confirms the manual browser results and explicitly asks for integration.

---

## Task dependency graph

```text
Task 1 versioned manifests
  ├── Task 4 creation/profile binding
  └── Task 5 v2 workflows

Task 2 schema 3/store
  ├── Task 3 references/snapshots
  ├── Task 4 creation
  ├── Task 7 artifacts
  └── Task 9 recovery/coordinator

Task 3 + Task 4 + Task 5 + Task 6 + Task 7
  └── Task 8 execution/retry
        └── Task 9 coordinator
              └── Task 10 API/WebSocket
                    ├── Task 11 wizard configuration
                    └── Task 12 live candidates/actions

Tasks 1–12
  └── Task 13 synthetic integration
        └── Task 14 real qualification, manual acceptance, handoff
```

Tasks 1 and 2 may be implemented sequentially in either order, but each must pass its full regression gate before dependent tasks begin. Tasks 3, 5, and 6 are technically parallelizable after their prerequisites; because all agents share one worktree, the recommended executor should still dispatch one task at a time unless it creates isolated worktrees.

## Plan self-review checklist

Before execution begins, the plan author must confirm:

- [x] Every Phase 5 design section 1–16 maps to at least one task.
- [x] Schema-1/2 job bytes and profile-v1 bytes have explicit immutability tests.
- [x] Native batch `1/2/4`, one seed per batch, output order, and all-or-nothing raw publication are tested.
- [x] `0–8` style references and optional structure reference use the shared validator and task-local snapshots.
- [x] Product creation cannot accept seeds, sampler, steps, CFG, LoRA weight, workflow nodes, or local paths.
- [x] Global single-job conflict is decided from canonical JSON under one lock, not SQLite.
- [x] Cancel persists intent, confirms prompt absence or safe owned-process stop, then releases the slot.
- [x] Queued restart, active interruption, orphan cleanup, completed/raw preservation, and lineage retry are tested.
- [x] Candidate artifacts are served through controlled IDs and verified hashes, including inherited artifacts.
- [x] WebSocket sends committed snapshots only and reconnects through HTTP truth.
- [x] Phase 5 never writes `pack/`, adopts, exports, merges overlays, or adds unsupported formats.
- [x] All normal tests are synthetic/fake; real packs and GPU artifacts remain ignored and untracked.
- [x] Manual verification is limited to real browser/GPU/visual risks; Phase 6 retains final polish.
- [x] The plan contains no undefined interface used by a later task and no implementation placeholder.
