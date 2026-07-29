# Phase 3 Durable Jobs and Project Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended when multi-agent
> support is explicitly available) or `superpowers:executing-plans` to
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Add durable project restoration and four-candidate job records whose
JSON and artifacts survive restart, with a disposable SQLite query index that
can be rebuilt without modifying resource-pack files.

**Architecture:** Project directories remain the portable source of truth.
Strict, versioned JSON files record project and job state; every mutable JSON
write is validated in the destination directory and atomically replaced.
SQLite stores only rebuildable project/job summaries for queries. Startup
recovery migrates manifests, converts interrupted work into explicit failures,
and rebuilds the index without reading or writing `pack/` contents.

**Tech Stack:** Python 3.12 standard-library `sqlite3`, Pydantic 2.13.4,
FastAPI 0.139.2, React 19, TypeScript, Vitest, pytest 9.1.1.

## Global Constraints

- Work on `codex/phase-3-durable-jobs`; do not implement directly on `master`.
- Phase 3 requires no CUDA, GPU, ComfyUI, model files, or network access.
- Project JSON and job JSON/images are canonical. SQLite is a disposable query
  index and must never be the only copy of product state.
- Existing schema-1 projects must open and migrate without changing
  `source/imported-pack.zip` or any file below `pack/`.
- Candidate count is exactly four. Seeds are chosen once at job creation,
  persisted, unique within the job, and never changed by parallelism, restart,
  cancel, or retry.
- Seed values are integers in `[0, 9_007_199_254_740_991]`, the JavaScript safe
  integer range, so the API and WebUI preserve them exactly.
- Target resolution is exactly 16, 32, or 64; parallelism is exactly 1, 2, or 4.
- Style-reference count is exactly 1–8 for a created job. References are
  project-relative POSIX paths and must resolve to ordinary files inside the
  project working copy.
- A structure-reference path is optional; when present it must resolve to an
  ordinary file below `uploads/structure-references/`.
- Legal job states are `queued`, `generating`, `postprocessing`, `completed`,
  `failed`, and `canceled`. Illegal transitions fail before any file changes.
- Restart leaves `queued` and terminal jobs unchanged. It converts
  `generating` and `postprocessing` jobs to `failed` with
  `JOB_INTERRUPTED`; completed candidates remain completed.
- Retry creates a new job with a new job ID, preserves the original request
  and four seeds, and records `retry_of_job_id`. It never rewrites the source
  job.
- OOM and other failures never alter resolution, parallelism, prompt, seeds,
  references, candidates, or the resource-pack working copy.
- JSON writes use validated same-directory temporary files and `os.replace`.
  Creation of a job uses `<job-id>.tmp` followed by a directory rename.
- Project/job scanning rejects symlinks, junctions, reparse points, noncanonical
  UUID directory names, oversized JSON, duplicate identities, and paths that
  escape the configured project root.
- API routes perform request/response mapping only. Project loading, catalog
  checks, state transitions, recovery, and SQLite work stay in services.
- Ordinary CI uses temporary directories and synthetic files only. No Mojang
  or Microsoft assets are committed.
- UI validation targets Windows desktop windows, including 400–900 px narrow
  desktop widths. Mobile support is not an acceptance condition.

---

## Locked Data and Recovery Design

### Project manifest migration

`project.json` schema 2 adds:

```json
{
  "schema_version": 2,
  "default_resolution": 16,
  "default_parallelism": 1,
  "style_references": []
}
```

All schema-1 fields remain unchanged. Loading a schema-1 manifest validates it
strictly, constructs schema 2 with the defaults above, validates the serialized
replacement, and atomically replaces only `project.json`. Migration preserves
`created_at` and `updated_at`; a schema migration is not a user edit.

### Job directory

```text
projects/<project-id>/jobs/<job-id>/
├─ request.json
├─ state.json
├─ raw/
├─ processed/
├─ previews/
└─ reports/
```

The four candidate stems are `candidate-0` through `candidate-3`. Phase 5 will
write artifacts under these directories; Phase 3 creates the directories but
does not create placeholder images or reports.

`request.json` is immutable after the job directory is published. It contains
the selected catalog target, user prompt, resolution, parallelism, references,
four seeds, creation time, and optional retry lineage.

`state.json` is the only mutable job file in Phase 3. It contains a monotonically
increasing `revision`, aggregate job status, four candidate records, timestamps,
and an optional structured failure.

### State transitions

```text
queued         -> generating | canceled
generating     -> postprocessing | failed | canceled
postprocessing -> completed | failed | canceled
completed      -> (terminal)
failed         -> (terminal; retry creates a new job)
canceled       -> (terminal; retry creates a new job)
```

Candidate records use the same forward path plus terminal `failed` and
`canceled` states. Cancel changes every nonterminal candidate to `canceled` in
the same new `state.json` revision. Recovery changes active candidates to
`failed`, pending candidates to `canceled`, and preserves terminal candidates.

### SQLite index

The index path is `projects/.aimctexturegen/index.sqlite3`. It stores project
and job summary rows only and uses `PRAGMA user_version = 1`. Rebuild scans
canonical project/job JSON into memory, populates a new
`index.sqlite3.tmp`, closes and validates it, then replaces the old index.
No absolute user path, prompt, technical error detail, or artifact payload is
stored in SQLite.

