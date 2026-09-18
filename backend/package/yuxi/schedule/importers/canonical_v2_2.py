"""Project the external v2.2 contract into the audit domain."""

from datetime import time

from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22
from yuxi.schedule.domain.models import (
    ScheduleCalendar,
    ScheduleCalendarException,
    ScheduleAssignment,
    ScheduleDependency,
    ScheduleResource,
    ScheduleSnapshot,
    ScheduleTask,
)


def import_canonical_schedule_v2_2(source: CanonicalScheduleV22) -> ScheduleSnapshot:
    """Discard unneeded source text before rules receive the schedule."""
    return _import_canonical_schedule(source)


def _import_canonical_schedule(source: CanonicalScheduleV22) -> ScheduleSnapshot:
    structured_exceptions = source.schema_version in {
        "canonical_schedule_v2.4",
        "canonical_schedule_v2.5",
        "canonical_schedule_v2.6",
        "canonical_schedule_v2.7",
        "canonical_schedule_v2.8",
    }
    return ScheduleSnapshot(
        schema_version=source.schema_version,
        project_id=source.project.project_id,
        time_zone=source.semantics.time_zone,
        default_calendar_id=source.project.default_calendar_id,
        status_date=source.project.status_date,
        planned_finish=source.project.planned_finish,
        required_finish=getattr(source.project, "required_finish", None),
        source_statistics=source.statistics.model_dump(mode="json"),
        source_capabilities=source.capabilities.model_dump(mode="json"),
        source_fidelity_valid=source.validation.summary.source_fidelity_valid,
        lag_calendar_policy=source.semantics.lag_calendar_policy,
        tasks=tuple(
            ScheduleTask(
                task_id=task.task_id,
                parent_task_id=task.parent_task_id,
                outline_level=task.outline_level,
                task_type=task.task_type,
                planned_start=task.planned_start,
                planned_finish=task.planned_finish,
                duration_minutes=task.duration_minutes,
                baseline_exists=task.baseline_0.exists,
                baseline_start=task.baseline_0.start,
                baseline_finish=task.baseline_0.finish,
                active=task.active,
                scheduling_mode=task.scheduling_mode,
                calendar_id=task.calendar_id,
                effective_calendar_id=task.effective_calendar_id,
                constraint_type=task.constraint.type,
                constraint_date=task.constraint.date,
                deadline=task.deadline,
                percent_complete=task.percent_complete,
                actual_start=task.actual_start,
                actual_finish=task.actual_finish,
                status=getattr(
                    task,
                    "status",
                    "COMPLETED" if task.percent_complete == 100 else "NOT_STARTED",
                ),
                remaining_duration_minutes=getattr(task, "remaining_duration_minutes", None),
                boundary_role=getattr(task, "boundary_role", None),
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
                day_types={day: value.day_type for day, value in calendar.weekly_pattern},
                working_intervals=_working_intervals(calendar.weekly_pattern),
                parent_calendar_id=calendar.parent_calendar_id,
                has_exceptions=bool(calendar.exceptions),
                exceptions_supported=structured_exceptions,
                exceptions=tuple(
                    ScheduleCalendarException(
                        exception_id=exception.exception_id,
                        start_date=exception.start_date,
                        finish_date=exception.finish_date,
                        working=exception.working,
                        intervals=tuple(
                            (interval.start, interval.finish) for interval in exception.intervals
                        ),
                    )
                    for exception in calendar.exceptions
                )
                if structured_exceptions
                else (),
                working_intervals_valid=_working_intervals_valid(
                    calendar.weekly_pattern,
                    allow_inherited=calendar.parent_calendar_id is not None,
                ),
            )
            for calendar in source.calendars
        ),
        resources=tuple(
            ScheduleResource(
                resource_id=resource.resource_id,
                semantic_type=resource.semantic_type,
                classification_status=resource.classification_status,
                resource_type=getattr(resource, "resource_type", None),
                max_units=float(resource.max_units) if hasattr(resource, "max_units") else None,
                standard_rate_per_hour=(
                    float(resource.standard_rate_per_hour)
                    if hasattr(resource, "standard_rate_per_hour")
                    else None
                ),
            )
            for resource in source.resources
        ),
        assignment_count=len(source.assignments),
        assignments=tuple(
            ScheduleAssignment(
                assignment_id=assignment.assignment_id,
                task_id=assignment.task_id,
                resource_id=assignment.resource_id,
                units=float(assignment.units),
            )
            for assignment in source.assignments
            if hasattr(assignment, "assignment_id")
        ),
    )


def _working_intervals(weekly_pattern) -> dict[str, tuple[tuple[time, time], ...]]:
    return {
        day: tuple((interval.start, interval.finish) for interval in value.intervals)
        for day, value in weekly_pattern
    }


def _working_intervals_valid(weekly_pattern, *, allow_inherited: bool = False) -> bool:
    has_interval = False
    for _, day in weekly_pattern:
        if day.day_type == "INHERITED":
            if not allow_inherited or day.intervals:
                return False
            continue
        if (day.day_type == "WORKING") != bool(day.intervals):
            return False
        previous_finish: time | None = None
        for interval in day.intervals:
            has_interval = True
            if interval.start >= interval.finish or (previous_finish is not None and interval.start < previous_finish):
                return False
            previous_finish = interval.finish
    return has_interval or allow_inherited
