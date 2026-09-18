[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BaseSnapshot,

    [Parameter(Mandatory = $true)]
    [string]$OutputSnapshot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Copy-Value {
    param($Value)

    $Value | ConvertTo-Json -Depth 30 | ConvertFrom-Json -AsHashtable -DateKind String
}

function Set-Constraint {
    param(
        [hashtable]$Task,
        [int]$Code,
        [string]$Name,
        [string]$Date
    )

    $Task.constraint_type_code = $Code
    $Task.constraint_type = $Name
    $Task.constraint_date = $Date
}

$snapshot = Get-Content -Raw -LiteralPath (Resolve-Path -LiteralPath $BaseSnapshot) |
    ConvertFrom-Json -AsHashtable -DateKind String

Set-Constraint -Task $snapshot.tasks[2] -Code 2 -Name 'MSO' -Date '2026-09-07T08:00:00+08:00'
Set-Constraint -Task $snapshot.tasks[3] -Code 4 -Name 'SNET' -Date '2026-09-09T08:00:00+08:00'
Set-Constraint -Task $snapshot.tasks[4] -Code 6 -Name 'FNET' -Date '2026-09-09T17:00:00+08:00'
$snapshot.tasks[4].deadline = '2026-09-10T17:00:00+08:00'

$fnltTask = Copy-Value -Value $snapshot.tasks[4]
$fnltTask.bridge_task_id = 'source-uid:6'
$fnltTask.source_id = 6
$fnltTask.source_unique_id = 6
$fnltTask.parent_task_id = 'source-uid:1'
$fnltTask.name = '完工上限验证'
$fnltTask.outline_level = 2
$fnltTask.wbs = '1.2'
$fnltTask.start = '2026-09-14T08:00:00+08:00'
$fnltTask.finish = '2026-09-14T17:00:00+08:00'
$fnltTask.duration_minutes = 480
$fnltTask.project_rollup_duration_minutes = 480
$fnltTask.deadline = $null
$fnltTask.predecessors_text = ''
Set-Constraint -Task $fnltTask -Code 7 -Name 'FNLT' -Date '2026-09-18T17:00:00+08:00'
$snapshot.tasks = @($snapshot.tasks) + @($fnltTask)

$semanticPayload = [ordered]@{
    schema_version = $snapshot.schema_version
    timezone = $snapshot.timezone
    input_mpp_sha256 = $snapshot.source.input_mpp_sha256
    project = $snapshot.project
    calendars = @($snapshot.calendars)
    tasks = @($snapshot.tasks)
    dependencies = @($snapshot.dependencies)
    unsupported_semantics = @($snapshot.unsupported_semantics)
    roundtrip_eligible = $snapshot.roundtrip_eligible
}
$semanticJson = $semanticPayload | ConvertTo-Json -Depth 30 -Compress
$semanticBytes = [System.Text.Encoding]::UTF8.GetBytes($semanticJson)
$snapshot.snapshot_semantic_sha256 = [Convert]::ToHexString(
    [System.Security.Cryptography.SHA256]::HashData($semanticBytes)
).ToLowerInvariant()

$outputPath = [System.IO.Path]::GetFullPath($OutputSnapshot)
$outputParent = Split-Path -Parent $outputPath
if (-not (Test-Path -LiteralPath $outputParent)) {
    [void](New-Item -ItemType Directory -Path $outputParent)
}
[System.IO.File]::WriteAllText(
    $outputPath,
    (($snapshot | ConvertTo-Json -Depth 30) + "`n"),
    [System.Text.UTF8Encoding]::new($false)
)

$outputPath