Disk commits happen before index updates. If an index update fails, the JSON
commit remains authoritative; the service attempts one full rebuild and
returns `INDEX_UNAVAILABLE` if the rebuild also fails. It never rolls back or
deletes valid project/job JSON.

## Planned File Map

```text
backend/src/aimctexturegen/core/atomic_files.py
    Validated same-directory atomic byte replacement.
backend/src/aimctexturegen/core/relative_paths.py
    Shared strict project-relative POSIX path syntax.
backend/src/aimctexturegen/projects/models.py
    Schema-1 reader, schema-2 ProjectManifest, migration/dump functions.
backend/src/aimctexturegen/projects/repository.py
    Safe project open/list/migration; moved out of API routes.
backend/src/aimctexturegen/projects/service.py
    Import/get/list/coverage orchestration and project index updates.
backend/src/aimctexturegen/jobs/errors.py
    Job-domain errors independent of FastAPI.
backend/src/aimctexturegen/jobs/models.py
    Request, candidate, state, error, API command and summary models.
backend/src/aimctexturegen/jobs/state_machine.py
    Pure legal-transition and restart-recovery rules.
backend/src/aimctexturegen/jobs/store.py
    Atomic job creation, load, state replacement, cancel and retry.
backend/src/aimctexturegen/jobs/service.py
    Catalog/reference validation, seed generation and index coordination.
backend/src/aimctexturegen/index/models.py
    Rebuild and query summaries.
backend/src/aimctexturegen/index/database.py
    SQLite schema, replace-snapshot, upsert and list queries.
backend/src/aimctexturegen/index/service.py
    Disk-to-index snapshot construction and one-shot index repair.
backend/src/aimctexturegen/jobs/recovery.py
    Startup migration, interrupted-job recovery and index rebuild.
backend/src/aimctexturegen/api/jobs.py
    Job create/list/detail/cancel/retry HTTP mapping.
backend/src/aimctexturegen/api/system.py
    Read-only startup recovery report endpoint.
frontend/src/ProjectList.tsx
    Restorable project selection.
frontend/src/JobHistory.tsx
    Read-only durable job history.
frontend/src/api.ts
    Strict project-list, job and recovery response parsing.
frontend/src/App.tsx
    Import plus restored-project dashboard orchestration.
```

---

### Task 1: Close the Deferred Phase 2 Processing Items

**Files:**
- Modify: `backend/src/aimctexturegen/processing/errors.py`
- Modify: `backend/src/aimctexturegen/processing/grid_snap.py`
- Modify: `backend/src/aimctexturegen/processing/palette.py`
- Modify: `backend/src/aimctexturegen/processing/pipeline.py`
- Modify: `backend/src/aimctexturegen/processing/previews.py`
- Modify: `backend/src/aimctexturegen/processing/seam.py`
- Modify: `backend/src/aimctexturegen/processing/validation.py`
- Modify: `backend/tests/processing/test_pipeline.py`

**Interfaces:**
- Preserves: `process_candidate(source, output_dir, *, stem, resolution,
  palette_limit=None) -> ProcessingReport`.
- Adds errors: `INVALID_RESOLUTION`, `INVALID_OUTPUT_STEM`.
- Preserves Phase 2 algorithm and report versions.

- [x] **Step 1: Add failing validation and cleanup tests**

Add tests that call `process_candidate` with resolution `48`, stems `""`,
`"../candidate"`, `"folder/candidate"`, and `"folder\\candidate"`. Each must
raise `ProcessingError` before `output_dir` is created.

Add an opaque-RGBA end-to-end test:

```python
def test_pipeline_accepts_opaque_rgba_and_records_original_mode(tmp_path, png_path):
    source = png_path(Image.new("RGBA", (32, 32), (10, 20, 30, 255)))
    report = process_candidate(source, tmp_path / "out", stem="c", resolution=16)
    assert report.input_mode == "RGBA"
    assert Image.open(tmp_path / "out" / "c.png").mode == "RGB"
```

Monkeypatch `os.replace` to fail on the second artifact and assert that no
`*.tmp` file remains. Existing successfully replaced files may remain; Phase 3
does not add a cross-file transaction to the Phase 2 pipeline.

- [x] **Step 2: Run the tests and confirm RED**

Run:

```powershell
.\.venv\Scripts\python -m pytest backend\tests\processing\test_pipeline.py -v
```

Expected: invalid values fail too late or with Pydantic errors, the RGBA test
passes only after it is added, and failed replacement leaves a temp file.

- [x] **Step 3: Add preflight validation and reliable temp cleanup**

At the top of `process_candidate`, before decoding or creating directories:

```python
if resolution not in (16, 32, 64):
    raise ProcessingError("INVALID_RESOLUTION", "目标分辨率必须是 16、32 或 64")
if (
    not stem
    or stem in {".", ".."}
    or "/" in stem
    or "\\" in stem
    or "\x00" in stem
):
    raise ProcessingError("INVALID_OUTPUT_STEM", "候选文件名无效")
```

Wrap `_replace_into` in `try/finally`. In the `finally` block, remove the
temporary path only when it is still a regular non-reparse file directly below
the expected directory. Ignore `FileNotFoundError`; propagate the original
write/replace error.

Add concise module and public-function docstrings to the processing files that
still lack them. Do not change any numeric or palette behavior.

