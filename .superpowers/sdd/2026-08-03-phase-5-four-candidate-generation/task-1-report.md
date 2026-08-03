# Phase 5 Task 1 Report

## Implementation summary

- Added version-aware model-profile manifest support with `ProfileKey`, `ModelProfileManifestV2`, `WorkflowVariantRecord`, and `ModelProfileManifestRecord`.
- Changed manifest registry/profile catalog lookups to use exact `(profile_id, profile_version)` keys and deterministic tuple-key ordering.
- Kept legacy job binding on exact profile version `1`; it now explicitly rejects version `2` and any unverified profile for the old schema-2 binding path.
- Updated managed inference setup to target the additive Phase 5 setup profile key `("sdxl-mapchip-ipadapter", "2")`.
- Added `manifests/model-profiles/sdxl-mapchip-ipadapter-v2.json` as a candidate-unverified additive manifest reusing the verified version-1 artifact hashes.
- Added focused RED/GREEN coverage for manifest schema 2, versioned registry/catalog lookup, legacy v1 byte immutability, install-plan version awareness, and legacy binding rejection of v2.

## Files changed

- `backend/src/aimctexturegen/comfy/manifests.py`
- `backend/src/aimctexturegen/comfy/registry.py`
- `backend/src/aimctexturegen/comfy/installer.py`
- `backend/src/aimctexturegen/model_profiles/registry.py`
- `backend/src/aimctexturegen/model_profiles/workflows.py`
- `backend/src/aimctexturegen/inference/service.py`
- `backend/tests/comfy/_helpers.py`
- `backend/tests/comfy/test_manifests.py`
- `backend/tests/comfy/test_registry.py`
- `backend/tests/comfy/test_install_plan.py`
- `backend/tests/model_profiles/test_registry.py`
- `backend/tests/model_profiles/test_workflows.py`
- `manifests/model-profiles/sdxl-mapchip-ipadapter-v2.json`

## RED command/output and why it failed as expected

Command:

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\comfy\test_manifests.py backend\tests\comfy\test_registry.py backend\tests\model_profiles\test_registry.py -q
```

Observed failure:

```text
ImportError: cannot import name 'ModelProfileManifestV2' from 'aimctexturegen.comfy.manifests'
ImportError: cannot import name 'ModelProfileManifestV2' from 'aimctexturegen.comfy.manifests'
```

Why this was the expected RED:

- The brief required new schema-2/v2 manifest types and version-aware profile lookup.
- The pre-change code had neither `ModelProfileManifestV2` nor version-aware registry/profile catalog support, so the new tests failed immediately for the missing feature rather than for test mistakes.

## GREEN command/output

Focused GREEN:

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\comfy\test_manifests.py backend\tests\comfy\test_registry.py backend\tests\comfy\test_install_plan.py backend\tests\model_profiles\test_registry.py backend\tests\model_profiles\test_workflows.py -q
```

```text
129 passed in 0.62s
```

Regression requested by the brief:

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\api\test_inference.py backend\tests\jobs -q
```

```text
268 passed in 6.13s
```

## Other verification

- Independent v1 hash confirmation before edits:

```powershell
(Get-FileHash .\manifests\model-profiles\sdxl-mapchip-ipadapter-v1.json -Algorithm SHA256).Hash.ToLowerInvariant()
```

```text
9b909dc2d3b250f03b9a72996f43b6eaa3fa50f5eef0a38900e301a41678ccdd
```

- Full backend suite:

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests --cov=aimctexturegen --cov-report=term-missing
```

```text
895 passed in 123.28s, total coverage 86%
```

- Full frontend suite/build:

```powershell
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
```

```text
7 test files passed, 134 tests passed, production build succeeded
```

- Whitespace check:

```powershell
git diff --check
```

passed.

## Self-review findings

- Confirmed the immutable v1 manifest bytes stayed unchanged via explicit hash assertion and pre-edit hash check.
- Confirmed setup/install readiness now targets explicit profile version `2` without changing the old schema-2 binding path.
- Confirmed legacy binding still resolves only version `1` and now fails closed on version `2` with `PROFILE_CAPABILITY_MISMATCH`.
- Preserved user-owned `temp/` as untracked workspace state.

## Concerns

- No code-level blocker remains for Task 1.
- The new v2 manifest is intentionally still `candidate_unverified` with unlocked workflow digests; later Phase 5 tasks must add the real workflow files, generation binding, and qualification before any product path can treat v2 as verified.

---

## Fix round 1

### Issue addressed

- Reviewer Important: `backend/src/aimctexturegen/model_profiles/smoke.py` still used unversioned `registry.profile("sdxl-mapchip-ipadapter")` after the registry contract changed to require explicit version lookup.

### Changed files

- `backend/src/aimctexturegen/model_profiles/smoke.py`
- `backend/tests/model_profiles/test_smoke_inputs.py`

### RED command/output

Command:

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\model_profiles\test_smoke_inputs.py -q
```

Observed RED:

```text
ImportError: cannot import name 'resolve_smoke_profile' from 'aimctexturegen.model_profiles.smoke'
```

Why this was expected:

- The new regression test was written against a dedicated version-aware smoke profile resolver that did not exist yet, so the failure proved the legacy smoke path still lacked an explicit versioned lookup.

### GREEN commands/output

Focused GREEN:

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\model_profiles\test_smoke_inputs.py -q
```

```text
6 passed in 0.54s
```

Broader relevant regression:

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\model_profiles -q
```

```text
35 passed in 1.06s
```

Formatting check:

```powershell
git diff --check
```

passed.

### Fix summary

- Added `resolve_smoke_profile(registry)` to `model_profiles/smoke.py`.
- The smoke path now resolves the explicit verified legacy profile key `("sdxl-mapchip-ipadapter", "1")`.
- Added a focused regression test proving smoke resolution selects verified v1 even when v2 is also present in the registry.
