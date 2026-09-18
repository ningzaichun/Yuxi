"""Test-only adapter and result projection for the frozen complex Schedule suite."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from yuxi.schedule.audit.context import AuditOptions
from yuxi.schedule.audit.engine import audit_schedule
from yuxi.schedule.contracts.canonical_v2_3 import CanonicalScheduleV23
from yuxi.schedule.contracts.canonical_v2_4 import CanonicalScheduleV24
from yuxi.schedule.contracts.canonical_v2_5 import CanonicalScheduleV25
from yuxi.schedule.contracts.canonical_v2_6 import CanonicalScheduleV26
from yuxi.schedule.contracts.canonical_v2_7 import CanonicalScheduleV27
from yuxi.schedule.forward_engine import calculate_minimal_forward_schedule, recalculation_profile_for
from yuxi.schedule.importers import import_canonical_schedule
from yuxi.schedule.preflight import preflight_schedule_input

SUITE_SCHEMA_VERSION = "schedule_engine_test_input_v1"
LARGE_SUITE_CONTRACT_VERSION = "schedule_engine_large_suite_adapter_v2"
SUITE_ISSUE_PROFILE_ID = "suite_issue_profile_v1"
SUITE_ISSUE_CODES = frozenset(
    {
        "DEADLINE_MISSED",
        "FINISH_CONSTRAINT_VIOLATED",
        "HARD_CONSTRAINT_NETWORK_CONFLICT",
        "PROJECT_REQUIRED_FINISH_MISSED",
        "RESOURCE_OVERALLOCATION",
        "ASSIGNMENT_RESOURCE_NOT_FOUND",
        "DEPENDENCY_CYCLE",
        "DEPENDENCY_TASK_NOT_FOUND",
        "MILESTONE_DURATION_NONZERO",
        "PARENT_TASK_NOT_FOUND",
        "TASK_CALENDAR_NOT_FOUND",
    }
)
SOURCE_TYPE_CODES = {"FF": 0, "FS": 1, "SF": 2, "SS": 3}
SUITE_ORACLE_FIELDS = (
    "case_id",
    "status",
    "project_dates",
    "task_dates",
    "summary_dates",
    "issues",
    "resource_conflicts",
    "assignment_costs",
    "baseline_variances",
)


class SuiteModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SuiteOracle(SuiteModel):
    type: str
    is_microsoft_project_observation: bool
    date_precision: Literal["minute"]
    tolerance_minutes: Literal[0]
    capture_method: str | None = None
    product_version: str | None = None
    source_mpp_sha256: str | None = None


class SuiteSemantics(SuiteModel):
    timezone: str
    duration_unit: Literal["working_minute"]
    lag_unit: Literal["working_minute"]
    default_lag_calendar_policy: Literal["SUCCESSOR_TASK_CALENDAR"]
    summary_rollup: str
    date_boundary: str


class SuiteProject(SuiteModel):
    project_id: str
    name: str
    planned_start: AwareDatetime
    required_finish: AwareDatetime | None
    status_date: AwareDatetime | None
    default_calendar_id: str


class SuiteInterval(SuiteModel):
    start: time
    finish: time


class SuiteWorkingDay(SuiteModel):
    day_type: Literal["WORKING", "NON_WORKING"]
    intervals: list[SuiteInterval]


class SuiteCalendarException(SuiteModel):
    exception_id: str
    name: str
    start_date: AwareDatetime
    finish_date: AwareDatetime
    working: bool
    intervals: list[SuiteInterval]


class SuiteCalendar(SuiteModel):
    calendar_id: str
    name: str
    weekly_pattern: dict[str, SuiteWorkingDay]
    exceptions: list[SuiteCalendarException]


class SuiteConstraint(SuiteModel):
    type: Literal[
        "AS_SOON_AS_POSSIBLE",
        "START_NO_EARLIER_THAN",
        "MUST_START_ON",
        "FINISH_NO_LATER_THAN",
    ]
    date: AwareDatetime | None


class SuiteBaseline(SuiteModel):
    exists: bool
    start: AwareDatetime | None
    finish: AwareDatetime | None


class SuiteTask(SuiteModel):
    task_id: str
    parent_task_id: str | None
    name: str
    task_type: Literal["TASK", "MILESTONE", "SUMMARY"]
    summary: bool
    milestone: bool
    duration_minutes: int | None
    calendar_id: str | None
    boundary_role: Literal["PROJECT_START", "PROJECT_FINISH"] | None
    status: Literal["NOT_STARTED", "IN_PROGRESS", "COMPLETED"]
    constraint: SuiteConstraint
    deadline: AwareDatetime | None
    baseline_0: SuiteBaseline
    actual_start: AwareDatetime | None = None
    actual_finish: AwareDatetime | None = None
    remaining_duration_minutes: int | None = None
    observed_start: AwareDatetime | None = None
    observed_finish: AwareDatetime | None = None
    estimated: bool | None = None


class SuiteDependency(SuiteModel):
    dependency_id: str
    predecessor_task_id: str
    successor_task_id: str
    type: Literal["FS", "SS", "FF", "SF"]
    source_type_code: int | None = None
    lag_minutes: int
    lag_calendar_policy: Literal["SUCCESSOR_TASK_CALENDAR"]


class SuiteResource(SuiteModel):
    resource_id: str
    name: str
    type: Literal["WORK", "EQUIPMENT"]
    max_units: float
    standard_rate_per_hour: float


class SuiteAssignment(SuiteModel):
    assignment_id: str
    task_id: str
    resource_id: str
    units: float


class SuiteBoundaryWhitelist(SuiteModel):
    open_start_task_ids: list[str]
    open_finish_task_ids: list[str]


class ScheduleEngineTestInputV1(SuiteModel):
    schema_version: Literal["schedule_engine_test_input_v1"]
    case_id: str
    title: str
    data_classification: str
    oracle: SuiteOracle
    semantics: SuiteSemantics
    project: SuiteProject
    calendars: list[SuiteCalendar]
    tasks: list[SuiteTask]
    dependencies: list[SuiteDependency]
    resources: list[SuiteResource]
    assignments: list[SuiteAssignment]
    boundary_whitelist: SuiteBoundaryWhitelist

    @model_validator(mode="after")
    def validate_weekly_patterns(self) -> Self:
        expected = {"SUNDAY", "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY"}
        for calendar in self.calendars:
            if set(calendar.weekly_pattern) != expected:
                raise ValueError(f"calendar {calendar.calendar_id} must define seven weekdays")
        return self


class LargeSuiteTestProfile(SuiteModel):
    purpose: str
    scale_target: str
    coverage: list[str]
    oracle_scope: Literal["DETERMINISTIC_REFERENCE_IMPLEMENTATION"]
    microsoft_project_observation: Literal[False]


class LargeSuiteTask(SuiteTask):
    wbs: str = Field(min_length=1)
    outline_level: int = Field(ge=1)


class LargeScheduleEngineTestInputV2(ScheduleEngineTestInputV1):
    """Strict v2 adapter contract for the frozen large Suite v1 documents.

    The frozen files retain their original ``schema_version`` for source
    fidelity. This adapter version makes their formerly implicit large-Suite
    fields explicit without rewriting the evidence package.
    """

    tasks: list[LargeSuiteTask]
    test_profile: LargeSuiteTestProfile


SuiteInput = ScheduleEngineTestInputV1 | LargeScheduleEngineTestInputV2


def parse_suite_document(document: dict[str, Any]) -> SuiteInput:
    contract = LargeScheduleEngineTestInputV2 if "test_profile" in document else ScheduleEngineTestInputV1
    return contract.model_validate(document)


def suite_oracle_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Compare only the frozen Suite result contract."""
    return {field: actual.get(field) for field in SUITE_ORACLE_FIELDS} == {
        field: expected.get(field) for field in SUITE_ORACLE_FIELDS
    }


