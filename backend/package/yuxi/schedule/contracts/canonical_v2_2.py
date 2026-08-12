"""Canonical Schedule v2.2 input contract.

Only source facts represented by the current contract are modeled here. Audit
rules deliberately live outside these Pydantic models so valid-but-problematic
schedules can still be stored and reviewed.
"""

from __future__ import annotations

from datetime import time
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

MAX_TASKS = 5_000
MAX_DEPENDENCIES = 25_000


class ContractModel(BaseModel):
    """Reject unknown fields at the external contract boundary."""

    model_config = ConfigDict(extra="forbid")


class SourceDescriptor(ContractModel):
    format: str
    file_name: str
    sha256: str
    extraction_method: str
    extraction_application_version: str
    opened_read_only: bool


class ScheduleSemantics(ContractModel):
    time_zone: str
    time_zone_source: str
    duration_storage_unit: Literal["working_minute"]
    lag_storage_unit: Literal["working_minute"]
    lag_calendar_policy: Literal["UNSPECIFIED_REQUIRES_ENGINE_PROFILE"]
    task_calendar_resolution: str
    source_dates_preserved: bool


class SourceProjectSummary(ContractModel):
    source_unique_id: int
    name: str
    start: AwareDatetime
    finish: AwareDatetime
    duration_minutes: int = Field(ge=0)


class CanonicalProject(ContractModel):
    project_id: str
    name: str
    source_file_name: str
    planned_start: AwareDatetime
    planned_finish: AwareDatetime
    planned_date_source: str
    source_project_summary: SourceProjectSummary
    current_date: AwareDatetime
    status_date: AwareDatetime | None
    default_calendar_id: str
    default_daily_work_minutes: int = Field(gt=0)
    default_calendar_weekly_work_minutes: int = Field(gt=0)


class DependencyTypeStatistics(ContractModel):
    FS: int = Field(ge=0)
    SS: int = Field(ge=0)
    FF: int = Field(ge=0)
    SF: int = Field(ge=0)


class CanonicalStatistics(ContractModel):
    tasks: int = Field(ge=0)
    summary_tasks: int = Field(ge=0)
    leaf_tasks: int = Field(ge=0)
    milestones: int = Field(ge=0)
    dependencies: int = Field(ge=0)
    dependency_types: DependencyTypeStatistics
    positive_lag_dependencies: int = Field(ge=0)
    negative_lag_dependencies: int = Field(ge=0)
    calendars: int = Field(ge=0)
    resources_raw: int = Field(ge=0)
    assignments: int = Field(ge=0)
    open_start_tasks: int = Field(ge=0)
    open_finish_tasks: int = Field(ge=0)
    summary_task_dependencies: int = Field(ge=0)
    source_schedule_dependency_violations: int = Field(ge=0)


class SourceCapability(ContractModel):
    allowed: bool
    reasons: list[str]


class SourceCapabilities(ContractModel):
    gantt_display: SourceCapability
    source_schedule_review: SourceCapability
    cpm_recalculation: SourceCapability
    resource_leveling: SourceCapability
    resource_cost_optimization: SourceCapability


class WorkingInterval(ContractModel):
    start: time
    finish: time


class WorkingDay(ContractModel):
    day_type: str
    intervals: list[WorkingInterval]


class WeeklyPattern(ContractModel):
    SUNDAY: WorkingDay
    MONDAY: WorkingDay
    TUESDAY: WorkingDay
    WEDNESDAY: WorkingDay
    THURSDAY: WorkingDay
    FRIDAY: WorkingDay
    SATURDAY: WorkingDay


class CalendarException(ContractModel):
    """Preserve a v2.2 calendar exception without inventing future semantics."""

    model_config = ConfigDict(extra="allow")


class CanonicalCalendar(ContractModel):
    calendar_id: str
    source_index: int
    name: str
    parent_calendar_id: str | None
    weekly_pattern: WeeklyPattern
    exceptions: list[CalendarException]


class CanonicalResource(ContractModel):
    resource_id: str
    source_id: int
    source_unique_id: int
    source_guid: str
    name: str
    source_declared_type: str
    semantic_type: str
    classification_status: str
    in_use: bool
    assignments_count: int = Field(ge=0)
    work_minutes: int = Field(ge=0)
    group: str | None
    code: str | None
    material_label: str | None
    notes: str
    base_calendar_id: str | None


class TaskConstraint(ContractModel):
    type: Literal["AS_SOON_AS_POSSIBLE", "START_NO_EARLIER_THAN", "FINISH_NO_EARLIER_THAN"]
    date: AwareDatetime | None


class TaskBaseline(ContractModel):
    exists: bool
    start: AwareDatetime | None
    finish: AwareDatetime | None


class SourceCalculation(ContractModel):
    early_start: AwareDatetime
    early_finish: AwareDatetime
    late_start: AwareDatetime
    late_finish: AwareDatetime
    total_slack_minutes: int
    free_slack_minutes: int
    critical: bool


class CanonicalTask(ContractModel):
    task_id: str
    source_id: int
    source_unique_id: int
    source_guid: str
    parent_task_id: str | None
    wbs: str
    outline_level: int = Field(ge=0)
    name: str
    task_type: Literal["activity", "summary"]
    active: bool
    scheduling_mode: Literal["automatic", "manual"]
    project_task_type: Literal["FIXED_DURATION", "FIXED_UNITS"]
    calendar_id: str | None
    effective_calendar_id: str
    planned_start: AwareDatetime
    planned_finish: AwareDatetime
    duration_minutes: int = Field(ge=0)
    source_work_minutes: int = Field(ge=0)
    percent_complete: int = Field(ge=0, le=100)
    actual_start: AwareDatetime | None
    actual_finish: AwareDatetime | None
    deadline: AwareDatetime | None
    constraint: TaskConstraint
    baseline_0: TaskBaseline
    source_calculation: SourceCalculation
    source_resource_names_text: str | None
    notes: str


