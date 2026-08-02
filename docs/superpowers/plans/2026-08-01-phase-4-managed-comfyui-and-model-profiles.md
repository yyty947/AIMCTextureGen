# Phase 4 Managed ComfyUI and Model Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:subagent-driven-development` when the user explicitly requests
> multi-agent execution; otherwise use `superpowers:executing-plans`. Apply
> `superpowers:test-driven-development` to every behavior change and
> `superpowers:verification-before-completion` before any completion claim.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install and manage one version-pinned official ComfyUI Windows NVIDIA
portable runtime and one version-pinned SDXL/mapchip/IP-Adapter model profile,
with explicit consent, safe verified downloads, fake-service CI and recorded
real text2img/img2img smoke evidence.

**Architecture:** Strict tracked manifests describe the runtime and model
profile. A repository-owned installer downloads into ignored staging paths,
validates every byte and archive member, and atomically publishes versioned
runtime/model artifacts. `ComfyUIManager` owns one loopback child process and
`ComfyClient` owns only the HTTP/WebSocket protocol. Profile-specific workflow
bindings translate generic inputs into fixed API workflow JSON. Existing
projects and Phase 2 processing remain independent of ComfyUI.

**Tech Stack:** Python 3.12 backend, FastAPI 0.139.2, Pydantic 2.13.4,
HTTPX 0.28.1, websockets 16.1.1, py7zr 1.1.3, React 19, TypeScript, Vitest,
pytest 9.1.1, official ComfyUI Windows NVIDIA portable v0.29.2.

**Approved design:**
[`2026-08-01-phase-4-managed-comfyui-and-model-profiles-design.md`](../specs/2026-08-01-phase-4-managed-comfyui-and-model-profiles-design.md)

**Architecture decision:**
[`ADR-0002`](../../adr/0002-managed-comfyui-runtime-and-versioned-model-profiles.md)

## Global constraints

- Work only on `codex/phase-4-managed-comfyui`; do not implement on `master`.
- Preserve the untracked `temp/` directory and all unrelated user changes.
- The selected runtime is the official
  `ComfyUI_windows_portable_nvidia.7z` v0.29.2 archive. Do not substitute the
  CUDA 12.6, AMD, Intel, Desktop or source-install variants.
- ComfyUI uses its embedded Python/PyTorch. Never run its package installer
  against the backend `.venv`, global Python, Conda or user environments.
- Do not scan for or modify a user-managed ComfyUI or model directory.
- No network or filesystem mutation occurs during read-only status/plan
  inspection or ordinary application startup.
- A download starts only after an exact manifest-plan digest and all component
  licenses have been explicitly accepted.
- Never trust names from an archive, HTTP response or ComfyUI response as
  filesystem paths without strict validation.
- All installed runtime, models, nodes, caches, logs and smoke outputs stay
  below ignored `runtime/`; none are committed.
- No Mojang/Microsoft texture, model JSON or game asset is used for tests or
  real smoke input. Generate synthetic inputs from project-owned code.
- Runtime/model/workflow changes require new manifest/profile versions; no
  silent update or mutable `latest` identity.
- Phase 4 does not dequeue or execute the durable four-candidate jobs, invoke
  Phase 2 postprocessing from inference, adopt a candidate or export a pack.
- Real GPU/model tests never run in ordinary CI or as part of the default
  backend test command.
- Error handling never changes user parameters or chooses a different runtime,
  model, workflow, resolution or concurrency.
- API routes remain mapping-only. Download, extraction, process and protocol
  behavior lives in injected services.
- Every independent task ends with its focused tests, documentation alignment
  where applicable and an independently reviewable commit.

## Locked candidate inputs

Implementation starts with these reviewed candidate pins:

```text
ComfyUI:
  release       v0.29.2
  commit        322122449c9d2ba8b8df1bb517364527dd0615f1
  asset bytes   2103175457
  sha256        e7a39a817002d85b4fb2d4f6bd176c10d104a0d04031f99b9d8b7b1fd920c6fc

SDXL Base:
  revision      462165984030d82259a11f4367a4eed129e94a7b
  bytes         6938078334
  sha256        31e35c80fc4829d14f90153f4c74cd59c90b779f6afe05a74cd6120b893f7e5b

mapchipLora:
  revision      7ff7d9e43c9c364eb25ca283851565b7c5778dbf
  bytes         912555676
  sha256        9a047fce0fd45e60aaee6bcf6ec465ba34397366b10a34bfb2175f5e129ac1ae

IP-Adapter SDXL ViT-H:
  revision      018e402774aeeddd60609b4ecdb7e298259dc729
  bytes         698391064
  sha256        ebf05d918348aec7abb02a5e9ecef77e0aaea6914a5c4ea13f50d45eb1681831

CLIP ViT-H image encoder:
  revision      018e402774aeeddd60609b4ecdb7e298259dc729
  bytes         2528373448
  sha256        6ca9667da1ca9e0b0f75e46bb030f7e011f44f86cbfb8d5a36590fcd7507b030

ComfyUI_IPAdapter_plus:
  commit        a0f451a5113cf9becb0847b92884cb10cbdec0ef
  bytes         306422
  sha256        c6c49c82aa65cb96b93bdf9f9b547f9c95310a2668a7a9aaa0285cccf4590347
```

These are not “supported” until Task 10 passes. If an upstream byte differs,
stop; investigate and update the design/manifest with user approval rather
than accepting the new byte.

