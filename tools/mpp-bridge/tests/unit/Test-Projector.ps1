[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$bridgeRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$projector = Join-Path $bridgeRoot 'src/Project-MppBridgeSnapshotToYuxi.ps1'
$snapshotSchema = Join-Path $bridgeRoot 'schemas/mpp_bridge_snapshot_v1.schema.json'
$interchangeSchema = Join-Path $bridgeRoot 'schemas/microsoft_project_interchange_v1_1.schema.json'
$reportSchema = Join-Path $bridgeRoot 'schemas/mpp_bridge_yuxi_projection_report_v1.schema.json'
$testRoot = Join-Path $bridgeRoot ".m3-runs/projector-test/$(New-Guid)"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$sourceHash = 'a' * 64

function Get-StringSha256 {
    param([string]$Value)

    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
    [Convert]::ToHexString(
        [System.Security.Cryptography.SHA256]::HashData($bytes)
    ).ToLowerInvariant()
}

function Get-SnapshotSemanticHash {
    param($Snapshot)

    $payload = [ordered]@{
        schema_version = $Snapshot.schema_version
        timezone = $Snapshot.timezone
        input_mpp_sha256 = $Snapshot.source.input_mpp_sha256
        project = $Snapshot.project
        calendars = @($Snapshot.calendars)
        tasks = @($Snapshot.tasks)
        dependencies = @($Snapshot.dependencies)
        unsupported_semantics = @($Snapshot.unsupported_semantics)
        roundtrip_eligible = $Snapshot.roundtrip_eligible
    }
    Get-StringSha256 -Value ($payload | ConvertTo-Json -Depth 30 -Compress)
}

function Write-Snapshot {
    param(
        $Snapshot,
        [string]$Path
    )

    $Snapshot.snapshot_semantic_sha256 = Get-SnapshotSemanticHash $Snapshot
    $json = ($Snapshot | ConvertTo-Json -Depth 30) + "`n"
    if (-not ($json | Test-Json -SchemaFile $snapshotSchema)) {
        throw 'Projector test fixture failed Snapshot Schema validation.'
    }
    [System.IO.File]::WriteAllText($Path, $json, $utf8NoBom)
}

[void](New-Item -ItemType Directory -Path $testRoot)
$workingIntervals = @(
    [ordered]@{ start = '08:00'; finish = '12:00' },
    [ordered]@{ start = '13:00'; finish = '17:00' }
)
$snapshot = [ordered]@{
    schema_version = 'mpp_bridge_snapshot_v1'
    run_id = [guid]::NewGuid().ToString('D')
    tool_version = '0.4.0-m3'
    captured_at = '2026-08-24T08:00:00+08:00'
    timezone = 'Asia/Shanghai'
    source = [ordered]@{
        input_file_name = 'fixture.mpp'
        input_mpp_sha256 = $sourceHash
        working_copy_mpp_sha256 = $sourceHash
        microsoft_project_version = '16.0'
        opened_after_save = $true
        recalculated_after_reopen = $true
    }
    project = [ordered]@{
        name = 'Projector Fixture'
        planned_start = '2026-08-24T08:00:00+08:00'
        planned_finish = '2026-08-24T17:00:00+08:00'
        default_calendar_id = 'calendar:source:1'
        default_calendar_name = 'Standard'
        status_date = $null
    }
    calendars = @(
        [ordered]@{
            calendar_id = 'calendar:source:1'
            source_index = 1
            name = 'Standard'
            base_calendar_name = $null
            week_days = @(
                [ordered]@{ day = 'SUNDAY'; working = $false; intervals = @() },
                [ordered]@{ day = 'MONDAY'; working = $true; intervals = $workingIntervals },
                [ordered]@{ day = 'TUESDAY'; working = $true; intervals = $workingIntervals },
                [ordered]@{ day = 'WEDNESDAY'; working = $true; intervals = $workingIntervals },
                [ordered]@{ day = 'THURSDAY'; working = $true; intervals = $workingIntervals },
                [ordered]@{ day = 'FRIDAY'; working = $true; intervals = $workingIntervals },
                [ordered]@{ day = 'SATURDAY'; working = $false; intervals = @() }
            )
            exceptions = @()
        }
    )
    tasks = @(
        [ordered]@{
            bridge_task_id = 'source-uid:1'
            source_id = 1
            source_unique_id = 1
            parent_task_id = $null
            name = 'Task 1'
            outline_level = 1
            wbs = '1'
            task_type = 'TASK'
            summary = $false
            milestone = $false
            scheduling_mode = 'AUTO'
            active = $true
            start = '2026-08-24T08:00:00+08:00'
            finish = '2026-08-24T17:00:00+08:00'
            duration_minutes = 480
            project_rollup_duration_minutes = 480
            percent_complete = 0
            constraint_type_code = 0
            constraint_type = 'ASAP'
            constraint_date = $null
            deadline = $null
            calendar_id = 'calendar:source:1'
            source_calendar_name = $null
            predecessors_text = ''
        }
    )
    dependencies = @()
    unsupported_semantics = @()
    roundtrip_eligible = $true
    snapshot_semantic_sha256 = $sourceHash
}

$snapshotPath = Join-Path $testRoot 'snapshot.json'
Write-Snapshot -Snapshot $snapshot -Path $snapshotPath
$first = & $projector -SnapshotPath $snapshotPath -OutputDirectory (Join-Path $testRoot 'first') |
    ConvertFrom-Json
$second = & $projector -SnapshotPath $snapshotPath -OutputDirectory (Join-Path $testRoot 'second') |
    ConvertFrom-Json

if ($first.interchange_artifact_sha256 -ne $second.interchange_artifact_sha256) {
    throw 'Projector output is not deterministic.'
}
$firstBytes = [System.IO.File]::ReadAllBytes($first.interchange)
$secondBytes = [System.IO.File]::ReadAllBytes($second.interchange)
if (-not [System.Linq.Enumerable]::SequenceEqual[byte]($firstBytes, $secondBytes)) {
    throw 'Projector output bytes differ for the same Snapshot.'
}
$interchangeJson = Get-Content -Raw -LiteralPath $first.interchange
$reportJson = Get-Content -Raw -LiteralPath $first.projection_report
if (-not ($interchangeJson | Test-Json -SchemaFile $interchangeSchema)) {
    throw 'Projected Interchange failed Schema validation.'
}
if (-not ($reportJson | Test-Json -SchemaFile $reportSchema)) {
    throw 'Projection Report failed Schema validation.'
}
$interchange = $interchangeJson | ConvertFrom-Json
if ($interchange.schema_version -ne 'microsoft_project_interchange_v1.1' -or
    $interchange.project.default_daily_work_minutes -ne 480 -or
    $interchange.project.default_weekly_work_minutes -ne 2400 -or
    @($interchange.tasks).Count -ne 1) {
    throw 'Projected Interchange does not preserve the frozen mapping.'
}

try {
    & $projector -SnapshotPath $snapshotPath -OutputDirectory (Join-Path $testRoot 'first') > $null
    throw 'Projector overwrote an existing output.'
}
catch {
    if ($_.Exception.Message -notmatch 'MPP_BRIDGE_PROJECT_YUXI_OUTPUT_EXISTS') { throw }
}

$blocked = $snapshot | ConvertTo-Json -Depth 30 | ConvertFrom-Json -AsHashtable
$blocked.roundtrip_eligible = $false
$blocked.unsupported_semantics = @(
    [ordered]@{
        code = 'RESOURCE_SEMANTICS_UNSUPPORTED'
        severity = 'BLOCKER'
        object_type = 'project'
        object_refs = @('fixture.mpp')
        detail = 'Fixture blocker.'
    }
)
$blockedPath = Join-Path $testRoot 'blocked-snapshot.json'
Write-Snapshot -Snapshot $blocked -Path $blockedPath
try {
    & $projector -SnapshotPath $blockedPath -OutputDirectory (Join-Path $testRoot 'blocked') > $null
    throw 'Projector accepted a blocker Snapshot.'
}
catch {
    if ($_.Exception.Message -notmatch 'MPP_BRIDGE_PROJECT_YUXI_SNAPSHOT_NOT_ELIGIBLE') { throw }
}

$statusDated = $snapshot | ConvertTo-Json -Depth 30 | ConvertFrom-Json -AsHashtable
$statusDated.project.status_date = '2026-08-24T08:00:00+08:00'
$statusDatedPath = Join-Path $testRoot 'status-dated-snapshot.json'
Write-Snapshot -Snapshot $statusDated -Path $statusDatedPath
try {
    & $projector -SnapshotPath $statusDatedPath -OutputDirectory (Join-Path $testRoot 'status-dated') > $null
    throw 'Projector silently dropped a non-null status date.'
}
catch {
    if ($_.Exception.Message -notmatch 'MPP_BRIDGE_PROJECT_YUXI_STATUS_DATE_UNSUPPORTED') { throw }
}

"MPP Bridge Yuxi Projector: PASS ($($first.interchange_artifact_sha256))"
