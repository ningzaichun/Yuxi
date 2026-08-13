param(
    [Parameter(Mandatory = $false)]
    [string]$CasePath
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

if (-not $CasePath) {
    $CasePath = Join-Path $PSScriptRoot "..\test\data\schedule\microsoft_project_s3_golden_case.json"
}
$case = Get-Content -Raw -Encoding UTF8 -LiteralPath $CasePath | ConvertFrom-Json
$taskIds = @($case.tasks | ForEach-Object { [string]$_.task_id })
if ($taskIds.Count -ne @($taskIds | Select-Object -Unique).Count) {
    throw "GOLDEN_CASE_TASK_IDS_MUST_BE_UNIQUE"
}
if (@($case.calendar.working_intervals).Count -ne 2) {
    throw "GOLDEN_CASE_REQUIRES_EXACTLY_TWO_WORKING_INTERVALS"
}
foreach ($dependency in @($case.dependencies)) {
    if ($taskIds -notcontains [string]$dependency.predecessor_task_id -or
        $taskIds -notcontains [string]$dependency.successor_task_id) {
        throw "GOLDEN_CASE_DEPENDENCY_TASK_UNKNOWN"
    }
    if (@("FS", "SS", "FF", "SF") -notcontains [string]$dependency.type) {
        throw "GOLDEN_CASE_DEPENDENCY_TYPE_UNSUPPORTED"
    }
    if ([int]$dependency.lag_minutes -lt 0) {
        throw "GOLDEN_CASE_NEGATIVE_LAG_UNSUPPORTED"
    }
}
$supportedConstraints = @("AS_SOON_AS_POSSIBLE", "START_NO_EARLIER_THAN", "FINISH_NO_EARLIER_THAN")
foreach ($taskSpec in @($case.tasks)) {
    $schedulingMode = if ([string]$taskSpec.scheduling_mode) {
        [string]$taskSpec.scheduling_mode
    }
    else {
        "automatic"
    }
    if (@("automatic", "manual") -notcontains $schedulingMode) {
        throw "GOLDEN_CASE_TASK_SCHEDULING_MODE_UNSUPPORTED"
    }
    if ($schedulingMode -eq "manual" -and -not [string]$taskSpec.planned_start) {
        throw "GOLDEN_CASE_MANUAL_TASK_START_REQUIRED"
    }
    $constraintType = if ($null -eq $taskSpec.constraint) {
        "AS_SOON_AS_POSSIBLE"
    }
    else {
        [string]$taskSpec.constraint.type
    }
    if ($supportedConstraints -notcontains $constraintType) {
        throw "GOLDEN_CASE_TASK_CONSTRAINT_UNSUPPORTED"
    }
    if ($constraintType -ne "AS_SOON_AS_POSSIBLE" -and -not [string]$taskSpec.constraint.date) {
        throw "GOLDEN_CASE_TASK_CONSTRAINT_DATE_REQUIRED"
    }
}
$app = New-Object -ComObject MSProject.Application
$projectCreated = $false

try {
    $app.DisplayAlerts = $false
    $app.Visible = $false | Out-Null
    $app.FileNew() | Out-Null
    Start-Sleep -Milliseconds 500

    $project = $app.ActiveProject
    if ($null -eq $project) {
        throw "MSPROJECT_ACTIVE_PROJECT_NOT_CREATED"
    }
    $projectCreated = $true
    $project.ScheduleFromStart = $true
    $project.ProjectStart = [datetime]$case.project_start
    $project.HoursPerDay = 8
    $project.HoursPerWeek = 40
    $project.DaysPerMonth = 20

    $calendar = $project.BaseCalendars.Item(1)
    $workingWeekdays = @($case.calendar.working_weekdays)
    $weekdayNames = @("SUNDAY", "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY")
    foreach ($dayIndex in 1..7) {
        $weekDay = $calendar.WeekDays.Item($dayIndex)
        $weekdayName = $weekdayNames[$dayIndex - 1]
        if ($workingWeekdays -contains $weekdayName) {
            $weekDay.Working = $true
            $weekDay.Shift1.Start = [datetime]"1899-12-30T$($case.calendar.working_intervals[0].start):00"
            $weekDay.Shift1.Finish = [datetime]"1899-12-30T$($case.calendar.working_intervals[0].finish):00"
            $weekDay.Shift2.Start = [datetime]"1899-12-30T$($case.calendar.working_intervals[1].start):00"
            $weekDay.Shift2.Finish = [datetime]"1899-12-30T$($case.calendar.working_intervals[1].finish):00"
        }
        else {
            $weekDay.Working = $false
        }
    }

    $projectTaskIds = @{}
    foreach ($taskSpec in $case.tasks) {
        $task = $project.Tasks.Add([string]$taskSpec.name)
        $isManual = ([string]$taskSpec.scheduling_mode -eq "manual")
        $task.Manual = $false
        $task.Duration = $app.DurationValue("$($taskSpec.duration_minutes)m")
        if ($isManual) {
            $task.Start = [datetime]$taskSpec.planned_start
            $task.Manual = $true
            $task.ConstraintType = 0
        }
        $projectTaskIds[[string]$taskSpec.task_id] = [int]$task.ID
    }
    foreach ($taskSpec in $case.tasks) {
        $task = $project.Tasks.Item($projectTaskIds[[string]$taskSpec.task_id])
        if ($null -ne $case.dependencies) {
            $incoming = @($case.dependencies | Where-Object { $_.successor_task_id -eq $taskSpec.task_id })
            if ($incoming.Count -gt 0) {
                $task.Predecessors = ($incoming | ForEach-Object {
                    $lag = if ([int]$_.lag_minutes -gt 0) { "+$($_.lag_minutes)m" } else { "" }
                    "$($projectTaskIds[[string]$_.predecessor_task_id])$($_.type)$lag"
                }) -join ","
            }
        }
        elseif ($taskSpec.predecessor_task_ids.Count -gt 0) {
            $task.Predecessors = ($taskSpec.predecessor_task_ids | ForEach-Object {
                $projectTaskIds[[string]$_]
            }) -join ","
        }
        if ($null -ne $taskSpec.constraint -and [string]$taskSpec.constraint.type -ne "AS_SOON_AS_POSSIBLE") {
            $task.ConstraintType = switch ([string]$taskSpec.constraint.type) {
                "START_NO_EARLIER_THAN" { 4 }
                "FINISH_NO_EARLIER_THAN" { 6 }
            }
            $task.ConstraintDate = [datetime]$taskSpec.constraint.date
        }
    }
    $app.CalculateProject() | Out-Null

    $taskDates = foreach ($taskSpec in $case.tasks) {
        $task = $project.Tasks.Item($projectTaskIds[[string]$taskSpec.task_id])
        [ordered]@{
            task_id = [string]$taskSpec.task_id
            early_start = ([datetime]$task.Start).ToString("yyyy-MM-ddTHH:mm:sszzz")
            early_finish = ([datetime]$task.Finish).ToString("yyyy-MM-ddTHH:mm:sszzz")
        }
    }
    $taskRelations = foreach ($taskSpec in $case.tasks) {
        $task = $project.Tasks.Item($projectTaskIds[[string]$taskSpec.task_id])
        if ([string]$task.Predecessors) {
            [ordered]@{
                task_id = [string]$taskSpec.task_id
                microsoft_project_predecessors = [string]$task.Predecessors
            }
        }
    }
    $taskConstraints = foreach ($taskSpec in $case.tasks) {
        $task = $project.Tasks.Item($projectTaskIds[[string]$taskSpec.task_id])
        [ordered]@{
            task_id = [string]$taskSpec.task_id
            microsoft_project_constraint_type = [int]$task.ConstraintType
            microsoft_project_constraint_date = if ([int]$task.ConstraintType -eq 0) {
                $null
            }
            else {
                ([datetime]$task.ConstraintDate).ToString("yyyy-MM-ddTHH:mm:sszzz")
            }
        }
    }
    $taskSchedulingModes = foreach ($taskSpec in $case.tasks) {
        $task = $project.Tasks.Item($projectTaskIds[[string]$taskSpec.task_id])
        [ordered]@{
            task_id = [string]$taskSpec.task_id
            microsoft_project_manual = [bool]$task.Manual
            microsoft_project_start_text = [string]$task.GetField(188744965)
            microsoft_project_scheduled_start = ([datetime]$task.ScheduledStart).ToString("yyyy-MM-ddTHH:mm:sszzz")
        }
    }
    [ordered]@{
        observation_status = "CAPTURED_BY_MS_PROJECT_COM"
        captured_at = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")
        capture_method = "WINDOWS_POWERSHELL_MS_PROJECT_COM_READ_ONLY_RESULT"
        manual_input_order = "MANUAL_START_THEN_RELATIONSHIPS"
        microsoft_project_version = [string]$app.Version
        calendar_name = [string]$calendar.Name
        project_start = ([datetime]$project.ProjectStart).ToString("yyyy-MM-ddTHH:mm:sszzz")
        hours_per_day = [double]$project.HoursPerDay
        hours_per_week = [double]$project.HoursPerWeek
        task_relations = @($taskRelations)
        task_constraints = @($taskConstraints)
        task_scheduling_modes = @($taskSchedulingModes)
        task_dates = @($taskDates)
    } | ConvertTo-Json -Depth 5
}
finally {
    if ($projectCreated) {
        $app.FileCloseEx(0) | Out-Null
    }
    $app.Quit()
}