## Planned file map

```text
manifests/runtimes/
    Tracked strict runtime manifests.
manifests/model-profiles/
    Tracked strict profile/artifact/capability manifests.
workflows/sdxl-mapchip-ipadapter-v1/
    Fixed API-format text2img and img2img workflow templates.

backend/src/aimctexturegen/comfy/errors.py
    Stable setup/install/process/transport domain errors.
backend/src/aimctexturegen/comfy/manifests.py
    Strict manifest and canonical digest models.
backend/src/aimctexturegen/comfy/registry.py
    Read-only tracked manifest/workflow registry.
backend/src/aimctexturegen/comfy/environment.py
    Windows/NVIDIA/disk inspection without mutation.
backend/src/aimctexturegen/comfy/downloads.py
    Bounded resumable streaming downloader.
backend/src/aimctexturegen/comfy/archives.py
    Safe 7z member validation and staging extraction.
backend/src/aimctexturegen/comfy/install_state.py
    Atomic install operations, receipts and selection state.
backend/src/aimctexturegen/comfy/installer.py
    Consent-bound runtime/model/custom-node installation.
backend/src/aimctexturegen/comfy/process.py
    Owned Windows child lifecycle and managed logs.
backend/src/aimctexturegen/comfy/client.py
    Generic ComfyUI HTTP/WebSocket protocol.
backend/src/aimctexturegen/comfy/manager.py
    Runtime integrity, start/stop and readiness orchestration.

backend/src/aimctexturegen/model_profiles/models.py
    Generic profile inputs and frozen durable profile binding.
backend/src/aimctexturegen/model_profiles/registry.py
    Capability lookup and profile selection.
backend/src/aimctexturegen/model_profiles/workflows.py
    Workflow template validation and binding protocol.
backend/src/aimctexturegen/model_profiles/sdxl.py
    SDXL/mapchip/IP-Adapter semantic-slot compiler only.

backend/src/aimctexturegen/api/inference.py
    Thin setup/install/process/status endpoints.
frontend/src/InferenceSetup.tsx
    Focused setup/status/consent/progress panel.
tools/Invoke-Phase4Smoke.ps1
    Manual real-profile smoke entry point.
backend/tests/fakes/comfy_server.py
    Local fake HTTP/WebSocket ComfyUI.
```

File names may be split further when a module grows, but responsibilities must
not be merged into API routes or `GenerationService`.

---

### Task 1: Lock dependency manifests and installer dependencies

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `manifests/runtimes/comfyui-windows-nvidia-v0.29.2.json`
- Create: `manifests/model-profiles/sdxl-mapchip-ipadapter-v1.json`
- Create: `backend/src/aimctexturegen/comfy/__init__.py`
- Create: `backend/src/aimctexturegen/comfy/errors.py`
- Create: `backend/src/aimctexturegen/comfy/manifests.py`
- Create: `backend/src/aimctexturegen/comfy/registry.py`
- Create: `backend/tests/comfy/test_manifests.py`
- Create: `backend/tests/comfy/test_registry.py`

**Interfaces:**
- Produces strict `RuntimeManifest`, `ModelProfileManifest`,
  `ArtifactManifest`, `LicenseRecord`, `ProfileCapabilities` and
  `WorkflowRecord`.
- Produces `canonical_manifest_bytes(model) -> bytes` and
  `manifest_sha256(model) -> str`.
- Produces read-only `ManifestRegistry.load(root)`.

- [x] **Step 1: Pin the new Python dependencies**

Move `httpx==0.28.1` into production dependencies and add
`websockets==16.1.1` and `py7zr==1.1.3`. Keep the existing dev dependency
list valid and regenerate the repository environment using its documented
install command. Do not install anything with ComfyUI's Python yet.

- [x] **Step 2: Independently lock the custom-node archive**

Download the commit-specific codeload archive for
`a0f451a5113cf9becb0847b92884cb10cbdec0ef` outside the repository, calculate
its exact size and SHA-256, and require the locked `306,422` bytes and
`c6c49c82aa65cb96b93bdf9f9b547f9c95310a2668a7a9aaa0285cccf4590347`.
Inspect that its single root corresponds to that commit and record the values
in the profile manifest. Preserve the upstream commit and GPL-3.0 source link.

Do not use a mutable branch archive or infer integrity solely from the URL.

- [x] **Step 3: Write manifest RED tests**

Cover:

- exact candidate IDs, revisions, sizes, hashes, destinations and licenses;
- unknown-field rejection;
- malformed/non-lowercase SHA, negative or unsafe sizes;
- mutable source identity such as bare `main`/`latest`;
- absolute, drive, UNC, traversal, device, reserved or case-colliding
  destinations;
- duplicate artifact IDs/hashes with conflicting metadata;
- missing runtime compatibility, workflow or required capability;
- canonical serialization and digest stability independent of input key order;
- workflow path escaping the tracked workflow root.

Run:

```powershell
.\.venv\Scripts\python -m pytest backend\tests\comfy\test_manifests.py backend\tests\comfy\test_registry.py -v
```

Expected: imports fail before implementation.

- [x] **Step 4: Implement strict models and registry**

Use frozen Pydantic models with explicit `schema_version = 1`. Reuse the
repository's strict relative-path rules but add manifest-specific Windows
case-collision and allowed-root checks. Registry loading is read-only and
deterministically ordered.

