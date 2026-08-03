# Phase 5 Task 6 Report

Date: 2026-08-03
Task: Extend the generic Comfy transport for ordered batches and prompt-scoped cancellation

## Implementation summary

- Extended `ComfyClient` with typed declared-output descriptors, deterministic output-node extraction, typed output downloads, queue ownership snapshots, prompt-scoped interrupt, and cancel-aware completion waiting.
- Added `ComfyCanceledError` as the transport-level cancellation signal.
- Extended `FakeComfyServer` to emulate prompt-scoped `/queue`, `/interrupt`, per-filename `/view`, and held WebSocket behavior needed by cancel-aware waiting tests.
- Preserved existing v1 client surfaces (`upload_image`, `submit_prompt`, `get_history`, `get_output`, `interrupt()` without prompt ID, and progress-filtered `wait_completion`) while layering the new Task 6 behavior on top.

## Files changed

- `backend/src/aimctexturegen/comfy/client.py`
- `backend/src/aimctexturegen/comfy/errors.py`
- `backend/tests/fakes/comfy_server.py`
- `backend/tests/comfy/test_client_http.py`
- `backend/tests/comfy/test_client_websocket.py`
- `backend/tests/comfy/test_client_errors.py`

## RED evidence

Ran before implementation:

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\comfy\test_client_http.py backend\tests\comfy\test_client_websocket.py backend\tests\comfy\test_client_errors.py -q
```

Observed expected collection failures because the new transport contract did not exist yet:

- `ImportError: cannot import name 'ComfyOutputImage'`
- `ImportError: cannot import name 'ComfyCanceledError'`

This confirmed the RED tests were targeting missing Task 6 API surface rather than unrelated behavior.

## GREEN evidence

Ran after implementation:

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\comfy\test_client_http.py backend\tests\comfy\test_client_websocket.py backend\tests\comfy\test_client_errors.py -q
```

Result:

- `29 passed in 20.46s`

Key newly covered behaviors:

- ordered extraction from the declared output node only;
- rejection of missing, duplicate, malformed, unsafe, and wrong-type output descriptors;
- bounded typed output download;
- queue snapshots returning prompt ownership only;
- prompt-scoped interrupt payloads;
- `wait_completion(..., cancel_requested=...)` raising `ComfyCanceledError` within the brief’s prompt bound.

## Regression results

Also ran:

```powershell
git diff --check
```

Result: passed.

The isolation test still passes, so `aimctexturegen.comfy.client` continues to avoid importing product-layer modules.

## Self-review

- Kept transport changes product-agnostic; no generation/coordinator/job imports were introduced.
- Preserved old `get_output(history_entry, filename)` behavior by implementing it via the new typed descriptor path.
- Kept `interrupt()` backward-compatible by allowing `prompt_id=None` while sending targeted JSON only when provided.
- Ensured cancellation polling is deadline-bounded by short WebSocket receive windows instead of sleeping or blocking until the full timeout.
- Tightened fake-server cleanup for held WebSocket tests to avoid leftover async tasks after cancellation scenarios.

## Concerns

- `ComfyOutputImage.type` is constrained to `"output"` as required by the brief; if a future profile legitimately needs another Comfy `/view` type, that should be introduced as a separate explicit contract rather than widening this Task 6 transport surface silently.

## Round 1 fix report

Date: 2026-08-03

### Findings fixed

- Restored the pre-Task-6 `get_output(history_entry, filename)` request path. It still requires the filename to be declared, but forwards the declared `subfolder` and original `type` directly to `/view`, including legacy values such as `"temp"`. The strict `ComfyOutputImage` parser remains enforced by the new ordered declared-output API and `get_output_image()`.
- Made the fake `GET /history/{prompt_id}` response prompt-scoped: it now returns only `{prompt_id: history_entry}` for the requested prompt, or `{}` when no dictionary entry exists.

### Changed files

- `backend/src/aimctexturegen/comfy/client.py`
- `backend/tests/fakes/comfy_server.py`
- `backend/tests/comfy/test_client_http.py`
- `.superpowers/sdd/2026-08-03-phase-5-four-candidate-generation/task-6-report.md`

### RED evidence

Added focused tests for both findings and ran:

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\comfy\test_client_http.py -q -k "legacy_get_output_forwards_non_output_type_to_view or history_endpoint_returns_only_requested_prompt"
```

Expected failures were observed: `2 failed, 20 deselected`. The legacy test failed with `ComfyUnsafeOutputError` for `type="temp"`; the history test showed the response incorrectly contained `p2`.

### GREEN and verification evidence

The focused command then passed with `2 passed, 20 deselected in 1.97s`.

The requested transport regression command passed with `31 passed in 21.93s`:

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\comfy\test_client_http.py backend\tests\comfy\test_client_websocket.py backend\tests\comfy\test_client_errors.py -q
```

`git diff --check` passed. Its only output was Git's normal LF-to-CRLF working-copy warning. The transport isolation test remains included in the passing error-test set.

### Self-review

- The legacy path is behaviorally separate from strict typed descriptor validation and retains the old response-size and HTTP-status checks.
- The fake records the exact `/view` query and the regression asserts `type="temp"` was sent; the history regression asserts `p2` is absent from `/history/p1`.
- Queue snapshots, prompt-scoped interrupts, cancel-aware WebSocket waiting, and the product-agnostic import boundary were not changed by this fix.
- No generation/coordinator/API/frontend code, real assets, or later task files were touched. The unrelated untracked `temp/` directory was preserved.

### Round 1 concerns

None blocking.
