[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SnapshotPath,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$bridgeRoot = Split-Path -Parent $PSScriptRoot
$snapshotFile = (Resolve-Path -LiteralPath $SnapshotPath).Path
$outputRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
$snapshotSchema = Join-Path $bridgeRoot 'schemas/mpp_bridge_snapshot_v1.schema.json'
$interchangeSchema = Join-Path $bridgeRoot 'schemas/microsoft_project_interchange_v1_1.schema.json'
$reportSchema = Join-Path $bridgeRoot 'schemas/mpp_bridge_yuxi_projection_report_v1.schema.json'
$interchangePath = Join-Path $outputRoot 'microsoft-project-interchange-v1.1.json'
$reportPath = Join-Path $outputRoot 'projection-report.json'
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

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

function Get-WorkingMinutes {
    param($Intervals)

    $minutes = 0
    foreach ($interval in @($Intervals)) {
        $start = [timespan]::Parse([string]$interval.start)
        $finish = [timespan]::Parse([string]$interval.finish)
        $minutes += [int]($finish - $start).TotalMinutes
    }
    $minutes
}

$snapshotJson = Get-Content -Raw -LiteralPath $snapshotFile
if (-not ($snapshotJson | Test-Json -SchemaFile $snapshotSchema)) {
    throw 'MPP_BRIDGE_PROJECT_YUXI_SNAPSHOT_SCHEMA_INVALID'
}
$snapshot = $snapshotJson | ConvertFrom-Json -AsHashtable -DateKind String
if ((Get-SnapshotSemanticHash $snapshot) -ne $snapshot.snapshot_semantic_sha256) {
    throw 'MPP_BRIDGE_PROJECT_YUXI_SNAPSHOT_SEMANTIC_HASH_INVALID'
}
if (-not [bool]$snapshot.roundtrip_eligible -or @($snapshot.unsupported_semantics).Count -gt 0) {
    throw 'MPP_BRIDGE_PROJECT_YUXI_SNAPSHOT_NOT_ELIGIBLE'
}
if ($null -ne $snapshot.project.status_date) {
    throw 'MPP_BRIDGE_PROJECT_YUXI_STATUS_DATE_UNSUPPORTED'
}
if ((Test-Path -LiteralPath $interchangePath) -or
    (Test-Path -LiteralPath $reportPath)) {
    throw 'MPP_BRIDGE_PROJECT_YUXI_OUTPUT_EXISTS'
}
if (-not (Test-Path -LiteralPath $outputRoot)) {
    [void](New-Item -ItemType Directory -Path $outputRoot)
}

$defaultCalendar = @($snapshot.calendars | Where-Object {
    $_.calendar_id -eq $snapshot.project.default_calendar_id
})
if ($defaultCalendar.Count -ne 1) {
    throw 'MPP_BRIDGE_PROJECT_YUXI_DEFAULT_CALENDAR_INVALID'
}
$dayMinutes = @($defaultCalendar[0].week_days | ForEach-Object {
    Get-WorkingMinutes $_.intervals
})
$positiveDayMinutes = @($dayMinutes | Where-Object { $_ -gt 0 })
if ($positiveDayMinutes.Count -eq 0) {
    throw 'MPP_BRIDGE_PROJECT_YUXI_DEFAULT_CALENDAR_HAS_NO_WORKING_TIME'
}
$defaultDailyMinutes = [int]($positiveDayMinutes | Measure-Object -Maximum).Maximum
$defaultWeeklyMinutes = [int]($dayMinutes | Measure-Object -Sum).Sum
$projectId = "project:mpp:$($snapshot.source.input_mpp_sha256.Substring(0, 24))"

$calendars = @($snapshot.calendars | ForEach-Object {
    [ordered]@{
        calendar_id = $_.calendar_id
        name = $_.name
        week_days = @($_.week_days | ForEach-Object {
            [ordered]@{
                day = $_.day
                working = $_.working
                intervals = @($_.intervals | ForEach-Object {
                    [ordered]@{ start = $_.start; finish = $_.finish }
                })
            }
        })
        exceptions = @($_.exceptions | ForEach-Object {
            [ordered]@{
                exception_id = $_.exception_id
                name = $_.name
                start_date = $_.start_date
                finish_date = $_.finish_date
                working = $_.working
                intervals = @($_.intervals | ForEach-Object {
                    [ordered]@{ start = $_.start; finish = $_.finish }
                })
            }
        })
    }
})
$tasks = @($snapshot.tasks | ForEach-Object {
    [ordered]@{
        task_id = $_.bridge_task_id
        source_id = $_.source_id
        source_unique_id = $_.source_unique_id
        parent_task_id = $_.parent_task_id
        wbs = $_.wbs
        outline_level = $_.outline_level
        name = $_.name
        task_type = $_.task_type
        active = $_.active
        scheduling_mode = $_.scheduling_mode
        duration_minutes = $_.duration_minutes
        project_rollup_duration_minutes = $_.project_rollup_duration_minutes
        start = $_.start
        finish = $_.finish
        percent_complete = $_.percent_complete
        constraint_type_code = $_.constraint_type_code
        constraint_date = $_.constraint_date
        deadline = $_.deadline
        calendar_id = $_.calendar_id
    }
})
$dependencies = @($snapshot.dependencies | ForEach-Object {
    [ordered]@{
        dependency_id = $_.dependency_id
        predecessor_task_id = $_.predecessor_task_id
        successor_task_id = $_.successor_task_id
        type = $_.type
        source_type_code = $_.source_type_code
        lag_minutes = $_.lag_minutes
        lag_calendar_policy = 'SUCCESSOR_TASK_CALENDAR'
    }
})

$interchange = [ordered]@{
    schema_version = 'microsoft_project_interchange_v1.1'
    artifact_version = 'mpp_bridge_projector_v1'
    data_classification = 'MPP_BRIDGE_VERIFIED_SOURCE'
    source = [ordered]@{
        format = 'MPP'
        file_name = $snapshot.source.input_file_name
        microsoft_project_version = $snapshot.source.microsoft_project_version
        extraction_method = 'Microsoft Project COM working-copy save/reopen/recalculate'
        extracted_at = $snapshot.captured_at
        timezone = $snapshot.timezone
        mpp_sha256 = $snapshot.source.input_mpp_sha256
        opened_after_save = $snapshot.source.opened_after_save
        project_recalculated_after_reopen = $snapshot.source.recalculated_after_reopen
    }
    semantics = [ordered]@{
        timezone = $snapshot.timezone
        duration_unit = 'working_minute'
        lag_unit = 'working_minute'
        lag_calendar_policy = 'SUCCESSOR_TASK_CALENDAR'
        task_calendar_resolution = 'task.calendar_id ?? project.default_calendar_id'
    }
    project = [ordered]@{
        project_id = $projectId
        name = $snapshot.project.name
        planned_start = $snapshot.project.planned_start
        planned_finish = $snapshot.project.planned_finish
        default_calendar_id = $snapshot.project.default_calendar_id
        default_daily_work_minutes = $defaultDailyMinutes
        default_weekly_work_minutes = $defaultWeeklyMinutes
        required_finish = $null
    }
    calendars = $calendars
    tasks = $tasks
    dependencies = $dependencies
    resources = @()
    assignments = @()
}
$interchangeJson = ($interchange | ConvertTo-Json -Depth 30) + "`n"
if (-not ($interchangeJson | Test-Json -SchemaFile $interchangeSchema)) {
    throw 'MPP_BRIDGE_PROJECT_YUXI_INTERCHANGE_SCHEMA_INVALID'
}
$interchangeHash = Get-StringSha256 $interchangeJson

$report = [ordered]@{
    schema_version = 'mpp_bridge_yuxi_projection_report_v1'
    status = 'PASS'
    source_snapshot_semantic_sha256 = $snapshot.snapshot_semantic_sha256
    interchange_artifact_sha256 = $interchangeHash
    preserved_fields = @(
        [ordered]@{ source_path = '/source/input_mpp_sha256'; target_path = '/source/mpp_sha256' },
        [ordered]@{ source_path = '/captured_at'; target_path = '/source/extracted_at' },
        [ordered]@{ source_path = '/timezone'; target_path = '/source/timezone' },
        [ordered]@{ source_path = '/source/input_file_name'; target_path = '/source/file_name' },
        [ordered]@{ source_path = '/source/microsoft_project_version'; target_path = '/source/microsoft_project_version' },
        [ordered]@{ source_path = '/source/opened_after_save'; target_path = '/source/opened_after_save' },
        [ordered]@{ source_path = '/source/recalculated_after_reopen'; target_path = '/source/project_recalculated_after_reopen' },
        [ordered]@{ source_path = '/project/name'; target_path = '/project/name' },
        [ordered]@{ source_path = '/project/planned_start'; target_path = '/project/planned_start' },
        [ordered]@{ source_path = '/project/planned_finish'; target_path = '/project/planned_finish' },
        [ordered]@{ source_path = '/project/default_calendar_id'; target_path = '/project/default_calendar_id' },
        [ordered]@{ source_path = '/calendars/*/{calendar_id,name,week_days,exceptions/{exception_id,name,start_date,finish_date,working,intervals}}'; target_path = '/calendars/*' },
        [ordered]@{ source_path = '/tasks/*/{bridge_task_id,source_id,source_unique_id,parent_task_id,wbs,outline_level,name,task_type,active,scheduling_mode,duration_minutes,project_rollup_duration_minutes,start,finish,percent_complete,constraint_type_code,constraint_date,deadline,calendar_id}'; target_path = '/tasks/*' },
        [ordered]@{ source_path = '/dependencies/*/{dependency_id,predecessor_task_id,successor_task_id,type,source_type_code,lag_minutes}'; target_path = '/dependencies/*' }
    )
    derived_fields = @(
        [ordered]@{ target_path = '/schema_version'; reason_code = 'YUXI_PROTOCOL_VERSION' },
        [ordered]@{ target_path = '/artifact_version'; reason_code = 'PROJECTOR_VERSION' },
        [ordered]@{ target_path = '/data_classification'; reason_code = 'BRIDGE_VERIFIED_SOURCE_CLASSIFICATION' },
        [ordered]@{ target_path = '/source/extraction_method'; reason_code = 'FROZEN_BRIDGE_EXTRACTION_METHOD' },
        [ordered]@{ target_path = '/semantics'; reason_code = 'FROZEN_YUXI_IMPORT_SEMANTICS' },
        [ordered]@{ target_path = '/project/project_id'; reason_code = 'DERIVED_FROM_INPUT_MPP_SHA256' },
        [ordered]@{ target_path = '/project/default_daily_work_minutes'; reason_code = 'DERIVED_FROM_DEFAULT_CALENDAR_MAX_WORKING_DAY' },
        [ordered]@{ target_path = '/project/default_weekly_work_minutes'; reason_code = 'DERIVED_FROM_DEFAULT_CALENDAR_WEEK' },
        [ordered]@{ target_path = '/project/required_finish'; reason_code = 'SOURCE_FIELD_UNAVAILABLE' },
        [ordered]@{ target_path = '/dependencies/*/lag_calendar_policy'; reason_code = 'FROZEN_SUCCESSOR_TASK_CALENDAR_POLICY' },
        [ordered]@{ target_path = '/resources'; reason_code = 'BRIDGE_V1_REQUIRES_NO_RESOURCES' },
        [ordered]@{ target_path = '/assignments'; reason_code = 'BRIDGE_V1_REQUIRES_NO_ASSIGNMENTS' }
    )
    omitted_fields = @(
        [ordered]@{ source_path = '/run_id'; reason_code = 'BRIDGE_EXECUTION_METADATA' },
        [ordered]@{ source_path = '/tool_version'; reason_code = 'BRIDGE_EXECUTION_METADATA' },
        [ordered]@{ source_path = '/source/working_copy_mpp_sha256'; reason_code = 'BRIDGE_WORKING_COPY_EVIDENCE' },
        [ordered]@{ source_path = '/project/default_calendar_name'; reason_code = 'REDUNDANT_CALENDAR_REFERENCE' },
        [ordered]@{ source_path = '/project/status_date'; reason_code = 'NULL_SOURCE_VALUE_ONLY' },
        [ordered]@{ source_path = '/calendars/*/source_index'; reason_code = 'BRIDGE_IDENTITY_METADATA' },
        [ordered]@{ source_path = '/calendars/*/base_calendar_name'; reason_code = 'BRIDGE_V1_REQUIRES_NO_CALENDAR_INHERITANCE' },
        [ordered]@{ source_path = '/calendars/*/exceptions/*/source_type_code'; reason_code = 'BRIDGE_EXCEPTION_METADATA' },
        [ordered]@{ source_path = '/calendars/*/exceptions/*/occurrences'; reason_code = 'BRIDGE_V1_REQUIRES_ONE_TIME_EXCEPTIONS' },
        [ordered]@{ source_path = '/tasks/*/{summary,milestone}'; reason_code = 'REDUNDANT_WITH_TASK_TYPE' },
        [ordered]@{ source_path = '/tasks/*/constraint_type'; reason_code = 'REDUNDANT_WITH_CONSTRAINT_TYPE_CODE' },
        [ordered]@{ source_path = '/tasks/*/source_calendar_name'; reason_code = 'REDUNDANT_WITH_CALENDAR_ID' },
        [ordered]@{ source_path = '/tasks/*/predecessors_text'; reason_code = 'REDUNDANT_WITH_STRUCTURED_DEPENDENCIES' },
        [ordered]@{ source_path = '/snapshot_semantic_sha256'; reason_code = 'RECORDED_IN_PROJECTION_REPORT' },
        [ordered]@{ source_path = '/unsupported_semantics'; reason_code = 'REQUIRED_EMPTY_BY_PROJECTOR_GATE' },
        [ordered]@{ source_path = '/roundtrip_eligible'; reason_code = 'REQUIRED_TRUE_BY_PROJECTOR_GATE' }
    )
    unsupported_semantics = @()
}
$reportJson = ($report | ConvertTo-Json -Depth 20) + "`n"
if (-not ($reportJson | Test-Json -SchemaFile $reportSchema)) {
    throw 'MPP_BRIDGE_PROJECT_YUXI_REPORT_SCHEMA_INVALID'
}

[System.IO.File]::WriteAllText($interchangePath, $interchangeJson, $utf8NoBom)
[System.IO.File]::WriteAllText($reportPath, $reportJson, $utf8NoBom)
[ordered]@{
    status = 'PASS'
    interchange = $interchangePath
    projection_report = $reportPath
    interchange_artifact_sha256 = $interchangeHash
    source_snapshot_semantic_sha256 = $snapshot.snapshot_semantic_sha256
} | ConvertTo-Json -Depth 5