Canonical bytes must not include local absolute paths or runtime status.

- [x] **Step 5: Write the two tracked manifests**

Encode every locked candidate from this plan, compatible runtime identity,
capabilities and planned workflow records. Set support state to
`candidate_unverified`. Do not invent measured VRAM or extracted-size values;
use a clearly named conservative installation headroom value and mark it as
an estimate.

- [x] **Step 6: Run focused and full manifest gates**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\comfy\test_manifests.py backend\tests\comfy\test_registry.py -v
git diff --check
```

- [x] **Step 7: Commit**

```powershell
git add backend\pyproject.toml backend\src\aimctexturegen\comfy backend\tests\comfy manifests
git commit -m "feat: lock ComfyUI runtime and SDXL profile manifests"
```

---

### Task 2: Read-only host inspection and consent-bound install plans

**Files:**
- Create: `backend/src/aimctexturegen/comfy/environment.py`
- Create: `backend/src/aimctexturegen/comfy/install_state.py`
- Create: `backend/src/aimctexturegen/comfy/installer.py`
- Create: `backend/tests/comfy/test_environment.py`
- Create: `backend/tests/comfy/test_install_plan.py`

**Interfaces:**
- Produces `EnvironmentInspector.inspect() -> EnvironmentReport`.
- Produces `Installer.inspect(runtime_id, profile_id) -> InstallPlan`.
- Produces `InstallConsent` bound to `plan_digest` and exact component IDs.
- Creates no directories during inspection.

- [x] **Step 1: Write host-inspection RED tests**

Inject command/filesystem probes rather than calling the real machine. Cover:

- supported Windows x64 with NVIDIA report;
- non-Windows, non-x64 and missing/failed `nvidia-smi`;
- driver/GPU strings returned as safe bounded values;
- free-space calculation on the actual runtime volume;
- no directory, subprocess mutation or network call during inspection;
- no absolute path in the API-safe report.

- [x] **Step 2: Write install-plan RED tests**

Cover exact byte totals, temporary headroom, unique license/component list and
deterministic plan digest. Assert stale digest, missing acceptance, extra
unknown acceptance, unsupported destination and insufficient disk fail before
creating `runtime/` or invoking a downloader.

Use fake manifests whose artifacts are a few bytes. Do not load production
model files.

- [x] **Step 3: Implement inspection and plan construction**

Execute `nvidia-smi` with a finite timeout and no shell. Treat its output as
untrusted bounded text. The environment result advises the user but never
chooses an alternate package.

Build the plan from canonical manifest digests and current installed-state
classification. The digest covers runtime/profile IDs, versions, artifacts,
sizes, hashes and licenses.

- [x] **Step 4: Add atomic install-operation records**

Below injected runtime-state roots, persist strict operation JSON with
`planned`, `downloading`, `extracting`, `installing`, `completed`, `failed`
and `canceled` states plus monotonic revision and structured error.

On startup, a nonterminal record from a prior process becomes
`failed/INSTALL_INTERRUPTED`. It does not resume automatically.

- [x] **Step 5: Run tests**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\comfy\test_environment.py backend\tests\comfy\test_install_plan.py -v
```

- [x] **Step 6: Commit**

```powershell
git add backend\src\aimctexturegen\comfy backend\tests\comfy
git commit -m "feat: add read-only inference install planning"
```

---

### Task 3: Safe resumable artifact downloader

**Files:**
- Create: `backend/src/aimctexturegen/comfy/downloads.py`
- Create: `backend/tests/comfy/test_downloads.py`
- Create: `backend/tests/fakes/artifact_server.py`

**Interfaces:**
- Produces `ArtifactDownloader.download(artifact, destination, *,
  progress, cancel) -> DownloadResult`.
- Final output is published only after exact size and SHA-256 validation.

- [x] **Step 1: Build a local controlled artifact server**

The fake server must support:

- normal bounded streaming;
- redirects;
- `Range` with valid `206`/`Content-Range`;
- intentionally ignored ranges;
- truncated and oversized bodies;
- wrong hashes;
- disconnect and timeout;
- controlled slow chunks for cancellation.

Bind only to a test loopback port and cleanly stop it in fixtures.

- [x] **Step 2: Write downloader RED tests**

Cover:

- happy path and progress monotonicity;
- resume from a valid `.part`/sidecar;
- sidecar mismatch refuses unsafe reuse;
- ignored range safely restarts from zero;
- redirect limit and HTTPS/allowed-host policy (inject policy for local test);
- connect/read timeout;
- early EOF, extra bytes, wrong `Content-Range`, size mismatch and hash
  mismatch;
- cancellation leaves no published final file and only a valid managed
  partial;
- existing correct final is idempotent;
- existing corrupt final is never overwritten during inspection;
- symlink/junction/reparse/unsafe partial or destination rejection;
- memory remains bounded relative to chunk size.

- [x] **Step 3: Implement streaming and resume**

Use an injected `httpx.Client`, finite timeouts and fixed chunks. Hash an
existing safe partial before resuming. Never send credentials or arbitrary
manifest headers across a redirect. Validate the final resolved host against
the artifact's allowed host set.

Write the resume sidecar atomically and validate it before use. Publish by
same-directory `os.replace` only after length/hash checks.

