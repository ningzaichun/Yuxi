"""Project the external v2.2 contract into the audit domain."""

from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22
from yuxi.schedule.domain.models import (
    ScheduleCalendar,
    ScheduleDependency,
    ScheduleResource,
    ScheduleSnapshot,
    ScheduleTask,
)


def import_canonical_schedule_v2_2(source: CanonicalScheduleV22) -> ScheduleSnapshot:
    """Discard unneeded source text before rules receive the schedule."""
    return ScheduleSnapshot(
        project_id=source.project.project_id,
        status_date=source.project.status_date,
        source_statistics=source.statistics.model_dump(mode="json"),
        source_capabilities=source.capabilities.model_dump(mode="json"),
        lag_calendar_policy=source.semantics.lag_calendar_policy,
        tasks=tuple(
            ScheduleTask(
                task_id=task.task_id,
                parent_task_id=task.parent_task_id,
                task_type=task.task_type,
                planned_start=task.planned_start,
                planned_finish=task.planned_finish,
                duration_minutes=task.duration_minutes,
                baseline_exists=task.baseline_0.exists,
            )
            for task in source.tasks
        ),
        dependencies=tuple(
            ScheduleDependency(
                dependency_id=dependency.dependency_id,
                predecessor_task_id=dependency.predecessor_task_id,
                successor_task_id=dependency.successor_task_id,
                relation_type=dependency.type,
                lag_minutes=dependency.lag_minutes,
                lag_calendar_policy=dependency.lag_calendar_policy,
            )
            for dependency in source.dependencies
        ),
        calendars=tuple(
            ScheduleCalendar(
                calendar_id=calendar.calendar_id,
                name=calendar.name,
                working_days=tuple(
                    day
                    for day, value in calendar.weekly_pattern.model_dump().items()
                    if value["day_type"] == "WORKING" and value["intervals"]
                ),
            )
            for calendar in source.calendars
        ),
        resources=tuple(
            ScheduleResource(
                resource_id=resource.resource_id,
                semantic_type=resource.semantic_type,
                classification_status=resource.classification_status,
            )
            for resource in source.resources
        ),
        assignment_count=len(source.assignments),
    )
