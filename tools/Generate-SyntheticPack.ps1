[CmdletBinding()]
param(
    [Parameter()]
    [string] $OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Add-DeterministicZipEntry {
    param(
        [Parameter(Mandatory)]
        [System.IO.Compression.ZipArchive] $Archive,

        [Parameter(Mandatory)]
        [string] $Name,

        [Parameter(Mandatory)]
        [byte[]] $Content
    )

    $entry = $Archive.CreateEntry(
        $Name,
        [System.IO.Compression.CompressionLevel]::NoCompression
    )
    $entry.LastWriteTime = [DateTimeOffset]::new(
        1980,
        1,
        1,
        0,
        0,
        0,
        [TimeSpan]::Zero
    )
    $entryStream = $entry.Open()
    try {
        $entryStream.Write($Content, 0, $Content.Length)
    }
    finally {
        $entryStream.Dispose()
    }
}

$repositoryRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path (
        Join-Path $repositoryRoot ".generated"
    ) "phase-3-synthetic-pack.zip"
}
elseif (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $repositoryRoot $OutputPath
}

$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
if ([System.IO.Path]::GetExtension($resolvedOutput) -cne ".zip") {
    throw "OutputPath must end with the lowercase .zip extension."
}

$outputDirectory = Split-Path -Parent $resolvedOutput
[System.IO.Directory]::CreateDirectory($outputDirectory) | Out-Null
$publicationId = [Guid]::NewGuid().ToString("N")
$temporaryPath = Join-Path (
    $outputDirectory
) (".phase-3-synthetic-pack-{0}.tmp" -f $publicationId)
$backupPath = Join-Path (
    $outputDirectory
) (".phase-3-synthetic-pack-{0}.backup" -f $publicationId)

# Both payloads are project-owned synthetic bytes. No Mojang assets are used.
$metadata = [System.Text.UTF8Encoding]::new($false).GetBytes(
    '{"pack":{"pack_format":34,"description":"AIMCTextureGen synthetic Phase 3 test pack"}}'
)
$stoneTexture = [Convert]::FromBase64String(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGN0SGhgYGBgYgADAA1qASTihlfEAAAAAElFTkSuQmCC"
)

try {
    $fileStream = [System.IO.File]::Open(
        $temporaryPath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    try {
        $archive = [System.IO.Compression.ZipArchive]::new(
            $fileStream,
            [System.IO.Compression.ZipArchiveMode]::Create,
            $false
        )
        try {
            Add-DeterministicZipEntry `
                -Archive $archive `
                -Name "pack.mcmeta" `
                -Content $metadata
            Add-DeterministicZipEntry `
                -Archive $archive `
                -Name "assets/minecraft/textures/block/stone.png" `
                -Content $stoneTexture
        }
        finally {
            $archive.Dispose()
        }
    }
    finally {
        $fileStream.Dispose()
    }

    if ([System.IO.File]::Exists($resolvedOutput)) {
        [System.IO.File]::Replace(
            $temporaryPath,
            $resolvedOutput,
            $backupPath
        )
        [System.IO.File]::Delete($backupPath)
    }
    else {
        [System.IO.File]::Move($temporaryPath, $resolvedOutput)
    }

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    $publishedStream = [System.IO.File]::OpenRead($resolvedOutput)
    try {
        $digestBytes = $sha256.ComputeHash($publishedStream)
    }
    finally {
        $publishedStream.Dispose()
        $sha256.Dispose()
    }
    $digest = -join ($digestBytes | ForEach-Object { $_.ToString("x2") })
    Write-Output ("OUTPUT_PATH={0}" -f $resolvedOutput)
    Write-Output ("SHA256={0}" -f $digest)
    Write-Output "COVERAGE=pack_format=34;covered=1;missing=1;unknown=0"
}
finally {
    if ([System.IO.File]::Exists($temporaryPath)) {
        Remove-Item -LiteralPath $temporaryPath -Force
    }
    if ([System.IO.File]::Exists($backupPath)) {
        Remove-Item -LiteralPath $backupPath -Force
    }
}