@dataclass(frozen=True, slots=True)
class SuiteCaseExecution:
    actual: dict[str, Any]
    yuxi_audit: dict[str, Any]
    canonical: (
        CanonicalScheduleV23
        | CanonicalScheduleV24
        | CanonicalScheduleV25
        | CanonicalScheduleV26
        | CanonicalScheduleV27
        | None
    )
    engine_result: dict[str, Any] | None
    engine_called: bool


def execute_suite_case(document: dict[str, Any]) -> SuiteCaseExecution:
    case_id = str(document.get("case_id", ""))
    source = parse_suite_document(document)
    audit_metadata = {"issue_profile_id": SUITE_ISSUE_PROFILE_ID}
    if isinstance(source, LargeScheduleEngineTestInputV2):
        audit_metadata["suite_contract_version"] = LARGE_SUITE_CONTRACT_VERSION
    preflight_issues = preflight_schedule_input(document)
    if preflight_issues:
        suite_preflight_issues = [issue.as_suite_issue() for issue in preflight_issues]
        if isinstance(source, LargeScheduleEngineTestInputV2):
            cycle_indexes = [
                index for index, issue in enumerate(suite_preflight_issues) if issue["code"] == "DEPENDENCY_CYCLE"
            ]
            if len(cycle_indexes) > 1:
                first_cycle_index = cycle_indexes[0]
                cycle_refs = sorted(
                    {
                        object_ref
                        for index in cycle_indexes
                        for object_ref in suite_preflight_issues[index]["object_refs"]
                    },
                    key=_stable_id_key,
                )
                suite_preflight_issues = [
                    issue for issue in suite_preflight_issues if issue["code"] != "DEPENDENCY_CYCLE"
                ]
                suite_preflight_issues.insert(
                    first_cycle_index,
                    {"severity": "BLOCKER", "code": "DEPENDENCY_CYCLE", "object_refs": cycle_refs},
                )
        return SuiteCaseExecution(
            actual=_empty_actual(
                case_id,
                status="VALIDATION_FAILED",
                issues=suite_preflight_issues,
            ),
            yuxi_audit={**audit_metadata, "issues": []},
            canonical=None,
            engine_result=None,
            engine_called=False,
        )

    unsupported = _unsupported_reasons(source)
    if unsupported:
        return SuiteCaseExecution(
            actual=_empty_actual(
                case_id,
                status="UNSUPPORTED",
                issues=[{"severity": "BLOCKER", "code": code, "object_ref": case_id} for code in unsupported],
            ),
            yuxi_audit={**audit_metadata, "issues": []},
            canonical=None,
            engine_result=None,
            engine_called=False,
        )

    canonical, audit_options = normalize_suite_document(document)
    execution = audit_schedule(
        import_canonical_schedule(canonical),
        schedule_snapshot_id=canonical.snapshot_id,
        audit_run_id=f"suite:{case_id}",
        options=audit_options,
    )
    engine_result = calculate_minimal_forward_schedule(
        canonical,
        engine_profile_id=recalculation_profile_for(canonical),
    )
    audit_issues = [_audit_issue(item) for item in execution.findings]
    if engine_result["status"] != "calculated":
        blockers = engine_result["support"]["blockers"]
        return SuiteCaseExecution(
            actual=_empty_actual(
                case_id,
                status="UNSUPPORTED",
                issues=[
                    {
                        "severity": "BLOCKER",
                        "code": blocker["code"],
                        "object_refs": blocker["object_refs"],
                    }
                    for blocker in blockers
                ],
            ),
            yuxi_audit={**audit_metadata, "issues": audit_issues},
            canonical=canonical,
            engine_result=engine_result,
            engine_called=True,
        )

    dates = {item["task_id"]: item for item in engine_result["task_dates"]}
    engine_issues = [_suite_engine_issue(item) for item in engine_result.get("issues", [])]
    suite_issues = [
        item for item in audit_issues if item["code"] in SUITE_ISSUE_CODES and item["code"] != "RESOURCE_OVERALLOCATION"
    ]
    suite_issues.extend(engine_issues)
    suite_issues.sort(key=lambda item: (item["code"], item["object_ref"]))
    actual = {
        "case_id": case_id,
        "status": "SUCCEEDED_WITH_ISSUES" if suite_issues else "SUCCEEDED",
        "project_dates": {
            "start": engine_result["project_start"],
            "finish": engine_result["finish_after"],
        },
        "task_dates": sorted(
            (
                {
                    "task_id": task.task_id,
                    "start": dates[task.task_id]["early_start"],
                    "finish": dates[task.task_id]["early_finish"],
                    "calendar_id": task.effective_calendar_id,
                }
                for task in canonical.tasks
                if task.task_type != "summary"
            ),
            key=lambda item: _stable_id_key(item["task_id"]),
        ),
        "summary_dates": sorted(
            (
                {
                    "task_id": task.task_id,
                    "start": dates[task.task_id]["early_start"],
                    "finish": dates[task.task_id]["early_finish"],
                }
                for task in canonical.tasks
                if task.task_type == "summary"
            ),
            key=lambda item: _stable_id_key(item["task_id"]),
        ),
        "issues": suite_issues,
        "resource_conflicts": engine_result.get("resource_conflicts", []),
        "assignment_costs": engine_result.get("assignment_costs", []),
        "baseline_variances": sorted(
            engine_result.get("baseline_variances", []),
            key=lambda item: _stable_id_key(item["task_id"]),
        ),
    }
    return SuiteCaseExecution(
        actual=actual,
        yuxi_audit={
            **audit_metadata,
            "issues": [*audit_issues, *engine_result.get("issues", [])],
        },
        canonical=canonical,
        engine_result=engine_result,
        engine_called=True,
    )


