"""Shared facts and indexes for one Schedule audit run."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

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


def _calculate_capabilities(
    schedule: ScheduleSnapshot, network: DependencyNetwork
) -> dict[str, Capability]:
    cpm_reasons: list[str] = []
    summary_task_ids = {task.task_id for task in schedule.tasks if task.task_type == "summary"}
    if any(dep.predecessor_task_id == dep.successor_task_id for dep in schedule.dependencies):
        cpm_reasons.append("SELF_DEPENDENCY")
    if network.cyclic_components():
        cpm_reasons.append("DEPENDENCY_CYCLE")
    if any(dep.lag_minutes != 0 for dep in schedule.dependencies):
        cpm_reasons.append("LAG_CALENDAR_POLICY_UNSPECIFIED")
    if any(
        dep.predecessor_task_id in summary_task_ids or dep.successor_task_id in summary_task_ids
        for dep in schedule.dependencies
    ):
        cpm_reasons.append("SUMMARY_TASK_DEPENDENCIES")

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


def _calculate_date_checks(
    schedule: ScheduleSnapshot, tasks_by_id: dict[str, ScheduleTask]
) -> DependencyDateChecks:
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


def dependency_date_is_valid(
    relation_type: str, predecessor: ScheduleTask, successor: ScheduleTask
) -> bool:
    anchors = {
        "FS": (predecessor.planned_finish, successor.planned_start),
        "SS": (predecessor.planned_start, successor.planned_start),
        "FF": (predecessor.planned_finish, successor.planned_finish),
        "SF": (predecessor.planned_start, successor.planned_finish),
    }
    required, actual = anchors[relation_type]
    return actual >= required
