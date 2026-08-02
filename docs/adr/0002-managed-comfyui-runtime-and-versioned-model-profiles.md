# ADR-0002: Managed ComfyUI Portable Runtime and Versioned Model Profiles

- Status: Accepted
- Date: 2026-08-01
- Decision owners: project maintainer and Phase 4 implementers

## Context

AIMCTextureGen must be usable from a GitHub checkout on Windows without
modifying a user's global Python, an existing ComfyUI installation, or the
backend `.venv`. It also needs reproducible GPU behavior: ComfyUI, custom
nodes, workflows and model files cannot silently change after a task is
created.

The planned first inference stack uses SDXL, mapchipLora and IP-Adapter.
FLUX.2 Klein 4B is a plausible later alternative because it provides native
text-to-image, image-to-image and multi-reference capabilities. Treating the
first SDXL stack as hard-coded product logic would make that later change a
rewrite and would make old jobs impossible to explain or reproduce.

## Decision

### Managed runtime

Phase 4 will download and verify the official Windows NVIDIA portable ComfyUI
archive. The first candidate lock is:

- release: `v0.29.2`;
- upstream commit:
  `322122449c9d2ba8b8df1bb517364527dd0615f1`;
- asset: `ComfyUI_windows_portable_nvidia.7z`;
- size: `2,103,175,457` bytes;
- SHA-256:
  `e7a39a817002d85b4fb2d4f6bd176c10d104a0d04031f99b9d8b7b1fd920c6fc`;
- source:
  <https://github.com/Comfy-Org/ComfyUI/releases/tag/v0.29.2>.

The word “candidate” is intentional. The manifest may be committed with these
values, but the runtime becomes a supported project profile only after the
archive is downloaded, independently hashed, extracted, started and used for
both required real workflow smoke tests.

The portable runtime is installed below the repository-ignored `runtime/`
directory and uses its own embedded Python and PyTorch. It does not install
packages into the backend `.venv`, global Python, Miniconda or a user-managed
ComfyUI. The application never scans for or adopts another ComfyUI
installation.

Runtime installation is explicit and confirmation-gated. The user sees the
component sources, licenses, exact download sizes and required free-space
estimate before any network or filesystem mutation. Downloads and extraction
use staging paths, validate hashes and layouts, and publish only complete
installations. Updates install side-by-side and switch a small validated
selection record; they never mutate a running runtime in place.

### Versioned model profiles

Runtime installation and model configuration are separate concepts:

- `RuntimeManifest` identifies the portable ComfyUI distribution and its host
  requirements.
- `ModelProfileManifest` identifies model/custom-node artifacts, capabilities,
  workflow API JSON, defaults and compatible runtime versions.

The first profile is `sdxl-mapchip-ipadapter-v1`. It is not represented by
SDXL-specific fields in project, task, API or UI orchestration code. A
profile-specific workflow binding translates generic product inputs into
ComfyUI node inputs.

Every newly created durable generation request records a frozen profile
binding: profile ID/version, manifest digest, runtime ID/version and workflow
digest. Existing schema-1 Phase 3 requests remain readable and immutable as
legacy unbound requests. They are not silently assigned the newest profile and
cannot be executed until a new, explicitly bound job is created.

Future stacks such as FLUX.2 Klein 4B are added under a new profile ID and
workflow binding. They do not replace or reinterpret the SDXL profile. Old
jobs therefore retain their original meaning even when the default profile
changes.

## Consequences

### Positive

- A GitHub checkout gets an isolated, known ComfyUI/Python/PyTorch stack.
- Hash and license records are visible before download and remain auditable.
- Runtime updates and model-profile changes are independently testable.
- FLUX.2 Klein or another backend can be added as a profile instead of
  rewriting jobs, projects, postprocessing or candidate storage.
- Ordinary CI can use fake manifests, archives, processes and ComfyUI
  transports without a GPU or large downloads.

### Costs and limitations

- The initial download is large and duplicates software a user may already
  have installed.
- Each runtime upgrade is explicit and needs new real smoke evidence.
- SDXL's IP-Adapter implementation currently depends on the maintenance-only
  `cubiq/ComfyUI_IPAdapter_plus` custom node. Its exact commit must remain
  pinned and compatibility must be proven against the selected ComfyUI
  runtime.
- Adding FLUX.2 later still requires new manifests, workflows, parameter
  calibration and visual validation. The decision prevents an infrastructure
  rewrite; it does not make model quality interchangeable.
- Phase 4 supports only the selected official NVIDIA portable variant.
  Supporting the CUDA 12.6 legacy portable, AMD, Intel, Linux, user-managed
  runtimes or automatic runtime selection requires a separate decision.

## Rejected alternatives

### Install ComfyUI into the backend `.venv`

Rejected because PyTorch/CUDA and node dependencies would couple the API
environment to GPU inference, make upgrades risky and make clean removal or
repair difficult.

### Reuse any ComfyUI found on the machine

Rejected because it requires filesystem scanning, cannot guarantee node or
model versions, and risks modifying user-owned installations.

### Hard-code the SDXL workflow in `GenerationService`

Rejected because model-specific node IDs and parameters would leak into
durable product state and make a later FLUX.2 profile a cross-project
migration.

### Mutate the existing profile when models change

Rejected because old task records would refer to a moving target. Material
artifact, workflow or default changes require a new profile version.

## Verification note (2026-08-02)

Task 10 real verification produced these measured corrections, which update
the candidate values above without changing the decision:

- The official portable archive's single root is
  `ComfyUI_windows_portable` (candidate value corrected in the runtime
  manifest).
- The archive uses a BCJ2 filter that the pinned py7zr cannot extract;
  inventory preflight still uses py7zr, while extraction shells to the
  OS-bundled `tar.exe` (libarchive) with fixed, shell-free arguments after
  the full safe-member preflight. No new dependency is downloaded.
- The first profile uses the `STANDARD (medium strength)` IPAdapter preset
  matching `ip-adapter_sdxl_vit-h.safetensors` (the PLUS variant is not
  bundled); multi-image style references are combined through LoadImage +
  ImageBatch with `combine_embeds=average`; img2img upscales the structure
  reference to 1024 via `ImageScale`.
- Both real smokes completed at 1024×1024 on the 16 GB development GPU;
  the profile is now `verified` (see `docs/MODEL_PROFILES.md` and
  `docs/evidence/phase-4/evidence.json`). These measurements do not
  constitute an 8 GB claim.
