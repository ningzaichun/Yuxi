[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$bridgeRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$schemaDirectory = Join-Path $bridgeRoot 'schemas'
$hash = 'a' * 64
$runId = [guid]::NewGuid().ToString('D')
$timestamp = [datetimeoffset]::UtcNow.ToString('o')
$processEvidence = [ordered]@{
    winproj_process_ids_before = @()
    winproj_process_ids_after_create = @(1234)
    winproj_process_ids_after = @()
    residual_new_process_ids = @()
}

$snapshot = [ordered]@{
    schema_version = 'mpp_bridge_snapshot_v1'
    run_id = $runId
    tool_version = '0.2.0-m1'
    captured_at = $timestamp
    timezone = 'Asia/Shanghai'
    source = [ordered]@{
        input_file_name = 'fixture.mpp'
        input_mpp_sha256 = $hash
        working_copy_mpp_sha256 = $hash
        microsoft_project_version = '16.0'
        opened_after_save = $true
        recalculated_after_reopen = $true
    }
    project = [ordered]@{
        name = 'Fixture'
        planned_start = '2026-08-21T08:00:00+08:00'
        planned_finish = '2026-08-21T17:00:00+08:00'
        default_calendar_id = 'calendar:source:1'
        default_calendar_name = 'Standard'
        status_date = $null
    }
    calendars = @(
        [ordered]@{
            calendar_id = 'calendar:source:1'
            source_index = 1
            name = 'Standard'
            base_calendar_name = $null
            week_days = @(
                [ordered]@{ day = 'SUNDAY'; working = $false; intervals = @() },
                [ordered]@{ day = 'MONDAY'; working = $true; intervals = @([ordered]@{ start = '08:00'; finish = '12:00' }, [ordered]@{ start = '13:00'; finish = '17:00' }) },
                [ordered]@{ day = 'TUESDAY'; working = $true; intervals = @([ordered]@{ start = '08:00'; finish = '12:00' }, [ordered]@{ start = '13:00'; finish = '17:00' }) },
                [ordered]@{ day = 'WEDNESDAY'; working = $true; intervals = @([ordered]@{ start = '08:00'; finish = '12:00' }, [ordered]@{ start = '13:00'; finish = '17:00' }) },
                [ordered]@{ day = 'THURSDAY'; working = $true; intervals = @([ordered]@{ start = '08:00'; finish = '12:00' }, [ordered]@{ start = '13:00'; finish = '17:00' }) },
                [ordered]@{ day = 'FRIDAY'; working = $true; intervals = @([ordered]@{ start = '08:00'; finish = '12:00' }, [ordered]@{ start = '13:00'; finish = '17:00' }) },
                [ordered]@{ day = 'SATURDAY'; working = $false; intervals = @() }
            )
            exceptions = @()
        }
    )
    tasks = @(
        [ordered]@{
            bridge_task_id = 'source-uid:1'
            source_id = 1
            source_unique_id = 1
            parent_task_id = $null
            name = 'Task 1'
            outline_level = 1
            wbs = '1'
            task_type = 'TASK'
            summary = $false
            milestone = $false
            scheduling_mode = 'AUTO'
            active = $true
            start = '2026-08-21T08:00:00+08:00'
            finish = '2026-08-21T17:00:00+08:00'
            duration_minutes = 480
            project_rollup_duration_minutes = 480
            percent_complete = 0
            constraint_type_code = 0
            constraint_type = 'ASAP'
            constraint_date = $null
            deadline = $null
            calendar_id = 'calendar:source:1'
            source_calendar_name = $null
            predecessors_text = ''
        }
    )
    dependencies = @()
    unsupported_semantics = @()
    roundtrip_eligible = $true
    snapshot_semantic_sha256 = $hash
}

$manifest = [ordered]@{
    schema_version = 'mpp_bridge_manifest_v1'
    run_id = $runId
    tool_version = '0.2.0-m1'
    command = 'extract'
    status = 'passed'
    created_at = $timestamp
    source = [ordered]@{
        input_file_name = 'fixture.mpp'
        input_mpp_sha256_before = $hash
        input_mpp_sha256_after = $hash
        source_unchanged = $true
    }
    artifacts = [ordered]@{
        snapshot_file = 'snapshot.json'
        snapshot_artifact_sha256 = $hash
        snapshot_semantic_sha256 = $hash
        original_byte_backup_file = 'original-byte-backup.mpp'
        original_byte_backup_sha256 = $hash
        working_copy_file = 'working-copy.mpp'
        working_copy_mpp_sha256 = $hash
    }
    result = [ordered]@{
        roundtrip_eligible = $true
        unsupported_count = 0
        blocker_count = 0
    }
    process_evidence = $processEvidence
}

$errorPayload = [ordered]@{
    schema_version = 'mpp_bridge_error_v1'
    run_id = $runId
    tool_version = '0.2.0-m1'
    command = 'extract'
    status = 'failed'
    occurred_at = $timestamp
    error = [ordered]@{
        code = 'MPP_BRIDGE_EXTRACT_FAILED'
        stage = 'open_working_copy_first_pass'
        message = 'simulated failure'
    }
    source = [ordered]@{
        input_file_name = 'fixture.mpp'
        input_mpp_sha256_before = $hash
        input_mpp_sha256_after = $hash
    }
    process_evidence = $processEvidence
}

$identityMap = [ordered]@{
    schema_version = 'mpp_bridge_identity_map_v1'
    run_id = $runId
    tool_version = '0.2.0-m1'
    source_snapshot_semantic_sha256 = $hash
    output_mpp_sha256 = $hash
    tasks = @(
        [ordered]@{
            bridge_task_id = 'source-uid:1'
            source = [ordered]@{ id = 1; unique_id = 1 }
            output = [ordered]@{ id = 2; unique_id = 2 }
        }
    )
}

$buildReport = [ordered]@{
    schema_version = 'mpp_bridge_build_report_v1'
    run_id = $runId
    tool_version = '0.3.0-m2'
    source_snapshot_semantic_sha256 = $hash
    output_mpp_sha256 = $hash
    calendars_written = 1
    tasks_written = 1
    dependencies_written = 0
    opened_after_save = $true
    recalculated_after_reopen = $true
}

$buildManifest = [ordered]@{
    schema_version = 'mpp_bridge_build_manifest_v1'
    run_id = $runId
    tool_version = '0.3.0-m2'
    command = 'build'
    status = 'passed'
    created_at = $timestamp
    source = [ordered]@{
        snapshot_file_name = 'snapshot.json'
        snapshot_artifact_sha256 = $hash
        snapshot_semantic_sha256 = $hash
        roundtrip_eligible = $true
    }
    artifacts = [ordered]@{
        output_mpp_file = 'output.mpp'
        output_mpp_sha256 = $hash
        identity_map_file = 'identity-map.json'
        identity_map_sha256 = $hash
        build_report_file = 'build-report.json'
        build_report_sha256 = $hash
    }
    result = [ordered]@{
        calendars_written = 1
        tasks_written = 1
        dependencies_written = 0
        opened_after_save = $true
        recalculated_after_reopen = $true
    }
    process_evidence = $processEvidence
}

$buildError = [ordered]@{
    schema_version = 'mpp_bridge_build_error_v1'
    run_id = $runId
    tool_version = '0.3.0-m2'
    command = 'build'
    status = 'failed'
    occurred_at = $timestamp
    error = [ordered]@{
        code = 'MPP_BRIDGE_BUILD_FAILED'
        stage = 'write_tasks'
        message = 'simulated failure'
    }
    source = [ordered]@{
        snapshot_file_name = 'snapshot.json'
        snapshot_artifact_sha256 = $hash
        snapshot_semantic_sha256 = $hash
    }
    output = [ordered]@{
        requested_file_name = 'output.mpp'
        created = $false
    }
    process_evidence = $processEvidence
}

$diff = [ordered]@{
    schema_version = 'mpp_bridge_diff_v1'
    run_id = $runId
    comparator_version = '0.2.0-m1'
    compared_at = $timestamp
    left_snapshot_semantic_sha256 = $hash
    right_snapshot_semantic_sha256 = $hash
    status = 'PASS'
    summary = [ordered]@{
        difference_count = 0
        blocker_count = 0
        allowed_reassignment_count = 0
        unsupported_count = 0
    }
    differences = @()
}

$roundtripReport = [ordered]@{
    schema_version = 'mpp_bridge_roundtrip_report_v1'
    run_id = $runId
    tool_version = '0.4.0-m3'
    command = 'roundtrip'
    status = 'PASS'
    created_at = $timestamp
    timezone = 'Asia/Shanghai'
    execution_system_timezone = 'China Standard Time'
    source = [ordered]@{
        input_file_name = 'fixture.mpp'
        input_mpp_sha256 = $hash
        snapshot_file = 'src/extract/snapshot.json'
        snapshot_semantic_sha256 = $hash
        microsoft_project_version = '16.0'
    }
    build = [ordered]@{
        output_mpp_file = 'built.mpp'
        output_mpp_sha256 = $hash
        identity_map_file = 'bld/build/identity-map.json'
        manifest_file = 'bld/build/manifest.json'
    }
    output = [ordered]@{
        snapshot_file = 'out/extract/snapshot.json'
        snapshot_semantic_sha256 = $hash
        microsoft_project_version = '16.0'
        roundtrip_eligible = $true
        unsupported_count = 0
    }
    comparator = [ordered]@{
        diff_file = 'cmp/compare/diff.json'
        diff_sha256 = $hash
        summary_file = 'cmp/compare/summary.txt'
        difference_count = 0
        blocker_count = 0
        allowed_reassignment_count = 0
        unsupported_count = 0
    }
    source_unchanged = $true
}

$version = [ordered]@{
    schema_version = 'mpp_bridge_version_v1'
    bridge_version = '0.5.0-y0'
    powershell_version = '7.5.4'
    operating_system = 'Microsoft Windows 10.0.26100'
    is_windows = $true
    microsoft_project_com_registered = $true
    commands = @('Invoke-MppBridgeExtract.ps1', 'Project-MppBridgeSnapshotToYuxi.ps1')
    contracts = @(
        'mpp_bridge_snapshot_v1.schema.json',
        'mpp_bridge_yuxi_projection_report_v1.schema.json',
        'microsoft_project_interchange_v1_1.schema.json'
    )
}

$packageManifest = [ordered]@{
    schema_version = 'mpp_bridge_package_manifest_v1'
    package_version = '0.5.0-y0'
    content_sha256 = $hash
    files = @(
        [ordered]@{ path = 'README.md'; sha256 = $hash; bytes = 100 }
    )
}

$cases = @(
    @{ Name = 'snapshot'; Value = $snapshot; Schema = 'mpp_bridge_snapshot_v1.schema.json' },
    @{ Name = 'manifest'; Value = $manifest; Schema = 'mpp_bridge_manifest_v1.schema.json' },
    @{ Name = 'error'; Value = $errorPayload; Schema = 'mpp_bridge_error_v1.schema.json' },
    @{ Name = 'identity map'; Value = $identityMap; Schema = 'mpp_bridge_identity_map_v1.schema.json' },
    @{ Name = 'build report'; Value = $buildReport; Schema = 'mpp_bridge_build_report_v1.schema.json' },
    @{ Name = 'build manifest'; Value = $buildManifest; Schema = 'mpp_bridge_build_manifest_v1.schema.json' },
    @{ Name = 'build error'; Value = $buildError; Schema = 'mpp_bridge_build_error_v1.schema.json' },
    @{ Name = 'diff'; Value = $diff; Schema = 'mpp_bridge_diff_v1.schema.json' },
    @{ Name = 'roundtrip report'; Value = $roundtripReport; Schema = 'mpp_bridge_roundtrip_report_v1.schema.json' },
    @{ Name = 'version'; Value = $version; Schema = 'mpp_bridge_version_v1.schema.json' },
    @{ Name = 'package manifest'; Value = $packageManifest; Schema = 'mpp_bridge_package_manifest_v1.schema.json' }
)

foreach ($case in $cases) {
    $json = $case.Value | ConvertTo-Json -Depth 30
    $schemaPath = Join-Path $schemaDirectory $case.Schema
    if (-not ($json | Test-Json -SchemaFile $schemaPath -ErrorAction Stop)) {
        throw "Valid $($case.Name) fixture failed schema validation."
    }
}

$invalidSnapshot = $snapshot | ConvertTo-Json -Depth 30 | ConvertFrom-Json -AsHashtable
$invalidSnapshot['unknown_field'] = 'must be rejected'
$invalidJson = $invalidSnapshot | ConvertTo-Json -Depth 30
$snapshotSchema = Join-Path $schemaDirectory 'mpp_bridge_snapshot_v1.schema.json'
if ($invalidJson | Test-Json -SchemaFile $snapshotSchema -ErrorAction SilentlyContinue) {
    throw 'Snapshot schema accepted an unknown root field.'
}

'MPP Bridge contracts: PASS'
