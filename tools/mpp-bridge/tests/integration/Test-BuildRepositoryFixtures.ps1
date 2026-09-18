[CmdletBinding()]
param(
    [string]$OutputDirectory,

    [ValidateRange(30, 1800)]
    [int]$TimeoutSeconds = 600,

    [ValidateSet('all', 'msp-006', 'water-pump', 'c08')]
    [string]$FixtureId = 'all'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function ConvertTo-ComparableJson {
    param($Value)

    $Value | ConvertTo-Json -Depth 30 -Compress
}

function Assert-JsonEqual {
    param($Expected, $Actual, [string]$Label)

    if ((ConvertTo-ComparableJson $Expected) -ne (ConvertTo-ComparableJson $Actual)) {
        throw "MPP_BRIDGE_BUILD_CORE_SEMANTICS_MISMATCH: $Label"
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
        exceptions = @($Calendar.exceptions | ForEach-Object {
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
    param($Task, [string]$ParentTaskId)

    [ordered]@{
        bridge_task_id = $Task.bridge_task_id
        parent_task_id = $ParentTaskId
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

function Assert-CoreSemantics {
    param($SourceSnapshot, $OutputSnapshot, $IdentityMap)

    Assert-JsonEqual -Label 'project' -Expected ([ordered]@{
        name = $SourceSnapshot.project.name
        planned_start = $SourceSnapshot.project.planned_start
        planned_finish = $SourceSnapshot.project.planned_finish
        default_calendar_name = $SourceSnapshot.project.default_calendar_name
        status_date = $SourceSnapshot.project.status_date
    }) -Actual ([ordered]@{
        name = $OutputSnapshot.project.name
        planned_start = $OutputSnapshot.project.planned_start
        planned_finish = $OutputSnapshot.project.planned_finish
        default_calendar_name = $OutputSnapshot.project.default_calendar_name
        status_date = $OutputSnapshot.project.status_date
    })

    $sourceCalendars = @($SourceSnapshot.calendars | ForEach-Object { Get-CalendarProjection $_ })
    $outputCalendars = @($OutputSnapshot.calendars | ForEach-Object { Get-CalendarProjection $_ })
    Assert-JsonEqual -Label 'calendars' -Expected $sourceCalendars -Actual $outputCalendars

    $outputToSourceTaskId = @{}
    foreach ($mapping in @($IdentityMap.tasks)) {
        $outputToSourceTaskId["source-uid:$($mapping.output.unique_id)"] = [string]$mapping.bridge_task_id
    }
    $outputTasksBySourceId = @{}
    foreach ($task in @($OutputSnapshot.tasks)) {
        if (-not $outputToSourceTaskId.ContainsKey([string]$task.bridge_task_id)) {
            throw "MPP_BRIDGE_BUILD_IDENTITY_MAP_MISSING: $($task.bridge_task_id)"
        }
        $sourceTaskId = $outputToSourceTaskId[[string]$task.bridge_task_id]
        $parentTaskId = if ($null -eq $task.parent_task_id) {
            $null
        }
        elseif ($outputToSourceTaskId.ContainsKey([string]$task.parent_task_id)) {
            $outputToSourceTaskId[[string]$task.parent_task_id]
        }
        else {
            throw "MPP_BRIDGE_BUILD_PARENT_IDENTITY_MISSING: $($task.parent_task_id)"
        }
        $task.bridge_task_id = $sourceTaskId
        $outputTasksBySourceId[$sourceTaskId] = Get-TaskProjection -Task $task -ParentTaskId $parentTaskId
    }
    $sourceTasks = @($SourceSnapshot.tasks | ForEach-Object {
        Get-TaskProjection -Task $_ -ParentTaskId $_.parent_task_id
    })
    $outputTasks = @($SourceSnapshot.tasks | ForEach-Object {
        if (-not $outputTasksBySourceId.ContainsKey([string]$_.bridge_task_id)) {
            throw "MPP_BRIDGE_BUILD_OUTPUT_TASK_MISSING: $($_.bridge_task_id)"
        }
        $outputTasksBySourceId[[string]$_.bridge_task_id]
    })
    Assert-JsonEqual -Label 'tasks' -Expected $sourceTasks -Actual $outputTasks

    $sourceDependencies = @($SourceSnapshot.dependencies | ForEach-Object {
        [ordered]@{
            predecessor_task_id = $_.predecessor_task_id
            successor_task_id = $_.successor_task_id
            type = $_.type
            source_type_code = $_.source_type_code
            lag_minutes = $_.lag_minutes
        }
    } | Sort-Object { "$($_.predecessor_task_id)|$($_.successor_task_id)|$($_.type)|$($_.lag_minutes)" })
    $outputDependencies = @($OutputSnapshot.dependencies | ForEach-Object {
        [ordered]@{
            predecessor_task_id = $outputToSourceTaskId[[string]$_.predecessor_task_id]
            successor_task_id = $outputToSourceTaskId[[string]$_.successor_task_id]
            type = $_.type
            source_type_code = $_.source_type_code
            lag_minutes = $_.lag_minutes
        }
    } | Sort-Object { "$($_.predecessor_task_id)|$($_.successor_task_id)|$($_.type)|$($_.lag_minutes)" })
    Assert-JsonEqual -Label 'dependencies' -Expected $sourceDependencies -Actual $outputDependencies
}

$bridgeRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$workspaceRoot = Split-Path -Parent (Split-Path -Parent $bridgeRoot)
$extractScript = Join-Path $bridgeRoot 'src/Invoke-MppBridgeExtract.ps1'
$buildScript = Join-Path $bridgeRoot 'src/Invoke-MppBridgeBuild.ps1'
$assertExtractScript = Join-Path $PSScriptRoot 'Assert-ExtractRun.ps1'
$assertBuildScript = Join-Path $PSScriptRoot 'Assert-BuildRun.ps1'
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $bridgeRoot '.m2-runs'
}
$outputRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
if (-not (Test-Path -LiteralPath $outputRoot)) {
    [void](New-Item -ItemType Directory -Path $outputRoot)
}
$matrixRoot = Join-Path $outputRoot ([string](New-Guid))
[void](New-Item -ItemType Directory -Path $matrixRoot)

$fixtures = @(
    [ordered]@{ id = 'msp-006'; directory = 'msp'; path = 'MSP-006-SUMMARY-ROLLUP-v2_Yuxi-v6/source/case_006_nested_summary_rollup_v2.mpp' },
    [ordered]@{ id = 'water-pump'; directory = 'wp'; path = 'Microsoft_Project_水泵站排期_MOCK_v1.1/source/water_pump_station_schedule_mock_v1_1.mpp' },
    [ordered]@{ id = 'c08'; directory = 'c08'; path = 'Yuxi_复杂排期测试套件_v1/C08_MICROSOFT_PROJECT_OBSERVED/source.mpp' }
)
if ($FixtureId -ne 'all') {
    $fixtures = @($fixtures | Where-Object { $_.id -eq $FixtureId })
}

$results = [System.Collections.Generic.List[object]]::new()
foreach ($fixture in $fixtures) {
    $sourcePath = Join-Path $workspaceRoot $fixture.path
    $sourceHashBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourcePath).Hash.ToLowerInvariant()
    $caseRoot = Join-Path $matrixRoot $fixture.directory
    [void](New-Item -ItemType Directory -Path $caseRoot)
    try {
        $extractResult = & $extractScript -InputMpp $sourcePath -OutputDirectory (Join-Path $caseRoot 'src') -Timezone 'Asia/Shanghai' -TimeoutSeconds $TimeoutSeconds | ConvertFrom-Json
        [void](& $assertExtractScript -RunDirectory $extractResult.run_directory | ConvertFrom-Json)
        $sourceSnapshot = Get-Content -Raw -LiteralPath $extractResult.snapshot | ConvertFrom-Json -Depth 100

        $outputMpp = Join-Path $caseRoot 'built.mpp'
        $buildResult = & $buildScript -SnapshotPath $extractResult.snapshot -OutputMpp $outputMpp -OutputDirectory (Join-Path $caseRoot 'bld') -TimeoutSeconds $TimeoutSeconds | ConvertFrom-Json
        [void](& $assertBuildScript -RunDirectory $buildResult.run_directory -OutputMpp $outputMpp | ConvertFrom-Json)

        $reextractResult = & $extractScript -InputMpp $outputMpp -OutputDirectory (Join-Path $caseRoot 'out') -Timezone 'Asia/Shanghai' -TimeoutSeconds $TimeoutSeconds | ConvertFrom-Json
        $reextractAssertion = & $assertExtractScript -RunDirectory $reextractResult.run_directory | ConvertFrom-Json
        $outputSnapshot = Get-Content -Raw -LiteralPath $reextractResult.snapshot | ConvertFrom-Json -Depth 100
        $identityMap = Get-Content -Raw -LiteralPath $buildResult.identity_map | ConvertFrom-Json -Depth 100
        Assert-CoreSemantics -SourceSnapshot $sourceSnapshot -OutputSnapshot $outputSnapshot -IdentityMap $identityMap

        $results.Add([ordered]@{
            fixture = $fixture.path
            status = 'PASS'
            source_mpp_sha256 = $sourceHashBefore
            output_mpp_sha256 = $buildResult.output_mpp_sha256
            source_snapshot_semantic_sha256 = $sourceSnapshot.snapshot_semantic_sha256
            output_snapshot_semantic_sha256 = $outputSnapshot.snapshot_semantic_sha256
            calendars = @($sourceSnapshot.calendars).Count
            tasks = @($sourceSnapshot.tasks).Count
            dependencies = @($sourceSnapshot.dependencies).Count
            output_unsupported = $reextractAssertion.unsupported
            output_roundtrip_eligible = $reextractAssertion.roundtrip_eligible
            core_semantics_match = $true
        })
    }
    catch {
        $results.Add([ordered]@{
            fixture = $fixture.path
            status = 'ERROR'
            source_mpp_sha256 = $sourceHashBefore
            error = [string]$_.Exception.Message
        })
    }
    finally {
        $sourceHashAfter = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourcePath).Hash.ToLowerInvariant()
        if ($sourceHashAfter -ne $sourceHashBefore) {
            throw "Repository fixture changed: $($fixture.path)"
        }
        if (@(Get-Process -Name WINPROJ -ErrorAction SilentlyContinue).Count -gt 0) {
            throw "Repository fixture left WINPROJ running: $($fixture.path)"
        }
    }
}

$status = if (@($results | Where-Object { $_.status -ne 'PASS' }).Count -eq 0) { 'PASS' } else { 'ERROR' }
$matrix = [ordered]@{ status = $status; run_directory = $matrixRoot; fixtures = @($results) }
$matrix | ConvertTo-Json -Depth 10
if ($status -ne 'PASS') {
    throw 'MPP Bridge Build repository fixture matrix completed with errors.'
}