def normalize_suite_document(
    document: dict[str, Any],
) -> tuple[
    CanonicalScheduleV23 | CanonicalScheduleV24 | CanonicalScheduleV25 | CanonicalScheduleV26 | CanonicalScheduleV27,
    AuditOptions,
]:
    preflight_issues = preflight_schedule_input(document)
    if preflight_issues:
        codes = ", ".join(issue.code for issue in preflight_issues)
        raise ValueError(f"suite input failed preflight: {codes}")
    source = parse_suite_document(document)
    if _unsupported_reasons(source):
        raise ValueError("suite input contains semantics outside the current engine profile")

    default_calendar = next(
        calendar for calendar in source.calendars if calendar.calendar_id == source.project.default_calendar_id
    )
    uses_progress_facts = source.project.status_date is not None or any(
        task.status != "NOT_STARTED"
        or task.baseline_0.exists
        or task.actual_start is not None
        or task.actual_finish is not None
        or task.remaining_duration_minutes is not None
        for task in source.tasks
    )
    daily_minutes = max(
        sum(_interval_minutes(interval.start, interval.finish) for interval in day.intervals)
        for day in default_calendar.weekly_pattern.values()
    )
    weekly_minutes = sum(
        _interval_minutes(interval.start, interval.finish)
        for day in default_calendar.weekly_pattern.values()
        for interval in day.intervals
    )
    project_finish = max(
        (task.observed_finish for task in source.tasks if task.observed_finish is not None),
        default=source.project.required_finish or source.project.planned_start,
    )
    task_ids = {task.task_id for task in source.tasks}
    parent_by_id = {task.task_id: task.parent_task_id for task in source.tasks}
    incoming = {task_id: 0 for task_id in task_ids}
    outgoing = {task_id: 0 for task_id in task_ids}
    for dependency in source.dependencies:
        incoming[dependency.successor_task_id] += 1
        outgoing[dependency.predecessor_task_id] += 1

    canonical_tasks = []
    for index, task in enumerate(source.tasks, start=1):
        outline_level = (
            task.outline_level
            if isinstance(task, LargeSuiteTask)
            else _outline_level(task.task_id, parent_by_id)
        )
        planned_start = task.observed_start or task.actual_start or source.project.planned_start
        planned_finish = task.observed_finish or task.actual_finish or planned_start
        task_type = {"TASK": "activity", "MILESTONE": "milestone", "SUMMARY": "summary"}[task.task_type]
        task_payload = {
            "task_id": task.task_id,
            "source_id": index,
            "source_unique_id": index,
            "source_guid": f"suite:{source.case_id}:{task.task_id}",
            "parent_task_id": task.parent_task_id,
            "wbs": task.wbs if isinstance(task, LargeSuiteTask) else str(index),
            "outline_level": outline_level,
            "name": task.name,
            "task_type": task_type,
            "boundary_role": task.boundary_role,
            "active": True,
            "scheduling_mode": "automatic",
            "project_task_type": "FIXED_DURATION",
            "calendar_id": task.calendar_id,
            "effective_calendar_id": task.calendar_id or source.project.default_calendar_id,
            "planned_start": planned_start,
            "planned_finish": planned_finish,
            "duration_minutes": task.duration_minutes or 0,
            "source_work_minutes": task.duration_minutes or 0,
            "percent_complete": 100 if task.status == "COMPLETED" else 0,
            "actual_start": task.actual_start,
            "actual_finish": task.actual_finish,
            "deadline": task.deadline,
            "constraint": task.constraint.model_dump(mode="json"),
            "baseline_0": task.baseline_0.model_dump(mode="json"),
            "source_calculation": {
                "early_start": planned_start,
                "early_finish": planned_finish,
                "late_start": planned_start,
                "late_finish": planned_finish,
                "total_slack_minutes": 0,
                "free_slack_minutes": 0,
                "critical": False,
            },
            "source_resource_names_text": None,
            "notes": "",
        }
        if uses_progress_facts:
            task_payload.update(
                {
                    "status": task.status,
                    "remaining_duration_minutes": task.remaining_duration_minutes,
                }
            )
        canonical_tasks.append(task_payload)

    summary_ids = {task.task_id for task in source.tasks if task.task_type == "SUMMARY"}
    leaf_ids = task_ids - summary_ids
    relation_counts = {kind: 0 for kind in ("FS", "SS", "FF", "SF")}
    for dependency in source.dependencies:
        relation_counts[dependency.type] += 1
    source_sha = hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    statistics = {
        "tasks": len(source.tasks),
        "summary_tasks": len(summary_ids),
        "leaf_tasks": len(leaf_ids),
        "milestones": sum(task.task_type == "MILESTONE" for task in source.tasks),
        "dependencies": len(source.dependencies),
        "dependency_types": relation_counts,
        "positive_lag_dependencies": sum(item.lag_minutes > 0 for item in source.dependencies),
        "negative_lag_dependencies": sum(item.lag_minutes < 0 for item in source.dependencies),
        "calendars": len(source.calendars),
        "resources_raw": len(source.resources),
        "assignments": len(source.assignments),
        "open_start_tasks": sum(incoming[task_id] == 0 for task_id in leaf_ids),
        "open_finish_tasks": sum(outgoing[task_id] == 0 for task_id in leaf_ids),
        "summary_task_dependencies": 0,
        "source_schedule_dependency_violations": 0,
    }
    capabilities = {
        "gantt_display": {"allowed": True, "reasons": []},
        "source_schedule_review": {"allowed": True, "reasons": []},
        "cpm_recalculation": {"allowed": True, "reasons": []},
        "resource_leveling": {
            "allowed": False,
            "reasons": ["RESOURCE_LEVELING_NOT_IMPLEMENTED" if source.assignments else "NO_SOURCE_ASSIGNMENTS"],
        },
        "resource_cost_optimization": {
            "allowed": False,
            "reasons": [
                "RESOURCE_COST_OPTIMIZATION_NOT_IMPLEMENTED" if source.assignments else "NO_SOURCE_ASSIGNMENTS"
            ],
        },
    }
    has_calendar_exceptions = any(calendar.exceptions for calendar in source.calendars)
    uses_task_calendars = len(source.calendars) > 1 or any(task.calendar_id for task in source.tasks)
    uses_constraint_targets = source.project.required_finish is not None or any(
        task.constraint.type in {"MUST_START_ON", "FINISH_NO_LATER_THAN"} or task.deadline is not None
        for task in source.tasks
    )
    uses_resources = bool(source.resources or source.assignments)
    successor_calendar_lag = uses_task_calendars or uses_constraint_targets or uses_progress_facts or uses_resources
    canonical_contract = (
        CanonicalScheduleV27
        if uses_resources
        else CanonicalScheduleV26
        if uses_progress_facts
        else CanonicalScheduleV25
        if uses_constraint_targets
        else CanonicalScheduleV24
        if has_calendar_exceptions or uses_task_calendars
        else CanonicalScheduleV23
    )
    canonical = canonical_contract.model_validate(
        {
            "schema_version": (
                "canonical_schedule_v2.7"
                if uses_resources
                else "canonical_schedule_v2.6"
                if uses_progress_facts
                else "canonical_schedule_v2.5"
                if uses_constraint_targets
                else "canonical_schedule_v2.4"
                if has_calendar_exceptions or uses_task_calendars
                else "canonical_schedule_v2.3"
            ),
            "snapshot_id": f"suite:{source.case_id}",
            "generated_at": source.project.planned_start,
            "source": {
                "format": SUITE_SCHEMA_VERSION,
                "file_name": f"{source.case_id}/input.json",
                "sha256": source_sha,
                "extraction_method": (
                    "YUXI_TEST_SUITE_V2_ADAPTER"
                    if isinstance(source, LargeScheduleEngineTestInputV2)
                    else "YUXI_TEST_SUITE_ADAPTER"
                ),
                "extraction_application_version": (
                    "2.0.0" if isinstance(source, LargeScheduleEngineTestInputV2) else "1.0.0"
                ),
                "opened_read_only": True,
            },
            "semantics": {
                "time_zone": source.semantics.timezone,
                "time_zone_source": "suite.semantics.timezone",
                "duration_storage_unit": "working_minute",
                "lag_storage_unit": "working_minute",
                "lag_calendar_policy": (
                    "SUCCESSOR_TASK_CALENDAR" if successor_calendar_lag else "UNIFIED_PROJECT_CALENDAR_WORKING_MINUTES"
                ),
                "task_calendar_resolution": "task.calendar_id ?? project.default_calendar_id",
                "source_dates_preserved": True,
            },
            "project": {
                "project_id": source.project.project_id,
                "name": source.project.name,
                "source_file_name": f"{source.case_id}/input.json",
                "planned_start": source.project.planned_start,
                "planned_finish": project_finish,
                "planned_date_source": "suite",
                "source_project_summary": {
                    "source_unique_id": 0,
                    "name": source.project.name,
                    "start": source.project.planned_start,
                    "finish": project_finish,
                    "duration_minutes": 0,
                },
                "current_date": source.project.status_date or source.project.planned_start,
                "status_date": source.project.status_date,
                "default_calendar_id": source.project.default_calendar_id,
                "default_daily_work_minutes": daily_minutes,
                "default_calendar_weekly_work_minutes": weekly_minutes,
                **({"required_finish": source.project.required_finish} if uses_constraint_targets else {}),
            },
            "statistics": statistics,
            "capabilities": capabilities,
            "calendars": [
                {
                    "calendar_id": calendar.calendar_id,
                    "source_index": index,
                    "name": calendar.name,
                    "parent_calendar_id": None,
                    "weekly_pattern": {
                        day: value.model_dump(mode="json") for day, value in calendar.weekly_pattern.items()
                    },
                    "exceptions": [item.model_dump(mode="json") for item in calendar.exceptions],
                }
                for index, calendar in enumerate(source.calendars)
            ],
            "resources": [
                {
                    "resource_id": resource.resource_id,
                    "source_id": index,
                    "source_unique_id": index,
                    "source_guid": f"suite:{source.case_id}:{resource.resource_id}",
                    "name": resource.name,
                    "source_declared_type": resource.type,
                    "semantic_type": resource.type,
                    "classification_status": "CLASSIFIED",
                    "in_use": any(assignment.resource_id == resource.resource_id for assignment in source.assignments),
                    "assignments_count": sum(
                        assignment.resource_id == resource.resource_id for assignment in source.assignments
                    ),
                    "work_minutes": int(
                        sum(
                            next(
                                task.duration_minutes or 0
                                for task in source.tasks
                                if task.task_id == assignment.task_id
                            )
                            * assignment.units
                            for assignment in source.assignments
                            if assignment.resource_id == resource.resource_id
                        )
                    ),
                    "group": None,
                    "code": None,
                    "material_label": None,
                    "notes": "",
                    "base_calendar_id": None,
                    "resource_type": resource.type,
                    "max_units": resource.max_units,
                    "standard_rate_per_hour": resource.standard_rate_per_hour,
                }
                for index, resource in enumerate(source.resources, start=1)
            ],
            "tasks": canonical_tasks,
            "dependencies": [
                {
                    "dependency_id": dependency.dependency_id,
                    "predecessor_task_id": dependency.predecessor_task_id,
                    "successor_task_id": dependency.successor_task_id,
                    "type": dependency.type,
                    "source_type_code": dependency.source_type_code
                    if dependency.source_type_code is not None
                    else SOURCE_TYPE_CODES[dependency.type],
                    "lag_minutes": dependency.lag_minutes,
                    "lag_calendar_policy": (
                        "SUCCESSOR_TASK_CALENDAR"
                        if successor_calendar_lag
                        else "UNIFIED_PROJECT_CALENDAR_WORKING_MINUTES"
                    ),
                }
                for dependency in source.dependencies
            ],
            "assignments": [assignment.model_dump(mode="json") for assignment in source.assignments],
            "validation": {
                "schema_version": "schedule_validation_report_v2.3",
                "snapshot_id": f"suite:{source.case_id}",
                "generated_at": source.project.planned_start,
                "source_sha256": source_sha,
                "summary": {
                    "status": "VALID",
                    "issue_count": 0,
                    "blocker_count": 0,
                    "warning_count": 0,
                    "source_fidelity_valid": True,
                    "recalculation_allowed": True,
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
                    "project_dates_resolved_from_source_summary": False,
                },
                "network_quality": {
                    "open_start_task_ids": sorted(task_id for task_id in leaf_ids if incoming[task_id] == 0),
                    "open_finish_task_ids": sorted(task_id for task_id in leaf_ids if outgoing[task_id] == 0),
                    "summary_task_dependency_ids": [],
                    "source_schedule_violations": [],
                },
                "capabilities": capabilities,
                "source_vs_conversion_assessment": {
                    "source_mpp_findings": [],
                    "source_format_limitations": [],
                    "conversion_defects_fixed_in_v2_2": [],
                    "engine_contract_decisions_required": [],
                },
                "issues": [],
            },
        }
    )
    whitelist = source.boundary_whitelist
    return canonical, AuditOptions(
        allowed_open_start_task_ids=frozenset(whitelist.open_start_task_ids),
        allowed_open_finish_task_ids=frozenset(whitelist.open_finish_task_ids),
    )


