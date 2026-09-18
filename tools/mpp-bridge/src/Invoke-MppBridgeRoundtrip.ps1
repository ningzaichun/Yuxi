[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InputMpp,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [Parameter(Mandatory = $true)]
    [string]$Timezone,

    [ValidateRange(30, 1800)]
    [int]$TimeoutSeconds = 600
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$toolVersion = '0.4.0-m3'
$runId = [guid]::NewGuid().ToString('D')
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$inputPath = (Resolve-Path -LiteralPath $InputMpp).Path
if ([System.IO.Path]::GetExtension($inputPath) -ine '.mpp') {
    throw 'MPP_BRIDGE_ROUNDTRIP_INPUT_EXTENSION_INVALID'
}
$sourceHashBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $inputPath).Hash.ToLowerInvariant()
$outputRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
if (-not (Test-Path -LiteralPath $outputRoot)) { [void](New-Item -ItemType Directory -Path $outputRoot) }
$runDirectory = Join-Path $outputRoot $runId
[void](New-Item -ItemType Directory -Path $runDirectory)

$extractScript = Join-Path $PSScriptRoot 'Invoke-MppBridgeExtract.ps1'
$buildScript = Join-Path $PSScriptRoot 'Invoke-MppBridgeBuild.ps1'
$compareScript = Join-Path $PSScriptRoot 'Compare-MppBridgeSnapshots.ps1'
$sourceExtract = & $extractScript -InputMpp $inputPath -OutputDirectory (Join-Path $runDirectory 'src') -Timezone $Timezone -TimeoutSeconds $TimeoutSeconds | ConvertFrom-Json
$left = Get-Content -Raw -LiteralPath $sourceExtract.snapshot | ConvertFrom-Json -Depth 100
if (-not [bool]$left.roundtrip_eligible -or @($left.unsupported_semantics).Count -gt 0) {
    throw "MPP_BRIDGE_ROUNDTRIP_SOURCE_UNSUPPORTED: $($sourceExtract.snapshot)"
}

$outputMpp = Join-Path $runDirectory 'built.mpp'
$build = & $buildScript -SnapshotPath $sourceExtract.snapshot -OutputMpp $outputMpp -OutputDirectory (Join-Path $runDirectory 'bld') -TimeoutSeconds $TimeoutSeconds | ConvertFrom-Json
$outputExtract = & $extractScript -InputMpp $outputMpp -OutputDirectory (Join-Path $runDirectory 'out') -Timezone $Timezone -TimeoutSeconds $TimeoutSeconds | ConvertFrom-Json
$right = Get-Content -Raw -LiteralPath $outputExtract.snapshot | ConvertFrom-Json -Depth 100
$comparison = & $compareScript -LeftSnapshot $sourceExtract.snapshot -RightSnapshot $outputExtract.snapshot -IdentityMap $build.identity_map -OutputDirectory (Join-Path $runDirectory 'cmp') | ConvertFrom-Json

$sourceHashAfter = (Get-FileHash -Algorithm SHA256 -LiteralPath $inputPath).Hash.ToLowerInvariant()
if ($sourceHashAfter -ne $sourceHashBefore) {
    throw 'MPP_BRIDGE_ROUNDTRIP_SOURCE_MODIFIED'
}
$relative = {
    param([string]$Path)
    [System.IO.Path]::GetRelativePath($runDirectory, $Path).Replace('\', '/')
}
$report = [ordered]@{
    schema_version = 'mpp_bridge_roundtrip_report_v1'
    run_id = $runId
    tool_version = $toolVersion
    command = 'roundtrip'
    status = $comparison.status
    created_at = [datetimeoffset]::UtcNow.ToString('o')
    timezone = $Timezone
    execution_system_timezone = [System.TimeZoneInfo]::Local.Id
    source = [ordered]@{
        input_file_name = [System.IO.Path]::GetFileName($inputPath)
        input_mpp_sha256 = $sourceHashBefore
        snapshot_file = & $relative $sourceExtract.snapshot
        snapshot_semantic_sha256 = $left.snapshot_semantic_sha256
        microsoft_project_version = $left.source.microsoft_project_version
    }
    build = [ordered]@{
        output_mpp_file = & $relative $outputMpp
        output_mpp_sha256 = $build.output_mpp_sha256
        identity_map_file = & $relative $build.identity_map
        manifest_file = & $relative $build.manifest
    }
    output = [ordered]@{
        snapshot_file = & $relative $outputExtract.snapshot
        snapshot_semantic_sha256 = $right.snapshot_semantic_sha256
        microsoft_project_version = $right.source.microsoft_project_version
        roundtrip_eligible = [bool]$right.roundtrip_eligible
        unsupported_count = @($right.unsupported_semantics).Count
    }
    comparator = [ordered]@{
        diff_file = & $relative $comparison.diff
        diff_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $comparison.diff).Hash.ToLowerInvariant()
        summary_file = & $relative $comparison.summary
        difference_count = $comparison.difference_count
        blocker_count = $comparison.blocker_count
        allowed_reassignment_count = $comparison.allowed_reassignment_count
        unsupported_count = $comparison.unsupported_count
    }
    source_unchanged = $true
}
$reportJson = $report | ConvertTo-Json -Depth 30
$reportSchema = Join-Path (Split-Path -Parent $PSScriptRoot) 'schemas/mpp_bridge_roundtrip_report_v1.schema.json'
if (-not ($reportJson | Test-Json -SchemaFile $reportSchema)) {
    throw 'MPP_BRIDGE_ROUNDTRIP_REPORT_SCHEMA_INVALID'
}
$reportPath = Join-Path $runDirectory 'roundtrip-report.json'
[System.IO.File]::WriteAllText($reportPath, "$reportJson`n", $utf8NoBom)

[ordered]@{
    status = $comparison.status
    run_directory = $runDirectory
    output_mpp = $outputMpp
    report = $reportPath
    diff = $comparison.diff
    summary = $comparison.summary
    source_unchanged = $true
    residual_winproj_count = @(Get-Process -Name WINPROJ -ErrorAction SilentlyContinue).Count
} | ConvertTo-Json -Depth 10