class CanonicalDependency(ContractModel):
    dependency_id: str
    predecessor_task_id: str
    successor_task_id: str
    type: Literal["FS", "SS", "FF", "SF"]
    source_type_code: int
    lag_minutes: int
    lag_calendar_policy: Literal["UNSPECIFIED_REQUIRES_ENGINE_PROFILE"]


class SourceValidationSummary(ContractModel):
    status: str
    issue_count: int = Field(ge=0)
    blocker_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    source_fidelity_valid: bool
    recalculation_allowed: bool
    display_allowed: bool


class SourceFidelityChecks(ContractModel):
    tasks_match_source: bool
    dependencies_match_source: bool
    calendars_match_source: bool
    resources_match_source: bool
    assignments_match_source: bool
    synthetic_project_summary_added: bool
    synthetic_resources_added: bool
    synthetic_assignments_added: bool
    project_dates_resolved_from_source_summary: bool


class SourceNetworkQuality(ContractModel):
    open_start_task_ids: list[str]
    open_finish_task_ids: list[str]
    summary_task_dependency_ids: list[str]
    source_schedule_violations: list[dict[str, Any]]


class SourceFinding(ContractModel):
    code: str
    severity: str
    object_ref: str
    object_refs: list[str] = Field(default_factory=list)


class ConversionDefect(ContractModel):
    code: str
    source: str
    previous_behavior: str
    corrected_behavior: str


class SourceVsConversionAssessment(ContractModel):
    source_mpp_findings: list[SourceFinding]
    source_format_limitations: list[SourceFinding]
    conversion_defects_fixed_in_v2_2: list[ConversionDefect]
    engine_contract_decisions_required: list[SourceFinding]


class SourceValidationIssue(ContractModel):
    issue_id: str
    code: str
    origin: str
    severity: str
    object_ref: str
    object_refs: list[str] = Field(default_factory=list)
    message: str


class SourceValidation(ContractModel):
    schema_version: str
    snapshot_id: str
    generated_at: AwareDatetime
    source_sha256: str
    summary: SourceValidationSummary
    source_fidelity_checks: SourceFidelityChecks
    network_quality: SourceNetworkQuality
    capabilities: SourceCapabilities
    source_vs_conversion_assessment: SourceVsConversionAssessment
    issues: list[SourceValidationIssue]


class CanonicalScheduleV22(ContractModel):
    schema_version: Literal["canonical_schedule_v2.2"]
    snapshot_id: str
    generated_at: AwareDatetime
    source: SourceDescriptor
    semantics: ScheduleSemantics
    project: CanonicalProject
    statistics: CanonicalStatistics
    capabilities: SourceCapabilities
    calendars: list[CanonicalCalendar]
    resources: list[CanonicalResource]
    tasks: list[CanonicalTask] = Field(max_length=MAX_TASKS)
    dependencies: list[CanonicalDependency] = Field(max_length=MAX_DEPENDENCIES)
    # Assignment semantics are not present in the v2.2 baseline. Preserve
    # source objects without fabricating a resource model, and keep all
    # resource optimization capabilities blocked until a later contract.
    assignments: list[dict[str, Any]]
    validation: SourceValidation

    @model_validator(mode="after")
    def validate_identity_and_references(self) -> CanonicalScheduleV22:
        task_ids = _unique_ids("task_id", self.tasks)
        _unique_ids("dependency_id", self.dependencies)
        calendar_ids = _unique_ids("calendar_id", self.calendars)
        _unique_ids("resource_id", self.resources)

        if self.project.default_calendar_id not in calendar_ids:
            raise ValueError("project.default_calendar_id references an unknown calendar")

        for calendar in self.calendars:
            if calendar.parent_calendar_id is not None and calendar.parent_calendar_id not in calendar_ids:
                raise ValueError(f"calendar {calendar.calendar_id} references an unknown parent calendar")

        for task in self.tasks:
            if task.parent_task_id is not None and task.parent_task_id not in task_ids:
                raise ValueError(f"task {task.task_id} references an unknown parent task")
            if task.effective_calendar_id not in calendar_ids:
                raise ValueError(f"task {task.task_id} references an unknown effective calendar")
            if task.calendar_id is not None and task.calendar_id not in calendar_ids:
                raise ValueError(f"task {task.task_id} references an unknown task calendar")

        for dependency in self.dependencies:
            if dependency.predecessor_task_id not in task_ids or dependency.successor_task_id not in task_ids:
                raise ValueError(f"dependency {dependency.dependency_id} references an unknown task")

        for resource in self.resources:
            if resource.base_calendar_id is not None and resource.base_calendar_id not in calendar_ids:
                raise ValueError(f"resource {resource.resource_id} references an unknown base calendar")

        _validate_parent_hierarchy(self.tasks, task_ids)
        return self


def _unique_ids(field_name: str, items: list[Any]) -> set[str]:
    values = [getattr(item, field_name) for item in items]
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {field_name}")
    return set(values)


def _validate_parent_hierarchy(tasks: list[CanonicalTask], task_ids: set[str]) -> None:
    parents = {task.task_id: task.parent_task_id for task in tasks}
    completed: set[str] = set()
    for task_id in task_ids:
        path: list[str] = []
        visiting: set[str] = set()
        current: str | None = task_id
        while current is not None and current not in completed:
            if current in visiting:
                raise ValueError(f"task parent hierarchy contains a cycle at {task_id}")
            path.append(current)
            visiting.add(current)
            current = parents[current]
        completed.update(path)
