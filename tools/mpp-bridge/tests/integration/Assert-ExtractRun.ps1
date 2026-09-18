[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RunDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$bridgeRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$runPath = (Resolve-Path -LiteralPath $RunDirectory).Path
$snapshotPath = Join-Path $runPath 'snapshot.json'
$manifestPath = Join-Path $runPath 'manifest.json'
$snapshotSchema = Join-Path $bridgeRoot 'schemas/mpp_bridge_snapshot_v1.schema.json'
$manifestSchema = Join-Path $bridgeRoot 'schemas/mpp_bridge_manifest_v1.schema.json'

if (-not (Get-Content -Raw -LiteralPath $snapshotPath | Test-Json -SchemaFile $snapshotSchema)) {
    throw 'Extract snapshot failed JSON Schema validation.'
}
if (-not (Get-Content -Raw -LiteralPath $manifestPath | Test-Json -SchemaFile $manifestSchema)) {
    throw 'Extract manifest failed JSON Schema validation.'
}

$snapshot = Get-Content -Raw -LiteralPath $snapshotPath | ConvertFrom-Json -AsHashtable -DateKind String
$manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json -AsHashtable -DateKind String
if ($snapshot.run_id -ne $manifest.run_id) {
    throw 'Snapshot and manifest run_id differ.'
}
if ($manifest.source.input_mpp_sha256_before -ne $manifest.source.input_mpp_sha256_after -or -not $manifest.source.source_unchanged) {
    throw 'Source MPP changed during extract.'
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $snapshotPath).Hash.ToLowerInvariant() -ne $manifest.artifacts.snapshot_artifact_sha256) {
    throw 'Snapshot artifact hash differs from manifest.'
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $runPath $manifest.artifacts.original_byte_backup_file)).Hash.ToLowerInvariant() -ne $manifest.artifacts.original_byte_backup_sha256) {
    throw 'Original byte backup hash differs from manifest.'
}
if ($manifest.artifacts.original_byte_backup_sha256 -ne $manifest.source.input_mpp_sha256_before) {
    throw 'Original byte backup does not preserve input bytes.'
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $runPath $manifest.artifacts.working_copy_file)).Hash.ToLowerInvariant() -ne $manifest.artifacts.working_copy_mpp_sha256) {
    throw 'Working-copy hash differs from manifest.'
}
if (@($manifest.process_evidence.residual_new_process_ids).Count -ne 0) {
    throw 'Extract left a new WINPROJ process.'
}

$calendarIds = @($snapshot.calendars | ForEach-Object { $_.calendar_id })
if ($calendarIds.Count -ne @($calendarIds | Select-Object -Unique).Count) {
    throw 'Duplicate calendar_id in snapshot.'
}
if ($snapshot.project.default_calendar_id -notin $calendarIds) {
    throw 'Project default_calendar_id is unknown.'
}
$taskIds = @($snapshot.tasks | ForEach-Object { $_.bridge_task_id })
if ($taskIds.Count -ne @($taskIds | Select-Object -Unique).Count) {
    throw 'Duplicate bridge_task_id in snapshot.'
}
foreach ($task in $snapshot.tasks) {
    if ($null -ne $task.parent_task_id -and $task.parent_task_id -notin $taskIds) {
        throw "Task $($task.bridge_task_id) references an unknown parent."
    }
    if ($task.calendar_id -notin $calendarIds) {
        throw "Task $($task.bridge_task_id) references an unknown calendar."
    }
}
foreach ($dependency in $snapshot.dependencies) {
    if ($dependency.predecessor_task_id -notin $taskIds -or $dependency.successor_task_id -notin $taskIds) {
        throw "Dependency $($dependency.dependency_id) references an unknown task."
    }
}

$blockerCount = @($snapshot.unsupported_semantics | Where-Object { $_.severity -eq 'BLOCKER' }).Count
if ($snapshot.roundtrip_eligible -ne ($blockerCount -eq 0)) {
    throw 'roundtrip_eligible contradicts blocker count.'
}
if ($manifest.result.roundtrip_eligible -ne $snapshot.roundtrip_eligible) {
    throw 'Manifest and snapshot roundtrip eligibility differ.'
}
if ($manifest.result.unsupported_count -ne @($snapshot.unsupported_semantics).Count -or $manifest.result.blocker_count -ne $blockerCount) {
    throw 'Manifest unsupported counts differ from snapshot.'
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
$semanticJson = $semanticPayload | ConvertTo-Json -Depth 30 -Compress
$semanticBytes = [System.Text.Encoding]::UTF8.GetBytes($semanticJson)
$semanticHash = [Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData($semanticBytes)).ToLowerInvariant()
if ($semanticHash -ne $snapshot.snapshot_semantic_sha256 -or $semanticHash -ne $manifest.artifacts.snapshot_semantic_sha256) {
    throw 'Semantic snapshot hash is not reproducible.'
}

[ordered]@{
    status = 'PASS'
    run_id = $snapshot.run_id
    project = $snapshot.project.name
    calendars = @($snapshot.calendars).Count
    tasks = @($snapshot.tasks).Count
    dependencies = @($snapshot.dependencies).Count
    unsupported = @($snapshot.unsupported_semantics).Count
    roundtrip_eligible = $snapshot.roundtrip_eligible
} | ConvertTo-Json -Depth 5
