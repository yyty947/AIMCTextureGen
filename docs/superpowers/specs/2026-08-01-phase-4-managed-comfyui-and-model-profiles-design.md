# Phase 4 Managed ComfyUI and Model Profiles Design

**Status:** Approved for implementation on 2026-08-01

**Decision record:** [`ADR-0002`](../../adr/0002-managed-comfyui-runtime-and-versioned-model-profiles.md)

**Parent product design:** [`2026-07-18-aimc-texturegen-mvp-design.md`](2026-07-18-aimc-texturegen-mvp-design.md)

## 1. Goal

Phase 4 adds a reproducible, application-managed inference substrate without
yet connecting durable four-candidate jobs to GPU execution.

At the end of the phase, a Windows/NVIDIA user can:

1. inspect the exact ComfyUI runtime and SDXL profile components;
2. explicitly accept their licenses and download sizes;
3. download, hash-check and install them below the repository-owned
   `runtime/` directory;
4. start, inspect and stop the owned ComfyUI child process;
5. run one fixed text-to-image and one fixed image-to-image profile smoke test;
6. see actionable setup, integrity, process and GPU errors through FastAPI and
   a focused WebUI setup panel.

Normal CI uses small synthetic artifacts and a fake ComfyUI service. It never
downloads the real runtime/models or requires CUDA.

## 2. Confirmed product decisions

- Runtime option A is selected: download and verify the official Windows
  NVIDIA portable ComfyUI archive. Its embedded Python/PyTorch remains
  isolated from the backend `.venv` and all user/global environments.
- The planned first model profile remains SDXL Base 1.0 + mapchipLora +
  IP-Adapter SDXL ViT-H.
- `pixel-art-xl` remains excluded from automatic download.
- FLUX.2 Klein 4B is not downloaded or executed in Phase 4. The manifest,
  workflow and durable-job boundaries must allow it to be added later as a
  separate profile without rewriting project storage, postprocessing or the
  ComfyUI transport.
- The repository does not scan for Minecraft, JAR files, another ComfyUI,
  global Python, Miniconda or model directories.
- Downloads never begin solely because the application started. User consent
  is bound to the exact manifest digest being installed.
- No runtime, node, model, download cache, generated image or real smoke
  artifact is committed to Git.
- Phase 5, not Phase 4, owns generation scheduling, four-candidate progress,
  cancellation and postprocessing orchestration.

## 3. Candidate dependency lock

These values are implementation candidates, not support claims. Task 1 of the
Phase 4 plan must encode them in strict manifests. Task 10 must independently
verify downloaded bytes and record the real smoke result before the profile is
called supported.

### 3.1 Runtime

