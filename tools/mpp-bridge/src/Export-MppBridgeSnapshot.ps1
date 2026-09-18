[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InputMpp,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [Parameter(Mandatory = $true)]
    [string]$Timezone
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$toolVersion = '0.2.0-m1'
$runId = [guid]::NewGuid().ToString('D')
$stage = 'validate_input'
$app = $null
$project = $null
$tasksCollection = $null
$projectOpened = $false
$capturedSnapshot = $null
$failure = $null
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)

    return (Get-FileHash -Algorithm SHA256 -LiteralPath $LiteralPath).Hash.ToLowerInvariant()
}

function Get-StringSha256 {
    param([Parameter(Mandatory = $true)][string]$Value)

    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
    $hash = [System.Security.Cryptography.SHA256]::HashData($bytes)
    return [Convert]::ToHexString($hash).ToLowerInvariant()
}

function Write-JsonFile {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)]$Value
    )

    $json = $Value | ConvertTo-Json -Depth 30
    [System.IO.File]::WriteAllText($LiteralPath, "$json`n", $utf8NoBom)
}

function Set-ExtractStage {
    param([Parameter(Mandatory = $true)][string]$Name)

    $script:stage = $Name
    if (-not [string]::IsNullOrWhiteSpace($script:progressPath)) {
        Write-JsonFile -LiteralPath $script:progressPath -Value ([ordered]@{
            run_id = $script:runId
            stage = $Name
            updated_at = [datetimeoffset]::UtcNow.ToString('o')
        })
    }
}

function Release-ComObject {
    param($Value)

    if ($null -ne $Value -and [System.Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
    }
}

function Get-ComProperty {
    param(
        [Parameter(Mandatory = $true)]$ComObject,
        [Parameter(Mandatory = $true)][string]$Name
    )

    $value = $ComObject.GetType().InvokeMember(
        $Name,
        [System.Reflection.BindingFlags]::GetProperty,
        $null,
        $ComObject,
        $null
    )
    return $value
}

function Convert-ToOffsetTimestamp {
    param(
        [Parameter(Mandatory = $true)]$Value,
        [Parameter(Mandatory = $true)][System.TimeZoneInfo]$TimeZoneInfo
    )

    $date = [datetime]$Value
    $unspecified = [datetime]::SpecifyKind($date, [DateTimeKind]::Unspecified)
    $offset = $TimeZoneInfo.GetUtcOffset($unspecified)
    return [datetimeoffset]::new($unspecified, $offset).ToString('o')
}

function Convert-ToOptionalOffsetTimestamp {
    param(
        $Value,
        [Parameter(Mandatory = $true)][System.TimeZoneInfo]$TimeZoneInfo
    )

    if ($null -eq $Value) {
        return $null
    }
    $text = [string]$Value
    if ([string]::IsNullOrWhiteSpace($text) -or $text -in @('NA', 'N/A', '不适用')) {
        return $null
    }
    try {
        return Convert-ToOffsetTimestamp -Value $Value -TimeZoneInfo $TimeZoneInfo
    }
    catch {
        return $null
    }
}

function Get-OptionalComProperty {
    param(
        [Parameter(Mandatory = $true)]$ComObject,
        [Parameter(Mandatory = $true)][string]$Name
    )

    try {
        return Get-ComProperty -ComObject $ComObject -Name $Name
    }
    catch {
        return $null
    }
}

function Convert-ToTimeText {
    param($Value)

    if ($null -eq $Value) {
        return $null
    }
    if ($Value -is [byte] -or $Value -is [int16] -or $Value -is [int32] -or $Value -is [int64] -or $Value -is [double]) {
        if ([double]$Value -eq 0) {
            return $null
        }
    }
    $text = [string]$Value
    if ([string]::IsNullOrWhiteSpace($text) -or $text -eq '0') {
        return $null
    }
    try {
        return ([datetime]$Value).ToString('HH:mm')
    }
    catch {
        return $null
    }
}

function Get-WorkingIntervals {
    param(
        [Parameter(Mandatory = $true)]$CalendarPeriod,
        [Parameter(Mandatory = $true)][string]$ObjectRef,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][System.Collections.Generic.List[object]]$Unsupported
    )

    $intervals = [System.Collections.Generic.List[object]]::new()
    for ($shiftIndex = 1; $shiftIndex -le 5; $shiftIndex++) {
        $shift = $null
        try {
            $shift = Get-OptionalComProperty -ComObject $CalendarPeriod -Name "Shift$shiftIndex"
            if ($null -eq $shift) {
                continue
            }
            $start = Convert-ToTimeText -Value (Get-OptionalComProperty -ComObject $shift -Name 'Start')
            $finish = Convert-ToTimeText -Value (Get-OptionalComProperty -ComObject $shift -Name 'Finish')
            if ($null -eq $start -and $null -eq $finish) {
                continue
            }
            if ($null -eq $start -or $null -eq $finish -or $start -ge $finish) {
                $Unsupported.Add([ordered]@{
                    code = 'CROSS_MIDNIGHT_OR_INVALID_CALENDAR_INTERVAL'
                    severity = 'BLOCKER'
                    object_type = 'CALENDAR'
                    object_refs = @($ObjectRef)
                    detail = "Shift $shiftIndex cannot be represented as a same-day interval."
                })
                continue
            }
            $intervals.Add([ordered]@{ start = $start; finish = $finish })
        }
        finally {
            Release-ComObject -Value $shift
        }
    }
    return @($intervals)
}

