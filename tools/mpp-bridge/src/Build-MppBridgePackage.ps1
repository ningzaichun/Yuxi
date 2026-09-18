[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$packageVersion = '0.5.0-y0'
$bridgeRoot = Split-Path -Parent $PSScriptRoot
$outputRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
if (-not (Test-Path -LiteralPath $outputRoot)) { [void](New-Item -ItemType Directory -Path $outputRoot) }
$zipPath = Join-Path $outputRoot "mpp-bridge-$packageVersion.zip"
if (Test-Path -LiteralPath $zipPath) {
    throw 'MPP_BRIDGE_PACKAGE_OUTPUT_EXISTS'
}

$sourceFiles = @(
    Get-ChildItem -LiteralPath $bridgeRoot -File -Filter '*.md'
    Get-ChildItem -LiteralPath (Join-Path $bridgeRoot 'schemas') -File -Filter '*.json'
    Get-ChildItem -LiteralPath (Join-Path $bridgeRoot 'src') -File -Filter '*.ps1'
    Get-ChildItem -LiteralPath (Join-Path $bridgeRoot 'tests/unit') -File -Filter '*.ps1'
) | Sort-Object { [System.IO.Path]::GetRelativePath($bridgeRoot, $_.FullName).Replace('\', '/') }

$files = @($sourceFiles | ForEach-Object {
    [ordered]@{
        path = [System.IO.Path]::GetRelativePath($bridgeRoot, $_.FullName).Replace('\', '/')
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
        bytes = $_.Length
    }
})
$contentJson = $files | ConvertTo-Json -Depth 10 -Compress
$contentHash = [Convert]::ToHexString(
    [System.Security.Cryptography.SHA256]::HashData([System.Text.Encoding]::UTF8.GetBytes($contentJson))
).ToLowerInvariant()
$manifest = [ordered]@{
    schema_version = 'mpp_bridge_package_manifest_v1'
    package_version = $packageVersion
    content_sha256 = $contentHash
    files = $files
}
$manifestJson = $manifest | ConvertTo-Json -Depth 10
$manifestSchema = Join-Path $bridgeRoot 'schemas/mpp_bridge_package_manifest_v1.schema.json'
if (-not ($manifestJson | Test-Json -SchemaFile $manifestSchema)) {
    throw 'MPP_BRIDGE_PACKAGE_MANIFEST_SCHEMA_INVALID'
}

Add-Type -AssemblyName System.IO.Compression
$zipStream = [System.IO.File]::Open($zipPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
try {
    $archive = [System.IO.Compression.ZipArchive]::new($zipStream, [System.IO.Compression.ZipArchiveMode]::Create, $true)
    try {
        foreach ($file in $sourceFiles) {
            $entryPath = [System.IO.Path]::GetRelativePath($bridgeRoot, $file.FullName).Replace('\', '/')
            $entry = $archive.CreateEntry($entryPath, [System.IO.Compression.CompressionLevel]::Optimal)
            $entry.LastWriteTime = [datetimeoffset]'1980-01-01T00:00:00+00:00'
            $input = [System.IO.File]::OpenRead($file.FullName)
            $output = $entry.Open()
            try { $input.CopyTo($output) }
            finally { $output.Dispose(); $input.Dispose() }
        }
        $manifestEntry = $archive.CreateEntry('package-manifest.json', [System.IO.Compression.CompressionLevel]::Optimal)
        $manifestEntry.LastWriteTime = [datetimeoffset]'1980-01-01T00:00:00+00:00'
        $manifestOutput = $manifestEntry.Open()
        try {
            $manifestBytes = [System.Text.UTF8Encoding]::new($false).GetBytes("$manifestJson`n")
            $manifestOutput.Write($manifestBytes, 0, $manifestBytes.Length)
        }
        finally { $manifestOutput.Dispose() }
    }
    finally { $archive.Dispose() }
}
finally { $zipStream.Dispose() }

[ordered]@{
    status = 'PASS'
    package_version = $packageVersion
    zip_file = $zipPath
    zip_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToLowerInvariant()
    content_sha256 = $contentHash
    file_count = $files.Count
} | ConvertTo-Json -Depth 5
