"""Shared raw-input validation before Canonical construction and scheduling."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PREFLIGHT_MESSAGES = {
    "ASSIGNMENT_ID_DUPLICATE": "Assignment ID 重复。",
    "ASSIGNMENT_RESOURCE_NOT_FOUND": "Assignment 引用了不存在的资源。",
    "ASSIGNMENT_TASK_NOT_FOUND": "Assignment 引用了不存在的任务。",
    "CALENDAR_ID_DUPLICATE": "日历 ID 重复。",
    "CALENDAR_INHERITANCE_CYCLE": "日历继承关系存在循环。",
    "CALENDAR_PARENT_NOT_FOUND": "日历引用了不存在的父日历。",
    "DEPENDENCY_CYCLE": "任务依赖网络存在循环。",
    "DEPENDENCY_ID_DUPLICATE": "依赖 ID 重复。",
    "DEPENDENCY_TASK_NOT_FOUND": "依赖引用了不存在的任务。",
    "MILESTONE_DURATION_NONZERO": "里程碑工期必须为零。",
    "PARENT_TASK_NOT_FOUND": "任务引用了不存在的父任务。",
    "PROJECT_CALENDAR_NOT_FOUND": "项目默认日历不存在。",
    "RESOURCE_ID_DUPLICATE": "资源 ID 重复。",
    "TASK_CALENDAR_NOT_FOUND": "任务引用了不存在的日历。",
    "TASK_ID_DUPLICATE": "任务 ID 重复。",
    "TASK_PARENT_CYCLE": "任务父子层级存在循环。",
}


@dataclass(frozen=True, slots=True)
class SchedulePreflightIssue:
    code: str
    object_ref: str | None = None
    object_refs: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def as_suite_issue(self) -> dict[str, Any]:
        issue: dict[str, Any] = {"severity": "BLOCKER", "code": self.code}
        if self.object_ref is not None:
            issue["object_ref"] = self.object_ref
        if self.object_refs:
            issue["object_refs"] = list(self.object_refs)
        issue.update(self.details)
        return issue

    def as_api_error(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "object_ref": self.object_ref,
            "object_refs": list(self.object_refs),
            "details": self.details,
            "message": PREFLIGHT_MESSAGES[self.code],
        }


def preflight_schedule_input(document: dict[str, Any]) -> tuple[SchedulePreflightIssue, ...]:
    """Collect independent reference/graph blockers without constructing Canonical."""
    tasks = _object_items(document, "tasks")
    calendars = _object_items(document, "calendars")
    dependencies = _object_items(document, "dependencies")
    resources = _object_items(document, "resources")
    assignments = _object_items(document, "assignments")
    issues: list[SchedulePreflightIssue] = []

    task_ids = _collect_ids(tasks, "task_id", "TASK_ID_DUPLICATE", issues)
    calendar_ids = _collect_ids(calendars, "calendar_id", "CALENDAR_ID_DUPLICATE", issues)
    _collect_ids(dependencies, "dependency_id", "DEPENDENCY_ID_DUPLICATE", issues)
    resource_ids = _collect_ids(resources, "resource_id", "RESOURCE_ID_DUPLICATE", issues)
    _collect_ids(assignments, "assignment_id", "ASSIGNMENT_ID_DUPLICATE", issues)

    project = document.get("project")
    if isinstance(project, dict):
        default_calendar_id = project.get("default_calendar_id")
        if isinstance(default_calendar_id, str) and default_calendar_id not in calendar_ids:
            issues.append(
                SchedulePreflightIssue(
                    "PROJECT_CALENDAR_NOT_FOUND",
                    object_ref=default_calendar_id,
                )
            )

    parent_edges: list[tuple[str, str]] = []

    for task in tasks:
        task_id = task.get("task_id")
        parent_task_id = task.get("parent_task_id")
        if (
            isinstance(task_id, str)
            and isinstance(parent_task_id, str)
            and parent_task_id not in task_ids
        ):
            issues.append(
                SchedulePreflightIssue(
                    "PARENT_TASK_NOT_FOUND",
                    object_ref=task_id,
                    details={"parent_task_id": parent_task_id},
                )
            )
        elif isinstance(task_id, str) and isinstance(parent_task_id, str):
            parent_edges.append((parent_task_id, task_id))
        calendar_id = task.get("calendar_id")
        if (
            isinstance(task_id, str)
            and isinstance(calendar_id, str)
            and calendar_id not in calendar_ids
        ):
            issues.append(
                SchedulePreflightIssue(
                    "TASK_CALENDAR_NOT_FOUND",
                    object_ref=task_id,
                    details={"calendar_id": calendar_id},
                )
            )
        duration_minutes = task.get("duration_minutes")
        if (
            isinstance(task_id, str)
            and task.get("task_type") in {"MILESTONE", "milestone"}
            and isinstance(duration_minutes, int)
            and not isinstance(duration_minutes, bool)
            and duration_minutes != 0
        ):
            issues.append(SchedulePreflightIssue("MILESTONE_DURATION_NONZERO", object_ref=task_id))

    for component in _cyclic_components(task_ids, parent_edges):
        issues.append(SchedulePreflightIssue("TASK_PARENT_CYCLE", object_refs=component))

    calendar_edges: list[tuple[str, str]] = []
    for calendar in calendars:
        calendar_id = calendar.get("calendar_id")
        parent_calendar_id = calendar.get("parent_calendar_id")
        if (
            isinstance(calendar_id, str)
            and isinstance(parent_calendar_id, str)
            and parent_calendar_id not in calendar_ids
        ):
            issues.append(
                SchedulePreflightIssue(
                    "CALENDAR_PARENT_NOT_FOUND",
                    object_ref=calendar_id,
                    details={"parent_calendar_id": parent_calendar_id},
                )
            )
        elif isinstance(calendar_id, str) and isinstance(parent_calendar_id, str):
            calendar_edges.append((parent_calendar_id, calendar_id))

    for component in _cyclic_components(calendar_ids, calendar_edges):
        issues.append(SchedulePreflightIssue("CALENDAR_INHERITANCE_CYCLE", object_refs=component))

    valid_edges: list[tuple[str, str]] = []
    for dependency in dependencies:
        predecessor_id = dependency.get("predecessor_task_id")
        successor_id = dependency.get("successor_task_id")
        if not isinstance(predecessor_id, str) or not isinstance(successor_id, str):
            continue
        if predecessor_id not in task_ids or successor_id not in task_ids:
            dependency_id = dependency.get("dependency_id")
            issues.append(
                SchedulePreflightIssue(
                    "DEPENDENCY_TASK_NOT_FOUND",
                    object_ref=dependency_id if isinstance(dependency_id, str) else None,
                )
            )
        else:
            valid_edges.append((predecessor_id, successor_id))

    if document.get("schema_version") in {
        "schedule_engine_test_input_v1",
        "canonical_schedule_v2.7",
        "canonical_schedule_v2.8",
    }:
        for assignment in assignments:
            assignment_id = assignment.get("assignment_id")
            object_ref = assignment_id if isinstance(assignment_id, str) else None
            task_id = assignment.get("task_id")
            resource_id = assignment.get("resource_id")
            if isinstance(task_id, str) and task_id not in task_ids:
                issues.append(
                    SchedulePreflightIssue(
                        "ASSIGNMENT_TASK_NOT_FOUND",
                        object_ref=object_ref,
                    )
                )
            if isinstance(resource_id, str) and resource_id not in resource_ids:
                issues.append(
                    SchedulePreflightIssue(
                        "ASSIGNMENT_RESOURCE_NOT_FOUND",
                        object_ref=object_ref,
                    )
                )

    for component in _cyclic_components(task_ids, valid_edges):
        issues.append(SchedulePreflightIssue("DEPENDENCY_CYCLE", object_refs=component))

    return tuple(sorted(issues, key=_issue_sort_key))


def _object_items(document: dict[str, Any], field_name: str) -> list[dict[str, Any]]:
    value = document.get(field_name)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _collect_ids(
    items: list[dict[str, Any]],
    field_name: str,
    duplicate_code: str,
    issues: list[SchedulePreflightIssue],
) -> set[str]:
    values = [item.get(field_name) for item in items if isinstance(item.get(field_name), str)]
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    for value in sorted(duplicates):
        issues.append(SchedulePreflightIssue(duplicate_code, object_ref=value))
    return set(values)


def _cyclic_components(task_ids: set[str], edges: list[tuple[str, str]]) -> tuple[tuple[str, ...], ...]:
    adjacency = {task_id: [] for task_id in task_ids}
    for predecessor_id, successor_id in edges:
        adjacency[predecessor_id].append(successor_id)
    for successors in adjacency.values():
        successors.sort()

    next_index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    low_links: dict[str, int] = {}
    cyclic: list[tuple[str, ...]] = []

    def visit(task_id: str) -> None:
        nonlocal next_index
        indices[task_id] = next_index
        low_links[task_id] = next_index
        next_index += 1
        stack.append(task_id)
        on_stack.add(task_id)

        for successor_id in adjacency[task_id]:
            if successor_id not in indices:
                visit(successor_id)
                low_links[task_id] = min(low_links[task_id], low_links[successor_id])
            elif successor_id in on_stack:
                low_links[task_id] = min(low_links[task_id], indices[successor_id])

        if low_links[task_id] != indices[task_id]:
            return
        component: list[str] = []
        while True:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == task_id:
                break
        has_self_loop = len(component) == 1 and component[0] in adjacency[component[0]]
        if len(component) > 1 or has_self_loop:
            cyclic.append(tuple(sorted(component)))

    for task_id in sorted(task_ids):
        if task_id not in indices:
            visit(task_id)
    return tuple(sorted(cyclic))


def _issue_sort_key(issue: SchedulePreflightIssue) -> tuple[str, str, tuple[str, ...]]:
    return issue.code, issue.object_ref or "", issue.object_refs
