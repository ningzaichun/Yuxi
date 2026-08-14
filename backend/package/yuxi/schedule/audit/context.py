"""Shared facts and indexes for one Schedule audit run."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from yuxi.schedule.contracts.audit import Capability, DependencyDateChecks
from yuxi.schedule.domain.models import ScheduleSnapshot, ScheduleTask
from yuxi.schedule.network import DependencyNetwork


@dataclass(slots=True)
class AuditContext:
    schedule: ScheduleSnapshot
    tasks_by_id: dict[str, ScheduleTask]
    network: DependencyNetwork
    statistics: dict[str, Any]
    capabilities: dict[str, Capability]
    dependency_date_checks: DependencyDateChecks

    @classmethod
    def build(cls, schedule: ScheduleSnapshot) -> AuditContext:
        tasks_by_id = {task.task_id: task for task in schedule.tasks}
        network = DependencyNetwork(set(tasks_by_id), schedule.dependencies)
        statistics = _calculate_statistics(schedule, network)
        capabilities = _calculate_capabilities(schedule, network)
        date_checks = _calculate_date_checks(schedule, tasks_by_id)
        statistics["source_schedule_dependency_violations"] = date_checks.violation_count
        return cls(schedule, tasks_by_id, network, statistics, capabilities, date_checks)


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
    if any(dep.predecessor_task_id == dep.successor_task_id for dep in schedule.dependencies):
        cpm_reasons.append("SELF_DEPENDENCY")
    if network.cyclic_components():
        cpm_reasons.append("DEPENDENCY_CYCLE")
    if any(dep.lag_minutes < 0 for dep in schedule.dependencies):
        cpm_reasons.append("NEGATIVE_DEPENDENCY_LAG_UNSUPPORTED")
    if any(
        dep.predecessor_task_id in summary_task_ids or dep.successor_task_id in summary_task_ids
        for dep in schedule.dependencies
    ):
        cpm_reasons.append("SUMMARY_TASK_DEPENDENCIES")
    if len(schedule.calendars) != 1:
        cpm_reasons.append("MULTIPLE_CALENDARS_UNSUPPORTED")
    else:
        calendar = schedule.calendars[0]
        if calendar.calendar_id != schedule.default_calendar_id or calendar.parent_calendar_id:
            cpm_reasons.append("CALENDAR_INHERITANCE_UNSUPPORTED")
        if calendar.has_exceptions:
            cpm_reasons.append("CALENDAR_EXCEPTIONS_UNSUPPORTED")
        if not calendar.working_intervals_valid:
            cpm_reasons.append("CALENDAR_INTERVALS_INVALID")
    activity_tasks = [task for task in schedule.tasks if task.task_type != "summary"]
    tasks_by_id = {task.task_id: task for task in schedule.tasks}
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
    if any(not task.active for task in activity_tasks):
        cpm_reasons.append("INACTIVE_TASK_UNSUPPORTED")
    if any(task.duration_minutes == 0 for task in activity_tasks):
        cpm_reasons.append("MILESTONE_UNSUPPORTED")
    if any(
        (task.constraint_type == "AS_SOON_AS_POSSIBLE" and task.constraint_date is not None)
        or (task.constraint_type != "AS_SOON_AS_POSSIBLE" and task.constraint_date is None)
        for task in activity_tasks
    ):
        cpm_reasons.append("TASK_CONSTRAINT_INVALID")
    if any(
        task.percent_complete or task.actual_start is not None or task.actual_finish is not None
        for task in activity_tasks
    ):
        cpm_reasons.append("ACTUAL_PROGRESS_UNSUPPORTED")
    if any(
        task.calendar_id is not None
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
    return {
        "gantt_display": Capability(allowed=True),
        "source_schedule_review": Capability(allowed=True),
        "cpm_recalculation": Capability(allowed=not cpm_reasons, reasons=cpm_reasons),
        "resource_leveling": Capability(
            allowed=not no_assignments,
            reasons=["NO_SOURCE_ASSIGNMENTS"] if no_assignments else [],
        ),
        "resource_cost_optimization": Capability(
            allowed=not no_assignments and not resource_unclassified,
            reasons=[
                reason
                for reason, applies in (
                    ("NO_SOURCE_ASSIGNMENTS", no_assignments),
                    ("RESOURCE_SEMANTICS_UNCLASSIFIED", resource_unclassified),
                )
                if applies
            ],
        ),
    }


def _calculate_date_checks(schedule: ScheduleSnapshot, tasks_by_id: dict[str, ScheduleTask]) -> DependencyDateChecks:
    checked = 0
    skipped = 0
    violations = 0
    for dependency in schedule.dependencies:
        if dependency.lag_minutes != 0:
            skipped += 1
            continue
        checked += 1
        predecessor = tasks_by_id[dependency.predecessor_task_id]
        successor = tasks_by_id[dependency.successor_task_id]
        if not dependency_date_is_valid(dependency.relation_type, predecessor, successor):
            violations += 1
    return DependencyDateChecks(
        checked=checked,
        skipped=skipped,
        violation_count=violations,
        skipped_reasons={"LAG_CALENDAR_POLICY_UNSPECIFIED": skipped} if skipped else {},
    )


def dependency_date_is_valid(relation_type: str, predecessor: ScheduleTask, successor: ScheduleTask) -> bool:
    anchors = {
        "FS": (predecessor.planned_finish, successor.planned_start),
        "SS": (predecessor.planned_start, successor.planned_start),
        "FF": (predecessor.planned_finish, successor.planned_finish),
        "SF": (predecessor.planned_start, successor.planned_finish),
    }
    required, actual = anchors[relation_type]
    return actual >= required
