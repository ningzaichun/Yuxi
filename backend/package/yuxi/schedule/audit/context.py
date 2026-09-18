"""Shared facts and indexes for one Schedule audit run."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from yuxi.schedule.contracts.audit import Capability, DependencyDateChecks
from yuxi.schedule.contracts.canonical_v2_2 import LAG_CALENDAR_POLICY_FROZEN
from yuxi.schedule.contracts.canonical_v2_4 import LAG_CALENDAR_POLICY_SUCCESSOR
from yuxi.schedule.domain.models import ScheduleDependency, ScheduleSnapshot, ScheduleTask
from yuxi.schedule.network import DependencyNetwork
from yuxi.schedule.work_calendar import (
    CalendarResolutionError,
    EffectiveWorkCalendar,
    build_effective_work_calendars,
    calendar_exception_conflicts,
)


@dataclass(frozen=True, slots=True)
class AuditOptions:
    allowed_open_start_task_ids: frozenset[str] = frozenset()
    allowed_open_finish_task_ids: frozenset[str] = frozenset()


@dataclass(slots=True)
class AuditContext:
    schedule: ScheduleSnapshot
    tasks_by_id: dict[str, ScheduleTask]
    network: DependencyNetwork
    statistics: dict[str, Any]
    capabilities: dict[str, Capability]
    dependency_date_checks: DependencyDateChecks
    lag_calendars: dict[str, EffectiveWorkCalendar] | None
    lag_unchecked_dependency_ids: tuple[str, ...]
    allowed_open_start_task_ids: frozenset[str]
    allowed_open_finish_task_ids: frozenset[str]

    @classmethod
    def build(cls, schedule: ScheduleSnapshot, options: AuditOptions | None = None) -> AuditContext:
        options = options or AuditOptions()
        tasks_by_id = {task.task_id: task for task in schedule.tasks}
        network = DependencyNetwork(set(tasks_by_id), schedule.dependencies)
        statistics = _calculate_statistics(schedule, network)
        capabilities = _calculate_capabilities(schedule, network)
        lag_calendars = _build_lag_calendars(schedule)
        date_checks, lag_unchecked_ids = _calculate_date_checks(schedule, tasks_by_id, lag_calendars)
        statistics["source_schedule_dependency_violations"] = date_checks.violation_count
        return cls(
            schedule,
            tasks_by_id,
            network,
            statistics,
            capabilities,
            date_checks,
            lag_calendars,
            lag_unchecked_ids,
            frozenset(
                task.task_id for task in schedule.tasks if task.boundary_role == "PROJECT_START"
            )
            | options.allowed_open_start_task_ids,
            frozenset(
                task.task_id for task in schedule.tasks if task.boundary_role == "PROJECT_FINISH"
            )
            | options.allowed_open_finish_task_ids,
        )


def _calculate_statistics(schedule: ScheduleSnapshot, network: DependencyNetwork) -> dict[str, Any]:
    leaf_tasks = [task for task in schedule.tasks if task.task_type != "summary"]
    summary_task_ids = {task.task_id for task in schedule.tasks if task.task_type == "summary"}
    relation_types = Counter(dependency.relation_type for dependency in schedule.dependencies)
    return {
        "tasks": len(schedule.tasks),
        "summary_tasks": len(schedule.tasks) - len(leaf_tasks),
        "leaf_tasks": len(leaf_tasks),
        "milestones": sum(task.duration_minutes == 0 for task in leaf_tasks),
        "dependencies": len(schedule.dependencies),
        "dependency_types": {kind: relation_types[kind] for kind in ("FS", "SS", "FF", "SF")},
        "positive_lag_dependencies": sum(dep.lag_minutes > 0 for dep in schedule.dependencies),
        "negative_lag_dependencies": sum(dep.lag_minutes < 0 for dep in schedule.dependencies),
        "calendars": len(schedule.calendars),
        "resources_raw": len(schedule.resources),
        "assignments": schedule.assignment_count,
        "open_start_tasks": sum(not network.incoming[task.task_id] for task in leaf_tasks),
        "open_finish_tasks": sum(not network.outgoing[task.task_id] for task in leaf_tasks),
        "summary_task_dependencies": sum(
            dep.predecessor_task_id in summary_task_ids or dep.successor_task_id in summary_task_ids
            for dep in schedule.dependencies
        ),
    }


def _calculate_capabilities(schedule: ScheduleSnapshot, network: DependencyNetwork) -> dict[str, Capability]:
    cpm_reasons: list[str] = []
    summary_task_ids = {task.task_id for task in schedule.tasks if task.task_type == "summary"}
    tasks_by_id = {task.task_id: task for task in schedule.tasks}
    if any(dep.predecessor_task_id == dep.successor_task_id for dep in schedule.dependencies):
        cpm_reasons.append("SELF_DEPENDENCY")
    if network.cyclic_components():
        cpm_reasons.append("DEPENDENCY_CYCLE")
    if any(
        dep.predecessor_task_id in summary_task_ids or dep.successor_task_id in summary_task_ids
        for dep in schedule.dependencies
    ):
        cpm_reasons.append("SUMMARY_TASK_DEPENDENCIES")
    if schedule.schema_version == "canonical_schedule_v2.8" and any(
        not tasks_by_id[dep.predecessor_task_id].active
        or not tasks_by_id[dep.successor_task_id].active
        for dep in schedule.dependencies
    ):
        cpm_reasons.append("INACTIVE_TASK_DEPENDENCIES")
    supports_multiple_calendars = (
        schedule.lag_calendar_policy == LAG_CALENDAR_POLICY_SUCCESSOR
        and bool(schedule.calendars)
        and all(calendar.exceptions_supported for calendar in schedule.calendars)
    )
    if len(schedule.calendars) != 1 and not supports_multiple_calendars:
        cpm_reasons.append("MULTIPLE_CALENDARS_UNSUPPORTED")
    else:
        calendars = schedule.calendars if supports_multiple_calendars else schedule.calendars[:1]
        if not supports_multiple_calendars and calendars and (
            calendars[0].calendar_id != schedule.default_calendar_id
            or calendars[0].parent_calendar_id
        ):
            cpm_reasons.append("CALENDAR_INHERITANCE_UNSUPPORTED")
        try:
            exception_time_zone = ZoneInfo(schedule.time_zone)
        except ZoneInfoNotFoundError:
            exception_time_zone = None
        for calendar in calendars:
            if calendar.has_exceptions and not calendar.exceptions_supported:
                cpm_reasons.append("CALENDAR_EXCEPTIONS_UNSUPPORTED")
            elif calendar.has_exceptions and exception_time_zone is not None:
                if calendar_exception_conflicts(calendar.exceptions, exception_time_zone):
                    cpm_reasons.append("CALENDAR_EXCEPTIONS_CONFLICT")
                if any(
                    exception.start_date.utcoffset()
                    != exception.start_date.astimezone(exception_time_zone).utcoffset()
                    or exception.finish_date.utcoffset()
                    != exception.finish_date.astimezone(exception_time_zone).utcoffset()
                    for exception in calendar.exceptions
                ):
                    cpm_reasons.append("CALENDAR_EXCEPTION_TIME_ZONE_MISMATCH")
            if not calendar.working_intervals_valid:
                cpm_reasons.append("CALENDAR_INTERVALS_INVALID")
        if supports_multiple_calendars and exception_time_zone is not None:
            try:
                build_effective_work_calendars(calendars, exception_time_zone)
            except CalendarResolutionError as exc:
                cpm_reasons.append(exc.code)
    activity_tasks = [
        task
        for task in schedule.tasks
        if task.task_type != "summary"
        and (task.active or schedule.schema_version != "canonical_schedule_v2.8")
    ]
    summary_child_count = {task_id: 0 for task_id in summary_task_ids}
    for task in schedule.tasks:
        if task.parent_task_id is None:
            continue
        parent = tasks_by_id[task.parent_task_id]
        if parent.task_type != "summary":
            cpm_reasons.append("TASK_PARENT_NOT_SUMMARY")
        else:
            summary_child_count[parent.task_id] += 1
        if task.outline_level <= parent.outline_level:
            cpm_reasons.append("TASK_OUTLINE_HIERARCHY_INVALID")
    if any(count == 0 for count in summary_child_count.values()):
        cpm_reasons.append("SUMMARY_WITHOUT_CHILDREN")
    if not activity_tasks:
        cpm_reasons.append("NO_ACTIVITY_TASKS")
    if schedule.schema_version != "canonical_schedule_v2.8" and any(
        not task.active for task in activity_tasks
    ):
        cpm_reasons.append("INACTIVE_TASK_UNSUPPORTED")
    if any(task.duration_minutes == 0 and task.task_type != "milestone" for task in activity_tasks):
        cpm_reasons.append("MILESTONE_UNSUPPORTED")
    if any(task.task_type == "milestone" and task.duration_minutes != 0 for task in activity_tasks):
        cpm_reasons.append("MILESTONE_DURATION_INVALID")
    if any(
        (task.constraint_type == "AS_SOON_AS_POSSIBLE" and task.constraint_date is not None)
        or (task.constraint_type != "AS_SOON_AS_POSSIBLE" and task.constraint_date is None)
        for task in activity_tasks
    ):
        cpm_reasons.append("TASK_CONSTRAINT_INVALID")
    if schedule.schema_version not in {
        "canonical_schedule_v2.6",
        "canonical_schedule_v2.8",
    } and any(
        task.percent_complete or task.actual_start is not None or task.actual_finish is not None
        for task in activity_tasks
    ):
        cpm_reasons.append("ACTUAL_PROGRESS_UNSUPPORTED")
    if supports_multiple_calendars:
        if any(
            task.effective_calendar_id != (task.calendar_id or schedule.default_calendar_id)
            for task in activity_tasks
        ):
            cpm_reasons.append("TASK_EFFECTIVE_CALENDAR_MISMATCH")
        if any(
            dependency.lag_calendar_policy != LAG_CALENDAR_POLICY_SUCCESSOR
            for dependency in schedule.dependencies
        ):
            cpm_reasons.append("DEPENDENCY_LAG_CALENDAR_POLICY_MISMATCH")
    elif any(
        task.calendar_id not in {None, schedule.default_calendar_id}
        or not schedule.calendars
        or task.effective_calendar_id != schedule.calendars[0].calendar_id
        for task in activity_tasks
    ):
        cpm_reasons.append("TASK_CALENDAR_UNSUPPORTED")
    try:
        project_time_zone = ZoneInfo(schedule.time_zone)
    except ZoneInfoNotFoundError:
        cpm_reasons.append("TIME_ZONE_UNSUPPORTED")
    else:
        if any(
            task.planned_start.utcoffset() != task.planned_start.astimezone(project_time_zone).utcoffset()
            or task.planned_finish.utcoffset() != task.planned_finish.astimezone(project_time_zone).utcoffset()
            for task in activity_tasks
        ):
            cpm_reasons.append("TASK_TIME_ZONE_MISMATCH")

    resource_unclassified = any(resource.semantic_type == "UNCLASSIFIED" for resource in schedule.resources)
    no_assignments = schedule.assignment_count == 0
    source_fidelity_invalid = not schedule.source_fidelity_valid
    return {
        "gantt_display": Capability(allowed=True),
        "source_schedule_review": Capability(
            allowed=not source_fidelity_invalid,
            reasons=["SOURCE_FIDELITY_INVALID"] if source_fidelity_invalid else [],
        ),
        "cpm_recalculation": Capability(allowed=not cpm_reasons, reasons=cpm_reasons),
        "resource_leveling": Capability(
            allowed=False,
            reasons=[
                "NO_SOURCE_ASSIGNMENTS"
                if no_assignments
                else "RESOURCE_LEVELING_NOT_IMPLEMENTED"
            ],
        ),
        "resource_cost_optimization": Capability(
            allowed=False,
            reasons=[
                reason
                for reason, applies in (
                    ("NO_SOURCE_ASSIGNMENTS", no_assignments),
                    ("RESOURCE_SEMANTICS_UNCLASSIFIED", resource_unclassified),
                    ("RESOURCE_COST_OPTIMIZATION_NOT_IMPLEMENTED", not no_assignments),
                )
                if applies
            ],
        ),
    }


def _build_lag_calendars(
    schedule: ScheduleSnapshot,
) -> dict[str, EffectiveWorkCalendar] | None:
    """Build the frozen unified or successor-task calendars used by lag review."""
    if schedule.lag_calendar_policy not in {
        LAG_CALENDAR_POLICY_FROZEN,
        LAG_CALENDAR_POLICY_SUCCESSOR,
    }:
        return None
    try:
        time_zone = ZoneInfo(schedule.time_zone)
    except ZoneInfoNotFoundError:
        return None
    if schedule.lag_calendar_policy == LAG_CALENDAR_POLICY_FROZEN:
        if len(schedule.calendars) != 1:
            return None
        calendar = schedule.calendars[0]
        if calendar.calendar_id != schedule.default_calendar_id or calendar.parent_calendar_id:
            return None
        if calendar.has_exceptions and not calendar.exceptions_supported:
            return None
        if not calendar.working_intervals_valid:
            return None
        if calendar_exception_conflicts(calendar.exceptions, time_zone):
            return None
        if _calendar_exception_time_zone_mismatch(calendar, time_zone):
            return None
        return {
            calendar.calendar_id: EffectiveWorkCalendar.from_weekday_intervals(
                calendar.working_intervals,
                time_zone,
                calendar.exceptions,
            )
        }

    if not schedule.calendars or not all(
        calendar.exceptions_supported and calendar.working_intervals_valid
        for calendar in schedule.calendars
    ):
        return None
    if any(
        calendar_exception_conflicts(calendar.exceptions, time_zone)
        or _calendar_exception_time_zone_mismatch(calendar, time_zone)
        for calendar in schedule.calendars
    ):
        return None
    try:
        return build_effective_work_calendars(schedule.calendars, time_zone)
    except CalendarResolutionError:
        return None


def _calendar_exception_time_zone_mismatch(calendar: Any, time_zone: ZoneInfo) -> bool:
    return any(
        exception.start_date.utcoffset()
        != exception.start_date.astimezone(time_zone).utcoffset()
        or exception.finish_date.utcoffset()
        != exception.finish_date.astimezone(time_zone).utcoffset()
        for exception in calendar.exceptions
    )


def _calculate_date_checks(
    schedule: ScheduleSnapshot,
    tasks_by_id: dict[str, ScheduleTask],
    lag_calendars: dict[str, EffectiveWorkCalendar] | None,
) -> tuple[DependencyDateChecks, tuple[str, ...]]:
    checked = 0
    skipped = 0
    violations = 0
    skipped_reasons: Counter[str] = Counter()
    unchecked_ids: list[str] = []
    for dependency in schedule.dependencies:
        predecessor = tasks_by_id[dependency.predecessor_task_id]
        successor = tasks_by_id[dependency.successor_task_id]
        if schedule.schema_version == "canonical_schedule_v2.8" and (
            not predecessor.active or not successor.active
        ):
            skipped += 1
            skipped_reasons["INACTIVE_TASK_DEPENDENCY_REQUIRES_DECISION"] += 1
            unchecked_ids.append(dependency.dependency_id)
            continue
        if dependency.lag_minutes == 0:
            checked += 1
            predecessor = tasks_by_id[dependency.predecessor_task_id]
            successor = tasks_by_id[dependency.successor_task_id]
            if not dependency_date_is_valid(dependency.relation_type, predecessor, successor):
                violations += 1
            continue
        lag_calendar = _lag_calendar_for_dependency(lag_calendars, dependency, tasks_by_id)
        reason = _lag_skip_reason(schedule, lag_calendar)
        if reason is not None:
            skipped += 1
            skipped_reasons[reason] += 1
            unchecked_ids.append(dependency.dependency_id)
            continue
        checked += 1
        if not lag_date_is_valid(lag_calendar, dependency, tasks_by_id):
            violations += 1
    return (
        DependencyDateChecks(
            checked=checked,
            skipped=skipped,
            violation_count=violations,
            skipped_reasons=dict(skipped_reasons) if skipped_reasons else {},
        ),
        tuple(sorted(unchecked_ids)),
    )


def _lag_skip_reason(
    schedule: ScheduleSnapshot,
    lag_calendar: EffectiveWorkCalendar | None,
) -> str | None:
    if schedule.lag_calendar_policy not in {
        LAG_CALENDAR_POLICY_FROZEN,
        LAG_CALENDAR_POLICY_SUCCESSOR,
    }:
        return "LAG_CALENDAR_POLICY_UNSPECIFIED"
    if lag_calendar is None:
        return "LAG_CALENDAR_UNSUPPORTED"
    return None


def _lag_calendar_for_dependency(
    lag_calendars: dict[str, EffectiveWorkCalendar] | None,
    dependency: ScheduleDependency,
    tasks_by_id: dict[str, ScheduleTask],
) -> EffectiveWorkCalendar | None:
    if lag_calendars is None:
        return None
    if dependency.lag_calendar_policy == LAG_CALENDAR_POLICY_SUCCESSOR:
        successor = tasks_by_id[dependency.successor_task_id]
        return lag_calendars.get(successor.effective_calendar_id)
    return next(iter(lag_calendars.values()), None)


def lag_date_is_valid(
    lag_calendar: EffectiveWorkCalendar,
    dependency: ScheduleDependency,
    tasks_by_id: dict[str, ScheduleTask],
) -> bool:
    """Check a signed-lag relationship under its frozen effective calendar."""
    predecessor = tasks_by_id[dependency.predecessor_task_id]
    successor = tasks_by_id[dependency.successor_task_id]
    anchors = {
        "FS": (predecessor.planned_finish, successor.planned_start),
        "SS": (predecessor.planned_start, successor.planned_start),
        "FF": (predecessor.planned_finish, successor.planned_finish),
        "SF": (predecessor.planned_start, successor.planned_finish),
    }
    required, actual = anchors[dependency.relation_type]
    return actual >= lag_calendar.shift_working_minutes(required, dependency.lag_minutes)


def dependency_date_is_valid(relation_type: str, predecessor: ScheduleTask, successor: ScheduleTask) -> bool:
    anchors = {
        "FS": (predecessor.planned_finish, successor.planned_start),
        "SS": (predecessor.planned_start, successor.planned_start),
        "FF": (predecessor.planned_finish, successor.planned_finish),
        "SF": (predecessor.planned_start, successor.planned_finish),
    }
    required, actual = anchors[relation_type]
    return actual >= required