| Component | Candidate version | Bytes | SHA-256 | License/source |
|---|---|---:|---|---|
| ComfyUI Windows NVIDIA portable | `v0.29.2`, commit `322122449c9d2ba8b8df1bb517364527dd0615f1` | 2,103,175,457 | `e7a39a817002d85b4fb2d4f6bd176c10d104a0d04031f99b9d8b7b1fd920c6fc` | [official immutable release](https://github.com/Comfy-Org/ComfyUI/releases/tag/v0.29.2), GPL-3.0 |

The selected asset is
`ComfyUI_windows_portable_nvidia.7z`, not the CUDA 12.6 legacy, AMD or Intel
variant. The official project currently describes this portable as Python
3.13/PyTorch CUDA 13.0 for NVIDIA 20-series and newer. Phase 4 reports a clear
unsupported-host result rather than silently choosing another archive.

### 3.2 SDXL profile artifacts

| Component | Revision/file | Bytes | SHA-256 | License/source |
|---|---|---:|---|---|
| SDXL Base 1.0 | revision `462165984030d82259a11f4367a4eed129e94a7b`, `sd_xl_base_1.0.safetensors` | 6,938,078,334 | `31e35c80fc4829d14f90153f4c74cd59c90b779f6afe05a74cd6120b893f7e5b` | [Stability AI](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/tree/462165984030d82259a11f4367a4eed129e94a7b), CreativeML Open RAIL++-M |
| mapchipLora | revision `7ff7d9e43c9c364eb25ca283851565b7c5778dbf`, `mapchipLora.safetensors` | 912,555,676 | `9a047fce0fd45e60aaee6bcf6ec465ba34397366b10a34bfb2175f5e129ac1ae` | [kokuren](https://huggingface.co/kokuren/mapchipLora), Apache-2.0 |
| IP-Adapter SDXL ViT-H | revision `018e402774aeeddd60609b4ecdb7e298259dc729`, `sdxl_models/ip-adapter_sdxl_vit-h.safetensors` | 698,391,064 | `ebf05d918348aec7abb02a5e9ecef77e0aaea6914a5c4ea13f50d45eb1681831` | [h94/IP-Adapter](https://huggingface.co/h94/IP-Adapter/tree/018e402774aeeddd60609b4ecdb7e298259dc729), Apache-2.0 |
| CLIP ViT-H image encoder | same revision, `models/image_encoder/model.safetensors` | 2,528,373,448 | `6ca9667da1ca9e0b0f75e46bb030f7e011f44f86cbfb8d5a36590fcd7507b030` | [h94/IP-Adapter](https://huggingface.co/h94/IP-Adapter/tree/018e402774aeeddd60609b4ecdb7e298259dc729), Apache-2.0 |
| ComfyUI IP-Adapter Plus | commit `a0f451a5113cf9becb0847b92884cb10cbdec0ef`, codeload ZIP | 306,422 | `c6c49c82aa65cb96b93bdf9f9b547f9c95310a2668a7a9aaa0285cccf4590347` | [cubiq/ComfyUI_IPAdapter_plus](https://github.com/cubiq/ComfyUI_IPAdapter_plus/commit/a0f451a5113cf9becb0847b92884cb10cbdec0ef), GPL-3.0 |

The four large model files total `11,077,398,522` bytes; runtime, models and
the custom-node archive total `13,180,880,401` bytes. The setup UI reports
exact manifest totals in decimal GB and GiB and separately reports temporary
extraction headroom. It does not confuse download size with installed size.

The IP-Adapter custom-node repository has been maintenance-only since
2025-04-14. It remains a candidate because the selected SDXL workflow needs
its style-reference behavior; its compatibility is a mandatory real smoke
condition and a documented reason to keep the model-profile boundary.

## 4. Tracked and untracked layout

Tracked configuration:

```text
manifests/
├─ runtimes/
│  └─ comfyui-windows-nvidia-v0.29.2.json
└─ model-profiles/
   └─ sdxl-mapchip-ipadapter-v1.json
workflows/
└─ sdxl-mapchip-ipadapter-v1/
   ├─ text2img.api.json
   └─ img2img.api.json
```

Repository-ignored installed state:

```text
runtime/
├─ downloads/
│  ├─ <artifact-id>.part
│  └─ <artifact-id>.part.json
├─ comfyui/
│  └─ v0.29.2-<manifest-prefix>/
├─ models/
│  ├─ checkpoints/
│  ├─ loras/
│  ├─ ipadapter/
│  └─ clip_vision/
├─ logs/
├─ smoke/
└─ state/
   ├─ selected-runtime.json
   ├─ installed-artifacts.json
   ├─ install-operations/
   └─ process.json
```

Runtime and model paths are always descendants of the configured project
runtime root. Manifest destinations are strict relative POSIX paths with no
drive, UNC, device, traversal, empty, case-colliding or reserved Windows
components.

Models live outside the versioned ComfyUI extraction so a runtime upgrade does
not redownload unchanged weights. The manager generates a controlled
`extra_model_paths.yaml` and passes it to ComfyUI. It does not edit a user's
configuration.

## 5. Manifest contracts

All tracked manifests use strict, versioned Pydantic models and reject unknown
fields.

### 5.1 `RuntimeManifest`

Required fields include:

- `schema_version`;
- stable `runtime_id` and semantic `runtime_version`;
- `platform`, `architecture`, `gpu_vendor` and documented host requirements;
- upstream release URL, source commit and license records;
- archive URL, byte size and lowercase SHA-256;
- expected archive root and required executable/script paths;
- fixed startup argument template;
- health endpoint and expected runtime identity;
- extraction headroom estimate and manifest revision notes.

### 5.2 `ModelProfileManifest`

Required fields include:

- `schema_version`, stable `profile_id` and `profile_version`;
- compatible runtime IDs/versions;
- capability declaration:
  - `text_to_image`;
  - `structure_reference`;
  - `style_reference_min` and `style_reference_max`;
  - `native_multi_reference`;
  - `requires_custom_nodes`;
- every model/custom-node artifact with source, revision, license, size,
  SHA-256 and strict destination;
- text2img and img2img workflow paths plus their SHA-256 digests;
- required ComfyUI node class names;
- generic defaults and profile-scoped advanced defaults;
- output contract: square RGB-decodable PNG and expected inference canvas;
- user-facing limitations and unverified/verified support state.

No manifest contains credentials, local absolute paths or mutable `main`/latest
URLs as the version identity.

### 5.3 Digests and profile evolution

Canonical JSON is UTF-8 with sorted keys and stable separators. Its SHA-256 is
the manifest digest used by install plans and durable job bindings.

Changing an artifact, workflow, capability or inference-affecting default
requires a new profile version. Documentation-only text that changes the
canonical manifest also changes its digest, so already-created installation
plans become invalid and need renewed confirmation.

## 6. Installation lifecycle

Installation has separate read-only inspection and explicit mutation paths.

### 6.1 Inspection

Inspection:

1. loads and validates tracked manifests;
2. computes the canonical plan digest;
3. checks OS, architecture, NVIDIA visibility, driver/runtime compatibility
   and free disk without changing anything;
4. classifies each component as `missing`, `partial`, `ready`, `corrupt` or
   `unsupported`;
5. returns exact downloads, licenses, disk estimates and recommended actions.

A missing NVIDIA driver does not prevent downloading documentation or viewing
the plan, but it blocks the runtime from being reported as runnable.

### 6.2 Confirmation

The install request contains:

- runtime ID/version;
- model profile ID/version;
- plan digest;
- the exact set of accepted component/license IDs.

The server recomputes the plan. A digest or accepted-component mismatch fails
before network or disk mutation. Acceptance is recorded with the install
operation, manifest digests and timestamp; it is not treated as acceptance of
future versions.

### 6.3 Download

Each artifact uses a bounded streaming downloader:

- HTTPS only, with an explicit redirect limit and allowed final host policy;
- finite connect/read timeouts;
- expected byte size and SHA-256;
- `<artifact>.part` and a small validated resume sidecar;
- `Range` resume only when sidecar, existing size and server response agree;
- restart from byte zero when the server safely ignores a range request;
- incremental progress without loading the artifact into memory;
- final length/hash validation before an atomic same-volume rename.

Cancellation or network failure never publishes a final artifact. A valid
partial may remain for an explicitly requested retry. Hash mismatch marks the
operation failed and quarantines or removes only the exact managed partial; it
never replaces an installed artifact.

### 6.4 Extraction and publication

The backend may use a pinned `py7zr` dependency solely as an installer tool.
It does not install into or import from ComfyUI's embedded Python.

Before extraction, the archive member list is checked for:

- traversal, rooted, drive, UNC and device paths;
- symlink/hardlink/reparse entries;
- duplicate or case-colliding destinations;
- unsupported special files;
- per-entry and total expanded-size bounds;
- the exact expected top-level root.

Extraction happens below a unique same-parent staging directory. The complete
tree is audited after extraction for regular files/directories, expected
executables and Windows reparse points. Only then is it renamed to a versioned
runtime directory and selected through an atomically replaced state record.

An existing ready runtime is idempotent. A corrupt runtime is never silently
patched. Explicit repair builds a complete replacement before switching and
preserves or safely removes only the exact managed old runtime according to
the operation result.

Model files use the same staged hash-verified publication, without a second
cache copy. Custom-node source is extracted into the managed runtime from a
commit-specific, hash-pinned archive and is revalidated on every status check.

An interrupted application restart marks the install operation failed with
`INSTALL_INTERRUPTED`; it does not automatically resume network activity.

## 7. Process ownership and health

The manager launches only the selected managed runtime:

```text
python_embeded/python.exe
  -s ComfyUI/main.py
  --windows-standalone-build
  --listen 127.0.0.1
  --port <configured-port>
  --disable-auto-launch
  --extra-model-paths-config <managed-config>
```

The exact supported arguments must be verified against the selected release;
the manifest and tests are updated if its real CLI differs.

Rules:

- bind to loopback only;
- use one explicit configured port and report `PORT_IN_USE` rather than
  connecting to an unknown listener;
- start the child without a visible console window;
- redirect stdout/stderr to bounded, rotation-aware managed logs;
- save PID, executable path, process creation identity, runtime/profile
  digests and start time atomically;
- stop only a process whose PID and creation identity still match the owned
  record;
- never kill a process merely because it owns the configured port;
- use a graceful terminate timeout followed by force termination only for the
  still-verified owned child;
- redact local absolute paths from ordinary API summaries while allowing the
  user to open the managed log through an explicit local action later.

Ready means more than “port open.” The manager must verify:

- `/system_stats` responds and identifies the expected ComfyUI version;
- `/object_info` contains every core and custom node required by the selected
  profile;
- the selected artifact files match size/hash records;
- the process remains alive through a bounded stabilization period.

## 8. ComfyUI transport and workflow boundary

### 8.1 Generic transport

`ComfyClient` owns only ComfyUI protocol mechanics:

- upload a synthetic/reference input;
- submit API-format workflow JSON with a generated client ID;
- observe prompt/progress/completion/error over WebSocket with bounded HTTP
  history polling as recovery;
- retrieve only output files named by the prompt history;
- interrupt an owned prompt when requested;
- map protocol, disconnect, timeout and execution errors to typed domain
  errors.

It does not know Minecraft paths, task state transitions, SDXL, LoRA,
IP-Adapter, FLUX or postprocessing.

### 8.2 Profile-specific workflow binding

`WorkflowBinding` receives generic immutable inputs:

- prompt and optional negative prompt;
- seed;
- inference canvas;
- one or more uploaded style-reference names;
- optional uploaded structure-reference name;
- profile-scoped advanced overrides.

The SDXL binding deep-copies a fixed workflow template, validates its digest,
sets only allowlisted node/input fields and returns a new request. It never
rewrites the tracked template.

Node IDs are workflow-private. Callers address semantic slots such as
`positive_prompt`, `seed`, `style_references`, `structure_reference` and
`denoise`, not numeric ComfyUI node IDs.

At registry load and again against a running server, the binding validates
template structure, required nodes, allowed field types and output node.
Unknown or missing nodes fail before prompt submission.

The SDXL profile's text2img and img2img templates both exercise mapchipLora and
IP-Adapter. Text2img starts from an empty latent; img2img encodes the uploaded
structure reference. Multiple style references use the profile's fixed
average-combination behavior. Calibration values remain profile-scoped and
must be versioned when changed.

### 8.3 Future profile proof

Tests register a second tiny fake profile with different workflow nodes and
capabilities. The same registry, installer plan, transport and API status code
must accept it without changes. This is the Phase 4 proof that later FLUX.2
support is additive.

It is not a claim that FLUX.2 output quality, parameters or runtime
compatibility have already been tested.

## 9. Durable job profile binding

Phase 3 `request.json` schema 1 contains generic generation inputs but no model
identity. Phase 4 introduces schema 2 for newly created requests:

```json
{
  "schema_version": 2,
  "model_profile": {
    "profile_id": "sdxl-mapchip-ipadapter",
    "profile_version": "1",
    "profile_manifest_sha256": "...",
    "runtime_id": "comfyui-windows-nvidia",
    "runtime_version": "0.29.2",
    "runtime_manifest_sha256": "...",
    "workflow_kind": "text2img",
    "workflow_sha256": "..."
  }
}
```

All Phase 3 fields remain. `workflow_kind` is derived from the presence of the
optional structure reference and is validated against profile capabilities.

Rules:

- schema-1 request bytes remain immutable and readable;
- schema-1 jobs expose `model_profile = null` and
  `execution_eligibility = legacy_unbound`;
- they are never silently migrated or run with the newest default;
- a Phase 5 retry/new-job operation may explicitly bind a currently available
  profile while preserving lineage and seeds according to its own plan;
- schema-2 creation rejects an unknown profile, capability mismatch or
  noncanonical manifest/workflow digest;
- installed/not-installed is not embedded in the immutable request; it is
  live environment status.

## 10. FastAPI and focused WebUI surface

Phase 4 adds a setup surface, not a generation wizard.

Suggested endpoints:

```text
GET  /api/system/inference
GET  /api/system/inference/install-plan
POST /api/system/inference/installations
GET  /api/system/inference/installations/{installation_id}
POST /api/system/inference/installations/{installation_id}/cancel
POST /api/system/inference/comfyui/start
POST /api/system/inference/comfyui/stop
GET  /api/system/inference/comfyui/log
```

Exact route naming may change during implementation if tests reveal a clearer
contract, but routes remain thin and call injected services. Large files and
technical logs are never returned as JSON payloads.

The WebUI adds an “推理环境” setup panel which:

- shows host checks, runtime/profile status and exact component list;
- expands source and license links;
- shows decimal GB/GiB download totals and free-space requirement;
- requires an explicit confirmation control;
- displays download/install progress, failure reason and safe retry;
- starts/stops only the owned runtime;
- shows readable recommendations for driver, disk, integrity, port, startup
  and node/profile failures.

It does not expose ComfyUI's own UI, arbitrary workflow upload, arbitrary
local paths, model switching, generation controls or automatic repair.

The existing project dashboard and 400–900 px Windows desktop behavior remain
intact. Mobile support is still outside the acceptance gate.

## 11. Error contract

Domain errors include a stable code, stage, user message, recommended actions,
safe technical detail and optional managed-log reference. Required categories
include:

- `UNSUPPORTED_HOST`;
- `NVIDIA_DRIVER_UNAVAILABLE`;
- `INSUFFICIENT_DISK_SPACE`;
- `INSTALL_CONFIRMATION_STALE`;
- `DOWNLOAD_FAILED`;
- `DOWNLOAD_SIZE_MISMATCH`;
- `DOWNLOAD_HASH_MISMATCH`;
- `UNSAFE_RUNTIME_ARCHIVE`;
- `RUNTIME_LAYOUT_INVALID`;
- `RUNTIME_CORRUPT`;
- `MODEL_ARTIFACT_MISSING`;
- `MODEL_ARTIFACT_CORRUPT`;
- `PORT_IN_USE`;
- `COMFYUI_START_FAILED`;
- `COMFYUI_EXITED`;
- `COMFYUI_UNHEALTHY`;
- `PROFILE_NODE_MISSING`;
- `WORKFLOW_INVALID`;
- `COMFYUI_DISCONNECTED`;
- `COMFYUI_TIMEOUT`;
- `COMFYUI_EXECUTION_FAILED`;
- `INSTALL_INTERRUPTED`.

No failure automatically changes prompt, seed, resolution, parallelism,
profile, workflow or installed selection. OOM remains a GenerationService
error exercised in Phase 5; Phase 4 may surface it from the real smoke tool
without adding automatic fallback.

## 12. Verification and exit gate

### 12.1 Ordinary automated tests

Tests use temporary directories and locally generated tiny artifacts to prove:

- strict manifest parsing, canonical digest and duplicate/path rejection;
- stale confirmation rejection before mutation;
- streaming/resume/redirect/timeout/size/hash/cancel behavior;
- safe 7z listing, extraction audit and atomic publication;
- idempotent install and corrupt/partial/interrupted classification;
- process ownership, port collision, early exit, timeout, stop and log
  rotation;
- HTTP/WebSocket happy path, disconnect, protocol error and execution error
  against a fake ComfyUI;
- workflow deep-copy, allowlisted mutation, node validation and fake second
  profile registration;
- schema-1 job compatibility and schema-2 frozen profile binding;
- API error envelopes and setup UI states;
- no read or write below any project `source/` or `pack/` directory.

### 12.2 Real manual evidence

On the selected Windows/NVIDIA machine:

1. inspect the plan before installation and capture exact totals;
2. confirm and download all selected components;
3. independently recompute every final SHA-256;
4. start the managed ComfyUI and capture version, Python, PyTorch, CUDA, GPU,
   driver and required-node status;
5. run the fixed text2img smoke with a repository-generated synthetic style
   reference;
6. run the fixed img2img smoke with synthetic style and structure references;
7. verify both outputs are square, decodable and associated with the requested
   seeds/workflow/profile digests;
8. stop and restart the owned runtime and verify health again;
9. verify the backend `.venv`, global environments, imported `source/` and
   project `pack/` hashes are unchanged.

Real outputs and logs remain ignored under `runtime/smoke/`. A small redacted
JSON evidence summary may be committed only after checking that it contains no
absolute user path or machine identifier.

The Phase 4 exit gate passes only when all automated tests pass and both real
smokes succeed. A successful download alone does not make the profile
supported.

## 13. Deferred work

Phase 4 deliberately does not implement:

- durable job execution or four-candidate scheduling;
- generation progress in the project dashboard;
- Phase 2 postprocessing invocation;
- candidate adoption or resource-pack export;
- automatic prompt construction or visual quality scoring;
- a production texture catalog;
- automatic runtime/model updates;
- arbitrary user model/workflow installation;
- FLUX.2 downloads or workflows;
- Linux, AMD, Intel, legacy CUDA portable or user-managed ComfyUI;
- mobile UI or a desktop application shell.