function Get-ConstraintName {
    param([int]$Code)

    return @{
        0 = 'ASAP'
        1 = 'ALAP'
        2 = 'MSO'
        3 = 'MFO'
        4 = 'SNET'
        5 = 'SNLT'
        6 = 'FNET'
        7 = 'FNLT'
    }[$Code]
}

function Get-DependencyType {
    param([int]$Code)

    return @{
        0 = 'FF'
        1 = 'FS'
        2 = 'SF'
        3 = 'SS'
    }[$Code]
}

function Get-NewWinProjProcessIds {
    param([int[]]$BeforeIds)

    $afterIds = @(Get-Process -Name WINPROJ -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
    return @($afterIds | Where-Object { $_ -notin $BeforeIds })
}

$inputPath = (Resolve-Path -LiteralPath $InputMpp).Path
if ([System.IO.Path]::GetExtension($inputPath) -ine '.mpp') {
    throw 'MPP_BRIDGE_INPUT_EXTENSION_INVALID: InputMpp must point to an .mpp file.'
}

$timeZoneInfo = [System.TimeZoneInfo]::FindSystemTimeZoneById($Timezone)
$outputPath = [System.IO.Path]::GetFullPath($OutputDirectory)
if (-not (Test-Path -LiteralPath $outputPath)) {
    [void](New-Item -ItemType Directory -Path $outputPath)
}
$outputPath = (Resolve-Path -LiteralPath $outputPath).Path
$runDirectory = Join-Path $outputPath "extract-$runId"
[void](New-Item -ItemType Directory -Path $runDirectory)

$originalByteBackupPath = Join-Path $runDirectory 'original-byte-backup.mpp'
$workingCopyPath = Join-Path $runDirectory 'working-copy.mpp'
$snapshotPath = Join-Path $runDirectory 'snapshot.json'
$manifestPath = Join-Path $runDirectory 'manifest.json'
$errorPath = Join-Path $runDirectory 'error.json'
$progressPath = Join-Path $runDirectory 'progress.json'
Set-ExtractStage -Name 'validate_input'
$sourceHashBefore = Get-Sha256 -LiteralPath $inputPath
Copy-Item -LiteralPath $inputPath -Destination $originalByteBackupPath
Copy-Item -LiteralPath $originalByteBackupPath -Destination $workingCopyPath
$processIdsBefore = @(Get-Process -Name WINPROJ -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
$processIdsAfterCreate = @()

try {
    Set-ExtractStage -Name 'validate_project_path'
    if ($workingCopyPath.Length -gt 259) {
        throw 'MPP_BRIDGE_PROJECT_PATH_TOO_LONG'
    }
    if ($processIdsBefore.Count -gt 0) {
        throw 'MPP_BRIDGE_PREEXISTING_PROJECT_PROCESS'
    }

    Set-ExtractStage -Name 'create_com_application'
    $app = New-Object -ComObject MSProject.Application
    [void]($app.DisplayAlerts = $false)
    [void]($app.Visible = $false)
    $processIdsAfterCreate = @(Get-Process -Name WINPROJ -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
    if ($processIdsAfterCreate.Count -eq 0) {
        throw 'MPP_BRIDGE_COM_PROCESS_NOT_OBSERVED'
    }

    Set-ExtractStage -Name 'open_working_copy_first_pass'
    $app.FileOpenEx($workingCopyPath) | Out-Null
    $projectOpened = $true
    $project = $app.GetType().InvokeMember(
        'ActiveProject',
        [System.Reflection.BindingFlags]::GetProperty,
        $null,
        $app,
        $null
    )

    Set-ExtractStage -Name 'calculate_and_save_first_pass'
    $app.CalculateProject() | Out-Null
    $app.FileSave() | Out-Null

    Set-ExtractStage -Name 'close_first_pass'
    $app.FileCloseEx(1) | Out-Null
    $projectOpened = $false
    Release-ComObject -Value $project
    $project = $null

    Set-ExtractStage -Name 'reopen_working_copy'
    $app.FileOpenEx($workingCopyPath) | Out-Null
    $projectOpened = $true
    $project = $app.GetType().InvokeMember(
        'ActiveProject',
        [System.Reflection.BindingFlags]::GetProperty,
        $null,
        $app,
        $null
    )

    Set-ExtractStage -Name 'recalculate_after_reopen'
    $app.CalculateProject() | Out-Null

    $unsupported = [System.Collections.Generic.List[object]]::new()

    Set-ExtractStage -Name 'read_calendars'
    $calendars = [System.Collections.Generic.List[object]]::new()
    $calendarNameToId = @{}
    $baseCalendars = $project.GetType().InvokeMember(
        'BaseCalendars',
        [System.Reflection.BindingFlags]::GetProperty,
        $null,
        $project,
        $null
    )
    try {
        $calendarCount = [int](Get-ComProperty -ComObject $baseCalendars -Name 'Count')
        for ($calendarIndex = 1; $calendarIndex -le $calendarCount; $calendarIndex++) {
            $calendar = $null
            $weekDays = $null
            $exceptions = $null
            try {
                $calendar = $baseCalendars.Item($calendarIndex)
                if ($null -eq $calendar) {
                    continue
                }
                $calendarName = [string](Get-ComProperty -ComObject $calendar -Name 'Name')
                if ([string]::IsNullOrWhiteSpace($calendarName)) {
                    throw 'MPP_BRIDGE_CALENDAR_NAME_MISSING'
                }
                if ($calendarNameToId.ContainsKey($calendarName)) {
                    throw "MPP_BRIDGE_DUPLICATE_CALENDAR_NAME: $calendarName"
                }
                $calendarId = "calendar:source:$calendarIndex"
                $calendarNameToId[$calendarName] = $calendarId

                $baseCalendarName = $null
                $baseCalendar = Get-OptionalComProperty -ComObject $calendar -Name 'BaseCalendar'
                if ($null -ne $baseCalendar) {
                    if ([System.Runtime.InteropServices.Marshal]::IsComObject($baseCalendar)) {
                        $baseCalendarName = [string](Get-OptionalComProperty -ComObject $baseCalendar -Name 'Name')
                        Release-ComObject -Value $baseCalendar
                    }
                    elseif (-not [string]::IsNullOrWhiteSpace([string]$baseCalendar)) {
                        $baseCalendarName = [string]$baseCalendar
                    }
                }

                $weekDayPayloads = [System.Collections.Generic.List[object]]::new()
                $weekDays = $calendar.GetType().InvokeMember(
                    'WeekDays',
                    [System.Reflection.BindingFlags]::GetProperty,
                    $null,
                    $calendar,
                    $null
                )
                $dayNames = @('SUNDAY', 'MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY')
                for ($dayIndex = 1; $dayIndex -le 7; $dayIndex++) {
                    $weekDay = $null
                    try {
                        $weekDay = $weekDays.Item($dayIndex)
                        $workingCode = [int](Get-ComProperty -ComObject $weekDay -Name 'Working')
                        $intervals = @(Get-WorkingIntervals -CalendarPeriod $weekDay -ObjectRef "$calendarId/week_days/$($dayNames[$dayIndex - 1])" -Unsupported $unsupported)
                        if ($workingCode -eq -1) {
                            $unsupported.Add([ordered]@{
                                code = 'INHERITED_CALENDAR_DAY_UNRESOLVED'
                                severity = 'BLOCKER'
                                object_type = 'CALENDAR'
                                object_refs = @($calendarId)
                                detail = "The $($dayNames[$dayIndex - 1]) definition inherits from a base calendar."
                            })
                        }
                        $working = $workingCode -eq 1
                        if ($working -ne ($intervals.Count -gt 0)) {
                            $unsupported.Add([ordered]@{
                                code = 'CALENDAR_WORKING_FLAG_INTERVAL_MISMATCH'
                                severity = 'BLOCKER'
                                object_type = 'CALENDAR'
                                object_refs = @($calendarId)
                                detail = "The $($dayNames[$dayIndex - 1]) working flag does not match its intervals."
                            })
                        }
                        $weekDayPayloads.Add([ordered]@{
                            day = $dayNames[$dayIndex - 1]
                            working = $working
                            intervals = $intervals
                        })
                    }
                    finally {
                        Release-ComObject -Value $weekDay
                    }
                }

                $exceptionPayloads = [System.Collections.Generic.List[object]]::new()
                $exceptions = $calendar.GetType().InvokeMember(
                    'Exceptions',
                    [System.Reflection.BindingFlags]::GetProperty,
                    $null,
                    $calendar,
                    $null
                )
                $exceptionCount = [int](Get-ComProperty -ComObject $exceptions -Name 'Count')
                for ($exceptionIndex = 1; $exceptionIndex -le $exceptionCount; $exceptionIndex++) {
                    $exception = $null
                    try {
                        $exception = $exceptions.Item($exceptionIndex)
                        $exceptionId = "$calendarId/exception:$exceptionIndex"
                        $occurrences = [int](Get-OptionalComProperty -ComObject $exception -Name 'Occurrences')
                        $sourceTypeCode = [int](Get-OptionalComProperty -ComObject $exception -Name 'Type')
                        if ($occurrences -gt 1) {
                            $unsupported.Add([ordered]@{
                                code = 'RECURRING_CALENDAR_EXCEPTION_UNSUPPORTED'
                                severity = 'BLOCKER'
                                object_type = 'CALENDAR_EXCEPTION'
                                object_refs = @($exceptionId)
                                detail = "The exception has $occurrences occurrences."
                            })
                        }
                        $intervals = @(Get-WorkingIntervals -CalendarPeriod $exception -ObjectRef $exceptionId -Unsupported $unsupported)
                        $exceptionPayloads.Add([ordered]@{
                            exception_id = $exceptionId
                            name = [string](Get-ComProperty -ComObject $exception -Name 'Name')
                            start_date = Convert-ToOffsetTimestamp -Value (Get-ComProperty -ComObject $exception -Name 'Start') -TimeZoneInfo $timeZoneInfo
                            finish_date = Convert-ToOffsetTimestamp -Value (Get-ComProperty -ComObject $exception -Name 'Finish') -TimeZoneInfo $timeZoneInfo
                            working = $intervals.Count -gt 0
                            intervals = $intervals
                            source_type_code = $sourceTypeCode
                            occurrences = $occurrences
                        })
                    }
                    finally {
                        Release-ComObject -Value $exception
                    }
                }

                $calendars.Add([ordered]@{
                    calendar_id = $calendarId
                    source_index = $calendarIndex
                    name = $calendarName
                    base_calendar_name = $baseCalendarName
                    week_days = @($weekDayPayloads)
                    exceptions = @($exceptionPayloads)
                })
            }
            finally {
                Release-ComObject -Value $exceptions
                Release-ComObject -Value $weekDays
                Release-ComObject -Value $calendar
            }
        }
    }
    finally {
        Release-ComObject -Value $baseCalendars
    }

    $defaultCalendar = $project.GetType().InvokeMember(
        'Calendar',
        [System.Reflection.BindingFlags]::GetProperty,
        $null,
        $project,
        $null
    )
    try {
        $defaultCalendarName = [string](Get-ComProperty -ComObject $defaultCalendar -Name 'Name')
    }
    finally {
        Release-ComObject -Value $defaultCalendar
    }
    if (-not $calendarNameToId.ContainsKey($defaultCalendarName)) {
        throw "MPP_BRIDGE_DEFAULT_CALENDAR_UNKNOWN: $defaultCalendarName"
    }
    $defaultCalendarId = $calendarNameToId[$defaultCalendarName]

    Set-ExtractStage -Name 'read_tasks'
    $tasks = [System.Collections.Generic.List[object]]::new()
    $taskIdsByUniqueId = @{}
    $assignmentCount = 0
    $tasksCollection = $project.GetType().InvokeMember(
        'Tasks',
        [System.Reflection.BindingFlags]::GetProperty,
        $null,
        $project,
        $null
    )
    $taskCount = [int](Get-ComProperty -ComObject $tasksCollection -Name 'Count')
    for ($index = 1; $index -le $taskCount; $index++) {
        $task = $null
        try {
            Set-ExtractStage -Name "read_tasks:${index}:item"
            $task = $tasksCollection.Item($index)
            if ($null -eq $task) {
                continue
            }

            Set-ExtractStage -Name "read_tasks:${index}:unique_id"
            $sourceUniqueId = [int](Get-ComProperty -ComObject $task -Name 'UniqueID')
            if ($taskIdsByUniqueId.ContainsKey($sourceUniqueId)) {
                throw "MPP_BRIDGE_DUPLICATE_TASK_UNIQUE_ID: $sourceUniqueId"
            }
            $bridgeTaskId = "source-uid:$sourceUniqueId"
            $taskIdsByUniqueId[$sourceUniqueId] = $bridgeTaskId
            Set-ExtractStage -Name "read_tasks:${index}:summary"
            $summary = [bool](Get-ComProperty -ComObject $task -Name 'Summary')
            Set-ExtractStage -Name "read_tasks:${index}:milestone"
            $milestone = [bool](Get-ComProperty -ComObject $task -Name 'Milestone')
            Set-ExtractStage -Name "read_tasks:${index}:manual"
            $manual = [bool](Get-ComProperty -ComObject $task -Name 'Manual')
            Set-ExtractStage -Name "read_tasks:${index}:constraint_type"
            $constraintCode = [int](Get-ComProperty -ComObject $task -Name 'ConstraintType')
            $constraintName = Get-ConstraintName -Code $constraintCode
            Set-ExtractStage -Name "read_tasks:${index}:constraint_date"
            $constraintDate = Convert-ToOptionalOffsetTimestamp -Value (Get-OptionalComProperty -ComObject $task -Name 'ConstraintDate') -TimeZoneInfo $timeZoneInfo
            Set-ExtractStage -Name "read_tasks:${index}:deadline"
            $deadline = Convert-ToOptionalOffsetTimestamp -Value (Get-OptionalComProperty -ComObject $task -Name 'Deadline') -TimeZoneInfo $timeZoneInfo
            Set-ExtractStage -Name "read_tasks:${index}:actual_start"
            $actualStart = Convert-ToOptionalOffsetTimestamp -Value (Get-OptionalComProperty -ComObject $task -Name 'ActualStart') -TimeZoneInfo $timeZoneInfo
            Set-ExtractStage -Name "read_tasks:${index}:actual_finish"
            $actualFinish = Convert-ToOptionalOffsetTimestamp -Value (Get-OptionalComProperty -ComObject $task -Name 'ActualFinish') -TimeZoneInfo $timeZoneInfo
            Set-ExtractStage -Name "read_tasks:${index}:baseline_start"
            $baselineStart = Convert-ToOptionalOffsetTimestamp -Value (Get-OptionalComProperty -ComObject $task -Name 'BaselineStart') -TimeZoneInfo $timeZoneInfo
            Set-ExtractStage -Name "read_tasks:${index}:baseline_finish"
            $baselineFinish = Convert-ToOptionalOffsetTimestamp -Value (Get-OptionalComProperty -ComObject $task -Name 'BaselineFinish') -TimeZoneInfo $timeZoneInfo
            Set-ExtractStage -Name "read_tasks:${index}:percent_complete"
            $percentComplete = [int](Get-ComProperty -ComObject $task -Name 'PercentComplete')

            $parentTaskId = $null
            $outlineParent = $null
            try {
                Set-ExtractStage -Name "read_tasks:${index}:outline_parent"
                $outlineParent = Get-OptionalComProperty -ComObject $task -Name 'OutlineParent'
                if ($null -ne $outlineParent) {
                    $parentUniqueId = [int](Get-ComProperty -ComObject $outlineParent -Name 'UniqueID')
                    if ($parentUniqueId -gt 0) {
                        $parentTaskId = "source-uid:$parentUniqueId"
                    }
                }
            }
            finally {
                Release-ComObject -Value $outlineParent
            }

            Set-ExtractStage -Name "read_tasks:${index}:calendar"
            $sourceCalendarName = [string](Get-OptionalComProperty -ComObject $task -Name 'Calendar')
            $calendarId = if ($calendarNameToId.ContainsKey($sourceCalendarName)) {
                $calendarNameToId[$sourceCalendarName]
            }
            else {
                $defaultCalendarId
            }

            if ($constraintCode -notin @(0, 2, 4, 6, 7)) {
                $unsupported.Add([ordered]@{
                    code = 'CONSTRAINT_TYPE_UNSUPPORTED'
                    severity = 'BLOCKER'
                    object_type = 'TASK'
                    object_refs = @($bridgeTaskId)
                    detail = "Constraint type $constraintCode ($constraintName) is outside the M1 scope."
                })
            }
            if ($manual) {
                $unsupported.Add([ordered]@{
                    code = 'MANUAL_TASK_ROUNDTRIP_UNSUPPORTED'
                    severity = 'BLOCKER'
                    object_type = 'TASK'
                    object_refs = @($bridgeTaskId)
                    detail = 'The manual task is observable but not eligible for M2 roundtrip.'
                })
            }
            if ($null -ne $actualStart -or $null -ne $actualFinish -or $percentComplete -gt 0) {
                $unsupported.Add([ordered]@{
                    code = 'ACTUAL_REMAINING_SEMANTICS_UNSUPPORTED'
                    severity = 'BLOCKER'
                    object_type = 'TASK'
                    object_refs = @($bridgeTaskId)
                    detail = 'Actual or progress facts require the later progress contract.'
                })
            }
            if ($null -ne $baselineStart -or $null -ne $baselineFinish) {
                $unsupported.Add([ordered]@{
                    code = 'BASELINE_SEMANTICS_UNSUPPORTED'
                    severity = 'BLOCKER'
                    object_type = 'TASK'
                    object_refs = @($bridgeTaskId)
                    detail = 'Baseline facts require the later baseline contract.'
                })
            }

            $taskAssignments = $null
            try {
                Set-ExtractStage -Name "read_tasks:${index}:assignments"
                $taskAssignments = $task.GetType().InvokeMember(
                    'Assignments',
                    [System.Reflection.BindingFlags]::GetProperty,
                    $null,
                    $task,
                    $null
                )
                $assignmentCount += [int](Get-ComProperty -ComObject $taskAssignments -Name 'Count')
            }
            catch {
            }
            finally {
                Release-ComObject -Value $taskAssignments
            }

            Set-ExtractStage -Name "read_tasks:${index}:payload"
            $tasks.Add([ordered]@{
                bridge_task_id = $bridgeTaskId
                source_id = [int](Get-ComProperty -ComObject $task -Name 'ID')
                source_unique_id = $sourceUniqueId
                parent_task_id = $parentTaskId
                name = [string](Get-ComProperty -ComObject $task -Name 'Name')
                outline_level = [int](Get-ComProperty -ComObject $task -Name 'OutlineLevel')
                wbs = [string](Get-ComProperty -ComObject $task -Name 'WBS')
                task_type = if ($summary) { 'SUMMARY' } elseif ($milestone) { 'MILESTONE' } else { 'TASK' }
                summary = $summary
                milestone = $milestone
                scheduling_mode = if ($manual) { 'MANUAL' } else { 'AUTO' }
                active = [bool](Get-ComProperty -ComObject $task -Name 'Active')
                start = Convert-ToOffsetTimestamp -Value (Get-ComProperty -ComObject $task -Name 'Start') -TimeZoneInfo $timeZoneInfo
                finish = Convert-ToOffsetTimestamp -Value (Get-ComProperty -ComObject $task -Name 'Finish') -TimeZoneInfo $timeZoneInfo
                duration_minutes = if ($summary) { $null } else { [int](Get-ComProperty -ComObject $task -Name 'Duration') }
                project_rollup_duration_minutes = [int](Get-ComProperty -ComObject $task -Name 'Duration')
                percent_complete = $percentComplete
                constraint_type_code = $constraintCode
                constraint_type = $constraintName
                constraint_date = $constraintDate
                deadline = $deadline
                calendar_id = $calendarId
                source_calendar_name = if ($calendarNameToId.ContainsKey($sourceCalendarName)) { $sourceCalendarName } else { $null }
                predecessors_text = [string](Get-OptionalComProperty -ComObject $task -Name 'Predecessors')
            })
        }
        finally {
            Release-ComObject -Value $task
        }
    }

    Set-ExtractStage -Name 'read_dependencies'
    $dependencies = [System.Collections.Generic.List[object]]::new()
    $dependencyKeys = @{}
    for ($index = 1; $index -le $taskCount; $index++) {
        $task = $null
        $taskDependencies = $null
        try {
            $task = $tasksCollection.Item($index)
            if ($null -eq $task) {
                continue
            }
            $taskDependencies = $task.GetType().InvokeMember(
                'TaskDependencies',
                [System.Reflection.BindingFlags]::GetProperty,
                $null,
                $task,
                $null
            )
            $dependencyCount = [int](Get-ComProperty -ComObject $taskDependencies -Name 'Count')
            for ($dependencyIndex = 1; $dependencyIndex -le $dependencyCount; $dependencyIndex++) {
                $dependency = $null
                $fromTask = $null
                $toTask = $null
                try {
                    $dependency = $taskDependencies.Item($dependencyIndex)
                    $fromTask = $dependency.GetType().InvokeMember('From', [System.Reflection.BindingFlags]::GetProperty, $null, $dependency, $null)
                    $toTask = $dependency.GetType().InvokeMember('To', [System.Reflection.BindingFlags]::GetProperty, $null, $dependency, $null)
                    $fromUniqueId = [int](Get-ComProperty -ComObject $fromTask -Name 'UniqueID')
                    $toUniqueId = [int](Get-ComProperty -ComObject $toTask -Name 'UniqueID')
                    $sourceTypeCode = [int](Get-ComProperty -ComObject $dependency -Name 'Type')
                    $lagMinutes = [int](Get-ComProperty -ComObject $dependency -Name 'Lag')
                    $dependencyKey = "$fromUniqueId|$toUniqueId|$sourceTypeCode|$lagMinutes"
                    if ($dependencyKeys.ContainsKey($dependencyKey)) {
                        continue
                    }
                    if (-not $taskIdsByUniqueId.ContainsKey($fromUniqueId) -or -not $taskIdsByUniqueId.ContainsKey($toUniqueId)) {
                        throw "MPP_BRIDGE_DEPENDENCY_TASK_UNKNOWN: $dependencyKey"
                    }
                    $dependencyType = Get-DependencyType -Code $sourceTypeCode
                    if ($null -eq $dependencyType) {
                        $dependencyType = 'UNKNOWN'
                        $unsupported.Add([ordered]@{
                            code = 'DEPENDENCY_TYPE_UNSUPPORTED'
                            severity = 'BLOCKER'
                            object_type = 'DEPENDENCY'
                            object_refs = @($dependencyKey)
                            detail = "Dependency type code $sourceTypeCode is unknown."
                        })
                    }
                    $dependencyKeys[$dependencyKey] = $true
                    $dependencies.Add([ordered]@{
                        dependency_id = "dependency:$dependencyKey"
                        predecessor_task_id = $taskIdsByUniqueId[$fromUniqueId]
                        successor_task_id = $taskIdsByUniqueId[$toUniqueId]
                        type = $dependencyType
                        source_type_code = $sourceTypeCode
                        lag_minutes = $lagMinutes
                    })
                }
                finally {
                    Release-ComObject -Value $toTask
                    Release-ComObject -Value $fromTask
                    Release-ComObject -Value $dependency
                }
            }
        }
        finally {
            Release-ComObject -Value $taskDependencies
            Release-ComObject -Value $task
        }
    }

    Set-ExtractStage -Name 'detect_unsupported_project_semantics'
    $resources = $null
    try {
        $resources = $project.GetType().InvokeMember('Resources', [System.Reflection.BindingFlags]::GetProperty, $null, $project, $null)
        $resourceCount = [int](Get-ComProperty -ComObject $resources -Name 'Count')
    }
    finally {
        Release-ComObject -Value $resources
    }
    if ($resourceCount -gt 0 -or $assignmentCount -gt 0) {
        $unsupported.Add([ordered]@{
            code = 'RESOURCE_ASSIGNMENT_SEMANTICS_UNSUPPORTED'
            severity = 'BLOCKER'
            object_type = 'PROJECT'
            object_refs = @('project')
            detail = "The source contains $resourceCount resources and $assignmentCount assignments."
        })
    }

    $statusDate = Convert-ToOptionalOffsetTimestamp -Value (Get-OptionalComProperty -ComObject $project -Name 'StatusDate') -TimeZoneInfo $timeZoneInfo
    if ($null -ne $statusDate) {
        $unsupported.Add([ordered]@{
            code = 'STATUS_DATE_SEMANTICS_UNSUPPORTED'
            severity = 'BLOCKER'
            object_type = 'PROJECT'
            object_refs = @('project')
            detail = 'The source status date requires the later progress contract.'
        })
    }

    $capturedAt = [datetimeoffset]::UtcNow.ToString('o')
    $projectSummaryTask = $null
    try {
        $projectSummaryTask = $project.GetType().InvokeMember('ProjectSummaryTask', [System.Reflection.BindingFlags]::GetProperty, $null, $project, $null)
        $projectName = [string](Get-ComProperty -ComObject $projectSummaryTask -Name 'Name')
        $projectStart = Convert-ToOffsetTimestamp -Value (Get-ComProperty -ComObject $projectSummaryTask -Name 'Start') -TimeZoneInfo $timeZoneInfo
        $projectFinish = Convert-ToOffsetTimestamp -Value (Get-ComProperty -ComObject $projectSummaryTask -Name 'Finish') -TimeZoneInfo $timeZoneInfo
    }
    finally {
        Release-ComObject -Value $projectSummaryTask
    }
    if ([string]::IsNullOrWhiteSpace($projectName)) {
        $projectName = [System.IO.Path]::GetFileNameWithoutExtension($inputPath)
    }
    $projectPayload = [ordered]@{
        name = $projectName
        planned_start = $projectStart
        planned_finish = $projectFinish
        default_calendar_id = $defaultCalendarId
        default_calendar_name = $defaultCalendarName
        status_date = $statusDate
    }
    $roundtripEligible = @($unsupported | Where-Object { $_.severity -eq 'BLOCKER' }).Count -eq 0
    $microsoftProjectVersion = [string](Get-ComProperty -ComObject $app -Name 'Version')

    Set-ExtractStage -Name 'save_and_close_recalculated_copy'
    $app.FileSave() | Out-Null
    Release-ComObject -Value $tasksCollection
    $tasksCollection = $null
    $app.FileCloseEx(1) | Out-Null
    $projectOpened = $false
    Release-ComObject -Value $project
    $project = $null
    $workingCopyHash = Get-Sha256 -LiteralPath $workingCopyPath

    $semanticPayload = [ordered]@{
        schema_version = 'mpp_bridge_snapshot_v1'
        timezone = $timeZoneInfo.Id
        input_mpp_sha256 = $sourceHashBefore
        project = $projectPayload
        calendars = @($calendars)
        tasks = @($tasks)
        dependencies = @($dependencies)
        unsupported_semantics = @($unsupported)
        roundtrip_eligible = $roundtripEligible
    }
    $semanticJson = $semanticPayload | ConvertTo-Json -Depth 30 -Compress
    $capturedSnapshot = [ordered]@{
        schema_version = 'mpp_bridge_snapshot_v1'
        run_id = $runId
        tool_version = $toolVersion
        captured_at = $capturedAt
        timezone = $timeZoneInfo.Id
        source = [ordered]@{
            input_file_name = [System.IO.Path]::GetFileName($inputPath)
            input_mpp_sha256 = $sourceHashBefore
            working_copy_mpp_sha256 = $workingCopyHash
            microsoft_project_version = $microsoftProjectVersion
            opened_after_save = $true
            recalculated_after_reopen = $true
        }
        project = $projectPayload
        calendars = @($calendars)
        tasks = @($tasks)
        dependencies = @($dependencies)
        unsupported_semantics = @($unsupported)
        roundtrip_eligible = $roundtripEligible
        snapshot_semantic_sha256 = Get-StringSha256 -Value $semanticJson
    }
}
catch {
    $failure = $_
}
finally {
    if ($null -ne $tasksCollection) {
        Release-ComObject -Value $tasksCollection
        $tasksCollection = $null
    }
    if ($projectOpened -and $null -ne $app) {
        try {
            $app.FileCloseEx(0) | Out-Null
        }
        catch {
        }
        $projectOpened = $false
    }
    if ($null -ne $project) {
        Release-ComObject -Value $project
        $project = $null
    }
    if ($null -ne $app) {
        try {
            $app.Quit() | Out-Null
        }
        catch {
        }
        Release-ComObject -Value $app
        $app = $null
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

$sourceHashAfter = Get-Sha256 -LiteralPath $inputPath
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    $residualProcessIds = @(Get-NewWinProjProcessIds -BeforeIds $processIdsBefore)
    if ($residualProcessIds.Count -eq 0) {
        break
    }
    Start-Sleep -Milliseconds 250
}
$processIdsAfter = @(Get-Process -Name WINPROJ -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
$residualProcessIds = @(Get-NewWinProjProcessIds -BeforeIds $processIdsBefore)

if ($sourceHashAfter -ne $sourceHashBefore -and $null -eq $failure) {
    Set-ExtractStage -Name 'verify_source_immutable'
    $failure = [System.Management.Automation.ErrorRecord]::new(
        [InvalidOperationException]::new('MPP_BRIDGE_SOURCE_MODIFIED'),
        'MPP_BRIDGE_SOURCE_MODIFIED',
        [System.Management.Automation.ErrorCategory]::InvalidData,
        $inputPath
    )
}
if ($residualProcessIds.Count -gt 0 -and $null -eq $failure) {
    Set-ExtractStage -Name 'verify_process_cleanup'
    $failure = [System.Management.Automation.ErrorRecord]::new(
        [InvalidOperationException]::new('MPP_BRIDGE_COM_PROCESS_RESIDUAL'),
        'MPP_BRIDGE_COM_PROCESS_RESIDUAL',
        [System.Management.Automation.ErrorCategory]::ResourceBusy,
        $residualProcessIds
    )
}

$processEvidence = [ordered]@{
    winproj_process_ids_before = @($processIdsBefore)
    winproj_process_ids_after_create = @($processIdsAfterCreate)
    winproj_process_ids_after = @($processIdsAfter)
    residual_new_process_ids = @($residualProcessIds)
}

if ($null -ne $failure) {
    $errorCode = if ($failure.Exception.Message -eq 'MPP_BRIDGE_SOURCE_MODIFIED') {
        'MPP_BRIDGE_SOURCE_MODIFIED'
    }
    elseif ($failure.Exception.Message -eq 'MPP_BRIDGE_COM_PROCESS_RESIDUAL') {
        'MPP_BRIDGE_COM_PROCESS_RESIDUAL'
    }
    elseif ($failure.Exception.Message -eq 'MPP_BRIDGE_PREEXISTING_PROJECT_PROCESS') {
        'MPP_BRIDGE_PREEXISTING_PROJECT_PROCESS'
    }
    elseif ($failure.Exception.Message -eq 'MPP_BRIDGE_COM_PROCESS_NOT_OBSERVED') {
        'MPP_BRIDGE_COM_PROCESS_NOT_OBSERVED'
    }
    elseif ($failure.Exception.Message -eq 'MPP_BRIDGE_PROJECT_PATH_TOO_LONG') {
        'MPP_BRIDGE_PROJECT_PATH_TOO_LONG'
    }
    else {
        'MPP_BRIDGE_EXTRACT_FAILED'
    }
    $errorPayload = [ordered]@{
        schema_version = 'mpp_bridge_error_v1'
        run_id = $runId
        tool_version = $toolVersion
        command = 'extract'
        status = 'failed'
        occurred_at = [datetimeoffset]::UtcNow.ToString('o')
        error = [ordered]@{
            code = $errorCode
            stage = $stage
            message = [string]$failure.Exception.Message
        }
        source = [ordered]@{
            input_file_name = [System.IO.Path]::GetFileName($inputPath)
            input_mpp_sha256_before = $sourceHashBefore
            input_mpp_sha256_after = $sourceHashAfter
        }
        process_evidence = $processEvidence
    }
    Write-JsonFile -LiteralPath $errorPath -Value $errorPayload
    throw "$errorCode at $stage. Evidence: $errorPath"
}

Write-JsonFile -LiteralPath $snapshotPath -Value $capturedSnapshot
$snapshotArtifactHash = Get-Sha256 -LiteralPath $snapshotPath
$manifestPayload = [ordered]@{
    schema_version = 'mpp_bridge_manifest_v1'
    run_id = $runId
    tool_version = $toolVersion
    command = 'extract'
    status = 'passed'
    created_at = [datetimeoffset]::UtcNow.ToString('o')
    source = [ordered]@{
        input_file_name = [System.IO.Path]::GetFileName($inputPath)
        input_mpp_sha256_before = $sourceHashBefore
        input_mpp_sha256_after = $sourceHashAfter
        source_unchanged = ($sourceHashBefore -eq $sourceHashAfter)
    }
    artifacts = [ordered]@{
        snapshot_file = [System.IO.Path]::GetFileName($snapshotPath)
        snapshot_artifact_sha256 = $snapshotArtifactHash
        snapshot_semantic_sha256 = $capturedSnapshot.snapshot_semantic_sha256
        original_byte_backup_file = [System.IO.Path]::GetFileName($originalByteBackupPath)
        original_byte_backup_sha256 = Get-Sha256 -LiteralPath $originalByteBackupPath
        working_copy_file = [System.IO.Path]::GetFileName($workingCopyPath)
        working_copy_mpp_sha256 = $capturedSnapshot.source.working_copy_mpp_sha256
    }
    result = [ordered]@{
        roundtrip_eligible = $capturedSnapshot.roundtrip_eligible
        unsupported_count = @($capturedSnapshot.unsupported_semantics).Count
        blocker_count = @($capturedSnapshot.unsupported_semantics | Where-Object { $_.severity -eq 'BLOCKER' }).Count
    }
    process_evidence = $processEvidence
}
Write-JsonFile -LiteralPath $manifestPath -Value $manifestPayload
Set-ExtractStage -Name 'complete'

[ordered]@{
    status = 'passed'
    run_directory = $runDirectory
    snapshot = $snapshotPath
    manifest = $manifestPath
    source_unchanged = $true
    roundtrip_eligible = $capturedSnapshot.roundtrip_eligible
    unsupported_count = @($capturedSnapshot.unsupported_semantics).Count
    residual_new_process_ids = @()
} | ConvertTo-Json -Depth 10