- [x] **Step 4: Run Phase 2 and full backend gates**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\processing -v
.\.venv\Scripts\python -W error -m pytest backend\tests
```

Expected: all tests pass with no warnings.

- [x] **Step 5: Commit**

```powershell
git add backend/src/aimctexturegen/processing backend/tests/processing
git commit -m "fix: close deferred Phase 2 processing guards"
```

---

### Task 2: Atomic Files and Project Manifest Schema 2

**Files:**
- Create: `backend/src/aimctexturegen/core/atomic_files.py`
- Create: `backend/src/aimctexturegen/core/relative_paths.py`
- Modify: `backend/src/aimctexturegen/projects/models.py`
- Create: `backend/tests/core/test_atomic_files.py`
- Create: `backend/tests/core/test_relative_paths.py`
- Modify: `backend/tests/projects/test_workspace.py`
- Create: `backend/tests/projects/test_manifest_migration.py`

**Interfaces:**
- Produces: `atomic_replace_bytes(destination: Path, payload: bytes,
  validator: Callable[[bytes], object]) -> None`.
- Produces: `validate_project_relative_path(value: str) -> str`.
- Produces: `ProjectManifestV1`, schema-2 `ProjectManifest`,
  `load_project_manifest(payload: bytes) -> tuple[ProjectManifest, bool]`,
  `ProjectSummary`, and `dump_project_manifest(manifest) -> bytes`.
- `bool` is `True` only when schema 1 was migrated.

- [x] **Step 1: Write failing atomic-file tests**

Cover:

- validator failure leaves the original destination byte-identical;
- successful replacement is byte-exact and leaves no `.tmp`;
- writer or publication failure removes only the exact temporary regular file;
- a stale bounded regular `.tmp` is removed before the new write;
- an existing temporary symlink/junction/reparse point is rejected and never
  followed or removed.

Run:

```powershell
.\.venv\Scripts\python -m pytest backend\tests\core\test_atomic_files.py -v
```

Expected: import failure naming `aimctexturegen.core.atomic_files`.

- [x] **Step 2: Implement validated atomic replacement**

`atomic_replace_bytes` must:

1. require an existing plain parent directory;
2. use `<destination-name>.tmp`;
3. remove an existing bounded regular temp file, but reject an unsafe one;
4. create exclusively (`"xb"` semantics), flush, and `os.fsync`;
5. read the bounded payload back and call `validator`;
6. on Windows, keep a read-only-sharing handle open through validation and
   publish that exact handle with `SetFileInformationByHandle(FileRenameInfo)`;
   the portable fallback rechecks identity immediately before `os.replace`;
7. remove the exact temp regular file in `finally`.

The helper raises `AtomicWriteError`; it does not import FastAPI or translate
user-facing errors.

- [x] **Step 3: Write failing schema and migration tests**

Use strict tests for schema-2 defaults and types:

```python
manifest = ProjectManifest(
    schema_version=2,
    # existing fields...
    default_resolution=16,
    default_parallelism=1,
    style_references=(),
)
assert manifest.schema_version == 2
```

Assert schema 1 migrates to schema 2 while preserving every old field and both
timestamps. Reject unsupported versions, coercion, more than eight references,
absolute/reference paths containing `..`, and extra fields.

Assert a new import writes schema 2 directly with resolution `16`, parallelism
`1`, and no style references.

- [x] **Step 4: Implement shared relative-path syntax**

`validate_project_relative_path` accepts only nonempty forward-slash paths
whose segments are neither empty, `.` nor `..`. It rejects leading slashes,
backslashes, drive/UNC/device prefixes, NUL, trailing slashes, Windows-invalid
characters and controls, trailing dots/spaces, and reserved device stems. It
performs syntax validation only; callers that read files must still hold the
containing directory identity and verify an ordinary non-reparse file.

Use this validator in project models and in the job Pydantic models added by
Task 4 so the two contracts cannot drift.

- [x] **Step 5: Implement strict project models**

Keep the current schema-1 model as `ProjectManifestV1`. Define schema 2 with
the existing fields plus:

```python
default_resolution: Literal[16, 32, 64]
default_parallelism: Literal[1, 2, 4]
style_references: tuple[str, ...] = Field(max_length=8)
```

Validate style paths with `validate_project_relative_path`.

`load_project_manifest` first decodes to a plain JSON object, inspects only the
integer `schema_version`, then strictly validates the matching model.
`dump_project_manifest` uses sorted keys, compact UTF-8 JSON, and one trailing
newline. Migration keeps both timestamps unchanged.

Define `ProjectSummary` from the stable list fields: project ID/name, edition,
Java pack format, catalog ID, and created/updated timestamps. It contains no
absolute path or source hash.

Update `ProjectWorkspace` to write schema 2 directly and validate the exact
bytes before publishing the project.

- [x] **Step 6: Run focused and full project tests**

```powershell
.\.venv\Scripts\python -m pytest backend\tests\core backend\tests\projects -v
```

Expected: all pass.

- [x] **Step 7: Commit**

```powershell
git add backend/src/aimctexturegen/core backend/src/aimctexturegen/projects backend/tests/core backend/tests/projects
git commit -m "feat: version and atomically migrate project manifests"
```

---

### Task 3: Safe Project Repository and Service Boundary

**Files:**
- Create: `backend/src/aimctexturegen/projects/repository.py`
- Create: `backend/src/aimctexturegen/projects/service.py`
- Modify: `backend/src/aimctexturegen/api/projects.py`
- Create: `backend/tests/projects/test_repository.py`
- Create: `backend/tests/projects/test_service.py`
- Modify: `backend/tests/api/test_projects.py`
- Modify: `backend/tests/api/test_services_and_errors.py`

**Interfaces:**
- Produces: frozen `OpenedProject(manifest, root, pack_root, jobs_root,
  uploads_root)`.
- Produces: `ProjectRepository.open(project_id)` context manager,
  `ProjectRepository.list_manifests() -> ProjectScanResult`.
- `ProjectScanResult` contains valid manifests and `ProjectScanIssue` values,
  both defined in `projects/repository.py`; recovery later translates those
  issues into its public report.
- Produces: `ProjectService.import_pack`, `get_project`, `list_projects`, and
  `get_coverage`.
- Removes filesystem, manifest, and catalog business logic from API routes.

- [x] **Step 1: Write repository RED tests**

Move the current API-level manifest safety cases to repository tests and add:

- schema-1 load performs one atomic schema-2 migration;
- migration never changes source/pack tree hashes;
- canonical UUID directories are listed in `updated_at DESC, project_id ASC`
  order;
- `.tmp`, `.aimctexturegen`, malformed names, junctions and symlinks are not
  followed;
- corrupt manifests produce a typed `ProjectRepositoryError` and do not hide
  valid sibling projects;
- open holds the project directory identity for the full context.

- [x] **Step 2: Implement `ProjectRepository`**

Move the bounded regular-file read and handle/path identity checks from
`api/projects.py` into `projects/repository.py`. Use
`MAX_PROJECT_MANIFEST_BYTES`, `load_project_manifest`, and
`atomic_replace_bytes`. A migrated manifest is replaced while the project
directory identity is held. Repository manifest writers are serialized, and
the migration validator rechecks that the observed destination identity and
exact schema-1 bytes still match before publication. Observable replacement is
preserved and reported as `PROJECT_MANIFEST_CONFLICT`. Per
[`ADR-0001`](../../adr/0001-running-project-mutation-boundary.md), one running
application process owns its project root: application writers are serialized
and use validated atomic publication, while manual/external mutation during
runtime and hostile CAS in the final OS-call window are unsupported. Do not use
deprecated TxF or require NTFS transactions.

`list_manifests` scans only direct children whose name equals `str(UUID(name))`.
Return both valid manifests and typed issues so startup recovery can report
corruption without aborting all projects.

- [x] **Step 3: Write and implement `ProjectService` tests**

The service composes `ProjectWorkspace`, `ProjectRepository`, catalog lookup,
coverage classification, and an index protocol:

```python
class ProjectIndexPort(Protocol):
    def upsert_project(self, manifest: ProjectManifest) -> None: ...
    def list_projects(self) -> tuple[ProjectSummary, ...]: ...
    def rebuild(self) -> None: ...

