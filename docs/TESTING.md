# AIMCTextureGen testing

Run these PowerShell blocks from the repository root. They are the current
Phase 3 closure commands and were executed on 2026-08-01.

## Full automated gate

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests --cov=aimctexturegen --cov-report=term-missing
```

```powershell
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

## Restart recovery audit

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\integration\test_restart_recovery.py -vv
```

The audit uses a real temporary imported project. It deletes the SQLite index,
migrates a schema-1 manifest, recovers an interrupted job, and asserts complete
path-to-SHA-256 map equality for `source/` and `pack/` before and after restart.

## Manual desktop recovery check

Use three PowerShell windows from the repository root. Start FastAPI in the
first window:

```powershell
.\.venv\Scripts\python -m uvicorn aimctexturegen.main:app --app-dir backend\src --host 127.0.0.1 --port 8000
```

Start Vite in the second window:

```powershell
Push-Location frontend
..\runtime\node-v24.18.0-win-x64\npm.cmd run dev -- --host 127.0.0.1 --port 5173
```

In the browser, import the ignored synthetic pack at
`.superpowers\sdd\2026-07-27-phase-3-durable-jobs-and-recovery\task-10-synthetic-pack.zip`
exactly once under a unique name, then use that same name below. It provides
the covered style reference `assets/minecraft/textures/block/stone.png` and
the missing eligible target `minecraft:deepslate`.

```powershell
$ErrorActionPreference = 'Stop'
$ProjectName = 'Task 10 Synthetic Pack <your-unique-suffix>'
$Projects = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8000/api/projects'
$SelectedProjects = @(
    $Projects | Where-Object { $_.project_name -eq $ProjectName }
)
if ($SelectedProjects.Count -ne 1) {
    throw "Expected exactly one matching project; found $($SelectedProjects.Count)."
}
$ProjectId = [string]$SelectedProjects[0].project_id

$Body = @{
    target_semantic_id = 'minecraft:deepslate'
    prompt = 'Task 10 synthetic cold blue-gray deepslate'
    resolution = 16
    parallelism = 1
    style_references = @('assets/minecraft/textures/block/stone.png')
    structure_reference = $null
} | ConvertTo-Json -Depth 4

$PostRequest = @{
    Method = 'Post'
    Uri = "http://127.0.0.1:8000/api/projects/$ProjectId/jobs"
    ContentType = 'application/json'
    Body = $Body
}
$Job = Invoke-RestMethod @PostRequest
if ($Job.state.status -ne 'queued' -or $Job.state.revision -ne 0) {
    throw 'Expected a revision-0 queued job.'
}
$Job | Select-Object @{Name='project_id'; Expression={$_.request.project_id}}, @{Name='job_id'; Expression={$_.request.job_id}}, @{Name='status'; Expression={$_.state.status}}, @{Name='revision'; Expression={$_.state.revision}}
```

Stop and restart both services, then select the same project under “已有项目”;
do not import again. Confirm format 34, one covered item, one missing item, the
queued job, and four pending candidates. Repeat at normal desktop, 400 px,
600 px, and 900 px widths. There must be no horizontal overflow, clipped
controls, application-origin console errors, or duplicate import. This manual
check does not edit job JSON or manufacture `JOB_INTERRUPTED`; that transition
is covered by the automated restart audit.

## Working-tree checks

```powershell
git diff --check
git status --short
```
