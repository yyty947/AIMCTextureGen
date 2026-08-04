# Task 12 report — live candidate streaming and actions

Status: DONE

Files changed:

- `frontend/src/generation/api.ts`
- `frontend/src/generation/useJobEvents.ts`
- `frontend/src/generation/useJobEvents.test.tsx`
- `frontend/src/generation/CandidateStep.tsx`
- `frontend/src/generation/CandidateStep.test.tsx`
- `frontend/src/generation/GenerationWizard.tsx`
- `frontend/src/generation/GenerationWizard.test.tsx`
- `frontend/src/JobHistory.tsx`
- `frontend/src/JobHistory.test.tsx`
- `frontend/src/styles.css`
- `docs/superpowers/plans/2026-08-03-phase-5-four-candidate-generation.md`
- `ONBOARDING.md`

What changed:

- Added strict schema-3 WebSocket snapshot parsing plus durable HTTP refresh and reconnect logic in `useJobEvents`.
- Added controlled report/artifact accessors and current/retry/cancel generation APIs.
- Added Candidate Step rendering for four stable candidate cards, artifact tabs, read-only batch seed/position, conflict/failure guidance, and continue/cancel/retry actions.
- Wired the wizard to keep queued/failed generation jobs visible, recover current conflict targets, and switch into the live candidate step only for renderable schema-3 details.
- Marked legacy history rows as read-only without executable controls.
- Added focused RED/GREEN tests for live events, candidate actions, wizard conflict/start handling, and legacy history labeling.
- Added functional layout styles for the Phase 5 candidate surface.

Verification evidence:

- RED:
  - `npm test -- src/generation/useJobEvents.test.tsx src/generation/CandidateStep.test.tsx src/JobHistory.test.tsx`
  - Initial result failed because `useJobEvents.ts` and `CandidateStep.tsx` did not exist.
- GREEN / focused:
  - `npm test -- src/generation/useJobEvents.test.tsx src/generation/CandidateStep.test.tsx src/JobHistory.test.tsx`
  - Result: 10/10 tests passed.
- Full frontend gate:
  - `npm test`
  - Result: 161/161 tests passed.
- Build:
  - `npm run build`
  - Result: passed (`vite build` completed successfully).
- Repo hygiene:
  - `git diff --check`
  - Result: passed.

Manual inspection:

- No targeted desktop visual inspection was run in this task.

Deferred minors:

- None recorded in this task.

Commit:

- Created as commit `801ca3c` with message `feat: display live generation candidates`.
