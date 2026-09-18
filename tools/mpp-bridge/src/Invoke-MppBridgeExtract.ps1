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

function Quote-ProcessArgument {
    param([Parameter(Mandatory = $true)][string]$Value)

    return '"' + $Value.Replace('"', '\"') + '"'
}

$inputPath = (Resolve-Path -LiteralPath $InputMpp).Path
$outputPath = [System.IO.Path]::GetFullPath($OutputDirectory)
if (-not (Test-Path -LiteralPath $outputPath)) {
    [void](New-Item -ItemType Directory -Path $outputPath)
}
$outputPath = (Resolve-Path -LiteralPath $outputPath).Path
$existingProjectIds = @(Get-Process -Name WINPROJ -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
if ($existingProjectIds.Count -gt 0) {
    throw 'MPP_BRIDGE_PREEXISTING_PROJECT_PROCESS'
}

$controlDirectory = Join-Path $outputPath ("control-" + [guid]::NewGuid().ToString('D'))
[void](New-Item -ItemType Directory -Path $controlDirectory)
$stdoutPath = Join-Path $controlDirectory 'stdout.log'
$stderrPath = Join-Path $controlDirectory 'stderr.log'
$extractScript = Join-Path $PSScriptRoot 'Export-MppBridgeSnapshot.ps1'
$pwshPath = (Get-Command pwsh -ErrorAction Stop).Source
$arguments = @(
    '-NoProfile',
    '-File', (Quote-ProcessArgument $extractScript),
    '-InputMpp', (Quote-ProcessArgument $inputPath),
    '-OutputDirectory', (Quote-ProcessArgument $outputPath),
    '-Timezone', (Quote-ProcessArgument $Timezone)
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
    $latestProgress = Get-ChildItem -LiteralPath $outputPath -Directory -Filter 'extract-*' |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1 |
        ForEach-Object {
            $progressFile = Join-Path $_.FullName 'progress.json'
            if (Test-Path -LiteralPath $progressFile) {
                Get-Content -Raw -LiteralPath $progressFile
            }
        }
    $timeoutEvidence = [ordered]@{
        code = 'MPP_BRIDGE_EXTRACT_TIMEOUT'
        timeout_seconds = $TimeoutSeconds
        input_file_name = [System.IO.Path]::GetFileName($inputPath)
        terminated_pwsh_process_id = $process.Id
        terminated_winproj_process_ids = $ownedProjectIds
        latest_progress = if ($latestProgress) { $latestProgress | ConvertFrom-Json } else { $null }
    }
    [System.IO.File]::WriteAllText(
        (Join-Path $controlDirectory 'timeout.json'),
        (($timeoutEvidence | ConvertTo-Json -Depth 10) + "`n"),
        [System.Text.UTF8Encoding]::new($false)
    )
    throw "MPP_BRIDGE_EXTRACT_TIMEOUT after ${TimeoutSeconds}s. Evidence: $controlDirectory"
}

$process.Refresh()
$stdout = if (Test-Path -LiteralPath $stdoutPath) { Get-Content -Raw -LiteralPath $stdoutPath } else { '' }
$stderr = if (Test-Path -LiteralPath $stderrPath) { Get-Content -Raw -LiteralPath $stderrPath } else { '' }
if ($process.ExitCode -ne 0) {
    throw "MPP_BRIDGE_EXTRACT_CHILD_FAILED with exit code $($process.ExitCode): $stderr"
}
if ([string]::IsNullOrWhiteSpace($stdout)) {
    throw 'MPP_BRIDGE_EXTRACT_CHILD_RETURNED_NO_RESULT'
}

$stdout
