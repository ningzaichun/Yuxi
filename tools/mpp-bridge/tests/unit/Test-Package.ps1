[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$bridgeRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$builder = Join-Path $bridgeRoot 'src/Build-MppBridgePackage.ps1'
$testRoot = Join-Path $bridgeRoot ".m4-runs/package-test/$(New-Guid)"
$first = & $builder -OutputDirectory (Join-Path $testRoot 'a') | ConvertFrom-Json
$second = & $builder -OutputDirectory (Join-Path $testRoot 'b') | ConvertFrom-Json
if ($first.zip_sha256 -ne $second.zip_sha256 -or $first.content_sha256 -ne $second.content_sha256) {
    throw 'Package ZIP is not reproducible.'
}

Add-Type -AssemblyName System.IO.Compression
$archive = [System.IO.Compression.ZipFile]::OpenRead($first.zip_file)
try {
    $manifestEntry = $archive.GetEntry('package-manifest.json')
    if ($null -eq $manifestEntry) { throw 'Package Manifest is missing.' }
    $reader = [System.IO.StreamReader]::new($manifestEntry.Open())
    try { $manifestJson = $reader.ReadToEnd() }
    finally { $reader.Dispose() }
    $manifestSchema = Join-Path $bridgeRoot 'schemas/mpp_bridge_package_manifest_v1.schema.json'
    if (-not ($manifestJson | Test-Json -SchemaFile $manifestSchema)) {
        throw 'Package Manifest failed Schema validation.'
    }
    $manifest = $manifestJson | ConvertFrom-Json
    if (@($archive.Entries).Count -ne @($manifest.files).Count + 1) {
        throw 'Package entry count differs from Manifest.'
    }
    foreach ($file in @($manifest.files)) {
        $entry = $archive.GetEntry([string]$file.path)
        if ($null -eq $entry -or $entry.Length -ne $file.bytes) {
            throw "Package entry is missing or has the wrong size: $($file.path)"
        }
        $stream = $entry.Open()
        try {
            $sha = [System.Security.Cryptography.SHA256]::Create()
            try { $hash = [Convert]::ToHexString($sha.ComputeHash($stream)).ToLowerInvariant() }
            finally { $sha.Dispose() }
        }
        finally { $stream.Dispose() }
        if ($hash -ne $file.sha256) {
            throw "Package entry hash differs from Manifest: $($file.path)"
        }
    }
}
finally { $archive.Dispose() }

"MPP Bridge package: PASS ($($first.zip_sha256))"
