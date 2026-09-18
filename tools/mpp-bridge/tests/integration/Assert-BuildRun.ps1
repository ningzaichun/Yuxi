[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RunDirectory,

    [Parameter(Mandatory = $true)]
    [string]$OutputMpp
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$bridgeRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$runPath = (Resolve-Path -LiteralPath $RunDirectory).Path
$outputPath = (Resolve-Path -LiteralPath $OutputMpp).Path
$artifacts = @(
    @{ File = 'manifest.json'; Schema = 'mpp_bridge_build_manifest_v1.schema.json' },
    @{ File = 'identity-map.json'; Schema = 'mpp_bridge_identity_map_v1.schema.json' },
    @{ File = 'build-report.json'; Schema = 'mpp_bridge_build_report_v1.schema.json' }
)
foreach ($artifact in $artifacts) {
    $artifactPath = Join-Path $runPath $artifact.File
    $schemaPath = Join-Path $bridgeRoot (Join-Path 'schemas' $artifact.Schema)
    if (-not (Get-Content -Raw -LiteralPath $artifactPath | Test-Json -SchemaFile $schemaPath)) {
        throw "$($artifact.File) failed JSON Schema validation."
    }
}

$manifestPath = Join-Path $runPath 'manifest.json'
$identityMapPath = Join-Path $runPath 'identity-map.json'
$reportPath = Join-Path $runPath 'build-report.json'
$manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
$identityMap = Get-Content -Raw -LiteralPath $identityMapPath | ConvertFrom-Json
$report = Get-Content -Raw -LiteralPath $reportPath | ConvertFrom-Json
$outputHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $outputPath).Hash.ToLowerInvariant()

if ($manifest.run_id -ne $identityMap.run_id -or $manifest.run_id -ne $report.run_id) {
    throw 'Build artifact run_id values differ.'
}
if ($manifest.source.snapshot_semantic_sha256 -ne $identityMap.source_snapshot_semantic_sha256 -or
    $manifest.source.snapshot_semantic_sha256 -ne $report.source_snapshot_semantic_sha256) {
    throw 'Build artifact source semantic hashes differ.'
}
if ($outputHash -ne $manifest.artifacts.output_mpp_sha256 -or
    $outputHash -ne $identityMap.output_mpp_sha256 -or
    $outputHash -ne $report.output_mpp_sha256) {
    throw 'Build output MPP hashes differ.'
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $identityMapPath).Hash.ToLowerInvariant() -ne $manifest.artifacts.identity_map_sha256) {
    throw 'Identity Map artifact hash differs from Manifest.'
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $reportPath).Hash.ToLowerInvariant() -ne $manifest.artifacts.build_report_sha256) {
    throw 'Build Report artifact hash differs from Manifest.'
}
if (@($identityMap.tasks).Count -ne $manifest.result.tasks_written) {
    throw 'Identity Map task count differs from Manifest.'
}
$bridgeIds = @($identityMap.tasks | ForEach-Object { $_.bridge_task_id })
if ($bridgeIds.Count -ne @($bridgeIds | Select-Object -Unique).Count) {
    throw 'Identity Map contains duplicate bridge_task_id values.'
}
$outputIds = @($identityMap.tasks | ForEach-Object { $_.output.id })
$outputUniqueIds = @($identityMap.tasks | ForEach-Object { $_.output.unique_id })
if ($outputIds.Count -ne @($outputIds | Select-Object -Unique).Count -or
    $outputUniqueIds.Count -ne @($outputUniqueIds | Select-Object -Unique).Count) {
    throw 'Identity Map contains duplicate output task identities.'
}
if (@($manifest.process_evidence.residual_new_process_ids).Count -ne 0) {
    throw 'Build left a new WINPROJ process.'
}

[ordered]@{
    status = 'PASS'
    run_id = $manifest.run_id
    output_mpp_sha256 = $outputHash
    calendars = $manifest.result.calendars_written
    tasks = $manifest.result.tasks_written
    dependencies = $manifest.result.dependencies_written
} | ConvertTo-Json