- [x] **Step 4: Run focused tests**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\comfy\test_downloads.py -v
```

- [x] **Step 5: Commit**

```powershell
git add backend\src\aimctexturegen\comfy\downloads.py backend\tests\comfy\test_downloads.py backend\tests\fakes
git commit -m "feat: add verified resumable artifact downloads"
```

---

### Task 4: Safe 7z extraction and atomic runtime publication

**Files:**
- Create: `backend/src/aimctexturegen/comfy/archives.py`
- Modify: `backend/src/aimctexturegen/comfy/installer.py`
- Create: `backend/tests/comfy/test_archives.py`
- Create: `backend/tests/comfy/test_runtime_install.py`

**Interfaces:**
- Produces `inspect_7z(archive, policy) -> ArchiveInventory`.
- Produces `extract_and_audit_7z(archive, staging, policy) -> ExtractedTree`.
- Produces idempotent runtime install/repair below an injected root.

- [x] **Step 1: Generate tiny synthetic 7z fixtures in tests**

Use `py7zr` to generate valid archives at test time. Add focused fake archive
metadata/injected readers for entries that `py7zr` cannot safely construct,
including traversal, drive/UNC/device names, duplicate case variants,
symlink-like entries and declared expansion bombs.

No binary production archive fixture is committed.

- [x] **Step 2: Write archive RED tests**

Require rejection before extraction for every unsafe member. Verify per-entry
and total-size bounds, exact single root and required portable paths. After
extraction, inject reparse points, unexpected special files, missing
executables and case collisions and require failure before publication.

Assert failures remove only the exact staging directory created by the
operation after resolving and checking it is below the injected test root.

- [x] **Step 3: Implement preflight, extraction and post-audit**

Never call `extractall` until the complete inventory passes. Extract into a
unique same-parent staging directory. Walk the full extracted tree without
following links, inspect Windows file attributes and verify the expected
portable layout.

- [x] **Step 4: Write runtime publication tests**

Cover:

- install from a verified tiny archive;
- atomically selected version record;
- status `missing`, `partial`, `ready` and `corrupt`;
- idempotent second install;
- publication failure leaves prior selected runtime intact;
- explicit repair switches only after the replacement is complete;
- no write outside the injected runtime root;
- verified archive cache cleanup after successful extraction;
- interrupted operation recovery.

- [x] **Step 5: Implement runtime installation**

Use version-plus-manifest-digest directory names. Never overwrite a selected
tree. Write and validate a strict installation receipt containing source
identity and hashes, then atomically publish the selection record.

- [x] **Step 6: Run focused tests**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\comfy\test_archives.py backend\tests\comfy\test_runtime_install.py -v
```

- [x] **Step 7: Commit**

```powershell
git add backend\src\aimctexturegen\comfy backend\tests\comfy
git commit -m "feat: safely publish managed ComfyUI runtimes"
```

---

### Task 5: Model, custom-node and profile installation

**Files:**
- Modify: `backend/src/aimctexturegen/comfy/installer.py`
- Modify: `backend/src/aimctexturegen/comfy/install_state.py`
- Create: `backend/src/aimctexturegen/model_profiles/__init__.py`
- Create: `backend/src/aimctexturegen/model_profiles/models.py`
- Create: `backend/src/aimctexturegen/model_profiles/registry.py`
- Create: `backend/tests/comfy/test_profile_install.py`
- Create: `backend/tests/model_profiles/test_registry.py`

**Interfaces:**
- Installs hash-addressed manifest artifacts into controlled ComfyUI model
  categories.
- Produces a managed `extra_model_paths.yaml`.
- Produces profile status independent of process status.

- [x] **Step 1: Write profile-install RED tests**

Using tiny local artifacts, prove:

- every artifact is downloaded exactly once and placed in its allowlisted
  category;
- two profiles may reuse an identical artifact without duplicate download;
- conflicting destinations or same destination/different hash fail;
- final size/hash is rechecked on status;
- missing/corrupt artifact never reports ready;
- custom-node archive commit/root/layout and hash are checked;
- custom node installs only into the selected managed runtime;
- generated YAML contains only managed absolute paths, uses stable encoding and
  cannot be influenced by a manifest string;
- partial failure leaves previously ready components and runtime selection
  intact, with no profile falsely marked ready.

- [x] **Step 2: Implement profile registry and capability checks**

The registry exposes generic capabilities and immutable default/profile
metadata. It must not import `sdxl.py` merely to list profiles.

Add a second test-only fake profile with different capability flags and
workflow node names. Registering it must not require edits to installer,
runtime manager or transport code.

- [x] **Step 3: Implement model/custom-node install coordination**

Use the Task 3 downloader for files and Task 4 archive path for custom-node
source. A content-ready model is its expected ordinary file plus validated
receipt; receipt alone is insufficient.

Do not run arbitrary custom-node install scripts. If the pinned node needs
Python dependencies, list and install only an explicit reviewed allowlist with
the portable embedded Python; record them in the runtime receipt. Never invoke
ComfyUI Manager.

