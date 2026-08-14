param(
    [string]$MppPath = (Join-Path $PSScriptRoot "source\case_006_nested_summary_rollup_v2.mpp"),
    [string]$OutputPath = (Join-Path $PSScriptRoot "oracle_recapture.json"),
    [int]$OpenTimeoutSeconds = 20
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()

function Convert-ToIsoMinute([object]$Value) {
    if ($null -eq $Value) { return $null }
    return ([datetime]$Value).ToString("yyyy-MM-ddTHH:mm:00") + "+08:00"
}

function Resolve-Project([object]$Application, [int]$TimeoutSeconds) {
    $deadline = [datetime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        try {
            if ($null -ne $Application.ActiveProject) { return $Application.ActiveProject }
        } catch {}
        try {
            $count = [int]$Application.Projects.Count
            if ($count -gt 0) { return $Application.Projects.Item($count) }
        } catch {}
        Start-Sleep -Milliseconds 250
    } while ([datetime]::UtcNow -lt $deadline)
    return $null
}

function Open-ProjectWithFallback([object]$Application, [string]$Path, [int]$TimeoutSeconds) {
    # The return value of FileOpenEx is not reliable across Project COM builds;
    # success is determined from ActiveProject and Projects.Count.
    try { $null = $Application.FileOpenEx($Path, $true) } catch {}
    $project = Resolve-Project $Application $TimeoutSeconds
    if ($null -ne $project) { return $project }

    # Older/locale-specific builds can expose FileOpen more reliably than FileOpenEx.
    try { $null = $Application.FileOpen($Path, $true) } catch {}
    return Resolve-Project $Application $TimeoutSeconds
}

$resolvedMppPath = [IO.Path]::GetFullPath($MppPath)
$resolvedOutputPath = [IO.Path]::GetFullPath($OutputPath)
if (-not (Test-Path -LiteralPath $resolvedMppPath -PathType Leaf)) {
    throw "MPP does not exist: $resolvedMppPath"
}
if ([IO.Path]::GetExtension($resolvedMppPath).ToLowerInvariant() -ne ".mpp") {
    throw "Expected an .mpp file: $resolvedMppPath"
}
$outputDirectory = Split-Path -Parent $resolvedOutputPath
if (-not (Test-Path -LiteralPath $outputDirectory)) {
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
}

$application = $null
try {
    $application = New-Object -ComObject MSProject.Application
    $application.Visible = $false
    try { $application.DisplayAlerts = $false } catch {}
    $project = Open-ProjectWithFallback $application $resolvedMppPath $OpenTimeoutSeconds
    if ($null -eq $project) {
        throw "Microsoft Project opened no ActiveProject after FileOpenEx/FileOpen fallback and ${OpenTimeoutSeconds}s waits. Verify that WINPROJ.EXE can open the MPP interactively and run this script with Windows PowerShell 5.1."
    }

    $application.CalculateProject()
    $tasks = @()
    foreach ($task in $project.Tasks) {
        if ($null -eq $task) { continue }
        $parentTaskId = $null
        try {
            if ($null -ne $task.OutlineParent -and [int]$task.OutlineParent.ID -gt 0) {
                $parentTaskId = "task:$([int]$task.OutlineParent.UniqueID)"
            }
        } catch {}
        $tasks += [ordered]@{
            task_id = "task:$([int]$task.UniqueID)"
            source_id = [int]$task.ID
            source_unique_id = [int]$task.UniqueID
            parent_task_id = $parentTaskId
            wbs = [string]$task.WBS
            outline_level = [int]$task.OutlineLevel
            name = [string]$task.Name
            task_type = if ([bool]$task.Summary) { "SUMMARY" } elseif ([bool]$task.Milestone) { "MILESTONE" } else { "TASK" }
            summary = [bool]$task.Summary
            scheduling_mode = if ([bool]$task.Manual) { "MANUAL" } else { "AUTO" }
            duration_minutes = if ([bool]$task.Summary) { $null } else { [int64]$task.Duration }
            start = Convert-ToIsoMinute $task.Start
            finish = Convert-ToIsoMinute $task.Finish
            predecessors_text = [string]$task.Predecessors
        }
    }
    if ($tasks.Count -ne 5) { throw "Expected 5 tasks, recaptured $($tasks.Count)." }
    if (@($tasks | Where-Object { $_.summary }).Count -ne 2) { throw "Expected exactly 2 summary tasks." }
    if (($tasks | ForEach-Object { [int]$_.outline_level } | Measure-Object -Maximum).Maximum -ne 3) {
        throw "Expected maximum outline level 3."
    }

    $summary = $project.ProjectSummaryTask
    $result = [ordered]@{
        case_id = "MSP-006-SUMMARY-ROLLUP"
        case_version = 2
        recaptured_at = [datetimeoffset]::Now.ToString("o")
        capture_method = "Microsoft Project COM FileOpenEx/FileOpen fallback with ActiveProject/Projects wait"
        source_mpp_path = $resolvedMppPath
        project_start = Convert-ToIsoMinute $summary.Start
        project_finish = Convert-ToIsoMinute $summary.Finish
        tasks = $tasks
    }
    [IO.File]::WriteAllText($resolvedOutputPath, ($result | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
    [ordered]@{
        status = "PASS"
        output_path = $resolvedOutputPath
        project_start = $result.project_start
        project_finish = $result.project_finish
        tasks = $tasks.Count
        summary_tasks = @($tasks | Where-Object { $_.summary }).Count
        max_outline_level = ($tasks | ForEach-Object { [int]$_.outline_level } | Measure-Object -Maximum).Maximum
    } | ConvertTo-Json -Depth 5
}
finally {
    if ($null -ne $application) {
        try { $application.FileClose(0) | Out-Null } catch {}
        try { $application.Quit() | Out-Null } catch {}
        try { [Runtime.InteropServices.Marshal]::FinalReleaseComObject($application) | Out-Null } catch {}
    }
}