class ProjectService:
    def import_pack(self, source: Path, project_name: str) -> ProjectManifest: ...
    def get_project(self, project_id: UUID) -> ProjectManifest: ...
    def list_projects(self) -> tuple[ProjectSummary, ...]: ...
    def get_coverage(self, project_id: UUID) -> CoverageReport: ...
```

Disk import is authoritative. After a successful import, an index failure
triggers one index rebuild through the injected index protocol; a second
failure becomes `INDEX_UNAVAILABLE` without deleting the project.

- [x] **Step 4: Thin the project API routes**

Add `GET /api/projects` and change existing endpoints to parse canonical UUIDs,
call `ProjectService`, and map typed domain errors. Delete route-local project
manifest opening, catalog classification, and directory traversal helpers once
their repository/service equivalents are covered.

- [x] **Step 5: Run project/API regression tests**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\projects backend\tests\api -v
```

Expected: all pass and API routes no longer open `project.json` or `pack/`.

- [x] **Step 6: Commit**

```powershell
git add backend/src/aimctexturegen/projects backend/src/aimctexturegen/api/projects.py backend/tests/projects backend/tests/api
git commit -m "refactor: move project persistence behind services"
```

---

### Task 4: Job Contracts and Pure State Machine

**Files:**
- Create: `backend/src/aimctexturegen/jobs/__init__.py`
- Create: `backend/src/aimctexturegen/jobs/errors.py`
- Create: `backend/src/aimctexturegen/jobs/models.py`
- Create: `backend/src/aimctexturegen/jobs/state_machine.py`
- Create: `backend/tests/jobs/test_models.py`
- Create: `backend/tests/jobs/test_state_machine.py`

**Interfaces:**
- Produces: `JobRequest`, `JobStateRecord`, `CandidateRecord`, `JobFailure`,
  `JobSummary`, `CreateJobCommand`.
- Produces: `validate_job_pair(request: JobRequest, state: JobStateRecord)
  -> None` for cross-file ID, seed and candidate-index consistency.
- Produces: `transition_job_state`, `transition_candidate_state`,
  `cancel_state`, and `recover_interrupted_state`.
- Produces: deterministic `dump_job_request` and `dump_job_state`.

- [x] **Step 1: Write strict model tests**

`JobRequest` contains:

