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
