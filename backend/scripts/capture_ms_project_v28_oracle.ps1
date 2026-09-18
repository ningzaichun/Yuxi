param(
    [Parameter(Mandatory = $false)]
    [string]$CasePath,

    [Parameter(Mandatory = $true)]
    [string]$MppPath,

    [Parameter(Mandatory = $false)]
    [switch]$Visible
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

if (-not $CasePath) {
    $CasePath = Join-Path $PSScriptRoot "..\test\data\schedule\microsoft_project_v28_inactive_summary_dependency_oracle.json"
}

$case = Get-Content -Raw -Encoding UTF8 -LiteralPath $CasePath | ConvertFrom-Json
$taskIds = @($case.tasks | ForEach-Object { [string]$_.task_id })
if ($taskIds.Count -ne @($taskIds | Select-Object -Unique).Count) {
    throw "ORACLE_TASK_IDS_MUST_BE_UNIQUE"
}

$constraintTypes = @{
    AS_SOON_AS_POSSIBLE = 0
    START_NO_EARLIER_THAN = 4
}
$mutationResults = [System.Collections.Generic.List[object]]::new()

function Invoke-RecordedMutation {
    param(
        [string]$Operation,
        [string]$ObjectRef,
        [scriptblock]$Action
    )

    try {
        & $Action
        $mutationResults.Add([ordered]@{
            operation = $Operation
            object_ref = $ObjectRef
            accepted = $true
            error = $null
        })
    }
    catch {
        $mutationResults.Add([ordered]@{
            operation = $Operation
            object_ref = $ObjectRef
            accepted = $false
            error = $_.Exception.Message
        })
    }
}

function Format-ProjectDate {
    param($Value)

    if ($null -eq $Value -or [string]$Value -match "^(NA|N/A)$") {
        return $null
    }
    try {
        return ([datetime]$Value).ToString("yyyy-MM-ddTHH:mm:sszzz")
    }
    catch {
        return [string]$Value
    }
}

function Get-Observation {
    param(
        $Project,
        [string]$Phase
    )

    $observedTasks = foreach ($task in @($Project.Tasks)) {
        if ($null -eq $task -or -not [string]$task.Text30) {
            continue
        }
        [ordered]@{
            task_id = [string]$task.Text30
            project_id = [int]$task.ID
            outline_level = [int]$task.OutlineLevel
            summary = [bool]$task.Summary
            active = [bool]$task.Active
            start = Format-ProjectDate $task.Start
            finish = Format-ProjectDate $task.Finish
            late_start = Format-ProjectDate $task.LateStart
            late_finish = Format-ProjectDate $task.LateFinish
            total_slack_minutes = [int]$task.TotalSlack
            free_slack_minutes = [int]$task.FreeSlack
            critical = [bool]$task.Critical
            predecessors = [string]$task.Predecessors
            successors = [string]$task.Successors
            constraint_type = [int]$task.ConstraintType
            constraint_date = if ([int]$task.ConstraintType -eq 0) { $null } else { Format-ProjectDate $task.ConstraintDate }
            calendar_name = [string]$task.Calendar
            percent_complete = [int]$task.PercentComplete
            actual_start = Format-ProjectDate $task.ActualStart
            actual_finish = Format-ProjectDate $task.ActualFinish
            warning = try { [bool]$task.Warning } catch { $null }
        }
    }

    [ordered]@{
        phase = $Phase
        project_start = Format-ProjectDate $Project.ProjectStart
        project_finish = Format-ProjectDate $Project.ProjectFinish
        tasks = @($observedTasks | Sort-Object project_id)
    }
}

$mppFullPath = [IO.Path]::GetFullPath($MppPath)
$mppParent = Split-Path -Parent $mppFullPath
if (-not (Test-Path -LiteralPath $mppParent)) {
    New-Item -ItemType Directory -Path $mppParent | Out-Null
}

$app = New-Object -ComObject MSProject.Application
$projectCreated = $false

try {
    [void]($app.DisplayAlerts = $false)
    [void]($app.Visible = [bool]$Visible)
    $app.FileNew() | Out-Null
    Start-Sleep -Milliseconds 800
    $project = $app.ActiveProject
    if ($null -eq $project) {
        throw "MSPROJECT_ACTIVE_PROJECT_NOT_CREATED"
    }
    $projectCreated = $true
    [void]($project.ScheduleFromStart = $true)
    [void]($project.ProjectStart = [datetime]$case.project_start)
    [void]($project.HoursPerDay = 8)
    [void]($project.HoursPerWeek = 40)
    [void]($project.DaysPerMonth = 20)

    $standardCalendar = $project.BaseCalendars.Item(1)
    $workingWeekdays = @($case.calendar.working_weekdays)
    $weekdayNames = @("SUNDAY", "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY")
    foreach ($dayIndex in 1..7) {
        $weekDay = $standardCalendar.WeekDays.Item($dayIndex)
        if ($workingWeekdays -contains $weekdayNames[$dayIndex - 1]) {
            [void]($weekDay.Working = $true)
            [void]($weekDay.Shift1.Start = [datetime]"1899-12-30T$($case.calendar.working_intervals[0].start):00")
            [void]($weekDay.Shift1.Finish = [datetime]"1899-12-30T$($case.calendar.working_intervals[0].finish):00")
            [void]($weekDay.Shift2.Start = [datetime]"1899-12-30T$($case.calendar.working_intervals[1].start):00")
            [void]($weekDay.Shift2.Finish = [datetime]"1899-12-30T$($case.calendar.working_intervals[1].finish):00")
        }
        else {
            [void]($weekDay.Working = $false)
        }
    }

    $app.BaseCalendarCreate("Yuxi Phase 9 Task Calendar", [string]$standardCalendar.Name) | Out-Null
    $taskCalendar = $project.BaseCalendars.Item("Yuxi Phase 9 Task Calendar")
    foreach ($dayIndex in 2..6) {
        $weekDay = $taskCalendar.WeekDays.Item($dayIndex)
        [void]($weekDay.Working = $true)
        [void]($weekDay.Shift1.Start = [datetime]"1899-12-30T09:00:00")
        [void]($weekDay.Shift1.Finish = [datetime]"1899-12-30T12:00:00")
        [void]($weekDay.Shift2.Start = [datetime]"1899-12-30T13:00:00")
        [void]($weekDay.Shift2.Finish = [datetime]"1899-12-30T18:00:00")
    }

    $taskMap = @{}
    foreach ($taskSpec in @($case.tasks)) {
        $task = $project.Tasks.Add([string]$taskSpec.name)
        [void]($task.Text30 = [string]$taskSpec.task_id)
        [void]($task.Manual = $false)
        [void]($task.Duration = $app.DurationValue("$([int]$taskSpec.duration_minutes)m"))
        $taskMap[[string]$taskSpec.task_id] = $task
    }

    foreach ($taskSpec in @($case.tasks)) {
        $indentCount = [int]$taskSpec.outline_level - 1
        if ($indentCount -le 0) {
            continue
        }
        $task = $taskMap[[string]$taskSpec.task_id]
        $app.SelectRow([int]$task.ID, $false) | Out-Null
        foreach ($unused in 1..$indentCount) {
            $app.OutlineIndent() | Out-Null
        }
    }

    foreach ($taskSpec in @($case.tasks)) {
        $task = $taskMap[[string]$taskSpec.task_id]
        if ([string]$taskSpec.task_calendar) {
            Invoke-RecordedMutation "SET_TASK_CALENDAR" ([string]$taskSpec.task_id) {
                [void]($task.Calendar = [string]$taskSpec.task_calendar)
            }
        }
        if ([string]$taskSpec.constraint_type) {
            Invoke-RecordedMutation "SET_CONSTRAINT" ([string]$taskSpec.task_id) {
                [void]($task.ConstraintType = [int]$constraintTypes[[string]$taskSpec.constraint_type])
                [void]($task.ConstraintDate = [datetime]$taskSpec.constraint_date)
            }
        }
        if ([string]$taskSpec.actual_start) {
            Invoke-RecordedMutation "SET_ACTUAL_START" ([string]$taskSpec.task_id) {
                [void]($task.ActualStart = [datetime]$taskSpec.actual_start)
            }
        }
        if ([string]$taskSpec.actual_finish) {
            Invoke-RecordedMutation "SET_ACTUAL_FINISH" ([string]$taskSpec.task_id) {
                [void]($task.ActualFinish = [datetime]$taskSpec.actual_finish)
            }
        }
    }

    foreach ($dependency in @($case.dependencies)) {
        $successor = $taskMap[[string]$dependency.successor_task_id]
        $predecessor = $taskMap[[string]$dependency.predecessor_task_id]
        $relationText = "$([int]$predecessor.ID)$([string]$dependency.type)"
        Invoke-RecordedMutation "SET_DEPENDENCY:$([string]$dependency.scenario)" ([string]$dependency.dependency_id) {
            $existing = [string]$successor.Predecessors
            [void]($successor.Predecessors = if ($existing) { "$existing,$relationText" } else { $relationText })
        }
    }

    foreach ($taskSpec in @($case.tasks | Where-Object { $null -ne $_.active -and -not [bool]$_.active })) {
        $task = $taskMap[[string]$taskSpec.task_id]
        Invoke-RecordedMutation "SET_INACTIVE" ([string]$taskSpec.task_id) {
            [void]($task.Active = $false)
        }
    }

    $app.CalculateProject() | Out-Null
    $beforeSave = Get-Observation $project "BEFORE_SAVE"
    $app.FileSaveAs($mppFullPath) | Out-Null
    $app.FileCloseEx(1) | Out-Null
    $projectCreated = $false
    $app.FileOpenEx($mppFullPath) | Out-Null
    Start-Sleep -Milliseconds 800
    $project = $app.ActiveProject
    if ($null -eq $project) {
        throw "MSPROJECT_SAVED_PROJECT_NOT_REOPENED"
    }
    $projectCreated = $true
    $app.CalculateProject() | Out-Null
    $afterReopen = Get-Observation $project "AFTER_SAVE_CLOSE_REOPEN_RECALCULATE"

    [ordered]@{
        schema_version = "microsoft_project_schedule_semantics_observation_v1"
        case_id = [string]$case.case_id
        observation_status = "CAPTURED_BY_MS_PROJECT_COM"
        captured_at = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")
        capture_method = "MICROSOFT_PROJECT_COM_AFTER_SAVE_CLOSE_REOPEN_RECALCULATE"
        microsoft_project_version = [string]$app.Version
        mutation_results = @($mutationResults)
        before_save = $beforeSave
        after_reopen = $afterReopen
    } | ConvertTo-Json -Depth 8
}
finally {
    if ($projectCreated) {
        $app.FileCloseEx(0) | Out-Null
    }
    $app.Quit()
}
