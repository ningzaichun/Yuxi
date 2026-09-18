"""Immutable domain projections for deterministic schedule review."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

from yuxi.schedule.contracts.audit import AuditResult


@dataclass(frozen=True, slots=True)
class ScheduleTask:
    task_id: str
    parent_task_id: str | None
    outline_level: int
    task_type: str
    planned_start: datetime
    planned_finish: datetime
    duration_minutes: int
    baseline_exists: bool
    baseline_start: datetime | None
    baseline_finish: datetime | None
    active: bool
    scheduling_mode: str
    calendar_id: str | None
    effective_calendar_id: str
    constraint_type: str
    constraint_date: datetime | None
    deadline: datetime | None
    percent_complete: int
    actual_start: datetime | None
    actual_finish: datetime | None
    status: str
    remaining_duration_minutes: int | None
    boundary_role: str | None = None


@dataclass(frozen=True, slots=True)
class ScheduleDependency:
    dependency_id: str
    predecessor_task_id: str
    successor_task_id: str
    relation_type: str
    lag_minutes: int
    lag_calendar_policy: str


@dataclass(frozen=True, slots=True)
class ScheduleCalendarException:
    exception_id: str
    start_date: datetime
    finish_date: datetime
    working: bool
    intervals: tuple[tuple[time, time], ...]


@dataclass(frozen=True, slots=True)
class ScheduleCalendar:
    calendar_id: str
    name: str
    working_days: tuple[str, ...]
    day_types: dict[str, str]
    working_intervals: dict[str, tuple[tuple[time, time], ...]]
    parent_calendar_id: str | None
    has_exceptions: bool
    exceptions_supported: bool
    exceptions: tuple[ScheduleCalendarException, ...]
    working_intervals_valid: bool


@dataclass(frozen=True, slots=True)
class ScheduleResource:
    resource_id: str
    semantic_type: str
    classification_status: str
    resource_type: str | None = None
    max_units: float | None = None
    standard_rate_per_hour: float | None = None


@dataclass(frozen=True, slots=True)
class ScheduleAssignment:
    assignment_id: str
    task_id: str
    resource_id: str
    units: float


@dataclass(frozen=True, slots=True)
class ScheduleSnapshot:
    schema_version: str
    project_id: str
    time_zone: str
    default_calendar_id: str
    status_date: datetime | None
    planned_finish: datetime
    required_finish: datetime | None
    source_statistics: dict[str, Any]
    source_capabilities: dict[str, dict[str, Any]]
    source_fidelity_valid: bool
    lag_calendar_policy: str
    tasks: tuple[ScheduleTask, ...]
    dependencies: tuple[ScheduleDependency, ...]
    calendars: tuple[ScheduleCalendar, ...]
    resources: tuple[ScheduleResource, ...]
    assignment_count: int
    assignments: tuple[ScheduleAssignment, ...] = ()


@dataclass(frozen=True, slots=True)
class AuditFinding:
    rule_id: str
    rule_version: str
    category: str
    severity: str
    object_refs: tuple[str, ...]
    evidence: dict[str, Any]
    message: str
    recommendation: str
    issue_key: str = ""


@dataclass(frozen=True, slots=True)
class AuditExecution:
    result: AuditResult
    findings: tuple[AuditFinding, ...]
