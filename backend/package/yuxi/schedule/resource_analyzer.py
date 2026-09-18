"""Read-only resource overallocation and assignment cost analysis."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from itertools import combinations
from typing import Any

from yuxi.schedule.contracts.canonical_v2_7 import CanonicalScheduleV27
from yuxi.schedule.domain.models import ScheduleSnapshot


def analyze_resources(
    source: CanonicalScheduleV27,
    task_dates: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    dates_by_task = {
        item["task_id"]: (
            datetime.fromisoformat(item["early_start"]),
            datetime.fromisoformat(item["early_finish"]),
        )
        for item in task_dates
    }
    return _analyze(
        source.resources,
        source.assignments,
        {task.task_id: task for task in source.tasks},
        dates_by_task,
    )


def analyze_snapshot_resources(schedule: ScheduleSnapshot) -> dict[str, list[dict[str, Any]]]:
    return _analyze(
        schedule.resources,
        schedule.assignments,
        {task.task_id: task for task in schedule.tasks},
        {task.task_id: (task.planned_start, task.planned_finish) for task in schedule.tasks},
    )


def _analyze(resources, assignments, tasks_by_id, dates_by_task) -> dict[str, list[dict[str, Any]]]:
    assignments_by_resource = {
        resource.resource_id: [
            assignment
            for assignment in assignments
            if assignment.resource_id == resource.resource_id
            and tasks_by_id[assignment.task_id].active
        ]
        for resource in resources
    }

    conflicts: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for resource in sorted(resources, key=lambda item: item.resource_id):
        resource_assignments = assignments_by_resource[resource.resource_id]
        resource_conflicts = []
        for left, right in combinations(resource_assignments, 2):
            overlap_start = max(dates_by_task[left.task_id][0], dates_by_task[right.task_id][0])
            overlap_finish = min(dates_by_task[left.task_id][1], dates_by_task[right.task_id][1])
            if overlap_start >= overlap_finish:
                continue
            combined_units = Decimal(str(left.units)) + Decimal(str(right.units))
            max_units = Decimal(str(resource.max_units))
            if combined_units <= max_units:
                continue
            resource_conflicts.append(
                {
                    "resource_id": resource.resource_id,
                    "task_ids": sorted({left.task_id, right.task_id}),
                    "overlap_start": overlap_start.isoformat(),
                    "overlap_finish": overlap_finish.isoformat(),
                    "combined_units": float(combined_units),
                    "max_units": float(max_units),
                }
            )

        boundaries = sorted(
            {
                boundary
                for assignment in resource_assignments
                for boundary in dates_by_task[assignment.task_id]
            }
        )
        for overlap_start, overlap_finish in zip(boundaries, boundaries[1:], strict=False):
            active_assignments = [
                assignment
                for assignment in resource_assignments
                if dates_by_task[assignment.task_id][0] <= overlap_start
                and dates_by_task[assignment.task_id][1] >= overlap_finish
            ]
            if len(active_assignments) < 3:
                continue

            units = [Decimal(str(assignment.units)) for assignment in active_assignments]
            max_units = Decimal(str(resource.max_units))
            combined_units = sum(units, Decimal(0))
            if combined_units <= max_units or any(
                left_units + right_units > max_units
                for left_units, right_units in combinations(units, 2)
            ):
                continue

            resource_conflicts.append(
                {
                    "resource_id": resource.resource_id,
                    "task_ids": sorted({assignment.task_id for assignment in active_assignments}),
                    "overlap_start": overlap_start.isoformat(),
                    "overlap_finish": overlap_finish.isoformat(),
                    "combined_units": float(combined_units),
                    "max_units": float(max_units),
                }
            )

        resource_conflicts.sort(
            key=lambda item: (
                item["task_ids"],
                item["overlap_start"],
                item["overlap_finish"],
                item["combined_units"],
            )
        )

        conflicts.extend(resource_conflicts)
        for conflict in resource_conflicts:
            issues.append(
                {
                    "severity": "warning",
                    "code": "RESOURCE_OVERALLOCATION",
                    "object_ref": resource.resource_id,
                    "object_refs": [resource.resource_id],
                    "evidence": {
                        "task_ids": conflict["task_ids"],
                        "overlap_start": conflict["overlap_start"],
                        "overlap_finish": conflict["overlap_finish"],
                        "combined_units": conflict["combined_units"],
                        "max_units": conflict["max_units"],
                    },
                    "message": "资源在重叠任务区间内的分配单位超过最大可用单位。",
                }
            )

    resources_by_id = {resource.resource_id: resource for resource in resources}
    assignment_costs = []
    for assignment in sorted(
        (
            item
            for item in assignments
            if tasks_by_id[item.task_id].active
        ),
        key=lambda item: item.assignment_id,
    ):
        task = tasks_by_id[assignment.task_id]
        resource = resources_by_id[assignment.resource_id]
        work_minutes = Decimal(task.duration_minutes) * Decimal(str(assignment.units))
        cost = (work_minutes / Decimal(60) * Decimal(str(resource.standard_rate_per_hour))).quantize(
            Decimal("0.01")
        )
        assignment_costs.append(
            {
                "assignment_id": assignment.assignment_id,
                "work_minutes": _work_minutes_number(work_minutes),
                "cost": float(cost),
            }
        )

    return {
        "issues": issues,
        "resource_conflicts": conflicts,
        "assignment_costs": assignment_costs,
    }


def _work_minutes_number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)