- [x] **Step 4: Run tests**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\comfy\test_profile_install.py backend\tests\model_profiles -v
```

- [x] **Step 5: Commit**

```powershell
git add backend\src\aimctexturegen\comfy backend\src\aimctexturegen\model_profiles backend\tests\comfy backend\tests\model_profiles
git commit -m "feat: install versioned model profiles"
```

---

### Task 6: Owned ComfyUI process and readiness management

**Files:**
- Create: `backend/src/aimctexturegen/comfy/process.py`
- Create: `backend/src/aimctexturegen/comfy/manager.py`
- Create: `backend/tests/fakes/fake_child.py`
- Create: `backend/tests/comfy/test_process.py`
- Create: `backend/tests/comfy/test_manager.py`

**Interfaces:**
- Produces `ComfyUIManager.status()`, `start()` and `stop()`.
- Owns one loopback process identified by PID plus Windows creation identity.
- Does not submit workflows.

- [x] **Step 1: Write process RED tests with a fake child**

Cover:

- exact executable/argument list and `shell=False`;
- hidden-window Windows creation flags;
- bounded environment inheritance with no secret dump;
- loopback/configured-port enforcement;
- port already occupied by an unrelated listener;
- early exit and startup timeout;
- stdout/stderr append and bounded rotation;
- PID reuse simulation: mismatched executable or creation identity is never
  stopped;
- graceful stop then forced stop only for the still-owned child;
- stale process record recovery;
- two concurrent start requests produce one child;
- application shutdown stops or cleanly records the owned child according to
  the chosen lifespan policy.

- [x] **Step 2: Implement Windows process identity**

Use standard library plus focused `ctypes` wrappers for process creation time
and executable identity. Keep the Windows code in a small internal adapter so
tests inject a fake. Do not add broad process scanning or kill-by-name.

- [x] **Step 3: Write readiness RED tests**

Inject a fake status client. Ready requires expected `/system_stats`, all
profile-required `/object_info` node classes and an alive stable process.
Cover wrong version, missing node, corrupt profile, disconnect and child exit
during stabilization.

- [x] **Step 4: Implement manager orchestration**

Validate runtime/profile integrity before launch. Generate the controlled
model-path config and exact startup args. Persist a strict process record
atomically and redact absolute paths from API-safe status.

- [x] **Step 5: Run tests**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\comfy\test_process.py backend\tests\comfy\test_manager.py -v
```

- [x] **Step 6: Commit**

```powershell
git add backend\src\aimctexturegen\comfy backend\tests\comfy backend\tests\fakes
git commit -m "feat: manage the owned ComfyUI child process"
```

---

### Task 7: Generic HTTP/WebSocket transport and fake ComfyUI

**Files:**
- Create: `backend/src/aimctexturegen/comfy/client.py`
- Create: `backend/tests/fakes/comfy_server.py`
- Create: `backend/tests/comfy/test_client_http.py`
- Create: `backend/tests/comfy/test_client_websocket.py`
- Create: `backend/tests/comfy/test_client_errors.py`

**Interfaces:**
- Produces a generic `ComfyClient` with system/object inspection, image upload,
  prompt submission, progress/completion wait, history/output retrieval and
  interrupt.
- Does not import jobs, projects, processing or SDXL modules.

- [x] **Step 1: Implement a protocol-faithful fake service**

Support the exact subset of:

```text
GET  /system_stats
GET  /object_info
POST /upload/image
POST /prompt
GET  /history/{prompt_id}
GET  /view
POST /interrupt
WS   /ws?clientId=<id>
```

Allow tests to script queue rejection, malformed payload, progress, completion,
execution error, disconnect, timeout, missing history, unsafe output name and
partial outputs.

- [x] **Step 2: Write client RED tests**

Cover:

- strict loopback base URL and generated client/prompt IDs;
- bounded upload size and safe uploaded filename;
- deep-copied JSON submission;
- WebSocket progress ordering and prompt-ID filtering;
- completion via WS and bounded history reconciliation;
- output retrieval only from history-declared safe names;
- queue error, protocol error, execution error, disconnect, timeout and
  interrupt;
- no arbitrary file path returned or opened;
- response body and log detail bounds;
- client cleanup without leaked sockets/tasks.

- [x] **Step 3: Implement transport**

Use HTTPX for HTTP and the pinned websockets client for WS. All operations have
finite timeouts and cancellation. Translate failures to stable domain errors;
do not retry a prompt submission whose acceptance is unknown.