```python
schema_version: Literal[1]
job_id: UUID
project_id: UUID
retry_of_job_id: UUID | None
catalog_id: str
target_semantic_id: str
target_display_name: str
target_relative_path: str
prompt: str
resolution: Literal[16, 32, 64]
parallelism: Literal[1, 2, 4]
style_references: tuple[str, ...]
structure_reference: str | None
seeds: tuple[int, int, int, int]
created_at: AwareDatetime
```

`JobStateRecord` contains schema version, IDs, `revision`, aggregate status,
four `CandidateRecord` values, optional failure, and created/updated/started/
finished timestamps.

Tests reject coercion, duplicate or out-of-range seeds, wrong candidate
indices, seed mismatch between request and candidates, invalid paths, empty or
over-4000-code-point prompts, style counts outside 1–8, naive timestamps,
unknown fields, and mutation.

- [x] **Step 2: Implement models and deterministic dumps**

Use frozen strict Pydantic models. Define:

```python
JobStatus = Literal[
    "queued", "generating", "postprocessing",
    "completed", "failed", "canceled",
]
CandidateStatus = Literal[
    "pending", "generating", "postprocessing",
    "completed", "failed", "canceled",
]
```

`JobFailure` uses `code`, `stage`, `user_message`,
`recommended_actions: tuple[str, ...]`, `technical_details: str | None`, and
`log_reference: str | None`. Dumps are sorted, compact UTF-8 JSON with a
trailing newline. `validate_job_pair` requires equal project/job IDs and exactly
four candidate records whose indices and seeds match the request tuple in
order; `JobStore.load` must call it before returning a job.

- [x] **Step 3: Write the state-machine matrix tests**

Parametrize every legal and illegal edge. Assert:

- illegal transitions return `INVALID_JOB_TRANSITION` without mutating input;
- `started_at` is set only on first transition to `generating`;
- terminal transitions set `finished_at`;
- failure data is required only for `failed`;
- cancel changes all nonterminal candidates in one revision;
- recovery leaves queued/terminal states byte-equivalent;
- recovery converts active jobs to `failed/JOB_INTERRUPTED`, active candidates
  to failed, pending candidates to canceled, and preserves completed candidates.

- [x] **Step 4: Implement pure transition functions**

Every function receives a record and explicit `now` time, returns a new record,
and performs no I/O. Increment `revision` exactly once per returned update.
Use the locked transition table above; do not add `paused`, `interrupted`, or
automatic retry states.

- [x] **Step 5: Run the job-contract tests**

```powershell
.\.venv\Scripts\python -m pytest backend\tests\jobs\test_models.py backend\tests\jobs\test_state_machine.py -v
```

Expected: all pass.

- [x] **Step 6: Commit**

```powershell
git add backend/src/aimctexturegen/jobs backend/tests/jobs
git commit -m "feat: define durable job contracts and state machine"
```

---

### Task 5: Atomic Job Store, Four Seeds, Cancel and Retry

**Files:**
- Create: `backend/src/aimctexturegen/jobs/store.py`
- Create: `backend/src/aimctexturegen/jobs/service.py`
- Create: `backend/tests/jobs/test_store.py`
- Create: `backend/tests/jobs/test_service.py`

**Interfaces:**
- Produces: frozen `LoadedJob(request, state, root)` plus
  `JobStore.create`, `load`, `list`, `replace_state`, `cancel`,
  `retry`, and `recover_interrupted`.
- Produces: frozen `JobScanResult(jobs, issues)` and path-free
  `JobScanIssue`; `JobStore.scan` isolates malformed canonical siblings while
  `list` remains fail-fast.
- Produces: `JobService.create_job`, `get_job`, `list_jobs`, `cancel_job`,
  `retry_job`.
- Consumes project repository, catalog registry and an index-writer protocol.

- [x] **Step 1: Write job-store creation and safety tests**

Assert creation:

- publishes exactly the locked directory layout;
- writes immutable `request.json` and revision-0 queued `state.json`;
- initializes candidate indices 0–3 with the corresponding persisted seeds;
- leaves no job or `.tmp` directory after any injected failure;
- rejects a pre-existing job ID, unsafe project/jobs paths, reparse points,
  oversized JSON, request/state ID mismatch and noncanonical job directories;
- never reads or writes `source/` or `pack/`.

- [x] **Step 2: Implement `JobStore`**

Create `<job-id>.tmp`, capture its identity, create the four artifact
directories, atomically write and re-read both JSON files, verify the tree has
no reparse points, then rename to `<job-id>`.

State replacement holds the project, jobs and job directory identities, checks
`expected_revision`, writes a validated `state.json.tmp`, and replaces only
`state.json`. A stale revision raises `JOB_REVISION_CONFLICT`.

- [x] **Step 3: Write cancel, retry and concurrency tests**

Cover:

- cancel queued/generating/postprocessing jobs;
- cancel of a terminal job fails without disk changes;
- retry is allowed only for failed or canceled jobs;
- retry publishes a new job ID, preserves every request field and seed except
  `job_id`, `retry_of_job_id`, and `created_at`;
- retry never changes original files;
- two updates using the same expected revision produce one success and one
  `JOB_REVISION_CONFLICT`;
- repeated load/list order is deterministic (`created_at DESC`, job ID ASC).

- [x] **Step 4: Implement `JobService` validation and seed creation**

