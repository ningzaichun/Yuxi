"""Immutable domain projections for deterministic schedule review."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from yuxi.schedule.contracts.audit import AuditResult


@dataclass(frozen=True, slots=True)
class ScheduleTask:
    task_id: str
    parent_task_id: str | None
    task_type: str
    planned_start: datetime
    planned_finish: datetime
    duration_minutes: int
    baseline_exists: bool
    active: bool
    scheduling_mode: str
    calendar_id: str | None
    effective_calendar_id: str
    constraint_type: str
    constraint_date: datetime | None
    percent_complete: int
    actual_start: datetime | None
    actual_finish: datetime | None


@dataclass(frozen=True, slots=True)
class ScheduleDependency:
    dependency_id: str
    predecessor_task_id: str
    successor_task_id: str
    relation_type: str
    lag_minutes: int
    lag_calendar_policy: str


@dataclass(frozen=True, slots=True)
class ScheduleCalendar:
    calendar_id: str
    name: str
    working_days: tuple[str, ...]
    parent_calendar_id: str | None
    has_exceptions: bool
    working_intervals_valid: bool


@dataclass(frozen=True, slots=True)
class ScheduleResource:
    resource_id: str
    semantic_type: str
    classification_status: str


@dataclass(frozen=True, slots=True)
class ScheduleSnapshot:
    project_id: str
    time_zone: str
    default_calendar_id: str
    status_date: datetime | None
    source_statistics: dict[str, Any]
    source_capabilities: dict[str, dict[str, Any]]
    lag_calendar_policy: str
    tasks: tuple[ScheduleTask, ...]
    dependencies: tuple[ScheduleDependency, ...]
    calendars: tuple[ScheduleCalendar, ...]
    resources: tuple[ScheduleResource, ...]
    assignment_count: int


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
