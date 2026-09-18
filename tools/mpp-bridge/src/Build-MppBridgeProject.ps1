[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SnapshotPath,

    [Parameter(Mandatory = $true)]
    [string]$OutputMpp,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$toolVersion = '0.3.0-m2'
$runId = [guid]::NewGuid().ToString('D')
$stage = 'validate_input'
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$app = $null
$project = $null
$projectOpened = $false
$createdTaskRefs = [System.Collections.Generic.List[object]]::new()
$processIdsBefore = @()
$processIdsAfterCreate = @()
$failure = $null

function Write-JsonFile {
    param([string]$LiteralPath, $Value)

    $json = $Value | ConvertTo-Json -Depth 30
    [System.IO.File]::WriteAllText($LiteralPath, "$json`n", $utf8NoBom)
}

function Get-Sha256 {
    param([string]$LiteralPath)

    (Get-FileHash -Algorithm SHA256 -LiteralPath $LiteralPath).Hash.ToLowerInvariant()
}

function Get-StringSha256 {
    param([string]$Value)

    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
    $hash = [System.Security.Cryptography.SHA256]::HashData($bytes)
    [Convert]::ToHexString($hash).ToLowerInvariant()
}

function Set-BuildStage {
    param([string]$Name)

    $script:stage = $Name
    if (-not [string]::IsNullOrWhiteSpace($script:progressPath)) {
        Write-JsonFile -LiteralPath $script:progressPath -Value ([ordered]@{
            run_id = $script:runId
            stage = $Name
            updated_at = [datetimeoffset]::UtcNow.ToString('o')
        })
    }
}

function Get-ComProperty {
    param($ComObject, [string]$Name)

    $value = $ComObject.GetType().InvokeMember(
        $Name,
        [System.Reflection.BindingFlags]::GetProperty,
        $null,
        $ComObject,
        $null
    )
    return $value
}

function Set-ComProperty {
    param($ComObject, [string]$Name, $Value)

    $ComObject.GetType().InvokeMember(
        $Name,
        [System.Reflection.BindingFlags]::SetProperty,
        $null,
        $ComObject,
        @($Value)
    ) | Out-Null
}

function Invoke-ComMethod {
    param($ComObject, [string]$Name, [object[]]$Arguments = @())

    $value = $ComObject.GetType().InvokeMember(
        $Name,
        [System.Reflection.BindingFlags]::InvokeMethod,
        $null,
        $ComObject,
        $Arguments
    )
    return $value
}

function Release-ComObject {
    param($Value)

    if ($null -ne $Value -and [System.Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
    }
}

function Convert-ToProjectDate {
    param([string]$Value)

    ([datetimeoffset]::Parse($Value)).DateTime
}

function Test-BuildCalendarSnapshot {
    param($Calendar)

    if ($null -ne $Calendar.base_calendar_name) {
        return $false
    }
    $expectedDays = @('SUNDAY', 'MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY')
    $days = @($Calendar.week_days)
    if ($days.Count -ne 7) {
        return $false
    }
    for ($index = 0; $index -lt 7; $index++) {
        if ($days[$index].day -ne $expectedDays[$index]) {
            return $false
        }
        $intervals = @($days[$index].intervals)
        if (-not [bool]$days[$index].working -and $intervals.Count -ne 0) {
            return $false
        }
        if ([bool]$days[$index].working -and ($intervals.Count -lt 1 -or $intervals.Count -gt 5)) {
            return $false
        }
    }
    foreach ($exception in @($Calendar.exceptions)) {
        $intervals = @($exception.intervals)
        if ([int]$exception.source_type_code -ne 1 -or [int]$exception.occurrences -ne 1) {
            return $false
        }
        if (-not [bool]$exception.working -and $intervals.Count -ne 0) {
            return $false
        }
        if ([bool]$exception.working -and ($intervals.Count -lt 1 -or $intervals.Count -gt 5)) {
            return $false
        }
    }
    $true
}

function Set-CalendarWeekPattern {
    param($CalendarObject, $CalendarPayload)

    $weekDays = $CalendarObject.GetType().InvokeMember('WeekDays', [System.Reflection.BindingFlags]::GetProperty, $null, $CalendarObject, $null)
    try {
        $days = @($CalendarPayload.week_days)
        for ($dayIndex = 1; $dayIndex -le 7; $dayIndex++) {
            Set-BuildStage -Name "write_calendar:$($CalendarPayload.name):day:$dayIndex"
            $weekDay = $weekDays.Item($dayIndex)
            try {
                $working = [bool]$days[$dayIndex - 1].working
                $weekDay.Working = $working
                if (-not $working) {
                    continue
                }
                $intervals = @($days[$dayIndex - 1].intervals)
                for ($shiftIndex = 1; $shiftIndex -le 5; $shiftIndex++) {
                    Set-BuildStage -Name "write_calendar:$($CalendarPayload.name):day:${dayIndex}:shift:$shiftIndex"
                    $shift = $weekDay.GetType().InvokeMember("Shift$shiftIndex", [System.Reflection.BindingFlags]::GetProperty, $null, $weekDay, $null)
                    try {
                        if ($shiftIndex -le $intervals.Count) {
                            $shift.Start = [datetime]"1899-12-30T$($intervals[$shiftIndex - 1].start):00"
                            $shift.Finish = [datetime]"1899-12-30T$($intervals[$shiftIndex - 1].finish):00"
                        }
                        else {
                            $shift.Clear()
                        }
                    }
                    finally {
                        Release-ComObject -Value $shift
                    }
                }
            }
            finally {
                Release-ComObject -Value $weekDay
            }
        }
    }
    finally {
        Release-ComObject -Value $weekDays
    }
}

function Add-CalendarExceptions {
    param($CalendarObject, $CalendarPayload)

    $exceptions = $CalendarObject.GetType().InvokeMember('Exceptions', [System.Reflection.BindingFlags]::GetProperty, $null, $CalendarObject, $null)
    try {
        foreach ($exceptionPayload in @($CalendarPayload.exceptions)) {
            Set-BuildStage -Name "write_calendar:$($CalendarPayload.name):exception:$($exceptionPayload.exception_id)"
            $arguments = @(
                [int]$exceptionPayload.source_type_code,
                (Convert-ToProjectDate $exceptionPayload.start_date),
                (Convert-ToProjectDate $exceptionPayload.finish_date),
                [int]$exceptionPayload.occurrences,
                [string]$exceptionPayload.name,
                [Type]::Missing,
                [Type]::Missing,
                [Type]::Missing,
                [Type]::Missing,
                [Type]::Missing,
                [Type]::Missing
            )
            $exception = $exceptions.GetType().InvokeMember('Add', [System.Reflection.BindingFlags]::InvokeMethod, $null, $exceptions, $arguments)
            try {
                $intervals = @($exceptionPayload.intervals)
                for ($shiftIndex = 1; $shiftIndex -le 5; $shiftIndex++) {
                    $shift = $exception.GetType().InvokeMember("Shift$shiftIndex", [System.Reflection.BindingFlags]::GetProperty, $null, $exception, $null)
                    try {
                        if ($shiftIndex -le $intervals.Count) {
                            $shift.Start = [datetime]"1899-12-30T$($intervals[$shiftIndex - 1].start):00"
                            $shift.Finish = [datetime]"1899-12-30T$($intervals[$shiftIndex - 1].finish):00"
                        }
                        else {
                            $shift.Clear()
                        }
                    }
                    finally {
                        Release-ComObject -Value $shift
                    }
                }
            }
            finally {
                Release-ComObject -Value $exception
            }
        }
    }
    finally {
        Release-ComObject -Value $exceptions
    }
}

$snapshotFile = (Resolve-Path -LiteralPath $SnapshotPath).Path
if ([System.IO.Path]::GetExtension($OutputMpp) -ine '.mpp') {
    throw 'MPP_BRIDGE_BUILD_OUTPUT_EXTENSION_INVALID'
}
$outputFile = [System.IO.Path]::GetFullPath($OutputMpp)
if (Test-Path -LiteralPath $outputFile) {
    throw 'MPP_BRIDGE_BUILD_OUTPUT_EXISTS'
}
$outputParent = Split-Path -Parent $outputFile
if (-not (Test-Path -LiteralPath $outputParent)) {
    [void](New-Item -ItemType Directory -Path $outputParent)
}
$artifactRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
if (-not (Test-Path -LiteralPath $artifactRoot)) {
    [void](New-Item -ItemType Directory -Path $artifactRoot)
}
$artifactRoot = (Resolve-Path -LiteralPath $artifactRoot).Path
$runDirectory = Join-Path $artifactRoot ("build-$runId")
[void](New-Item -ItemType Directory -Path $runDirectory)
$progressPath = Join-Path $runDirectory 'progress.json'
$errorPath = Join-Path $runDirectory 'error.json'
$identityMapPath = Join-Path $runDirectory 'identity-map.json'
$buildReportPath = Join-Path $runDirectory 'build-report.json'
$manifestPath = Join-Path $runDirectory 'manifest.json'
$stagingMppPath = Join-Path $runDirectory 'staging-output.mpp'

$snapshotArtifactHash = Get-Sha256 -LiteralPath $snapshotFile
$snapshot = Get-Content -Raw -LiteralPath $snapshotFile | ConvertFrom-Json -AsHashtable -DateKind String
$snapshotSemanticHash = if ($snapshot.ContainsKey('snapshot_semantic_sha256')) { [string]$snapshot.snapshot_semantic_sha256 } else { '0' * 64 }

try {
    Set-BuildStage -Name 'validate_snapshot_schema'
    $snapshotSchema = Join-Path (Split-Path -Parent $PSScriptRoot) 'schemas/mpp_bridge_snapshot_v1.schema.json'
    if (-not (Get-Content -Raw -LiteralPath $snapshotFile | Test-Json -SchemaFile $snapshotSchema)) {
        throw 'MPP_BRIDGE_BUILD_SNAPSHOT_SCHEMA_INVALID'
    }
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
    $recomputedSemanticHash = Get-StringSha256 -Value ($semanticPayload | ConvertTo-Json -Depth 30 -Compress)
    if ($recomputedSemanticHash -ne $snapshotSemanticHash) {
        throw 'MPP_BRIDGE_BUILD_SEMANTIC_HASH_INVALID'
    }
    if (-not [bool]$snapshot.roundtrip_eligible -or @($snapshot.unsupported_semantics | Where-Object { $_.severity -eq 'BLOCKER' }).Count -gt 0) {
        throw 'MPP_BRIDGE_BUILD_SNAPSHOT_NOT_ELIGIBLE'
    }
    $calendars = @($snapshot.calendars)
    if (@($calendars | Where-Object { -not (Test-BuildCalendarSnapshot -Calendar $_) }).Count -gt 0) {
        throw 'MPP_BRIDGE_BUILD_CALENDAR_UNSUPPORTED'
    }
    $calendarNames = @($calendars | ForEach-Object { $_.name })
    if ($calendarNames.Count -ne @($calendarNames | Select-Object -Unique).Count -or
        $snapshot.project.default_calendar_name -notin $calendarNames) {
        throw 'MPP_BRIDGE_BUILD_CALENDAR_UNSUPPORTED'
    }
    $tasks = @($snapshot.tasks)
    if (@($tasks | Where-Object { $_.scheduling_mode -ne 'AUTO' -or $_.percent_complete -ne 0 }).Count -gt 0) {
        throw 'MPP_BRIDGE_BUILD_TASK_STATE_UNSUPPORTED'
    }
    $taskIds = @($tasks | ForEach-Object { $_.bridge_task_id })
    if ($taskIds.Count -ne @($taskIds | Select-Object -Unique).Count) {
        throw 'MPP_BRIDGE_BUILD_DUPLICATE_TASK_ID'
    }
    foreach ($task in $tasks) {
        if ($null -ne $task.parent_task_id -and $task.parent_task_id -notin $taskIds) {
            throw "MPP_BRIDGE_BUILD_PARENT_UNKNOWN: $($task.bridge_task_id)"
        }
    }
    foreach ($dependency in @($snapshot.dependencies)) {
        if ($dependency.predecessor_task_id -notin $taskIds -or $dependency.successor_task_id -notin $taskIds) {
            throw "MPP_BRIDGE_BUILD_DEPENDENCY_TASK_UNKNOWN: $($dependency.dependency_id)"
        }
    }

    Set-BuildStage -Name 'validate_project_path'
    if ($stagingMppPath.Length -gt 259 -or $outputFile.Length -gt 259) {
        throw 'MPP_BRIDGE_PROJECT_PATH_TOO_LONG'
    }

    $processIdsBefore = @(Get-Process -Name WINPROJ -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
    if ($processIdsBefore.Count -gt 0) {
        throw 'MPP_BRIDGE_PREEXISTING_PROJECT_PROCESS'
    }

    Set-BuildStage -Name 'create_com_application'
    $app = New-Object -ComObject MSProject.Application
    $app.DisplayAlerts = $false
    $app.Visible = $false
    $processIdsAfterCreate = @(Get-Process -Name WINPROJ -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
    if ($processIdsAfterCreate.Count -eq 0) {
        throw 'MPP_BRIDGE_COM_PROCESS_NOT_OBSERVED'
    }

    Set-BuildStage -Name 'create_project'
    $projects = $app.GetType().InvokeMember('Projects', [System.Reflection.BindingFlags]::GetProperty, $null, $app, $null)
    try {
        Set-BuildStage -Name 'create_project:add'
        $project = $projects.GetType().InvokeMember('Add', [System.Reflection.BindingFlags]::InvokeMethod, $null, $projects, $null)
    }
    finally {
        Release-ComObject -Value $projects
    }
    $projectOpened = $true
    Set-BuildStage -Name 'create_project:read_base_calendars'
    $baseCalendars = $project.GetType().InvokeMember('BaseCalendars', [System.Reflection.BindingFlags]::GetProperty, $null, $project, $null)
    try {
        Set-BuildStage -Name 'create_project:write_calendars'
        $templateCalendar = $baseCalendars.Item(1)
        try {
            $templateCalendarName = [string](Get-ComProperty $templateCalendar 'Name')
        }
        finally {
            Release-ComObject -Value $templateCalendar
        }
        foreach ($calendarPayload in $calendars) {
            $calendar = $null
            for ($index = 1; $index -le [int](Get-ComProperty $baseCalendars 'Count'); $index++) {
                $candidate = $baseCalendars.Item($index)
                if ([string](Get-ComProperty $candidate 'Name') -eq [string]$calendarPayload.name) {
                    $calendar = $candidate
                    break
                }
                Release-ComObject -Value $candidate
            }
            if ($null -eq $calendar) {
                $app.BaseCalendarCreate([string]$calendarPayload.name, $templateCalendarName) | Out-Null
                $calendar = $baseCalendars.Item([string]$calendarPayload.name)
            }
            try {
                Set-CalendarWeekPattern -CalendarObject $calendar -CalendarPayload $calendarPayload
                Add-CalendarExceptions -CalendarObject $calendar -CalendarPayload $calendarPayload
            }
            finally {
                Release-ComObject -Value $calendar
            }
        }
        $projectInfoArguments = @()
        for ($index = 0; $index -lt 16; $index++) { $projectInfoArguments += [Type]::Missing }
        $projectInfoArguments[12] = [string]$snapshot.project.default_calendar_name
        Set-BuildStage -Name 'create_project:set_default_calendar'
        $app.GetType().InvokeMember('ProjectSummaryInfo', [System.Reflection.BindingFlags]::InvokeMethod, $null, $app, $projectInfoArguments) | Out-Null
        $writtenCalendarNames = @()
        for ($index = 1; $index -le [int](Get-ComProperty $baseCalendars 'Count'); $index++) {
            $writtenCalendar = $baseCalendars.Item($index)
            try {
                $writtenCalendarNames += [string](Get-ComProperty $writtenCalendar 'Name')
            }
            finally {
                Release-ComObject -Value $writtenCalendar
            }
        }
        if (@($writtenCalendarNames | Where-Object { $_ -notin $calendarNames }).Count -gt 0 -or
            @($calendarNames | Where-Object { $_ -notin $writtenCalendarNames }).Count -gt 0) {
            throw 'MPP_BRIDGE_BUILD_CALENDAR_SET_MISMATCH'
        }
    }
    finally {
        Release-ComObject -Value $baseCalendars
    }

    Set-BuildStage -Name 'create_project:set_project_start'
    try {
        Set-ComProperty -ComObject $project -Name 'ProjectStart' -Value (Convert-ToProjectDate $snapshot.project.planned_start)
    }
    catch {
        throw "MPP_BRIDGE_BUILD_PROJECT_START_FAILED: $($_.Exception.Message)"
    }
    $projectSummaryTask = $project.GetType().InvokeMember('ProjectSummaryTask', [System.Reflection.BindingFlags]::GetProperty, $null, $project, $null)
    try {
        Set-ComProperty $projectSummaryTask 'Name' ([string]$snapshot.project.name)
    }
    finally {
        Release-ComObject -Value $projectSummaryTask
    }

    Set-BuildStage -Name 'write_tasks'
    $tasksCollection = $project.GetType().InvokeMember('Tasks', [System.Reflection.BindingFlags]::GetProperty, $null, $project, $null)
    $taskRefsByBridgeId = @{}
    try {
        foreach ($taskPayload in $tasks) {
            Set-BuildStage -Name "write_tasks:$($taskPayload.bridge_task_id)"
            $task = $tasksCollection.GetType().InvokeMember('Add', [System.Reflection.BindingFlags]::InvokeMethod, $null, $tasksCollection, @([string]$taskPayload.name))
            $createdTaskRefs.Add($task)
            $taskRefsByBridgeId[$taskPayload.bridge_task_id] = $task
            Set-ComProperty $task 'Manual' $false
            while ([int](Get-ComProperty $task 'OutlineLevel') -lt [int]$taskPayload.outline_level) {
                Invoke-ComMethod $task 'OutlineIndent' | Out-Null
            }
            while ([int](Get-ComProperty $task 'OutlineLevel') -gt [int]$taskPayload.outline_level) {
                Invoke-ComMethod $task 'OutlineOutdent' | Out-Null
            }
            if ($null -ne $taskPayload.source_calendar_name) {
                $task.Calendar = [string]$taskPayload.source_calendar_name
            }
            if (-not [bool]$taskPayload.summary) {
                Set-ComProperty $task 'Duration' ([int]$taskPayload.duration_minutes)
                Set-ComProperty $task 'ConstraintType' ([int]$taskPayload.constraint_type_code)
                if ($null -ne $taskPayload.constraint_date) {
                    Set-ComProperty $task 'ConstraintDate' (Convert-ToProjectDate $taskPayload.constraint_date)
                }
                if ($null -ne $taskPayload.deadline) {
                    Set-ComProperty $task 'Deadline' (Convert-ToProjectDate $taskPayload.deadline)
                }
            }
        }

        Set-BuildStage -Name 'write_dependencies'
        foreach ($dependency in @($snapshot.dependencies)) {
            $predecessor = $taskRefsByBridgeId[$dependency.predecessor_task_id]
            $successor = $taskRefsByBridgeId[$dependency.successor_task_id]
            Invoke-ComMethod $successor 'LinkPredecessors' @(
                $predecessor,
                [int]$dependency.source_type_code,
                "$([int]$dependency.lag_minutes)m"
            ) | Out-Null
        }
        Set-BuildStage -Name 'write_inactive_tasks'
        foreach ($taskPayload in @($tasks | Where-Object { -not [bool]$_.active })) {
            Set-ComProperty $taskRefsByBridgeId[$taskPayload.bridge_task_id] 'Active' $false
        }
    }
    finally {
        Release-ComObject -Value $tasksCollection
    }

    Set-BuildStage -Name 'save_first_pass'
    $app.FileSaveAs($stagingMppPath) | Out-Null
    foreach ($taskRef in $createdTaskRefs) {
        Release-ComObject -Value $taskRef
    }
    $createdTaskRefs.Clear()
    Release-ComObject -Value $project
    $project = $null
    $app.FileCloseEx(1) | Out-Null
    $projectOpened = $false

    Set-BuildStage -Name 'reopen_and_recalculate'
    $app.FileOpenEx($stagingMppPath) | Out-Null
    $projectOpened = $true
    $project = $app.GetType().InvokeMember('ActiveProject', [System.Reflection.BindingFlags]::GetProperty, $null, $app, $null)
    $app.CalculateProject() | Out-Null
    $app.FileSave() | Out-Null

    Set-BuildStage -Name 'capture_output_identity'
    $reopenedTasks = $project.GetType().InvokeMember('Tasks', [System.Reflection.BindingFlags]::GetProperty, $null, $project, $null)
    $identityTasks = [System.Collections.Generic.List[object]]::new()
    try {
        if ([int](Get-ComProperty $reopenedTasks 'Count') -ne $tasks.Count) {
            throw 'MPP_BRIDGE_BUILD_TASK_COUNT_CHANGED_AFTER_REOPEN'
        }
        for ($index = 1; $index -le $tasks.Count; $index++) {
            $outputTask = $reopenedTasks.Item($index)
            try {
                $sourceTask = $tasks[$index - 1]
                $identityTasks.Add([ordered]@{
                    bridge_task_id = $sourceTask.bridge_task_id
                    source = [ordered]@{ id = [int]$sourceTask.source_id; unique_id = [int]$sourceTask.source_unique_id }
                    output = [ordered]@{ id = [int](Get-ComProperty $outputTask 'ID'); unique_id = [int](Get-ComProperty $outputTask 'UniqueID') }
                })
            }
            finally {
                Release-ComObject -Value $outputTask
            }
        }
    }
    finally {
        Release-ComObject -Value $reopenedTasks
    }
    Release-ComObject -Value $project
    $project = $null
    $app.FileCloseEx(1) | Out-Null
    $projectOpened = $false
    $app.Quit() | Out-Null
    Release-ComObject -Value $app
    $app = $null
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()

    Set-BuildStage -Name 'publish_output'
    [System.IO.File]::Move($stagingMppPath, $outputFile)
    $outputHash = Get-Sha256 -LiteralPath $outputFile
    $identityMap = [ordered]@{
        schema_version = 'mpp_bridge_identity_map_v1'
        run_id = $runId
        tool_version = $toolVersion
        source_snapshot_semantic_sha256 = $snapshotSemanticHash
        output_mpp_sha256 = $outputHash
        tasks = @($identityTasks)
    }
    Write-JsonFile $identityMapPath $identityMap
    $buildReport = [ordered]@{
        schema_version = 'mpp_bridge_build_report_v1'
        run_id = $runId
        tool_version = $toolVersion
        source_snapshot_semantic_sha256 = $snapshotSemanticHash
        output_mpp_sha256 = $outputHash
        calendars_written = $calendars.Count
        tasks_written = $tasks.Count
        dependencies_written = @($snapshot.dependencies).Count
        opened_after_save = $true
        recalculated_after_reopen = $true
    }
    Write-JsonFile $buildReportPath $buildReport
}
catch {
    $failure = $_
}
finally {
    foreach ($taskRef in $createdTaskRefs) {
        Release-ComObject -Value $taskRef
    }
    if ($null -ne $project) {
        Release-ComObject -Value $project
        $project = $null
    }
    if ($projectOpened -and $null -ne $app) {
        try { $app.FileCloseEx(0) | Out-Null } catch {}
        $projectOpened = $false
    }
    if ($null -ne $app) {
        try { $app.Quit() | Out-Null } catch {}
        Release-ComObject -Value $app
        $app = $null
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

for ($attempt = 0; $attempt -lt 20; $attempt++) {
    $residualProcessIds = @(Get-Process -Name WINPROJ -ErrorAction SilentlyContinue | Where-Object { $_.Id -notin $processIdsBefore } | ForEach-Object { $_.Id })
    if ($residualProcessIds.Count -eq 0) { break }
    Start-Sleep -Milliseconds 250
}
$processIdsAfter = @(Get-Process -Name WINPROJ -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
$residualProcessIds = @($processIdsAfter | Where-Object { $_ -notin $processIdsBefore })
$processEvidence = [ordered]@{
    winproj_process_ids_before = @($processIdsBefore)
    winproj_process_ids_after_create = @($processIdsAfterCreate)
    winproj_process_ids_after = @($processIdsAfter)
    residual_new_process_ids = @($residualProcessIds)
}

if ($null -ne $failure -or $residualProcessIds.Count -gt 0) {
    if (Test-Path -LiteralPath $stagingMppPath) {
        Remove-Item -LiteralPath $stagingMppPath -Force
    }
    if (Test-Path -LiteralPath $outputFile) {
        Remove-Item -LiteralPath $outputFile -Force
    }
    $knownErrorCodes = @(
        'MPP_BRIDGE_BUILD_SNAPSHOT_SCHEMA_INVALID',
        'MPP_BRIDGE_BUILD_SEMANTIC_HASH_INVALID',
        'MPP_BRIDGE_BUILD_SNAPSHOT_NOT_ELIGIBLE',
        'MPP_BRIDGE_BUILD_CALENDAR_UNSUPPORTED',
        'MPP_BRIDGE_BUILD_CALENDAR_SET_MISMATCH',
        'MPP_BRIDGE_BUILD_TASK_STATE_UNSUPPORTED',
        'MPP_BRIDGE_BUILD_DUPLICATE_TASK_ID',
        'MPP_BRIDGE_PROJECT_PATH_TOO_LONG',
        'MPP_BRIDGE_PREEXISTING_PROJECT_PROCESS',
        'MPP_BRIDGE_COM_PROCESS_NOT_OBSERVED',
        'MPP_BRIDGE_BUILD_TASK_COUNT_CHANGED_AFTER_REOPEN'
    )
    $errorCode = if ($residualProcessIds.Count -gt 0) {
        'MPP_BRIDGE_BUILD_COM_PROCESS_RESIDUAL'
    }
    elseif ($null -ne $failure -and $failure.Exception.Message -in $knownErrorCodes) {
        $failure.Exception.Message
    }
    else {
        'MPP_BRIDGE_BUILD_FAILED'
    }
    $message = if ($null -ne $failure) { [string]$failure.Exception.Message } else { 'Build left a Microsoft Project process running.' }
    $errorPayload = [ordered]@{
        schema_version = 'mpp_bridge_build_error_v1'
        run_id = $runId
        tool_version = $toolVersion
        command = 'build'
        status = 'failed'
        occurred_at = [datetimeoffset]::UtcNow.ToString('o')
        error = [ordered]@{ code = $errorCode; stage = $stage; message = $message }
        source = [ordered]@{
            snapshot_file_name = [System.IO.Path]::GetFileName($snapshotFile)
            snapshot_artifact_sha256 = $snapshotArtifactHash
            snapshot_semantic_sha256 = $snapshotSemanticHash
        }
        output = [ordered]@{
            requested_file_name = [System.IO.Path]::GetFileName($outputFile)
            created = Test-Path -LiteralPath $outputFile
        }
        process_evidence = $processEvidence
    }
    Write-JsonFile $errorPath $errorPayload
    throw "$errorCode at $stage. Evidence: $errorPath"
}

$manifest = [ordered]@{
    schema_version = 'mpp_bridge_build_manifest_v1'
    run_id = $runId
    tool_version = $toolVersion
    command = 'build'
    status = 'passed'
    created_at = [datetimeoffset]::UtcNow.ToString('o')
    source = [ordered]@{
        snapshot_file_name = [System.IO.Path]::GetFileName($snapshotFile)
        snapshot_artifact_sha256 = $snapshotArtifactHash
        snapshot_semantic_sha256 = $snapshotSemanticHash
        roundtrip_eligible = $true
    }
    artifacts = [ordered]@{
        output_mpp_file = [System.IO.Path]::GetFileName($outputFile)
        output_mpp_sha256 = $outputHash
        identity_map_file = [System.IO.Path]::GetFileName($identityMapPath)
        identity_map_sha256 = Get-Sha256 $identityMapPath
        build_report_file = [System.IO.Path]::GetFileName($buildReportPath)
        build_report_sha256 = Get-Sha256 $buildReportPath
    }
    result = [ordered]@{
        calendars_written = $calendars.Count
        tasks_written = $tasks.Count
        dependencies_written = @($snapshot.dependencies).Count
        opened_after_save = $true
        recalculated_after_reopen = $true
    }
    process_evidence = $processEvidence
}
try {
    Write-JsonFile $manifestPath $manifest
}
catch {
    if (Test-Path -LiteralPath $outputFile) {
        Remove-Item -LiteralPath $outputFile -Force
    }
    throw
}
Set-BuildStage -Name 'complete'

[ordered]@{
    status = 'passed'
    run_directory = $runDirectory
    output_mpp = $outputFile
    identity_map = $identityMapPath
    build_report = $buildReportPath
    manifest = $manifestPath
    output_mpp_sha256 = $outputHash
    residual_new_process_ids = @($residualProcessIds)
} | ConvertTo-Json -Depth 10