`CreateJobCommand` identifies a catalog target and user inputs but contains no
job ID or seeds. `JobService.create_job`:

1. opens the project through `ProjectRepository`;
2. resolves `catalog_id` and exact `semantic_id`;
3. requires an MVP-eligible missing target;
4. verifies 1–8 style references are ordinary files inside `pack/`;
5. verifies an optional structure reference inside
   `uploads/structure-references/`;
6. draws four unique JavaScript-safe seeds from an injected seed source;
7. persists the job through `JobStore`;
8. upserts the index after the disk commit.

Use an injected deterministic seed source in tests. Retry calls `JobStore.retry`
and preserves seeds; it does not call the seed source.

- [x] **Step 5: Run store/service tests**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\jobs -v
```

Expected: all pass.

- [x] **Step 6: Commit**

```powershell
git add backend/src/aimctexturegen/jobs backend/tests/jobs
git commit -m "feat: persist and manage four-candidate jobs"
```

---

### Task 6: Disposable SQLite Query Index

**Files:**
- Create: `backend/src/aimctexturegen/index/__init__.py`
- Create: `backend/src/aimctexturegen/index/models.py`
- Create: `backend/src/aimctexturegen/index/database.py`
- Create: `backend/src/aimctexturegen/index/service.py`
- Create: `backend/tests/index/test_database.py`
- Create: `backend/tests/index/test_rebuild.py`

**Interfaces:**
- Produces: `ProjectIndex.upsert_project`, `upsert_job`, `list_projects`,
  `list_jobs`, and `replace_snapshot`.
- Produces: `IndexSnapshot(projects, jobs)`.
- Produces: `IndexService.rebuild()` plus guarded upsert/query methods that
  attempt one rebuild after an SQLite failure.
- Uses only Python `sqlite3`; no runtime dependency is added.

- [x] **Step 1: Write schema and query RED tests**

Verify `PRAGMA user_version == 1`, foreign keys are enabled, project/job IDs
are canonical UUID text, timestamps sort correctly, retry lineage is
queryable, and neither prompts, seeds, file-system paths nor technical details
appear in the database schema or rows.

Assert queries return frozen summary models rather than raw SQLite rows.

- [x] **Step 2: Implement the connection and schema**

Open one connection per operation with a finite busy timeout and:

```sql
PRAGMA foreign_keys = ON;
CREATE TABLE projects (...);
CREATE TABLE jobs (
    job_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    ...
);
CREATE INDEX jobs_project_updated
    ON jobs(project_id, updated_at DESC, job_id ASC);
PRAGMA user_version = 1;
```

All writes use explicit transactions. Reject unknown schema versions rather
than silently upgrading them. Every public operation lazily ensures the
schema, so tests and injected services remain usable even when FastAPI lifespan
has not run.

- [x] **Step 3: Write atomic rebuild tests**

Build an `IndexSnapshot`, replace the index, reopen it, and compare every
summary. Inject population/validation/replace failures and assert the previous
index remains usable and `index.sqlite3.tmp` is removed.

Delete the final index, call `replace_snapshot`, and verify identical project
and job visibility.

- [x] **Step 4: Implement `replace_snapshot`**

Create `.aimctexturegen` as a plain directory, populate
`index.sqlite3.tmp`, run `PRAGMA integrity_check`, verify row counts and
foreign keys, close all handles, then `os.replace` it onto the final path.
Never inspect or change resource-pack files.

- [x] **Step 5: Implement disk-to-index coordination**

`IndexService` receives `ProjectRepository`, `JobStore`, and `ProjectIndex`.
`rebuild()` scans valid project manifests and jobs, constructs an
`IndexSnapshot`, and calls `replace_snapshot`. Upsert/query methods attempt the
database operation once; on `sqlite3.DatabaseError`, they perform one rebuild
and retry once. A second failure raises `IndexUnavailableError` without
changing canonical JSON.

- [x] **Step 6: Run index tests**

```powershell
.\.venv\Scripts\python -m pytest backend\tests\index -v
```

Expected: all pass.

- [x] **Step 7: Commit**

```powershell
git add backend/src/aimctexturegen/index backend/tests/index
git commit -m "feat: add rebuildable SQLite project and job index"
```

---

### Task 7: Startup Migration and Interrupted-Job Recovery

**Files:**
- Create: `backend/src/aimctexturegen/jobs/recovery.py`
- Create: `backend/tests/jobs/test_recovery.py`
- Create: `backend/tests/integration/test_restart_recovery.py`

**Interfaces:**
- Produces: `RecoveryService.run() -> RecoveryReport`.
- `RecoveryReport`, defined in `jobs/recovery.py`, contains counts, immutable
  `RecoveryIssue` values and a completion timestamp.
- Consumes `ProjectRepository`, `JobStore`, and `IndexService`.

- [x] **Step 1: Write recovery service tests**

Prepare multiple synthetic projects/jobs:

- queued;
- generating with one completed candidate and one active candidate;
- postprocessing;
- completed;
- failed;
- canceled;
- one malformed job beside valid siblings.

Assert one run:

- migrates schema-1 projects;
- preserves queued and terminal state bytes;
- marks only active jobs `failed/JOB_INTERRUPTED`;
- preserves completed candidate state;
- reports malformed jobs without hiding valid siblings;
- rebuilds index summaries from the final recovered JSON state;
- is idempotent on a second run.

- [x] **Step 2: Implement recovery scanning**

`RecoveryService.run`:

1. asks `ProjectRepository` for valid projects and issues;
2. lists each canonical job directory through `JobStore`;
3. calls `recover_interrupted_state` only for active jobs;
4. persists recovered state through revision-checked atomic replacement;
5. asks `IndexService` to rebuild from the final disk state;
6. returns a frozen report.

Do not repair malformed JSON by guessing and do not delete corrupt entries.

- [x] **Step 3: Write the restart integration test**

Use a real temporary project import, real repository/store/index and two app
instances. Before restart, hash every file under `source/` and `pack/`. Delete
the SQLite index between instances. Start the second app and assert:

- the project and all valid jobs remain visible;
- completed jobs remain completed;
- active jobs are exposed as failed with `JOB_INTERRUPTED`;
- queued jobs remain queued;
- source and pack path/byte hash maps are exactly unchanged.

- [x] **Step 4: Run recovery and integration tests**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\jobs\test_recovery.py backend\tests\integration\test_restart_recovery.py -v
```