- [x] **Step 4: Run tests**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\comfy\test_client_http.py backend\tests\comfy\test_client_websocket.py backend\tests\comfy\test_client_errors.py -v
```

- [x] **Step 5: Commit**

```powershell
git add backend\src\aimctexturegen\comfy\client.py backend\tests\comfy backend\tests\fakes
git commit -m "feat: add tested ComfyUI protocol transport"
```

---

### Task 8: Fixed workflows, semantic binding and durable profile identity

**Files:**
- Create: `workflows/sdxl-mapchip-ipadapter-v1/text2img.api.json`
- Create: `workflows/sdxl-mapchip-ipadapter-v1/img2img.api.json`
- Create: `backend/src/aimctexturegen/model_profiles/workflows.py`
- Create: `backend/src/aimctexturegen/model_profiles/sdxl.py`
- Modify: `backend/src/aimctexturegen/jobs/models.py`
- Modify: `backend/src/aimctexturegen/jobs/service.py`
- Modify: `backend/src/aimctexturegen/api/jobs.py`
- Create: `backend/tests/model_profiles/test_workflows.py`
- Create: `backend/tests/model_profiles/test_sdxl.py`
- Modify: `backend/tests/jobs/test_models.py`
- Modify: `backend/tests/jobs/test_service.py`
- Modify: `backend/tests/api/test_jobs.py`

**Interfaces:**
- Produces `WorkflowBinding.compile(GenericWorkflowInputs) -> dict`.
- Produces schema-2 `JobRequest` with frozen `ModelProfileBinding`.
- Reads schema-1 job requests unchanged as legacy unbound jobs.

- [x] **Step 1: Derive API workflows from the pinned real node contracts**

Use the selected ComfyUI and IP-Adapter commit's actual `/object_info` or
reviewed example workflows. Remove UI-only metadata and save API-format JSON.
Both workflows must include SDXL Base, mapchipLora and IP-Adapter; img2img must
encode the optional structure reference.

Never paste an unreviewed Internet workflow. Record every node class and
semantic input slot in the profile manifest, then update the workflow SHA-256
values.

- [x] **Step 2: Write workflow RED tests**

Cover:

- tracked digest equality;
- template schema/node/input/output validation;
- deep-copy: compile never mutates the registry template;
- only allowlisted semantic fields change;
- seed is exact and JavaScript-safe;
- input/output canvas and batch semantics are fixed for the smoke;
- safe synthetic upload names only;
- text2img rejects structure reference;
- img2img requires one structure reference;
- style-reference count and average-combination contract;
- unknown advanced key/type/range rejection;
- fake second profile uses the same protocol with different nodes;
- a missing required server node fails before submission.

- [x] **Step 3: Implement generic binding and SDXL adapter**

Numeric ComfyUI node IDs live only in `sdxl.py`/the profile workflow record.
The generic transport and callers use semantic slots. Keep prompt/default
calibration profile-scoped; changing an inference-affecting value requires a
profile version bump after smoke.

- [x] **Step 4: Write job schema compatibility RED tests**

Prove:

- existing schema-1 fixtures load without byte migration and expose
  `model_profile=None`, `execution_eligibility=legacy_unbound`;
- schema-2 records freeze runtime/profile/workflow IDs and digests;
- unknown profile, capability mismatch and digest mismatch fail before job
  directory creation;
- structure-reference presence selects exactly the matching workflow kind;
- retry/cancel/history behavior for schema 1 remains unchanged;
- no installed-state or local absolute path enters immutable `request.json`.

- [x] **Step 5: Implement schema 2 creation without rewriting schema 1**

Update the create API contract to require an explicit profile ID for new
generation jobs. Registry resolution constructs the frozen binding. Keep a
focused test helper for creating legacy schema-1 jobs.

Do not make Phase 4 execute either schema.

- [x] **Step 6: Run focused and regression tests**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\model_profiles backend\tests\jobs backend\tests\api\test_jobs.py -v
```

- [x] **Step 7: Commit**

```powershell
git add workflows backend\src\aimctexturegen\model_profiles backend\src\aimctexturegen\jobs backend\src\aimctexturegen\api\jobs.py backend\tests\model_profiles backend\tests\jobs backend\tests\api\test_jobs.py manifests
git commit -m "feat: bind fixed model profiles to durable jobs"
```

---

### Task 9: FastAPI setup surface and focused WebUI

**Files:**
- Create: `backend/src/aimctexturegen/api/inference.py`
- Modify: `backend/src/aimctexturegen/main.py`
- Create: `backend/tests/api/test_inference.py`
- Create: `frontend/src/InferenceSetup.tsx`
- Create: `frontend/src/InferenceSetup.test.tsx`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/api.test.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Exposes install-plan, installation progress/cancel, process start/stop/status
  and bounded log-summary APIs.
- Adds setup/status UI only; no generation button.

- [x] **Step 1: Write API RED tests**

Cover strict response parsing and stable error envelopes for:

- read-only status and install plan;
- stale/missing consent;
- accepted install returning `202` and an operation ID;
- single active install constraint;
- progress/detail and explicit cancellation;
- startup recovery as `INSTALL_INTERRUPTED`;
- start/stop idempotency and port/health failures;
- bounded log tail without arbitrary path input;
- injected fake services only—no real runtime root, network or process.

Ensure app startup with default services performs no download, extraction or
ComfyUI launch.

- [x] **Step 2: Implement thin inference routes and service graph**

Add services to `AppServices` with dependency injection. Long downloads run
off the event loop and expose persisted progress. Coordinate shutdown so no
new mutation starts while the app is closing.

- [x] **Step 3: Add strict frontend API models and RED tests**

Reject unknown status values, unsafe sizes, malformed hashes/URLs, duplicate
licenses, nonmonotonic progress and absolute technical paths. Use
`AbortController` for polling cleanup.

- [x] **Step 4: Write component RED tests**

Cover:

- missing/ready/corrupt/unsupported states;
- exact decimal GB and GiB totals;
- source/license links;
- confirmation disabled until every required acceptance is selected;
- installation progress and safe cancel/retry;
- readable driver, disk, hash, port, startup and node recommendations;
- owned runtime start/stop;
- no generation or arbitrary workflow/path control;
- existing project import/history remains usable when inference status fails;
- keyboard/accessibility labels and no duplicate polling after unmount.

- [x] **Step 5: Implement the setup panel**

Keep network orchestration in `App` or a focused hook and presentation in
`InferenceSetup`. Preserve the established visual language and Windows desktop
400–900 px behavior. Do not add mobile-specific navigation.