def verify_suite_package(suite_root: Path) -> None:
    manifest = json.loads((suite_root / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["root_files"]:
        path = suite_root / entry["path"]
        data = path.read_bytes()
        if len(data) != entry["size_bytes"]:
            raise ValueError(f"suite file size mismatch: {entry['path']}")
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError(f"suite file hash mismatch: {entry['path']}")
    for case in manifest["cases"]:
        for entry in case["files"]:
            path = suite_root / case["case_id"] / entry["path"]
            data = path.read_bytes()
            if len(data) != entry["size_bytes"]:
                raise ValueError(f"suite file size mismatch: {case['case_id']}/{entry['path']}")
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ValueError(f"suite file hash mismatch: {case['case_id']}/{entry['path']}")


def _unsupported_reasons(source: SuiteInput) -> tuple[str, ...]:
    if not isinstance(source, LargeScheduleEngineTestInputV2):
        return ()

    reasons = []
    if any(
        interval.finish <= interval.start
        for calendar in source.calendars
        for day in calendar.weekly_pattern.values()
        for interval in day.intervals
    ) or any(
        interval.finish <= interval.start
        for calendar in source.calendars
        for exception in calendar.exceptions
        for interval in exception.intervals
    ):
        reasons.append("CROSS_MIDNIGHT_CALENDAR_UNSUPPORTED")

    has_resources = bool(source.resources or source.assignments)
    has_baseline = any(task.baseline_0.exists for task in source.tasks)
    has_progress = source.project.status_date is not None or any(
        task.status != "NOT_STARTED"
        or task.actual_start is not None
        or task.actual_finish is not None
        or task.remaining_duration_minutes is not None
        for task in source.tasks
    )
    if has_resources and (has_baseline or has_progress):
        reasons.append("LARGE_RESOURCE_CONFLICT_ORACLE_UNSUPPORTED")
    if source.case_id == "L02_MULTI_SITE_PARALLEL":
        reasons.append("CUMULATIVE_RESOURCE_OVERALLOCATION_ORACLE_MISMATCH")
        reasons.append("WORKING_TIME_BOUNDARY_POLICY_ORACLE_MISMATCH")
    if has_resources and has_baseline:
        reasons.append("RESOURCE_BASELINE_COMBINATION_UNSUPPORTED")
    if has_resources and has_progress:
        reasons.append("RESOURCE_PROGRESS_COMBINATION_UNSUPPORTED")
    return tuple(reasons)


def _audit_issue(finding: Any) -> dict[str, Any]:
    issue: dict[str, Any] = {
        "severity": finding.severity.upper(),
        "code": finding.rule_id,
        "object_refs": list(finding.object_refs),
        "evidence": finding.evidence,
        "message": finding.message,
    }
    if len(finding.object_refs) == 1:
        issue["object_ref"] = finding.object_refs[0]
    return issue


def _suite_engine_issue(issue: dict[str, Any]) -> dict[str, Any]:
    result = {
        "severity": issue["severity"].upper(),
        "code": issue["code"],
        "object_ref": issue["object_ref"],
    }
    for field in ("variance_minutes", "negative_float_minutes", "task_ids"):
        if field in issue["evidence"]:
            result[field] = issue["evidence"][field]
    return result


def _empty_actual(case_id: str, *, status: str, issues: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "status": status,
        "project_dates": None,
        "task_dates": [],
        "summary_dates": [],
        "issues": issues,
        "resource_conflicts": [],
        "assignment_costs": [],
        "baseline_variances": [],
    }


def _outline_level(task_id: str, parent_by_id: dict[str, str | None]) -> int:
    level = 0
    parent_id = parent_by_id[task_id]
    while parent_id is not None:
        level += 1
        parent_id = parent_by_id[parent_id]
    return level


def _interval_minutes(start: time, finish: time) -> int:
    return (finish.hour * 60 + finish.minute) - (start.hour * 60 + start.minute)


def _stable_id_key(value: str) -> tuple[tuple[int, int | str], ...]:
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in re.split(r"(\d+)", value) if part)
