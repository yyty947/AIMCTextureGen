param(
    [int]$Port = 8188
)

$ErrorActionPreference = 'Stop'
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
Push-Location $RepositoryRoot
try {
    $Python = Join-Path $RepositoryRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $Python)) {
        throw "Repository Python was not found at $Python"
    }
    $SmokeDirectory = Join-Path $RepositoryRoot 'runtime\smoke'
    New-Item -ItemType Directory -Force -Path $SmokeDirectory | Out-Null
    $env:PYTHONPATH = Join-Path $RepositoryRoot 'backend\src'
    $code = @'
import json
import sys
from aimctexturegen.model_profiles.smoke import run_smoke_from_env

evidence = run_smoke_from_env()
path = sys.argv[1]
with open(path, 'w', encoding='utf-8') as output:
    json.dump(evidence.model_dump(mode='json'), output, ensure_ascii=False, indent=2)
print('SMOKE_COMPLETED statuses=' + str([r.status for r in evidence.results]))
'@
    $EvidencePath = Join-Path $SmokeDirectory 'evidence.json'
    & $Python -c $code $EvidencePath
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    Write-Host "SMOKE EVIDENCE: $EvidencePath"
}
finally {
    Pop-Location
}
