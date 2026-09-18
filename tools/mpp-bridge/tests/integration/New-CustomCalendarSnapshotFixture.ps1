[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BaseSnapshot,

    [Parameter(Mandatory = $true)]
    [string]$OutputSnapshot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$snapshot = Get-Content -Raw -LiteralPath (Resolve-Path -LiteralPath $BaseSnapshot) |
    ConvertFrom-Json -AsHashtable -DateKind String
$standardCalendar = $snapshot.calendars[0]
$defaultCalendar = $standardCalendar | ConvertTo-Json -Depth 30 | ConvertFrom-Json -AsHashtable -DateKind String
$defaultCalendar.calendar_id = 'calendar:source:2'
$defaultCalendar.source_index = 2
$defaultCalendar.name = '六天施工日历'
$defaultCalendar.week_days[6].working = $true
$defaultCalendar.week_days[6].intervals = @(
    [ordered]@{ start = '07:00'; finish = '12:00' },
    [ordered]@{ start = '13:00'; finish = '18:00' }
)
$defaultCalendar.exceptions = @(
    [ordered]@{
        exception_id = 'calendar:source:2/exception:1'
        name = '停工日'
        start_date = '2026-09-09T00:00:00+08:00'
        finish_date = '2026-09-09T00:00:00+08:00'
        working = $false
        intervals = @()
        source_type_code = 1
        occurrences = 1
    },
    [ordered]@{
        exception_id = 'calendar:source:2/exception:2'
        name = '周日补班'
        start_date = '2026-09-13T00:00:00+08:00'
        finish_date = '2026-09-13T00:00:00+08:00'
        working = $true
        intervals = @(
            [ordered]@{ start = '08:00'; finish = '12:00' },
            [ordered]@{ start = '13:00'; finish = '17:00' }
        )
        source_type_code = 1
        occurrences = 1
    }
)
$taskCalendar = $defaultCalendar | ConvertTo-Json -Depth 30 | ConvertFrom-Json -AsHashtable -DateKind String
$taskCalendar.calendar_id = 'calendar:source:3'
$taskCalendar.source_index = 3
$taskCalendar.name = '九点任务日历'
$taskCalendar.exceptions = @()
foreach ($dayIndex in 1..5) {
    $taskCalendar.week_days[$dayIndex].intervals = @(
        [ordered]@{ start = '09:00'; finish = '12:00' },
        [ordered]@{ start = '13:00'; finish = '18:00' }
    )
}
$snapshot.calendars = @($standardCalendar, $defaultCalendar, $taskCalendar)
$snapshot.project.default_calendar_id = $defaultCalendar.calendar_id
$snapshot.project.default_calendar_name = $defaultCalendar.name
$snapshot.tasks[0].calendar_id = $taskCalendar.calendar_id
$snapshot.tasks[0].source_calendar_name = $taskCalendar.name
$snapshot.tasks[4].calendar_id = $taskCalendar.calendar_id
$snapshot.tasks[4].source_calendar_name = $taskCalendar.name
$snapshot.tasks[3].active = $false

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
