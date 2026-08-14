"""Normalize Microsoft Project interchange v1.1 into strict Canonical v2.2."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from yuxi.schedule.audit.context import AuditContext
from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22
from yuxi.schedule.contracts.import_v1 import (
    FieldDisposition,
    MicrosoftProjectInterchangeV11,
    ScheduleNormalizationReport,
    UnsupportedSemantic,
    collect_preserved_field_paths,
)
from yuxi.schedule.importers.canonical_v2_2 import import_canonical_schedule_v2_2
from yuxi.schedule.importers.registry import ScheduleImportResult

SCHEMA_VERSION = "microsoft_project_interchange_mock_v1.1"
ADAPTER_ID = "microsoft_project_interchange_v1_1"
ADAPTER_VERSION = "1.0.0"
CANONICAL_LAG_POLICY = "UNSPECIFIED_REQUIRES_ENGINE_PROFILE"


class MicrosoftProjectInterchangeV11Adapter:
    schema_version = SCHEMA_VERSION
    adapter_id = ADAPTER_ID
    adapter_version = ADAPTER_VERSION

    def normalize(self, document: dict[str, Any]) -> ScheduleImportResult:
        source = MicrosoftProjectInterchangeV11.model_validate(document)
        if source.resources or source.assignments:
            raise ValueError("adapter v1.0 only supports interchange documents without resources or assignments")

        canonical = _build_canonical(source)
        report = _build_report(source)
        return ScheduleImportResult(
            source_document=deepcopy(document),
            canonical=canonical,
            normalization_report=report,
        )


def _build_canonical(source: MicrosoftProjectInterchangeV11) -> CanonicalScheduleV22:
    snapshot_id = f"snapshot:mpp:{source.source.mpp_sha256[:24]}"
    root_task = min(source.tasks, key=lambda task: (task.outline_level, task.source_id))
    calendar_payloads = [_calendar_payload(calendar, index) for index, calendar in enumerate(source.calendars)]
    task_payloads = [_task_payload(task, source.project.default_calendar_id) for task in source.tasks]
    dependency_payloads = [_dependency_payload(dependency) for dependency in source.dependencies]

    payload = {
        "schema_version": "canonical_schedule_v2.2",
        "snapshot_id": snapshot_id,
        "generated_at": source.source.extracted_at,
        "source": {
            "format": source.source.format,
            "file_name": source.source.file_name,
            "sha256": source.source.mpp_sha256,
            "extraction_method": source.source.extraction_method,
            "extraction_application_version": source.source.microsoft_project_version,
            "opened_read_only": False,
        },
        "semantics": {
            "time_zone": source.semantics.timezone,
            "time_zone_source": "SOURCE_DOCUMENT_EXPLICIT",
            "duration_storage_unit": "working_minute",
            "lag_storage_unit": "working_minute",
            "lag_calendar_policy": CANONICAL_LAG_POLICY,
            "task_calendar_resolution": "task.calendar_id ?? project.default_calendar_id",
            "source_dates_preserved": True,
        },
        "project": {
            "project_id": source.project.project_id,
            "name": source.project.name,
            "source_file_name": source.source.file_name,
            "planned_start": source.project.planned_start,
            "planned_finish": source.project.planned_finish,
            "planned_date_source": "SOURCE_PROJECT",
            "source_project_summary": {
                "source_unique_id": root_task.source_unique_id,
                "name": source.project.name,
                "start": source.project.planned_start,
                "finish": source.project.planned_finish,
                "duration_minutes": root_task.project_rollup_duration_minutes,
            },
            # The source format does not expose Project.CurrentDate. The extraction
            # timestamp is a deterministic compatibility value and is reported as unavailable.
            "current_date": source.source.extracted_at,
            "status_date": None,
            "default_calendar_id": source.project.default_calendar_id,
            "default_daily_work_minutes": source.project.default_daily_work_minutes,
            "default_calendar_weekly_work_minutes": source.project.default_weekly_work_minutes,
        },
        "statistics": _placeholder_statistics(),
        "capabilities": _placeholder_capabilities(),
        "calendars": calendar_payloads,
        "resources": [],
        "tasks": task_payloads,
        "dependencies": dependency_payloads,
        "assignments": [],
        "validation": _validation_payload(
            source,
            snapshot_id=snapshot_id,
            capabilities=_placeholder_capabilities(),
            open_start_task_ids=[],
            open_finish_task_ids=[],
            summary_task_dependency_ids=[],
            source_schedule_violations=[],
        ),
    }

    provisional = CanonicalScheduleV22.model_validate(payload)
    schedule = import_canonical_schedule_v2_2(provisional)
    context = AuditContext.build(schedule)
    capabilities = {name: capability.model_dump(mode="json") for name, capability in context.capabilities.items()}
    network_quality = _network_quality(provisional)
    payload["statistics"] = context.statistics
    payload["capabilities"] = capabilities
    payload["validation"] = _validation_payload(
        source,
        snapshot_id=snapshot_id,
        capabilities=capabilities,
        **network_quality,
    )
    return CanonicalScheduleV22.model_validate(payload)


def _calendar_payload(calendar, source_index: int) -> dict[str, Any]:
    return {
        "calendar_id": calendar.calendar_id,
        "source_index": source_index,
        "name": calendar.name,
        "parent_calendar_id": None,
        "weekly_pattern": {
            day.day: {
                "day_type": "WORKING" if day.working else "NON_WORKING",
                "intervals": [interval.model_dump(mode="json") for interval in day.intervals],
            }
            for day in calendar.week_days
        },
        "exceptions": deepcopy(calendar.exceptions),
    }


def _task_payload(task, default_calendar_id: str) -> dict[str, Any]:
    duration_minutes = task.project_rollup_duration_minutes if task.task_type == "SUMMARY" else task.duration_minutes
    constraint_types = {0: "AS_SOON_AS_POSSIBLE", 4: "START_NO_EARLIER_THAN", 6: "FINISH_NO_EARLIER_THAN"}
    return {
        "task_id": task.task_id,
        "source_id": task.source_id,
        "source_unique_id": task.source_unique_id,
        "source_guid": "",
        "parent_task_id": task.parent_task_id,
        "wbs": task.wbs,
        "outline_level": task.outline_level,
        "name": task.name,
        "task_type": "summary" if task.task_type == "SUMMARY" else "activity",
        "active": True,
        "scheduling_mode": "automatic" if task.scheduling_mode == "AUTO" else "manual",
        "project_task_type": "FIXED_DURATION",
        "calendar_id": None if task.calendar_id == default_calendar_id else task.calendar_id,
        "effective_calendar_id": task.calendar_id,
        "planned_start": task.start,
        "planned_finish": task.finish,
        "duration_minutes": duration_minutes,
        "source_work_minutes": 0,
        "percent_complete": task.percent_complete,
        "actual_start": None,
        "actual_finish": None,
        "deadline": task.deadline,
        "constraint": {
            "type": constraint_types[task.constraint_type_code],
            "date": task.constraint_date,
        },
        "baseline_0": {"exists": False, "start": None, "finish": None},
        # v2.2 requires source calculation fields, but this interchange version
        # does not provide them. Identity values keep source dates immutable;
        # the normalization report marks the entire projection as unsupported.
        "source_calculation": {
            "early_start": task.start,
            "early_finish": task.finish,
            "late_start": task.start,
            "late_finish": task.finish,
            "total_slack_minutes": 0,
            "free_slack_minutes": 0,
            "critical": False,
        },
        "source_resource_names_text": None,
        "notes": "",
    }


def _dependency_payload(dependency) -> dict[str, Any]:
    return {
        "dependency_id": dependency.dependency_id,
        "predecessor_task_id": dependency.predecessor_task_id,
        "successor_task_id": dependency.successor_task_id,
        "type": dependency.type,
        "source_type_code": dependency.source_type_code,
        "lag_minutes": dependency.lag_minutes,
        "lag_calendar_policy": CANONICAL_LAG_POLICY,
    }


def _network_quality(canonical: CanonicalScheduleV22) -> dict[str, list[Any]]:
    leaf_task_ids = {task.task_id for task in canonical.tasks if task.task_type != "summary"}
    summary_task_ids = {task.task_id for task in canonical.tasks if task.task_type == "summary"}
    incoming = {task_id: 0 for task_id in leaf_task_ids}
    outgoing = {task_id: 0 for task_id in leaf_task_ids}
    summary_dependencies = []
    violations = []
    tasks_by_id = {task.task_id: task for task in canonical.tasks}
    for dependency in canonical.dependencies:
        if dependency.successor_task_id in incoming:
            incoming[dependency.successor_task_id] += 1
        if dependency.predecessor_task_id in outgoing:
            outgoing[dependency.predecessor_task_id] += 1
        if dependency.predecessor_task_id in summary_task_ids or dependency.successor_task_id in summary_task_ids:
            summary_dependencies.append(dependency.dependency_id)
        if dependency.lag_minutes == 0 and not _dependency_date_valid(dependency, tasks_by_id):
            violations.append({"dependency_id": dependency.dependency_id, "code": "SOURCE_DATE_VIOLATION"})
    return {
        "open_start_task_ids": sorted(task_id for task_id, count in incoming.items() if count == 0),
        "open_finish_task_ids": sorted(task_id for task_id, count in outgoing.items() if count == 0),
        "summary_task_dependency_ids": sorted(summary_dependencies),
        "source_schedule_violations": violations,
    }


def _dependency_date_valid(dependency, tasks_by_id: dict[str, Any]) -> bool:
    predecessor = tasks_by_id[dependency.predecessor_task_id]
    successor = tasks_by_id[dependency.successor_task_id]
    required, actual = {
        "FS": (predecessor.planned_finish, successor.planned_start),
        "SS": (predecessor.planned_start, successor.planned_start),
        "FF": (predecessor.planned_finish, successor.planned_finish),
        "SF": (predecessor.planned_start, successor.planned_finish),
    }[dependency.type]
    return actual >= required


def _validation_payload(
    source: MicrosoftProjectInterchangeV11,
    *,
    snapshot_id: str,
    capabilities: dict[str, Any],
    open_start_task_ids: list[str],
    open_finish_task_ids: list[str],
    summary_task_dependency_ids: list[str],
    source_schedule_violations: list[dict[str, Any]],
) -> dict[str, Any]:
    milestone_ids = sorted(task.task_id for task in source.tasks if task.task_type == "MILESTONE")
    all_task_ids = sorted(task.task_id for task in source.tasks)
    issues = [
        _validation_issue(
            "IMPORT-V1-001",
            "SOURCE_CALCULATION_UNAVAILABLE",
            "warning",
            "tasks",
            all_task_ids,
            "Source early/late dates, float and critical flags were not supplied.",
        ),
        _validation_issue(
            "IMPORT-V1-002",
            "BASELINE_UNAVAILABLE",
            "warning",
            "tasks",
            all_task_ids,
            "Source baseline facts were not supplied.",
        ),
        _validation_issue(
            "IMPORT-V1-003",
            "NO_SOURCE_ASSIGNMENTS",
            "warning",
            "assignments",
            [],
            "Source resources and assignments are unavailable.",
        ),
    ]
    engine_decisions = []
    for index, code in enumerate(capabilities["cpm_recalculation"]["reasons"], start=4):
        object_refs = milestone_ids if code == "MILESTONE_UNSUPPORTED" else []
        issues.append(
            _validation_issue(
                f"IMPORT-V1-{index:03d}",
                code,
                "blocker",
                "tasks" if object_refs else "schedule",
                object_refs,
                "The current CPM profile does not support this source semantic.",
            )
        )
        engine_decisions.append(
            {
                "code": code,
                "severity": "blocker",
                "object_ref": "tasks" if object_refs else "schedule",
                "object_refs": object_refs,
            }
        )
    return {
        "schema_version": "schedule_validation_report_v2.2",
        "snapshot_id": snapshot_id,
        "generated_at": source.source.extracted_at,
        "source_sha256": source.source.mpp_sha256,
        "summary": {
            "status": (
                "review_allowed" if capabilities["cpm_recalculation"]["allowed"] else "blocked_for_recalculation"
            ),
            "issue_count": len(issues),
            "blocker_count": sum(issue["severity"] == "blocker" for issue in issues),
            "warning_count": sum(issue["severity"] == "warning" for issue in issues),
            "source_fidelity_valid": True,
            "recalculation_allowed": capabilities["cpm_recalculation"]["allowed"],
            "display_allowed": True,
        },
        "source_fidelity_checks": {
            "tasks_match_source": True,
            "dependencies_match_source": True,
            "calendars_match_source": True,
            "resources_match_source": True,
            "assignments_match_source": True,
            "synthetic_project_summary_added": False,
            "synthetic_resources_added": False,
            "synthetic_assignments_added": False,
            "project_dates_resolved_from_source_summary": True,
        },
        "network_quality": {
            "open_start_task_ids": open_start_task_ids,
            "open_finish_task_ids": open_finish_task_ids,
            "summary_task_dependency_ids": summary_task_dependency_ids,
            "source_schedule_violations": source_schedule_violations,
        },
        "capabilities": capabilities,
        "source_vs_conversion_assessment": {
            "source_mpp_findings": [
                {
                    "code": "NO_SOURCE_ASSIGNMENTS",
                    "severity": "warning",
                    "object_ref": "assignments",
                }
            ],
            "source_format_limitations": [
                {
                    "code": "SOURCE_CALCULATION_UNAVAILABLE",
                    "severity": "warning",
                    "object_ref": "tasks",
                    "object_refs": all_task_ids,
                },
                {
                    "code": "BASELINE_UNAVAILABLE",
                    "severity": "warning",
                    "object_ref": "tasks",
                    "object_refs": all_task_ids,
                },
            ],
            "conversion_defects_fixed_in_v2_2": [],
            "engine_contract_decisions_required": engine_decisions,
        },
        "issues": issues,
    }


def _validation_issue(
    issue_id: str,
    code: str,
    severity: str,
    object_ref: str,
    object_refs: list[str],
    message: str,
) -> dict[str, Any]:
    return {
        "issue_id": issue_id,
        "code": code,
        "origin": "IMPORT_ADAPTER",
        "severity": severity,
        "object_ref": object_ref,
        "object_refs": object_refs,
        "message": message,
    }


def _build_report(source: MicrosoftProjectInterchangeV11) -> ScheduleNormalizationReport:
    preserved_fields = sorted(set(collect_preserved_field_paths(source)))
    ignored_claims = [
        FieldDisposition(path=path, reason_code="SOURCE_CLAIM_RECOMPUTED")
        for path in ("/statistics", "/capabilities", "/validation")
    ]
    ignored_extensions = [
        FieldDisposition(path=path, reason_code="UNKNOWN_SOURCE_FIELD_NOT_MAPPED") for path in preserved_fields
    ]
    return ScheduleNormalizationReport(
        schema_version="schedule_normalization_report_v1",
        source_schema_version=source.schema_version,
        adapter_id=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
        preserved_fields=preserved_fields,
        ignored_for_audit=[*ignored_claims, *ignored_extensions],
        ignored_for_calculation=[*ignored_claims, *ignored_extensions],
        unsupported_semantics=[
            UnsupportedSemantic(
                code="SOURCE_CALCULATION_UNAVAILABLE",
                object_refs=sorted(task.task_id for task in source.tasks),
            ),
            UnsupportedSemantic(
                code="BASELINE_UNAVAILABLE",
                object_refs=sorted(task.task_id for task in source.tasks),
            ),
            UnsupportedSemantic(
                code="PROJECT_CURRENT_DATE_UNAVAILABLE",
                object_refs=[source.project.project_id],
            ),
            UnsupportedSemantic(
                code="MILESTONE_UNSUPPORTED",
                object_refs=sorted(task.task_id for task in source.tasks if task.task_type == "MILESTONE"),
            ),
            UnsupportedSemantic(code="RESOURCE_ASSIGNMENTS_UNAVAILABLE"),
        ],
    )


def _placeholder_statistics() -> dict[str, Any]:
    return {
        "tasks": 0,
        "summary_tasks": 0,
        "leaf_tasks": 0,
        "milestones": 0,
        "dependencies": 0,
        "dependency_types": dict.fromkeys(("FS", "SS", "FF", "SF"), 0),
        "positive_lag_dependencies": 0,
        "negative_lag_dependencies": 0,
        "calendars": 0,
        "resources_raw": 0,
        "assignments": 0,
        "open_start_tasks": 0,
        "open_finish_tasks": 0,
        "summary_task_dependencies": 0,
        "source_schedule_dependency_violations": 0,
    }


def _placeholder_capabilities() -> dict[str, Any]:
    return {
        name: {"allowed": False, "reasons": ["NORMALIZATION_PENDING"]}
        for name in (
            "gantt_display",
            "source_schedule_review",
            "cpm_recalculation",
            "resource_leveling",
            "resource_cost_optimization",
        )
    }
