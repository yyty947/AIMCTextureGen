# AIMCTextureGen testing

Run these PowerShell blocks from the repository root. Commands that require the
ignored managed runtime or GPU are explicitly marked; ordinary CI must not
download multi-GB artifacts or launch the real ComfyUI.

The Phase 4 baseline was re-run on 2026-08-02 with Python 3.12.10 and global
Node v24.13.0 (the documented portable Node v24.18.0 directory is not present
in this checkout). The latest result is recorded in the handoff only after the
commands below are run on the current checkout.

## Phase 3 regression gate

```powershell
.\.venv\Scripts\python -W error -m pytest backend\tests --cov=aimctexturegen --cov-report=term-missing
```

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

## Phase 4 WebUI manual acceptance

The following checks are intentionally manual because they exercise a real
Windows browser, process lifecycle and layout. They do not require repeating
the multi-GB download when `runtime/` already contains the verified profile.

### Port 8000 troubleshooting

If starting FastAPI reports WinError 10048 on `127.0.0.1:8000`, do not start a
second copy. Check the listener and command line first:

```powershell
Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 -LocalPort 8000 |
  Select-Object OwningProcess
Get-CimInstance Win32_Process -Filter "ProcessId = <PID>" |
  Select-Object -ExpandProperty CommandLine
```

If the command line is the repository's `uvicorn aimctexturegen.main:app`,
reuse that server or stop only that verified PID before restarting it:

```powershell
Stop-Process -Id <PID>
```

Do not terminate an unknown process merely because it owns port 8000.

Open three PowerShell windows at the repository root.

Window 1 — FastAPI:

```powershell
.\.venv\Scripts\python -m uvicorn aimctexturegen.main:app --app-dir backend\src --host 127.0.0.1 --port 8000
```

Window 2 — Vite (use the globally installed Node in this checkout):

```powershell
Push-Location frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

Window 3 is reserved for the optional occupied-port check below. Open
`http://127.0.0.1:5173` in a Windows desktop browser.

### Already-installed status and no-redownload check

1. Expand **受管 ComfyUI 与模型配置**. Before and after a browser refresh,
   expect **主机支持：是**, the NVIDIA GPU/driver, **运行时：ready** and
   **profile components ready**. On the verified machine the plan shows all
   components as `ready`, download total `0.00 GB (0.00 GiB)`, and the install
   button is disabled; this is expected because the archive is deleted after
   verified publication.
2. In DevTools Network, refresh the panel once. Expect only `GET` requests for
   status/install-plan (and polling while expanded), no `POST /installations`.
   Do not click the install button in this already-ready state.

### Start, health, stop and restart

1. Click **启动受管 ComfyUI**. Expect the process line to become `ready` and
   the version to be `0.29.2`. A cold start may take up to about 60 seconds;
   the managed log button should return a bounded text tail after readiness.
2. Click **停止受管 ComfyUI**. Expect `stopped`; the button states should
   update without an error.
3. Repeat start and stop once. The second cycle should have the same result.

### Occupied-port safety check (optional but recommended)

1. Ensure the managed process is stopped. In Window 3 run:

   ```powershell
   .\.venv\Scripts\python.exe -c "import http.server; http.server.ThreadingHTTPServer(('127.0.0.1',8188), http.server.SimpleHTTPRequestHandler).serve_forever()"
   ```

2. Click **启动受管 ComfyUI**. Expect a readable `PORT_IN_USE` error that says
   port 8188 is occupied and recommends closing the other application/ComfyUI;
   it must explicitly say the app will not terminate the external process.
   The listener in Window 3 must remain running.
3. Stop the listener with `Ctrl+C`, then start and stop the managed runtime
   normally to prove recovery.

### Layout, console and existing data

1. Check the expanded panel at normal desktop width, then resize the browser
   viewport to approximately 400 px, 600 px and 900 px wide. Expect no
   horizontal scrollbar, clipped buttons, or inaccessible license/status text.
2. In DevTools Console, treat only application-origin errors as failures.
   Browser-extension messages such as `Extension context invalidated` and
   MIME-type warnings from the development server are not application errors.
3. Select an existing project and confirm its coverage summary and Phase 3
   queued job/four pending candidates are unchanged. Do not import a duplicate
   ZIP for this check.

Record each result and any screenshot or console line in the task response.
The Phase 4 branch remains open until these checks pass and the user explicitly
authorizes merge/push.
