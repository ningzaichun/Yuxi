[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SnapshotPath,

    [Parameter(Mandatory = $true)]
    [string]$OutputMpp,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [ValidateRange(30, 1800)]
    [int]$TimeoutSeconds = 600
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Quote-ProcessArgument {
    param([string]$Value)

    return '"' + $Value.Replace('"', '\"') + '"'
}

$snapshotFile = (Resolve-Path -LiteralPath $SnapshotPath).Path
$outputFile = [System.IO.Path]::GetFullPath($OutputMpp)
if (Test-Path -LiteralPath $outputFile) {
    throw 'MPP_BRIDGE_BUILD_OUTPUT_EXISTS'
}
$artifactRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
if (-not (Test-Path -LiteralPath $artifactRoot)) {
    [void](New-Item -ItemType Directory -Path $artifactRoot)
}
$artifactRoot = (Resolve-Path -LiteralPath $artifactRoot).Path
$existingProjectIds = @(Get-Process -Name WINPROJ -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
if ($existingProjectIds.Count -gt 0) {
    throw 'MPP_BRIDGE_PREEXISTING_PROJECT_PROCESS'
}

$controlDirectory = Join-Path $artifactRoot ("control-" + [guid]::NewGuid().ToString('D'))
[void](New-Item -ItemType Directory -Path $controlDirectory)
$stdoutPath = Join-Path $controlDirectory 'stdout.log'
$stderrPath = Join-Path $controlDirectory 'stderr.log'
$buildScript = Join-Path $PSScriptRoot 'Build-MppBridgeProject.ps1'
$pwshPath = (Get-Command pwsh -ErrorAction Stop).Source
$arguments = @(
    '-NoProfile',
    '-File', (Quote-ProcessArgument $buildScript),
    '-SnapshotPath', (Quote-ProcessArgument $snapshotFile),
    '-OutputMpp', (Quote-ProcessArgument $outputFile),
    '-OutputDirectory', (Quote-ProcessArgument $artifactRoot)
)

$process = Start-Process `
    -FilePath $pwshPath `
    -ArgumentList $arguments `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -WindowStyle Hidden `
    -PassThru

if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
    $ownedProjectIds = @(
        Get-Process -Name WINPROJ -ErrorAction SilentlyContinue |
            Where-Object { $_.Id -notin $existingProjectIds } |
            ForEach-Object { $_.Id }
    )
    foreach ($processId in $ownedProjectIds) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $residualOwnedProjectIds = @(
            Get-Process -Name WINPROJ -ErrorAction SilentlyContinue |
                Where-Object { $_.Id -notin $existingProjectIds } |
                ForEach-Object { $_.Id }
        )
        if ($residualOwnedProjectIds.Count -eq 0) { break }
        Start-Sleep -Milliseconds 250
    }
    if (Test-Path -LiteralPath $outputFile) {
        Remove-Item -LiteralPath $outputFile -Force
    }
    $latestRun = Get-ChildItem -LiteralPath $artifactRoot -Directory -Filter 'build-*' |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    $latestProgress = if ($null -ne $latestRun) {
        $progressFile = Join-Path $latestRun.FullName 'progress.json'
        if (Test-Path -LiteralPath $progressFile) {
            Get-Content -Raw -LiteralPath $progressFile
        }
    }
    $stagingPath = if ($null -ne $latestRun) { Join-Path $latestRun.FullName 'staging-output.mpp' } else { $null }
    if ($null -ne $stagingPath -and (Test-Path -LiteralPath $stagingPath)) {
        Remove-Item -LiteralPath $stagingPath -Force
    }
    $timeoutEvidence = [ordered]@{
        code = 'MPP_BRIDGE_BUILD_TIMEOUT'
        timeout_seconds = $TimeoutSeconds
        snapshot_file_name = [System.IO.Path]::GetFileName($snapshotFile)
        requested_output_file_name = [System.IO.Path]::GetFileName($outputFile)
        terminated_pwsh_process_id = $process.Id
        terminated_winproj_process_ids = $ownedProjectIds
        output_removed = -not (Test-Path -LiteralPath $outputFile)
        staging_removed = $null -eq $stagingPath -or -not (Test-Path -LiteralPath $stagingPath)
        residual_winproj_process_ids = @($residualOwnedProjectIds)
        latest_progress = if ($latestProgress) { $latestProgress | ConvertFrom-Json } else { $null }
    }
    [System.IO.File]::WriteAllText(
        (Join-Path $controlDirectory 'timeout.json'),
        (($timeoutEvidence | ConvertTo-Json -Depth 10) + "`n"),
        [System.Text.UTF8Encoding]::new($false)
    )
    throw "MPP_BRIDGE_BUILD_TIMEOUT after ${TimeoutSeconds}s. Evidence: $controlDirectory"
}

$process.Refresh()
$stdout = if (Test-Path -LiteralPath $stdoutPath) { Get-Content -Raw -LiteralPath $stdoutPath } else { '' }
$stderr = if (Test-Path -LiteralPath $stderrPath) { Get-Content -Raw -LiteralPath $stderrPath } else { '' }
if ($process.ExitCode -ne 0) {
    throw "MPP_BRIDGE_BUILD_CHILD_FAILED with exit code $($process.ExitCode): $stderr"
}
if ([string]::IsNullOrWhiteSpace($stdout)) {
    throw 'MPP_BRIDGE_BUILD_CHILD_RETURNED_NO_RESULT'
}

$stdout