Expected: all pass.

- [x] **Step 5: Commit**

```powershell
git add backend/src/aimctexturegen/jobs/recovery.py backend/tests/jobs/test_recovery.py backend/tests/integration/test_restart_recovery.py
git commit -m "feat: recover projects and interrupted jobs on restart"
```

---

### Task 8: FastAPI Job and Recovery Endpoints

**Files:**
- Create: `backend/src/aimctexturegen/api/jobs.py`
- Create: `backend/src/aimctexturegen/api/system.py`
- Modify: `backend/src/aimctexturegen/main.py`
- Modify: `backend/src/aimctexturegen/api/projects.py`
- Create: `backend/tests/api/test_jobs.py`
- Create: `backend/tests/api/test_recovery.py`
- Modify: `backend/tests/api/test_services_and_errors.py`

**Interfaces:**
- Adds:
  - `GET /api/projects`
  - `POST /api/projects/{project_id}/jobs`
  - `GET /api/projects/{project_id}/jobs`
  - `GET /api/projects/{project_id}/jobs/{job_id}`
  - `POST /api/projects/{project_id}/jobs/{job_id}/cancel`
  - `POST /api/projects/{project_id}/jobs/{job_id}/retry`
  - `GET /api/system/recovery`
- Adds app lifespan startup recovery.

- [x] **Step 1: Write API contract tests**

Test canonical UUID enforcement, strict request values, exact four seeds,
deterministic ordering, detail responses, cancel/retry lineage, not-found,
revision conflict, invalid transition, unsafe reference, corrupt JSON, and
index-unavailable error envelopes.

The create request accepts:

```json
{
  "target_semantic_id": "minecraft:deepslate",
  "prompt": "cold blue-gray stone",
  "resolution": 16,
  "parallelism": 1,
  "style_references": [
    "assets/minecraft/textures/block/stone.png"
  ],
  "structure_reference": null
}
```

Seeds and IDs are server-generated and returned, not accepted from the client.

- [x] **Step 2: Expand `AppServices` and lifespan**

Construct and inject `ProjectRepository`, `ProjectService`, `JobStore`,
`JobService`, `ProjectIndex`, and `RecoveryService`. The FastAPI lifespan runs
recovery before yielding and stores the report at
`app.state.recovery_report`. `ProjectIndex` opens connections per operation,
so lifespan owns no long-lived SQLite connection.

Injected test services remain supported; tests must not touch the default
repository project root.

- [x] **Step 3: Implement thin routes and error translation**

Routes parse canonical IDs, validate Pydantic commands, invoke services, and
return models. Map job/repository/index errors to the existing stable envelope.
Return technical details only when the domain error explicitly marks them safe.

`GET /api/system/recovery` returns counts and user-readable issues; it does not
return absolute paths.

