# Task 13 report — synthetic end-to-end generation contracts

Status: DONE

## Scope

Task 13 now covers the real FastAPI/HTTP/WebSocket/service graph with only
project-generated synthetic assets and FakeComfy. The implementation also keeps
the pre-existing legacy `/jobs` transport working when its route overlaps the
schema-3 generation routes.

Changed intended files:

- `backend/src/aimctexturegen/api/generation.py`
- `backend/src/aimctexturegen/generation/coordinator.py`
- `backend/src/aimctexturegen/generation/service.py`
- `backend/tests/fakes/comfy_server.py`
- `backend/tests/integration/test_generation_flow.py`
- `backend/tests/integration/test_generation_cancel.py`
- `backend/tests/integration/test_generation_restart.py`
- `backend/tests/tools/test_synthetic_pack_generator.py`
- `tools/Generate-SyntheticPack.ps1`
- `ONBOARDING.md`
- `docs/superpowers/plans/2026-08-03-phase-5-four-candidate-generation.md`
- this report

The three production changes are confined to the Phase 5 generation API and
generation coordinator/service modules: JSON upload reference IDs are parsed at
the transport boundary, cancellation state writes retry bounded revision races,
and overlapping legacy/schema-3 job routes dispatch by the durable job schema.

## RED

Initial placeholder RED, before replacing the cancel/restart placeholders:

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\integration\test_generation_flow.py backend\tests\integration\test_generation_cancel.py backend\tests\integration\test_generation_restart.py -q
```

Result: `10 failed, 1 passed in 3.95s`.

The first real-graph run exposed five failures in 15 tests. Those failures
identified the upload-reference JSON contract, native img2img `amount` batch
shape, cancellation revision races, and transient state polling races. The
assertions were retained and the smallest boundary/fake corrections were made.

## GREEN and focused verification

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\tools\test_synthetic_pack_generator.py -q
```

Result: `2 passed in 1.68s`.

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\integration\test_generation_flow.py backend\tests\integration\test_generation_cancel.py backend\tests\integration\test_generation_restart.py -q
```

Result: `15 passed in 27.41s`.

The focused suite proves import/list/upload/create/start, schema-3 WebSocket
revisions, four PNG outputs and all artifact kinds, exact source/pack hashes,
atomic output counts and invalid outputs, typed disconnect/timeout/queue/
execution/OOM failures, confirmed cancellation, global slot conflict, restart
recovery, orphan prompt interruption, completed artifact survival, lineage
retry, raw-only postprocess, incomplete-batch rerun, and SQLite rebuild
invariance.

Legacy compatibility verification after the route-dispatch correction:

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\api\test_jobs.py backend\tests\api\test_services_and_errors.py -q
```

Result: `26 passed in 22.70s`.

## Required gates

Synthetic Phase 5 generator:

```powershell
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .\tools\Generate-SyntheticPack.ps1 -Phase5
```

Result: passed. Output was the ignored
`.generated\\phase-5-synthetic-pack.zip`, with:

- SHA-256 `a505a3b1dfff1762d8e743dcd45555e8157574897d9179a6ee389d4935ec11a8`
- `COVERAGE=pack_format=34;covered=1;missing=2;unknown=1`

The default generator test pins the unchanged default ZIP SHA-256 to
`8ec378c876fe12b17e784c2d03ee59e7ea8a6c1601d7bf00e0a36980e2d24478` and the
Phase 5 test audits that no real-asset path or file-reading API is present.

Full backend coverage:

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests --cov=aimctexturegen --cov-report=term-missing
```

Final result: `1124 passed in 181.49s (0:03:01)`, 1124 collected, total
coverage `85%` (`9525` statements, `1443` missed).

One earlier full-suite run had a transient background inference-install failure
(`1123 passed, 1 failed`); the isolated test passed `1/1`, and the exact full
command was rerun unchanged to the final `1124/1124` result. No inference code
was changed for that transient failure.

Frontend tests:

```powershell
Push-Location frontend
try { npm test }
finally { Pop-Location }
```

Result: 11 test files, `167 passed`.

Frontend build:

```powershell
Push-Location frontend
try { npm run build }
finally { Pop-Location }
```

Result: TypeScript and Vite production build passed; Vite transformed 27
modules.

Repository hygiene:

```powershell
git diff --check
git status --short
```

`git diff --check` passed. Git emitted only the existing Windows LF-to-CRLF
working-copy warnings. User-owned `.tmp-review-*` and `temp/` directories were
preserved and are not intended Task 13 files.

## Deferred Minors

- The fixed repository Node runtime path remains unavailable; the frontend
  gates used the documented global Node fallback.
- Real GPU/model/ComfyUI qualification and ignored real-pack manual acceptance
  remain Task 14 work. Task 13 uses FakeComfy only by design.

## Commit

Commit: pending
