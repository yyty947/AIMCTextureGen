# Phase 5 Task 2 Report

## Implementation summary

Implemented strict schema-3 generation persistence alongside legacy schema-1/schema-2 jobs without rewriting legacy bytes. Added new durable generation request/state models, discriminated codecs, pure generation state transitions, schema-aware store creation/loading/replacement, schema-3 frozen input snapshots, and index summaries that can read both legacy and generation job families.

## Files changed

- Added `backend/src/aimctexturegen/jobs/models_v3.py`
- Added `backend/src/aimctexturegen/jobs/codec.py`
- Added `backend/src/aimctexturegen/jobs/generation_state.py`
- Modified `backend/src/aimctexturegen/jobs/models.py`
- Modified `backend/src/aimctexturegen/jobs/store.py`
- Modified `backend/src/aimctexturegen/index/database.py`
- Modified `backend/src/aimctexturegen/index/service.py`
- Added `backend/tests/jobs/test_models_v3.py`
- Added `backend/tests/jobs/test_codec.py`
- Added `backend/tests/jobs/test_generation_state.py`
- Modified `backend/tests/jobs/test_store.py`
- Modified `backend/tests/index/test_rebuild.py`

## TDD RED

Command:

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\jobs\test_models_v3.py backend\tests\jobs\test_codec.py backend\tests\jobs\test_generation_state.py -q
```

Output excerpt:

```text
ModuleNotFoundError: No module named 'aimctexturegen.jobs.models_v3'
ModuleNotFoundError: No module named 'aimctexturegen.jobs.codec'
ModuleNotFoundError: No module named 'aimctexturegen.jobs.generation_state'
```

Why it failed as expected: the new schema-3 persistence/domain modules required by the brief did not exist yet.

## GREEN

Command:

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\jobs\test_models_v3.py backend\tests\jobs\test_codec.py backend\tests\jobs\test_generation_state.py -q
```

Output:

```text
31 passed in 0.10s
```

## Regression / full verification

Commands:

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\jobs backend\tests\index -q
.\.venv\Scripts\python -W error -m pytest backend\tests\integration\test_restart_recovery.py -q
git diff --check
```

Outputs:

```text
318 passed in 3.60s
1 passed in 0.40s
git diff --check exit 0 (only CRLF conversion warnings, no diff-check errors)
```

## Self-review findings

- Legacy request/state dump bytes still go through the existing serializers; schema-aware loading is discriminated by bounded JSON `schema_version`.
- Store corruption handling was preserved by wrapping malformed schema-aware decode failures back into `CORRUPT_JOB_RECORD`.
- SQLite job summary candidate-status checks were expanded for schema-3-only statuses (`raw_ready`, `inherited`) without changing legacy rows.

## Concerns

- No functional blockers found.
- `git diff --check` emitted CRLF normalization warnings for touched files, but returned success and no whitespace errors.

## Fix round 1 (2026-08-03)

### Reviewer findings addressed

- Schema-3 reload explicitly checks `references.json`, every style child, and
  `structure.png` as bounded regular non-reparse-point files using the repository
  `is_reparse_point()` helper before reading them.
- Reload cross-validates frozen metadata and staged inputs against the request:
  style/structure counts, relative paths, SHA-256 values, staged file set, and
  recalculated staged-file hashes must all match.
- Candidate artifact kinds are constrained to `raw/`, `processed/`, `previews/`,
  and `reports/` for raw, final, nearest/tile, and report respectively.

### Changed files

- `backend/src/aimctexturegen/jobs/models_v3.py`
- `backend/src/aimctexturegen/jobs/store.py`
- `backend/tests/jobs/test_models_v3.py`
- `backend/tests/jobs/test_store.py`

### TDD and verification evidence

Initial RED command:

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\jobs\test_models_v3.py backend\tests\jobs\test_store.py -q
```

Output: `9 failed, 51 passed in 1.50s`; the new placement assertions failed
because placement was not enforced, and the new reload cases were accepted or
reached inconsistent fixtures.

GREEN command:

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\jobs\test_models_v3.py backend\tests\jobs\test_store.py -q
```

Output: `60 passed in 1.15s`.

Regression commands and outputs:

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\jobs backend\tests\index -q
# 327 passed in 3.75s
.\.venv\Scripts\python -W error -m pytest backend\tests\integration\test_restart_recovery.py -q
# 1 passed in 0.64s
git diff --check
# exit code 0; only LF-to-CRLF normalization warnings, no whitespace errors
```

### Self-review

The fix is limited to schema-3 model/store validation and focused tests. Legacy
layouts and serializers are untouched. Reparse checks occur before input reads,
and mismatch failures are fail-closed as corrupt job records. Candidate output
placement is validated only in `CandidateArtifacts`, leaving frozen reference
inputs correctly under `inputs/`. No later-task code, runtime assets, or `temp/`
was modified.
