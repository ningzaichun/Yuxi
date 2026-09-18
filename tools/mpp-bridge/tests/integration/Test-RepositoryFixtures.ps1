[CmdletBinding()]
param(
    [string]$OutputDirectory,

    [ValidateRange(30, 1800)]
    [int]$TimeoutSeconds = 600
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$bridgeRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$workspaceRoot = Split-Path -Parent (Split-Path -Parent $bridgeRoot)
$extractScript = Join-Path $bridgeRoot 'src/Invoke-MppBridgeExtract.ps1'
$assertScript = Join-Path $PSScriptRoot 'Assert-ExtractRun.ps1'
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $bridgeRoot '.m1-runs'
}

$fixtures = @(
    'MSP-006-SUMMARY-ROLLUP-v2_Yuxi-v6/source/case_006_nested_summary_rollup_v2.mpp',
    'Microsoft_Project_水泵站排期_MOCK_v1.1/source/water_pump_station_schedule_mock_v1_1.mpp',
    'Yuxi_复杂排期测试套件_v1/C08_MICROSOFT_PROJECT_OBSERVED/source.mpp'
)

$results = [System.Collections.Generic.List[object]]::new()
foreach ($fixture in $fixtures) {
    $inputPath = Join-Path $workspaceRoot $fixture
    $sourceHashBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $inputPath).Hash.ToLowerInvariant()
    try {
        $extractResult = & $extractScript -InputMpp $inputPath -OutputDirectory $OutputDirectory -Timezone 'Asia/Shanghai' -TimeoutSeconds $TimeoutSeconds | ConvertFrom-Json
        $assertion = & $assertScript -RunDirectory $extractResult.run_directory | ConvertFrom-Json
        $snapshot = Get-Content -Raw -LiteralPath $extractResult.snapshot | ConvertFrom-Json
        $results.Add([ordered]@{
            fixture = $fixture
            status = if ($assertion.roundtrip_eligible) { 'PASS' } else { 'UNSUPPORTED' }
            source_sha256 = $sourceHashBefore
            calendars = $assertion.calendars
            tasks = $assertion.tasks
            dependencies = $assertion.dependencies
            roundtrip_eligible = $assertion.roundtrip_eligible
            unsupported_codes = @($snapshot.unsupported_semantics | ForEach-Object { $_.code } | Select-Object -Unique)
            error = $null
        })
    }
    catch {
        $latestControl = Get-ChildItem -LiteralPath $OutputDirectory -Directory -Filter 'control-*' -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        $timeoutPath = if ($null -ne $latestControl) { Join-Path $latestControl.FullName 'timeout.json' } else { $null }
        $timeoutEvidence = if ($null -ne $timeoutPath -and (Test-Path -LiteralPath $timeoutPath)) {
            Get-Content -Raw -LiteralPath $timeoutPath | ConvertFrom-Json
        }
        else {
            $null
        }
        $results.Add([ordered]@{
            fixture = $fixture
            status = 'ERROR'
            source_sha256 = $sourceHashBefore
            calendars = $null
            tasks = $null
            dependencies = $null
            roundtrip_eligible = $false
            unsupported_codes = @()
            error = [ordered]@{
                message = [string]$_.Exception.Message
                timeout = $timeoutEvidence
            }
        })
    }
    finally {
        $sourceHashAfter = (Get-FileHash -Algorithm SHA256 -LiteralPath $inputPath).Hash.ToLowerInvariant()
        if ($sourceHashAfter -ne $sourceHashBefore) {
            throw "Repository fixture changed: $fixture"
        }
        if (@(Get-Process -Name WINPROJ -ErrorAction SilentlyContinue).Count -gt 0) {
            throw "Repository fixture left WINPROJ running: $fixture"
        }
    }
}

$matrixStatus = if (@($results | Where-Object { $_.status -eq 'ERROR' }).Count -gt 0) {
    'ERROR'
}
elseif (@($results | Where-Object { $_.status -eq 'UNSUPPORTED' }).Count -gt 0) {
    'UNSUPPORTED'
}
else {
    'PASS'
}
$matrixResult = [ordered]@{
    status = $matrixStatus
    fixtures = @($results)
}
$matrixResult | ConvertTo-Json -Depth 10
if ($matrixStatus -eq 'ERROR') {
    throw 'MPP Bridge repository fixture matrix completed with errors.'
}
