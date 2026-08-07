[CmdletBinding()]
param(
    [string]$SourceZip,
    [string]$OutputZip
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$PackRoot = Join-Path $RepositoryRoot 'runtime\manual-test-packs\phase-5'
if ([string]::IsNullOrWhiteSpace($SourceZip)) {
    $SourceZip = Join-Path $PackRoot 'third-party.zip'
}
if ([string]::IsNullOrWhiteSpace($OutputZip)) {
    $OutputZip = Join-Path $PackRoot 'third-party-missing-deepslate.zip'
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$RemovedMember = 'assets/minecraft/textures/block/deepslate.png'
$RequiredStyleMember = 'assets/minecraft/textures/block/stone.png'

function Get-FullPath([string]$Path) {
    return [System.IO.Path]::GetFullPath($Path)
}

function Get-Sha256([string]$Path) {
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    $stream = $null
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        return ([System.BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        if ($null -ne $stream) { $stream.Dispose() }
        $algorithm.Dispose()
    }
}

function Normalize-ZipMember([string]$Name) {
    $normalized = $Name.Replace('\', '/')
    if ([string]::IsNullOrWhiteSpace($normalized) -or
        $normalized.StartsWith('/') -or
        $normalized -match '^[A-Za-z]:/' -or
        $normalized.Split('/') | Where-Object { $_ -in @('', '.', '..') }) {
        throw "Unsafe ZIP member name: $Name"
    }
    return $normalized
}

function Read-ZipBytes($Entry) {
    $input = $null
    $memory = $null
    try {
        $input = $Entry.Open()
        $memory = New-Object System.IO.MemoryStream
        $input.CopyTo($memory)
        return $memory.ToArray()
    }
    finally {
        if ($null -ne $memory) { $memory.Dispose() }
        if ($null -ne $input) { $input.Dispose() }
    }
}

function Read-ZipRecords([string]$Path) {
    $archive = $null
    try {
        $archive = [System.IO.Compression.ZipFile]::OpenRead($Path)
        $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
        $records = New-Object System.Collections.Generic.List[object]
        foreach ($entry in $archive.Entries) {
            $name = Normalize-ZipMember $entry.FullName
            if (-not $seen.Add($name)) {
                throw "Case-insensitive ZIP member collision: $name"
            }
            $isDirectory = $entry.FullName.EndsWith('/') -or [string]::IsNullOrEmpty($entry.Name)
            $bytes = if ($isDirectory) { [byte[]]@() } else { Read-ZipBytes $entry }
            $records.Add([pscustomobject]@{
                Name = $name
                IsDirectory = $isDirectory
                Bytes = $bytes
            })
        }
        return $records.ToArray()
    }
    finally {
        if ($null -ne $archive) { $archive.Dispose() }
    }
}

function Assert-PackSource($Records) {
    $packRecord = $Records | Where-Object { $_.Name -eq 'pack.mcmeta' }
    if ($null -eq $packRecord -or $packRecord.IsDirectory) {
        throw 'Source ZIP is missing root pack.mcmeta'
    }
    try {
        $metadata = [System.Text.Encoding]::UTF8.GetString($packRecord.Bytes) | ConvertFrom-Json
    }
    catch {
        throw 'Source pack.mcmeta is not valid JSON'
    }
    $packObject = $metadata.pack
    if ($null -eq $packObject -or
        -not ($packObject.PSObject.Properties.Name -contains 'pack_format')) {
        throw 'Source pack.mcmeta is missing primary pack_format; refusing to guess'
    }
    $packFormat = $packObject.pack_format
    if ([int]$packFormat -ne 34) {
        throw "Source ZIP must have pack_format 34; found $packFormat"
    }
    $removed = $Records | Where-Object { $_.Name -eq $RemovedMember }
    if ($null -eq $removed -or $removed.IsDirectory) {
        throw "Source ZIP is missing the required root member $RemovedMember"
    }
    $style = $Records | Where-Object { $_.Name -eq $RequiredStyleMember }
    if ($null -eq $style -or $style.IsDirectory) {
        throw "Source ZIP is missing the required style member $RequiredStyleMember"
    }
}

function Write-DerivedZip($Records, [string]$Path) {
    $stream = $null
    $archive = $null
    try {
        $stream = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
        $archive = [System.IO.Compression.ZipArchive]::new(
            $stream,
            [System.IO.Compression.ZipArchiveMode]::Create,
            $false
        )
        foreach ($record in $Records) {
            if ($record.Name -eq $RemovedMember) { continue }
            $entry = $archive.CreateEntry(
                $record.Name,
                [System.IO.Compression.CompressionLevel]::Optimal
            )
            $entry.LastWriteTime = [DateTimeOffset]::new(
                1980, 1, 1, 0, 0, 0, [TimeSpan]::Zero
            )
            if (-not $record.IsDirectory) {
                $output = $null
                try {
                    $output = $entry.Open()
                    $output.Write($record.Bytes, 0, $record.Bytes.Length)
                }
                finally {
                    if ($null -ne $output) { $output.Dispose() }
                }
            }
        }
    }
    finally {
        if ($null -ne $archive) { $archive.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function Assert-DerivedZip([string]$Path, $ExpectedRecords) {
    $actual = Read-ZipRecords $Path
    $expectedNames = @($ExpectedRecords | Where-Object { $_.Name -ne $RemovedMember } | ForEach-Object Name)
    $actualNames = @($actual | ForEach-Object Name)
    if ($actualNames.Count -ne $expectedNames.Count -or
        -not (@(Compare-Object -ReferenceObject $expectedNames -DifferenceObject $actualNames).Count -eq 0)) {
        throw 'Derived ZIP member set does not match the source minus exactly one member'
    }
    if ($actualNames -notcontains $RequiredStyleMember) {
        throw 'Derived ZIP lost the required stone style member'
    }
    $sourceOverlays = @($ExpectedRecords | Where-Object { $_.Name.StartsWith('overlays/') } | ForEach-Object Name)
    $derivedOverlays = @($actual | Where-Object { $_.Name.StartsWith('overlays/') } | ForEach-Object Name)
    if ($sourceOverlays.Count -ne $derivedOverlays.Count -or
        -not (@(Compare-Object -ReferenceObject $sourceOverlays -DifferenceObject $derivedOverlays).Count -eq 0)) {
        throw 'Derived ZIP overlays were not preserved'
    }
    return $actual
}

$sourceFull = Get-FullPath $SourceZip
$outputFull = Get-FullPath $OutputZip
if (-not (Test-Path -LiteralPath $sourceFull -PathType Leaf)) {
    throw "Source ZIP does not exist: $sourceFull"
}
if ([System.StringComparer]::OrdinalIgnoreCase.Equals($sourceFull, $outputFull)) {
    throw 'Output ZIP must be a derived path different from the source ZIP'
}

$outputParent = Split-Path -Parent $outputFull
New-Item -ItemType Directory -Force -Path $outputParent | Out-Null
$temporary = Join-Path $outputParent ('.phase5-pack-' + [guid]::NewGuid().ToString('N') + '.tmp.zip')
$sourceHashBefore = Get-Sha256 $sourceFull
$records = $null
try {
    $records = Read-ZipRecords $sourceFull
    Assert-PackSource $records
    Write-DerivedZip $records $temporary
    $derivedRecords = Assert-DerivedZip $temporary $records
    $sourceHashAfter = Get-Sha256 $sourceFull
    if ($sourceHashBefore -ne $sourceHashAfter) {
        throw 'Source ZIP changed while preparing the derived ZIP'
    }
    Move-Item -LiteralPath $temporary -Destination $outputFull -Force
    $sourceHashFinal = Get-Sha256 $sourceFull
    if ($sourceHashBefore -ne $sourceHashFinal) {
        throw 'Source ZIP changed after derived ZIP publication'
    }
    [ordered]@{
        status = 'prepared'
        source_member_count = @($records).Count
        derived_member_count = @($derivedRecords).Count
        removed_member = $RemovedMember
        stone_preserved = $true
        overlays_preserved = $true
        source_sha256 = $sourceHashFinal
        derived_sha256 = Get-Sha256 $outputFull
        output = $outputFull
    } | ConvertTo-Json -Compress
}
catch {
    if (Test-Path -LiteralPath $temporary -PathType Leaf) {
        Remove-Item -LiteralPath $temporary -Force
    }
    throw
}