- [x] **Step 6: Run backend/frontend gates**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\api\test_inference.py -v
Push-Location frontend
try {
    ..\runtime\node-v24.18.0-win-x64\npm.cmd test
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    ..\runtime\node-v24.18.0-win-x64\npm.cmd run build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
```

- [x] **Step 7: Commit**

```powershell
git add backend\src\aimctexturegen\api backend\src\aimctexturegen\main.py backend\tests\api frontend\src
git commit -m "feat: expose managed inference setup"
```

---

### Task 10: Real portable install and GPU workflow smoke

**Files:**
- Create: `tools/Invoke-Phase4Smoke.ps1`
- Create: `backend/src/aimctexturegen/model_profiles/smoke.py`
- Create: `backend/tests/model_profiles/test_smoke_inputs.py`
- Create after success: `docs/MODEL_PROFILES.md`
- Modify after success: production runtime/profile manifests if measured facts
  require a new candidate version
- Create after success: a redacted evidence JSON under
  `docs/evidence/phase-4/` only if it passes the privacy review

**Interfaces:**
- Generates synthetic style/structure PNGs from project-owned deterministic
  code.
- Runs one text2img and one img2img request through the same manager, client
  and workflow binding used by the application.
- Writes all large/raw outputs below ignored `runtime/smoke/`.

- [x] **Step 1: Test deterministic synthetic smoke inputs**

Generate obvious non-Minecraft geometric/color patterns at runtime. Tests
check exact dimensions/mode/hash and confirm no file is copied from a resource
pack or external asset.

- [x] **Step 2: Implement the PowerShell smoke entry**

The script must be valid as a single PowerShell command invocation:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\tools\Invoke-Phase4Smoke.ps1
```

It calls backend modules; it does not duplicate download, hash or ComfyUI
protocol logic. It prints concise progress and the ignored evidence directory.
No multiline paste into a browser console is required.

- [x] **Step 3: Present the exact install plan to the user**

Before the first real download, show:

- all components, versions, sources and licenses;
- exact `13,180,880,401`-byte runtime, model and custom-node download total;
- decimal GB/GiB display and conservative temporary disk headroom;
- the selected standard NVIDIA portable requirements;
- that installation writes only below repository `runtime/`;
- that runtime archives/cache may be removed after verified publication.

Obtain explicit confirmation through the implemented setup surface. Do not
interpret the earlier architecture decision as consent to start a multi-GB
download.

- [x] **Step 4: Download, verify and install**

After confirmation, run the real installer. Independently calculate SHA-256
for every final artifact and compare it to the tracked manifest. Record:

- final URL;
- bytes;
- SHA-256;
- install duration;
- extracted runtime size;
- remaining disk space;
- component status.

If any value differs, stop and leave the profile unsupported.

- [x] **Step 5: Start and record environment identity**

Record without exposing personal paths:

- Windows version;
- NVIDIA GPU model and VRAM;
- driver version;
- ComfyUI release/commit;
- embedded Python, PyTorch and CUDA versions;
- loaded required node classes;
- runtime/profile/workflow manifest digests.

- [x] **Step 6: Run text2img smoke**

Use one fixed seed, fixed prompt, synthetic style reference and the tracked
text2img workflow. Verify:

- prompt accepted and completed;
- returned prompt ID belongs to this client;
- output is a square decodable image of the expected canvas;
- seed and workflow/profile digests are recorded;
- no project pack file changed.

- [x] **Step 7: Run img2img smoke**

Use another fixed seed, the same synthetic style reference, a synthetic
structure reference and the tracked img2img workflow. Apply the same checks.

- [x] **Step 8: Restart and integrity audit**

Stop then restart only the owned ComfyUI process and recheck readiness. Hash:

- backend `.venv` package metadata or record the pre/post installed-package
  list;
- every imported project `source/` and `pack/` path/byte map used by the
  existing test repository;
- all final model artifacts.

The backend environment and project files must be unchanged by ComfyUI setup
and smoke.

- [x] **Step 9: Promote or reject the candidate profile**

If both smokes pass, change the manifest support state from
`candidate_unverified` to the exact verified state and update its digest. If
this digest change invalidates the installation receipt, perform the designed
metadata-only revalidation path or version the manifest; never falsify the old
receipt.

Create `docs/MODEL_PROFILES.md` with exact verified versions, licenses,
measured machine context, smoke commands, limitations and the maintenance-only
IP-Adapter warning. Do not publish generalized 8 GB claims from the 16 GB
development machine.

If either smoke fails, keep the profile candidate, document the failure and
return to the responsible earlier task. Do not mark Task 10 complete.

- [x] **Step 10: Commit only small reviewed evidence and documentation**

```powershell
git add tools\Invoke-Phase4Smoke.ps1 backend\src\aimctexturegen\model_profiles\smoke.py backend\tests\model_profiles docs\MODEL_PROFILES.md docs\evidence manifests workflows
git commit -m "test: record managed SDXL profile smoke"
```

Omit paths that were not created. Before staging, explicitly verify no model,
runtime, raw image, personal path, machine ID or log is included.

---

### Task 11: Full gates, user manual verification and Phase 4 handoff

**Files:**
- Modify: `ONBOARDING.md`
- Modify: `docs/TESTING.md`
- Modify: `docs/superpowers/plans/2026-07-21-aimc-texturegen-mvp-roadmap.md`
- Modify: this plan

- [x] **Step 1: Run complete automated gates**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests --cov=aimctexturegen --cov-report=term-missing
Push-Location frontend
try {
    ..\runtime\node-v24.18.0-win-x64\npm.cmd test
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    ..\runtime\node-v24.18.0-win-x64\npm.cmd run build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
git diff --check
git status --short
```

Record exact totals; do not copy Phase 3 counts.

当前 checkout 未包含 portable Node `v24.18.0` 目录，因此前端门禁实际使用
全局 Node `v24.13.0` 的 `npm` 命令；恢复 portable Node 后再切回固定路径。

- [x] **Step 2: Run focused no-mutation and fake-service audits**

Run the install/archive/process/transport suites separately with `-W error
-vv`. Run the existing restart audit and compare project `source/`/`pack/`
hash maps. Prove the default test suite performs no real network download and
does not launch the real ComfyUI.

- [x] **Step 3: Give the user the manual Windows procedure**

The procedure is recorded in `docs/TESTING.md`. The user reported successful
Windows desktop verification: ready installed state, start/health/stop/restart,
occupied-port protection and recovery, representative 400/600/900 px layouts,
no application-origin console errors, and unchanged Phase 3 project/job data.

Ask the user to verify through the WebUI:

1. the exact runtime/profile/license/download plan appears before consent;
2. refreshing/restarting before consent causes no download;
3. the already installed verified profile appears ready without redownload;
4. start, health, stop and restart work;
5. a deliberately occupied ComfyUI port produces the readable
   `PORT_IN_USE` guidance and does not kill the other process;
6. normal desktop and representative 400, 600 and 900 px Windows widths have
   no horizontal overflow or clipped setup controls;
7. the browser has no application-origin console errors;
8. existing projects and Phase 3 job history still load.

The user does not need to rerun the multi-GB download solely for UI
verification. Automated tests cover hash mismatch, cancellation and unsafe
archive cases.

- [x] **Step 4: Update handoff documents**

`ONBOARDING.md` must state:

- branch and exact completed commits;
- candidate versus verified runtime/profile status;
- exact automated and real/manual results;
- installed artifacts remain ignored/local;
- known limitations or unresolved defects;
- Phase 5 as the only next implementation entry.

Update `docs/TESTING.md` only with commands that now exist and were actually
run. Update the roadmap Phase 4 exit gate only with demonstrated facts.

- [x] **Step 5: Review and closure commit**

Use `superpowers:requesting-code-review`, address findings and rerun relevant
gates. The current review findings were addressed by revalidating profile
artifact hashes/receipts, rechecking process identity before force termination
and on manager restart, and serializing install lifecycle transitions. Then:

```powershell
git add ONBOARDING.md docs
git commit -m "docs: record Phase 4 managed inference gate"
```

本轮收尾提交为 `f9d803b`（安装状态、进程身份和生命周期并发修正）、
`b9180eb`（身份记录失败时清理未登记子进程）、`d69d363`（有界内存的
profile 完整性校验）以及文档提交 `4fd8ab8`、`920e44a`；对应回归测试均
已重跑。冷启动窗口与对应提示修正提交为 `a19ea87`；
用户已完成并通过 Step 3 的手动 WebUI 验收；截图中的 ready/stopped 状态与
已安装配置均符合预期。

非阻塞已知限制：本阶段不做最终视觉 polish；推理环境面板的许可 checkbox
仍继承通用 `input` 尺寸规则，尺寸与对齐将在 Phase 6 MVP 验收前统一修正。

- [x] **Step 6: Complete the Phase 4 handoff**

Use `superpowers:finishing-a-development-branch`. The user authorized the
integration after reviewing the manual results. The branch was merged into
`master` as `46c8d0e`, the digest-stability `.gitattributes` fix was recorded
as `54a58b5`, and `master` was pushed to `origin/master`. The exact tests,
real GPU evidence, manual checks and local ignored runtime state are recorded
above and in `ONBOARDING.md`/`docs/TESTING.md`.

---

## Task dependency graph

```text
Task 1 manifests
  └─ Task 2 inspection/consent
      ├─ Task 3 downloader
      │   └─ Task 4 runtime extraction
      │       └─ Task 5 profile install
      │           └─ Task 6 process/readiness
      │               └─ Task 7 transport
      │                   └─ Task 8 workflows/job binding
      │                       └─ Task 9 API/WebUI
      │                           └─ Task 10 real smoke
      │                               └─ Task 11 closure
```

Tasks are intentionally ordered. Parallel work is safe only for test-fixture
preparation that does not modify the same interfaces; implementers must not
invent downstream contracts before their producing task is reviewed.

## Plan self-review

- The approved official portable installation choice is explicit and does not
  touch the backend `.venv` or user environments.
- Runtime and model profiles are separate, versioned and digest-bound.
- A fake second profile proves future FLUX.2 integration is additive, while
  Phase 4 does not claim FLUX support.
- Phase 3 schema-1 jobs remain byte-immutable; new jobs freeze model identity.
- Network, archive, process and protocol boundaries each have focused RED/GREEN
  tests and failure semantics.
- Ordinary CI uses only tiny synthetic artifacts and a loopback fake service.
- The only real multi-GB download requires a second explicit consent at Task
  10; architecture approval is not treated as download consent.
- The real exit gate includes both text2img and img2img through the exact fixed
  profile, not merely a ComfyUI health response.
- Phase 5 retains ownership of durable job execution, four candidates,
  progress, OOM orchestration and deterministic postprocessing integration.