- [x] **Step 4: Run all API tests**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\api -v
```

Expected: all pass.

- [x] **Step 5: Commit**

```powershell
git add backend/src/aimctexturegen/api backend/src/aimctexturegen/main.py backend/tests/api
git commit -m "feat: expose durable project and job history APIs"
```

---

### Task 9: WebUI Project Restoration and Job History

**Files:**
- Create: `frontend/src/ProjectList.tsx`
- Create: `frontend/src/JobHistory.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/styles.css`
- Create: `frontend/src/ProjectList.test.tsx`
- Create: `frontend/src/JobHistory.test.tsx`
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes project-list, project-detail, coverage, job-list and recovery APIs.
- Produces a desktop project selector and read-only job history.
- Does not add target/reference/generation controls; those remain Phase 5.

- [x] **Step 1: Add strict API parsing tests**

Add `ProjectSummary`, `JobSummary`, `JobDetail`, `CandidateRecord`, and
`RecoveryReport` TypeScript interfaces. Test rejection of noncanonical UUIDs,
unsafe integers, wrong seed count, duplicate seeds, unknown states, naive
timestamps, negative revisions, and malformed retry lineage.

Implement:

```typescript
listProjects(): Promise<readonly ProjectSummary[]>
listJobs(projectId: string): Promise<readonly JobSummary[]>
getJob(projectId: string, jobId: string): Promise<JobDetail>
getRecoveryReport(): Promise<RecoveryReport>
```

- [x] **Step 2: Write component RED tests**

Test:

- startup lists existing projects without requiring ZIP re-import;
- choosing a project loads coverage and job history;
- a newly imported project appears and becomes selected;
- job rows show target, resolution, parallelism, four candidate counts, state,
  updated time, and retry lineage;
- `JOB_INTERRUPTED` displays a readable recovery explanation;
- malformed/corrupt recovery issues appear as a warning without hiding valid
  projects;
- empty history clearly says no generation jobs exist;
- request failures preserve the selected project and expose retry actions.

- [x] **Step 3: Implement focused components**

`ProjectList` owns no network state; it renders passed summaries and emits a
selected project ID. `JobHistory` is read-only and renders passed jobs. `App`
owns loading/error orchestration and continues to use the existing import form.

Keep Phase 1 import behavior and accessibility labels intact. Change the hero
copy from “Phase 1” to a product-neutral project dashboard label.

- [x] **Step 4: Add desktop-responsive styles**

At wide widths, show project navigation and the selected project dashboard in
two columns. At 400–900 px desktop widths, stack them without horizontal
overflow. Do not add mobile-only navigation or touch-specific behavior.

- [x] **Step 5: Run frontend tests and build**

```powershell
Push-Location frontend
..\runtime\node-v24.18.0-win-x64\npm.cmd test
..\runtime\node-v24.18.0-win-x64\npm.cmd run build
Pop-Location
```

Expected: all tests and the production build pass.

- [x] **Step 6: Commit**

```powershell
git add frontend/src
git commit -m "feat: restore projects and show durable job history"
```

---

### Task 10: Full Gates, Manual Desktop Procedure, and Handoff

**Files:**
- Modify: `ONBOARDING.md`
- Modify: `docs/superpowers/plans/2026-07-21-aimc-texturegen-mvp-roadmap.md`
- Create: `docs/TESTING.md` only if every command recorded below exists and was
  actually run by this task.

**Interfaces:**
- No new runtime interface.
- Records exact automated and manual evidence and identifies Phase 4 as next.

- [ ] **Step 1: Run the complete automated gate**

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests --cov=aimctexturegen --cov-report=term-missing
Push-Location frontend
..\runtime\node-v24.18.0-win-x64\npm.cmd test
..\runtime\node-v24.18.0-win-x64\npm.cmd run build
Pop-Location
git diff --check
git status --short
```

Record exact test counts and coverage. Do not copy the Phase 2 totals.

- [ ] **Step 2: Run the no-pack-mutation recovery audit**

Run the restart integration test separately with `-W error -vv`, then inspect
its recorded source/pack hash maps. The test must prove index deletion,
schema migration and interrupted-job recovery do not change any pack file.

- [ ] **Step 3: Give the user the manual desktop test procedure**

Ask the user to:

1. start FastAPI and Vite using the documented development commands;
2. import a synthetic Java pack and note its project ID;
3. use the exact PowerShell `Invoke-RestMethod` command supplied by this task
   to create one queued job through `POST /api/projects/{project-id}/jobs`;
   the command must use a covered synthetic style-reference path and a missing
   eligible target from the imported pack;
4. restart both services and open the same project from “已有项目” without
   re-importing;
5. confirm coverage and the queued job-history row remain visible;
6. repeat at a normal desktop width and at representative 400 px, 600 px and
   900 px window widths;
7. confirm no horizontal page overflow, clipped controls, application-origin
   console errors, or duplicate imports.

Pass criteria: all steps succeed; browser-extension errors and Vite development
MIME warnings remain third-party/development noise unless they originate from
application code. `JOB_INTERRUPTED` presentation remains an automated component
and restart-integration assertion; the manual procedure does not edit job JSON
or expose an internal transition endpoint merely to manufacture that state.

- [ ] **Step 4: Update handoff documents**

Update `ONBOARDING.md` with:

- Phase 3 branch and exact completed commits;
- schema-2 project migration and job/index/recovery contracts;
- exact automated and user-confirmed manual results;
- any unresolved defects;
- Phase 4 managed ComfyUI/model-profile planning as the next work entry.

Update the roadmap Phase 3 exit gate only with facts actually demonstrated.
Do not claim real GPU, model, ComfyUI or production catalog support.

- [ ] **Step 5: Commit closure**

```powershell
git add ONBOARDING.md docs
git commit -m "docs: record Phase 3 recovery gate results"
```

- [ ] **Step 6: Prepare the branch for review**

Use `superpowers:requesting-code-review`, address findings, rerun the full
gate, then use `superpowers:finishing-a-development-branch`. Merge or push only
after explicit user authorization.

---

## Plan Self-Review

- Phase 2 deferred cleanup is Task 1 and lands before any GenerationService can
  consume `process_candidate`.
- Durable request records, four seeds, artifact layout, legal transitions,
  cancellation and retry lineage are Tasks 4–5.
- JSON-as-truth and schema-1 migration are Tasks 2–3.
- SQLite query/index rebuild is Task 6.
- restart recovery and pack immutability are Task 7 plus Task 10's audit.
- project restoration and job history APIs/UI are Tasks 8–9.
- No task installs models, runs ComfyUI, touches CUDA, builds a production
  catalog, adopts a candidate into `pack/`, or exports a ZIP.
- Every code task has a focused RED/GREEN command and an independently
  reviewable commit.
