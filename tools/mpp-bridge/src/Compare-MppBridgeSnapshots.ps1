[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$LeftSnapshot,

    [Parameter(Mandatory = $true)]
    [string]$RightSnapshot,

    [Parameter(Mandatory = $true)]
    [string]$IdentityMap,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$comparatorVersion = '0.4.0-m3'
$runId = [guid]::NewGuid().ToString('D')
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$bridgeRoot = Split-Path -Parent $PSScriptRoot

function Get-StringSha256 {
    param([string]$Value)

    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
    [Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
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

function ConvertTo-ComparableJson {
    param($Value)

    $Value | ConvertTo-Json -Depth 30 -Compress
}

function ConvertTo-JsonPointerToken {
    param([string]$Value)

    $Value.Replace('~', '~0').Replace('/', '~1')
}

function Add-Difference {
    param(
        [System.Collections.Generic.List[object]]$Differences,
        [string]$Path,
        [string]$Disposition,
        [string]$ReasonCode,
        $Left,
        $Right
    )

    $Differences.Add([ordered]@{
        path = $Path
        disposition = $Disposition
        reason_code = $ReasonCode
        left = $Left
        right = $Right
    })
}

function Compare-NormalizedValue {
    param(
        $Left,
        $Right,
        [string]$Path,
        [System.Collections.Generic.List[object]]$Differences
    )

    if ($null -eq $Left -or $null -eq $Right) {
        if ($null -ne $Left -or $null -ne $Right) {
            Add-Difference $Differences $Path 'BLOCKER' 'VALUE_MISMATCH' $Left $Right
        }
        return
    }
    if ($Left -is [System.Collections.IDictionary] -and $Right -is [System.Collections.IDictionary]) {
        $keys = @(@($Left.Keys) + @($Right.Keys) | Sort-Object -Unique)
        foreach ($key in $keys) {
            $childPath = "$Path/$(ConvertTo-JsonPointerToken ([string]$key))"
            if (-not $Left.Contains($key)) {
                Add-Difference $Differences $childPath 'BLOCKER' 'LEFT_FIELD_MISSING' $null $Right[$key]
            }
            elseif (-not $Right.Contains($key)) {
                Add-Difference $Differences $childPath 'BLOCKER' 'RIGHT_FIELD_MISSING' $Left[$key] $null
            }
            else {
                Compare-NormalizedValue $Left[$key] $Right[$key] $childPath $Differences
            }
        }
        return
    }
    if ($Left -is [System.Collections.IEnumerable] -and $Left -isnot [string] -and
        $Right -is [System.Collections.IEnumerable] -and $Right -isnot [string]) {
        $leftItems = @($Left)
        $rightItems = @($Right)
        $count = [Math]::Max($leftItems.Count, $rightItems.Count)
        for ($index = 0; $index -lt $count; $index++) {
            $childPath = "$Path/$index"
            if ($index -ge $leftItems.Count) {
                Add-Difference $Differences $childPath 'BLOCKER' 'LEFT_ITEM_MISSING' $null $rightItems[$index]
            }
            elseif ($index -ge $rightItems.Count) {
                Add-Difference $Differences $childPath 'BLOCKER' 'RIGHT_ITEM_MISSING' $leftItems[$index] $null
            }
            else {
                Compare-NormalizedValue $leftItems[$index] $rightItems[$index] $childPath $Differences
            }
        }
        return
    }
    if ((ConvertTo-ComparableJson $Left) -cne (ConvertTo-ComparableJson $Right)) {
        Add-Difference $Differences $Path 'BLOCKER' 'VALUE_MISMATCH' $Left $Right
    }
}

function Get-CalendarProjection {
    param($Calendar)

    [ordered]@{
        name = $Calendar.name
        base_calendar_name = $Calendar.base_calendar_name
        week_days = @($Calendar.week_days | ForEach-Object {
            [ordered]@{ day = $_.day; working = $_.working; intervals = @($_.intervals) }
        })
        exceptions = @($Calendar.exceptions | Sort-Object { "$($_.name)|$($_.start_date)|$($_.finish_date)" } | ForEach-Object {
            [ordered]@{
                name = $_.name
                start_date = $_.start_date
                finish_date = $_.finish_date
                working = $_.working
                intervals = @($_.intervals)
                source_type_code = $_.source_type_code
                occurrences = $_.occurrences
            }
        })
    }
}

function Get-TaskProjection {
    param($Task, [string]$StableTaskId, $StableParentTaskId)

    [ordered]@{
        bridge_task_id = $StableTaskId
        parent_task_id = $StableParentTaskId
        name = $Task.name
        outline_level = $Task.outline_level
        wbs = $Task.wbs
        task_type = $Task.task_type
        summary = $Task.summary
        milestone = $Task.milestone
        scheduling_mode = $Task.scheduling_mode
        active = $Task.active
        start = $Task.start
        finish = $Task.finish
        duration_minutes = $Task.duration_minutes
        project_rollup_duration_minutes = $Task.project_rollup_duration_minutes
        percent_complete = $Task.percent_complete
        constraint_type_code = $Task.constraint_type_code
        constraint_type = $Task.constraint_type
        constraint_date = $Task.constraint_date
        deadline = $Task.deadline
        source_calendar_name = $Task.source_calendar_name
    }
}

function Get-NormalizedSnapshot {
    param($Snapshot, [hashtable]$OutputToStableTaskId, [bool]$IsRight)

    $calendarNames = @($Snapshot.calendars | ForEach-Object { [string]$_.name })
    if ($calendarNames.Count -ne @($calendarNames | Select-Object -Unique).Count) {
        throw 'MPP_BRIDGE_COMPARE_DUPLICATE_CALENDAR_NAME'
    }
    $calendars = [ordered]@{}
    foreach ($calendar in @($Snapshot.calendars | Sort-Object { $_.name })) {
        $calendars[[string]$calendar.name] = Get-CalendarProjection $calendar
    }

    $tasks = [ordered]@{}
    foreach ($task in @($Snapshot.tasks)) {
        $stableTaskId = if ($IsRight) { $OutputToStableTaskId[[string]$task.bridge_task_id] } else { [string]$task.bridge_task_id }
        if ([string]::IsNullOrWhiteSpace($stableTaskId) -or $tasks.Contains($stableTaskId)) {
            throw "MPP_BRIDGE_COMPARE_TASK_ID_INVALID: $($task.bridge_task_id)"
        }
        $stableParentTaskId = if ($null -eq $task.parent_task_id) {
            $null
        }
        elseif ($IsRight) {
            $OutputToStableTaskId[[string]$task.parent_task_id]
        }
        else {
            [string]$task.parent_task_id
        }
        if ($null -ne $task.parent_task_id -and [string]::IsNullOrWhiteSpace($stableParentTaskId)) {
            throw "MPP_BRIDGE_COMPARE_PARENT_IDENTITY_MISSING: $($task.parent_task_id)"
        }
        $tasks[$stableTaskId] = Get-TaskProjection $task $stableTaskId $stableParentTaskId
    }

    $dependencies = @($Snapshot.dependencies | ForEach-Object {
        $predecessor = if ($IsRight) { $OutputToStableTaskId[[string]$_.predecessor_task_id] } else { [string]$_.predecessor_task_id }
        $successor = if ($IsRight) { $OutputToStableTaskId[[string]$_.successor_task_id] } else { [string]$_.successor_task_id }
        if ([string]::IsNullOrWhiteSpace($predecessor) -or [string]::IsNullOrWhiteSpace($successor)) {
            throw "MPP_BRIDGE_COMPARE_DEPENDENCY_IDENTITY_MISSING: $($_.dependency_id)"
        }
        [ordered]@{
            predecessor_task_id = $predecessor
            successor_task_id = $successor
            type = $_.type
            source_type_code = $_.source_type_code
            lag_minutes = $_.lag_minutes
        }
    } | Sort-Object { "$($_.predecessor_task_id)|$($_.successor_task_id)|$($_.type)|$($_.lag_minutes)" })

    [ordered]@{
        timezone = $Snapshot.timezone
        microsoft_project_version = $Snapshot.source.microsoft_project_version
        project = [ordered]@{
            name = $Snapshot.project.name
            planned_start = $Snapshot.project.planned_start
            planned_finish = $Snapshot.project.planned_finish
            default_calendar_name = $Snapshot.project.default_calendar_name
            status_date = $Snapshot.project.status_date
        }
        calendars = $calendars
        tasks = $tasks
        dependencies = $dependencies
    }
}

$leftPath = (Resolve-Path -LiteralPath $LeftSnapshot).Path
$rightPath = (Resolve-Path -LiteralPath $RightSnapshot).Path
$identityPath = (Resolve-Path -LiteralPath $IdentityMap).Path
$snapshotSchema = Join-Path $bridgeRoot 'schemas/mpp_bridge_snapshot_v1.schema.json'
$identitySchema = Join-Path $bridgeRoot 'schemas/mpp_bridge_identity_map_v1.schema.json'
foreach ($path in @($leftPath, $rightPath)) {
    if (-not (Get-Content -Raw -LiteralPath $path | Test-Json -SchemaFile $snapshotSchema)) {
        throw "MPP_BRIDGE_COMPARE_SNAPSHOT_SCHEMA_INVALID: $path"
    }
}
if (-not (Get-Content -Raw -LiteralPath $identityPath | Test-Json -SchemaFile $identitySchema)) {
    throw 'MPP_BRIDGE_COMPARE_IDENTITY_SCHEMA_INVALID'
}

$left = Get-Content -Raw -LiteralPath $leftPath | ConvertFrom-Json -AsHashtable -DateKind String
$right = Get-Content -Raw -LiteralPath $rightPath | ConvertFrom-Json -AsHashtable -DateKind String
$identity = Get-Content -Raw -LiteralPath $identityPath | ConvertFrom-Json -AsHashtable -DateKind String
if ((Get-SnapshotSemanticHash $left) -ne $left.snapshot_semantic_sha256 -or
    (Get-SnapshotSemanticHash $right) -ne $right.snapshot_semantic_sha256) {
    throw 'MPP_BRIDGE_COMPARE_SEMANTIC_HASH_INVALID'
}
if ($identity.source_snapshot_semantic_sha256 -ne $left.snapshot_semantic_sha256 -or
    $identity.output_mpp_sha256 -ne $right.source.input_mpp_sha256) {
    throw 'MPP_BRIDGE_COMPARE_ARTIFACT_IDENTITY_MISMATCH'
}

$leftTasksById = @{}
foreach ($task in @($left.tasks)) { $leftTasksById[[string]$task.bridge_task_id] = $task }
$rightTasksById = @{}
foreach ($task in @($right.tasks)) { $rightTasksById[[string]$task.bridge_task_id] = $task }
$outputToStableTaskId = @{}
$differences = [System.Collections.Generic.List[object]]::new()
foreach ($mapping in @($identity.tasks)) {
    $stableId = [string]$mapping.bridge_task_id
    $outputId = "source-uid:$($mapping.output.unique_id)"
    if ($outputToStableTaskId.ContainsKey($outputId) -or
        -not $leftTasksById.ContainsKey($stableId) -or
        -not $rightTasksById.ContainsKey($outputId)) {
        throw "MPP_BRIDGE_COMPARE_IDENTITY_MAP_INVALID: $stableId"
    }
    $leftTask = $leftTasksById[$stableId]
    $rightTask = $rightTasksById[$outputId]
    if ([int]$leftTask.source_id -ne [int]$mapping.source.id -or
        [int]$leftTask.source_unique_id -ne [int]$mapping.source.unique_id -or
        [int]$rightTask.source_id -ne [int]$mapping.output.id -or
        [int]$rightTask.source_unique_id -ne [int]$mapping.output.unique_id) {
        throw "MPP_BRIDGE_COMPARE_IDENTITY_MAP_INVALID: $stableId"
    }
    $outputToStableTaskId[$outputId] = $stableId
    $taskPath = "/tasks/$(ConvertTo-JsonPointerToken $stableId)"
    if ([int]$mapping.source.id -ne [int]$mapping.output.id) {
        Add-Difference $differences "$taskPath/source_id" 'ALLOWED_REASSIGNMENT' 'PROJECT_TASK_ID_REASSIGNED' ([int]$mapping.source.id) ([int]$mapping.output.id)
    }
    if ([int]$mapping.source.unique_id -ne [int]$mapping.output.unique_id) {
        Add-Difference $differences "$taskPath/source_unique_id" 'ALLOWED_REASSIGNMENT' 'PROJECT_TASK_UNIQUE_ID_REASSIGNED' ([int]$mapping.source.unique_id) ([int]$mapping.output.unique_id)
    }
}
if ($outputToStableTaskId.Count -ne @($left.tasks).Count -or $outputToStableTaskId.Count -ne @($right.tasks).Count) {
    throw 'MPP_BRIDGE_COMPARE_IDENTITY_MAP_TASK_COUNT_MISMATCH'
}

for ($index = 0; $index -lt @($left.unsupported_semantics).Count; $index++) {
    Add-Difference $differences "/left/unsupported_semantics/$index" 'UNSUPPORTED' 'LEFT_SNAPSHOT_UNSUPPORTED' $left.unsupported_semantics[$index] $null
}
for ($index = 0; $index -lt @($right.unsupported_semantics).Count; $index++) {
    Add-Difference $differences "/right/unsupported_semantics/$index" 'UNSUPPORTED' 'RIGHT_SNAPSHOT_UNSUPPORTED' $null $right.unsupported_semantics[$index]
}
if (-not [bool]$left.roundtrip_eligible -and @($left.unsupported_semantics).Count -eq 0) {
    Add-Difference $differences '/left/roundtrip_eligible' 'UNSUPPORTED' 'LEFT_SNAPSHOT_NOT_ELIGIBLE' $false $true
}
if (-not [bool]$right.roundtrip_eligible -and @($right.unsupported_semantics).Count -eq 0) {
    Add-Difference $differences '/right/roundtrip_eligible' 'UNSUPPORTED' 'RIGHT_SNAPSHOT_NOT_ELIGIBLE' $true $false
}

$leftNormalized = Get-NormalizedSnapshot $left @{} $false
$rightNormalized = Get-NormalizedSnapshot $right $outputToStableTaskId $true
Compare-NormalizedValue $leftNormalized $rightNormalized '' $differences

$blockerCount = @($differences | Where-Object { $_.disposition -eq 'BLOCKER' }).Count
$allowedCount = @($differences | Where-Object { $_.disposition -eq 'ALLOWED_REASSIGNMENT' }).Count
$unsupportedCount = @($differences | Where-Object { $_.disposition -eq 'UNSUPPORTED' }).Count
$status = if ($unsupportedCount -gt 0) { 'UNSUPPORTED' } elseif ($blockerCount -gt 0) { 'FAIL' } else { 'PASS' }
$diff = [ordered]@{
    schema_version = 'mpp_bridge_diff_v1'
    run_id = $runId
    comparator_version = $comparatorVersion
    compared_at = [datetimeoffset]::UtcNow.ToString('o')
    left_snapshot_semantic_sha256 = $left.snapshot_semantic_sha256
    right_snapshot_semantic_sha256 = $right.snapshot_semantic_sha256
    status = $status
    summary = [ordered]@{
        difference_count = $differences.Count
        blocker_count = $blockerCount
        allowed_reassignment_count = $allowedCount
        unsupported_count = $unsupportedCount
    }
    differences = @($differences)
}

$outputRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
if (-not (Test-Path -LiteralPath $outputRoot)) { [void](New-Item -ItemType Directory -Path $outputRoot) }
$runDirectory = Join-Path $outputRoot "compare-$runId"
[void](New-Item -ItemType Directory -Path $runDirectory)
$diffPath = Join-Path $runDirectory 'diff.json'
$summaryPath = Join-Path $runDirectory 'summary.txt'
$diffJson = $diff | ConvertTo-Json -Depth 30
$diffSchema = Join-Path $bridgeRoot 'schemas/mpp_bridge_diff_v1.schema.json'
if (-not ($diffJson | Test-Json -SchemaFile $diffSchema)) {
    throw 'MPP_BRIDGE_COMPARE_DIFF_SCHEMA_INVALID'
}
[System.IO.File]::WriteAllText($diffPath, ($diffJson + "`n"), $utf8NoBom)
$summaryLines = [System.Collections.Generic.List[string]]::new()
$summaryLines.Add("Status: $status")
$summaryLines.Add("Differences: $($differences.Count); Blockers: $blockerCount; Allowed reassignments: $allowedCount; Unsupported: $unsupportedCount")
foreach ($difference in $differences) {
    $summaryLines.Add("[$($difference.disposition)] $($difference.path) $($difference.reason_code)")
}
[System.IO.File]::WriteAllText($summaryPath, (($summaryLines -join "`n") + "`n"), $utf8NoBom)

[ordered]@{
    status = $status
    run_directory = $runDirectory
    diff = $diffPath
    summary = $summaryPath
    difference_count = $differences.Count
    blocker_count = $blockerCount
    allowed_reassignment_count = $allowedCount
    unsupported_count = $unsupportedCount
} | ConvertTo-Json -Depth 10
