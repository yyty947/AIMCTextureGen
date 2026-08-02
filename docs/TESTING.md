# AIMCTextureGen testing

Run these PowerShell blocks from the repository root. They are the current
Phase 3 closure commands and were executed on 2026-08-01.

Latest final-review result: 616 backend tests passed at 89% coverage
(3,483 statements, 392 missing), 6 frontend files / 113 tests passed, the
19-module production build passed, and the separate restart and generator
audits each passed.

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

## Tracked synthetic-pack fixture

Generate the project-owned fixture from any clean checkout:

```powershell
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .\tools\Generate-SyntheticPack.ps1
```

The default output is the ignored
`.generated\phase-3-synthetic-pack.zip`. The generator prints its resolved
path, SHA-256 and expected classification. The deterministic current SHA-256 is
`8ec378c876fe12b17e784c2d03ee59e7ea8a6c1601d7bf00e0a36980e2d24478`;
the expected catalog result is `pack_format=34;covered=1;missing=1;unknown=0`.
It contains only a generated `pack.mcmeta` and a project-owned uniform 2×2 RGB
PNG at `assets/minecraft/textures/block/stone.png`; it contains no game asset.

Verify the generator independently:

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\tools\test_synthetic_pack_generator.py -vv
```

The test runs the PowerShell generator three times across two paths containing
spaces, including replacement at an existing output path. It compares the ZIP
bytes, checks the reported digest/classification, and validates member order,
fixed timestamps, metadata and synthetic pixels.

## Manual desktop recovery check

Use three PowerShell windows from the repository root. Start FastAPI in the
first window. Generate the fixture above before importing it.

```powershell
.\.venv\Scripts\python -m uvicorn aimctexturegen.main:app --app-dir backend\src --host 127.0.0.1 --port 8000
```

Start Vite in the second window:

```powershell
Push-Location frontend
..\runtime\node-v24.18.0-win-x64\npm.cmd run dev -- --host 127.0.0.1 --port 5173
```

In the browser, import the generated synthetic pack at
`.generated\phase-3-synthetic-pack.zip`
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

## Phase 4 managed inference gate (2026-08-02)

真实 GPU 冒烟入口（安装/重启审计已包含在内，只写 `runtime/` 忽略目录）：

```powershell
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .\tools\Invoke-Phase4Smoke.ps1
```

预期输出：`SMOKE_COMPLETED statuses=['completed', 'completed']`，并在
`runtime\smoke\evidence.json` 写出脱敏证据。该命令会解压/校验受管运行时、
复用已就位模型、启动本机受管 ComfyUI（8188）、执行两次真实推理并做
重启审计；需要 NVIDIA GPU 且不能与占用 8188 端口的程序同时运行。

新增的确定性测试套件（无需 GPU）：

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests\model_profiles backend\tests\comfy -q
```
