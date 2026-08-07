[CmdletBinding()]
param(
    [int]$Port = 8188
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$previousPythonPath = $null
$WrapperPath = $null
Push-Location $RepositoryRoot
try {
    $Python = Join-Path $RepositoryRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Repository Python is missing"
    }
    $SmokeDirectory = Join-Path $RepositoryRoot 'runtime\smoke\phase-5'
    $ignoreProbe = & git check-ignore --quiet -- $SmokeDirectory
    if ($LASTEXITCODE -ne 0) {
        throw 'runtime smoke output is not covered by git ignore'
    }
    $listeners = @(Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 -LocalPort $Port -ErrorAction SilentlyContinue)
    if ($listeners.Count -gt 0) {
        throw "PHASE5_SMOKE_FAILED port_${Port}_already_listening"
    }

    New-Item -ItemType Directory -Force -Path $SmokeDirectory | Out-Null
    $previousPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = Join-Path $RepositoryRoot 'backend\src'
    $code = @'
import json
import sys
from pathlib import Path

from aimctexturegen.model_profiles.smoke_v2 import (
    SmokeQualificationError,
    run_smoke_from_env,
)

evidence_path = Path(sys.argv[1])
try:
    evidence = run_smoke_from_env(port=int(sys.argv[2]))
except Exception as exc:
    if evidence_path.is_file():
        try:
            payload = json.loads(evidence_path.read_text(encoding="utf-8"))
            cells = payload.get("cells", [])
            statuses = [cell.get("status") for cell in cells]
            print("PHASE5_SMOKE_FAILED cells={} statuses={} restart_audit={} failure_type={}".format(
                len(cells), statuses, payload.get("restart_audit", "not_run"), type(exc).__name__
            ))
        except Exception:
            print(f"PHASE5_SMOKE_FAILED failure_type={type(exc).__name__}")
    else:
        print(f"PHASE5_SMOKE_FAILED failure_type={type(exc).__name__}")
    raise

print("PHASE5_SMOKE_COMPLETED cells={} statuses={} restart_audit={}".format(
    len(evidence.cells), [cell.status for cell in evidence.cells], evidence.restart_audit
))
'@
    $EvidencePath = Join-Path $SmokeDirectory 'evidence.json'
    $WrapperPath = Join-Path $SmokeDirectory ('.phase5-wrapper-' + [guid]::NewGuid().ToString('N') + '.py')
    [System.IO.File]::WriteAllText(
        $WrapperPath,
        $code,
        [System.Text.UTF8Encoding]::new($false)
    )
    & $Python $WrapperPath $EvidencePath $Port
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        exit $exitCode
    }
    Write-Host 'PHASE5_SMOKE_EVIDENCE runtime\smoke\phase-5\evidence.json'
}
finally {
    if ($null -ne $WrapperPath -and (Test-Path -LiteralPath $WrapperPath -PathType Leaf)) {
        Remove-Item -LiteralPath $WrapperPath -Force -ErrorAction SilentlyContinue
    }
    if ($null -eq $previousPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
    else {
        $env:PYTHONPATH = $previousPythonPath
    }
    Pop-Location
}
