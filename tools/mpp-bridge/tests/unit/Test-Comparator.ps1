[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Copy-Value {
    param($Value)

    $Value | ConvertTo-Json -Depth 30 | ConvertFrom-Json -AsHashtable -DateKind String
}

function Update-SemanticHash {
    param([hashtable]$Snapshot)

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
    $bytes = [System.Text.Encoding]::UTF8.GetBytes(($payload | ConvertTo-Json -Depth 30 -Compress))
    $Snapshot.snapshot_semantic_sha256 = [Convert]::ToHexString(
        [System.Security.Cryptography.SHA256]::HashData($bytes)
    ).ToLowerInvariant()
}

function Write-JsonFixture {
    param([string]$Path, $Value)

    [System.IO.File]::WriteAllText(
        $Path,
        (($Value | ConvertTo-Json -Depth 30) + "`n"),
        [System.Text.UTF8Encoding]::new($false)
    )
}

$contractOutput = . (Join-Path $PSScriptRoot 'Test-Contracts.ps1')
$left = Copy-Value $snapshot
$leftTask2 = Copy-Value $left.tasks[0]
$leftTask2.bridge_task_id = 'source-uid:2'
$leftTask2.source_id = 2
$leftTask2.source_unique_id = 2
$leftTask2.name = 'Task 2'
$leftTask2.wbs = '2'
$left.tasks = @($left.tasks) + @($leftTask2)
$left.dependencies = @(
    [ordered]@{
        dependency_id = 'dependency:1|2|1|0'
        predecessor_task_id = 'source-uid:1'
        successor_task_id = 'source-uid:2'
        type = 'FS'
        source_type_code = 1
        lag_minutes = 0
    }
)
$right = Copy-Value $left
$right.tasks[0].bridge_task_id = 'source-uid:11'
$right.tasks[0].source_id = 11
$right.tasks[0].source_unique_id = 11
$right.tasks[1].bridge_task_id = 'source-uid:12'
$right.tasks[1].source_id = 12
$right.tasks[1].source_unique_id = 12
$right.dependencies[0].dependency_id = 'dependency:11|12|1|0'
$right.dependencies[0].predecessor_task_id = 'source-uid:11'
$right.dependencies[0].successor_task_id = 'source-uid:12'
$map = [ordered]@{
    schema_version = 'mpp_bridge_identity_map_v1'
    run_id = $identityMap.run_id
    tool_version = $identityMap.tool_version
    source_snapshot_semantic_sha256 = $identityMap.source_snapshot_semantic_sha256
    output_mpp_sha256 = $identityMap.output_mpp_sha256
    tasks = @(
        [ordered]@{
            bridge_task_id = 'source-uid:1'
            source = [ordered]@{ id = 1; unique_id = 1 }
            output = [ordered]@{ id = 11; unique_id = 11 }
        },
        [ordered]@{
            bridge_task_id = 'source-uid:2'
            source = [ordered]@{ id = 2; unique_id = 2 }
            output = [ordered]@{ id = 12; unique_id = 12 }
        }
    )
}
Update-SemanticHash $left
Update-SemanticHash $right
$map.source_snapshot_semantic_sha256 = $left.snapshot_semantic_sha256

$testRoot = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) ".m2-runs/unit-comparator/$(New-Guid)"
[void](New-Item -ItemType Directory -Path $testRoot)
$leftPath = Join-Path $testRoot 'left.json'
$rightPath = Join-Path $testRoot 'right.json'
$mapPath = Join-Path $testRoot 'identity-map.json'
Write-JsonFixture $leftPath $left
Write-JsonFixture $rightPath $right
Write-JsonFixture $mapPath $map

$comparator = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'src/Compare-MppBridgeSnapshots.ps1'
$diffSchema = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'schemas/mpp_bridge_diff_v1.schema.json'
$pass = & $comparator -LeftSnapshot $leftPath -RightSnapshot $rightPath -IdentityMap $mapPath -OutputDirectory $testRoot | ConvertFrom-Json
if ($pass.status -ne 'PASS' -or $pass.allowed_reassignment_count -ne 4 -or $pass.blocker_count -ne 0) {
    throw 'Comparator did not accept task identity reassignment.'
}
if (-not (Get-Content -Raw -LiteralPath $pass.diff | Test-Json -SchemaFile $diffSchema)) {
    throw 'Comparator PASS diff failed Schema validation.'
}

$rightFailure = Copy-Value $right
$rightFailure.tasks[0].start = '2026-08-21T09:00:00+08:00'
$rightFailure.tasks[0].duration_minutes = 600
$rightFailure.tasks[0].constraint_type_code = 4
$rightFailure.tasks[0].constraint_type = 'SNET'
$rightFailure.tasks[0].constraint_date = '2026-08-21T09:00:00+08:00'
$rightFailure.calendars[0].week_days[1].intervals[0].finish = '11:00'
$rightFailure.dependencies[0].lag_minutes = 60
Update-SemanticHash $rightFailure
Write-JsonFixture $rightPath $rightFailure
$failure = & $comparator -LeftSnapshot $leftPath -RightSnapshot $rightPath -IdentityMap $mapPath -OutputDirectory $testRoot | ConvertFrom-Json
$failureDiff = Get-Content -Raw -LiteralPath $failure.diff | ConvertFrom-Json
$expectedBlockerPaths = @(
    '/calendars/Standard/week_days/1/intervals/0/finish',
    '/dependencies/0/lag_minutes',
    '/tasks/source-uid:1/constraint_date',
    '/tasks/source-uid:1/constraint_type',
    '/tasks/source-uid:1/constraint_type_code',
    '/tasks/source-uid:1/duration_minutes',
    '/tasks/source-uid:1/start'
)
if ($failure.status -ne 'FAIL' -or @($expectedBlockerPaths | Where-Object { $_ -notin @($failureDiff.differences.path) }).Count -gt 0) {
    throw 'Comparator did not report every field-level blocker.'
}

$rightUnsupported = Copy-Value $right
$rightUnsupported.unsupported_semantics = @(
    [ordered]@{
        code = 'TEST_UNSUPPORTED'
        severity = 'BLOCKER'
        object_type = 'TASK'
        object_refs = @('source-uid:11')
        detail = 'Comparator unit test.'
    }
)
$rightUnsupported.roundtrip_eligible = $false
Update-SemanticHash $rightUnsupported
Write-JsonFixture $rightPath $rightUnsupported
$unsupported = & $comparator -LeftSnapshot $leftPath -RightSnapshot $rightPath -IdentityMap $mapPath -OutputDirectory $testRoot | ConvertFrom-Json
if ($unsupported.status -ne 'UNSUPPORTED' -or $unsupported.unsupported_count -ne 1) {
    throw 'Comparator reported an unsupported Snapshot as PASS.'
}

'MPP Bridge comparator: PASS'
