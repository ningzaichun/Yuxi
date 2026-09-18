"""Deterministic forward scheduling for the supported S3/S4 profile."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from yuxi.schedule.contracts.canonical import CanonicalSchedule
from yuxi.schedule.contracts.canonical_v2_2 import CanonicalCalendar
from yuxi.schedule.contracts.canonical_v2_4 import LAG_CALENDAR_POLICY_SUCCESSOR
from yuxi.schedule.contracts.canonical_v2_7 import CanonicalScheduleV27
from yuxi.schedule.resource_analyzer import analyze_resources
from yuxi.schedule.work_calendar import (
    CalendarResolutionError,
    EffectiveWorkCalendar,
    build_effective_work_calendars,
    calendar_exception_conflicts,
)

ENGINE_PROFILE_ID = "yuxi-forward-unified-calendar-fs-ss-ff-sf-positive-lag-snet-fnet-manual-v5"
ENGINE_VERSION = "5.0.0"
SUMMARY_ROLLUP_ENGINE_PROFILE_ID = (
    "yuxi-forward-unified-calendar-fs-ss-ff-sf-positive-lag-snet-fnet-manual-summary-rollup-v6"
)
SUMMARY_ROLLUP_ENGINE_VERSION = "6.0.0"
REVERSE_FLOAT_ENGINE_PROFILE_ID = (
    "yuxi-forward-unified-calendar-fs-ss-ff-sf-positive-lag-snet-fnet-manual-summary-rollup-"
    "reverse-float-critical-v7"
)
REVERSE_FLOAT_ENGINE_VERSION = "7.0.0"
MILESTONE_ENGINE_PROFILE_ID = (
    "yuxi-forward-unified-calendar-fs-ss-ff-sf-positive-lag-snet-fnet-manual-summary-rollup-"
    "reverse-float-critical-milestone-v8"
)
MILESTONE_ENGINE_VERSION = "8.0.0"
NEGATIVE_LAG_ENGINE_PROFILE_ID = (
    "yuxi-forward-unified-calendar-fs-ss-ff-sf-signed-lag-snet-fnet-manual-summary-rollup-"
    "reverse-float-critical-milestone-v9"
)
NEGATIVE_LAG_ENGINE_VERSION = "9.0.0"
CALENDAR_EXCEPTIONS_ENGINE_PROFILE_ID = (
    "yuxi-forward-unified-calendar-fs-ss-ff-sf-signed-lag-snet-fnet-manual-summary-rollup-"
    "reverse-float-critical-milestone-calendar-exceptions-v10"
)
CALENDAR_EXCEPTIONS_ENGINE_VERSION = "10.0.0"
MULTI_CALENDAR_ENGINE_PROFILE_ID = (
    "yuxi-forward-fs-ss-ff-sf-signed-lag-snet-fnet-manual-summary-rollup-reverse-float-critical-"
    "milestone-calendar-exceptions-multi-task-calendar-successor-lag-v11"
)
MULTI_CALENDAR_ENGINE_VERSION = "11.0.0"
CONSTRAINTS_ENGINE_PROFILE_ID = (
    "yuxi-forward-fs-ss-ff-sf-signed-lag-snet-fnet-mso-fnlt-deadline-required-finish-"
    "manual-summary-rollup-reverse-float-critical-milestone-calendar-exceptions-"
    "multi-task-calendar-successor-lag-v12"
)
CONSTRAINTS_ENGINE_VERSION = "12.0.0"
COMPLETED_PROGRESS_ENGINE_PROFILE_ID = (
    "yuxi-forward-fs-ss-ff-sf-signed-lag-snet-fnet-mso-fnlt-deadline-required-finish-"
    "status-date-completed-baseline-manual-summary-rollup-reverse-float-critical-"
    "milestone-calendar-exceptions-multi-task-calendar-successor-lag-v13"
)
COMPLETED_PROGRESS_ENGINE_VERSION = "13.0.0"
IN_PROGRESS_ENGINE_PROFILE_ID = (
    "yuxi-forward-fs-ss-ff-sf-signed-lag-snet-fnet-mso-fnlt-deadline-required-finish-"
    "status-date-completed-in-progress-remaining-work-baseline-manual-summary-rollup-"
    "reverse-float-critical-milestone-calendar-exceptions-multi-task-calendar-"
    "successor-lag-v14"
)
IN_PROGRESS_ENGINE_VERSION = "14.0.0"
RESOURCE_ANALYSIS_ENGINE_PROFILE_ID = (
    "yuxi-forward-fs-ss-ff-sf-signed-lag-snet-fnet-mso-fnlt-deadline-required-finish-"
    "manual-summary-rollup-reverse-float-critical-milestone-calendar-exceptions-"
    "multi-task-calendar-successor-lag-resource-pair-and-cumulative-conflict-analysis-v3"
)
RESOURCE_ANALYSIS_ENGINE_VERSION = "3.0.0"
INACTIVE_ENGINE_PROFILE_ID = (
    "yuxi-forward-fs-ss-ff-sf-signed-lag-snet-fnet-mso-fnlt-deadline-required-finish-"
    "manual-summary-rollup-reverse-float-critical-milestone-calendar-exceptions-"
    "multi-task-calendar-successor-lag-resource-pair-and-cumulative-conflict-analysis-inactive-exclusion-v17"
)
INACTIVE_ENGINE_VERSION = "17.0.0"

# Compatibility export for pre-v10 callers; implementation now lives in work_calendar.py.
UnifiedWorkCalendar = EffectiveWorkCalendar


@dataclass(frozen=True, slots=True)
class EngineBlocker:
    code: str
    object_refs: tuple[str, ...]
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "object_refs": list(self.object_refs),
            "message": self.message,
        }


def recalculation_profile_for(source: CanonicalSchedule) -> str:
    if source.schema_version == "canonical_schedule_v2.8":
        return INACTIVE_ENGINE_PROFILE_ID
    if source.schema_version == "canonical_schedule_v2.7":
        return RESOURCE_ANALYSIS_ENGINE_PROFILE_ID
    if source.schema_version == "canonical_schedule_v2.6":
        if any(task.status == "IN_PROGRESS" for task in source.tasks):
            return IN_PROGRESS_ENGINE_PROFILE_ID
        return COMPLETED_PROGRESS_ENGINE_PROFILE_ID
    if source.schema_version == "canonical_schedule_v2.5":
        return CONSTRAINTS_ENGINE_PROFILE_ID
    if source.schema_version == "canonical_schedule_v2.4":
        if (
            source.semantics.lag_calendar_policy == LAG_CALENDAR_POLICY_SUCCESSOR
            or len(source.calendars) != 1
            or any(calendar.parent_calendar_id for calendar in source.calendars)
            or any(
                task.task_type != "summary"
                and task.effective_calendar_id != source.project.default_calendar_id
                for task in source.tasks
            )
        ):
            return MULTI_CALENDAR_ENGINE_PROFILE_ID
        return CALENDAR_EXCEPTIONS_ENGINE_PROFILE_ID
    if any(dependency.lag_minutes < 0 for dependency in source.dependencies):
        return NEGATIVE_LAG_ENGINE_PROFILE_ID
    if source.schema_version == "canonical_schedule_v2.3":
        return MILESTONE_ENGINE_PROFILE_ID
    return REVERSE_FLOAT_ENGINE_PROFILE_ID


def calculate_minimal_forward_schedule(
    source: CanonicalSchedule,
    *,
    locked_task_ids: set[str] | None = None,
    engine_profile_id: str = ENGINE_PROFILE_ID,
) -> dict[str, Any]:
    """Calculate early dates or return explicit blockers without approximating input."""
    if engine_profile_id == ENGINE_PROFILE_ID:
        engine_version = ENGINE_VERSION
    elif engine_profile_id == SUMMARY_ROLLUP_ENGINE_PROFILE_ID:
        engine_version = SUMMARY_ROLLUP_ENGINE_VERSION
    elif engine_profile_id == REVERSE_FLOAT_ENGINE_PROFILE_ID:
        engine_version = REVERSE_FLOAT_ENGINE_VERSION
    elif engine_profile_id == MILESTONE_ENGINE_PROFILE_ID:
        engine_version = MILESTONE_ENGINE_VERSION
    elif engine_profile_id == NEGATIVE_LAG_ENGINE_PROFILE_ID:
        engine_version = NEGATIVE_LAG_ENGINE_VERSION
    elif engine_profile_id == CALENDAR_EXCEPTIONS_ENGINE_PROFILE_ID:
        engine_version = CALENDAR_EXCEPTIONS_ENGINE_VERSION
    elif engine_profile_id == MULTI_CALENDAR_ENGINE_PROFILE_ID:
        engine_version = MULTI_CALENDAR_ENGINE_VERSION
    elif engine_profile_id == CONSTRAINTS_ENGINE_PROFILE_ID:
        engine_version = CONSTRAINTS_ENGINE_VERSION
    elif engine_profile_id == COMPLETED_PROGRESS_ENGINE_PROFILE_ID:
        engine_version = COMPLETED_PROGRESS_ENGINE_VERSION
    elif engine_profile_id == IN_PROGRESS_ENGINE_PROFILE_ID:
        engine_version = IN_PROGRESS_ENGINE_VERSION
    elif engine_profile_id == RESOURCE_ANALYSIS_ENGINE_PROFILE_ID:
        engine_version = RESOURCE_ANALYSIS_ENGINE_VERSION
    elif engine_profile_id == INACTIVE_ENGINE_PROFILE_ID:
        engine_version = INACTIVE_ENGINE_VERSION
    else:
        raise ValueError(f"unsupported schedule engine profile: {engine_profile_id}")

    include_summary_rollup = engine_profile_id in {
        SUMMARY_ROLLUP_ENGINE_PROFILE_ID,
        REVERSE_FLOAT_ENGINE_PROFILE_ID,
        MILESTONE_ENGINE_PROFILE_ID,
        NEGATIVE_LAG_ENGINE_PROFILE_ID,
        CALENDAR_EXCEPTIONS_ENGINE_PROFILE_ID,
        MULTI_CALENDAR_ENGINE_PROFILE_ID,
        CONSTRAINTS_ENGINE_PROFILE_ID,
        COMPLETED_PROGRESS_ENGINE_PROFILE_ID,
        IN_PROGRESS_ENGINE_PROFILE_ID,
        RESOURCE_ANALYSIS_ENGINE_PROFILE_ID,
        INACTIVE_ENGINE_PROFILE_ID,
    }
    include_reverse_float = engine_profile_id in {
        REVERSE_FLOAT_ENGINE_PROFILE_ID,
        MILESTONE_ENGINE_PROFILE_ID,
        NEGATIVE_LAG_ENGINE_PROFILE_ID,
        CALENDAR_EXCEPTIONS_ENGINE_PROFILE_ID,
        MULTI_CALENDAR_ENGINE_PROFILE_ID,
        CONSTRAINTS_ENGINE_PROFILE_ID,
        COMPLETED_PROGRESS_ENGINE_PROFILE_ID,
        IN_PROGRESS_ENGINE_PROFILE_ID,
        RESOURCE_ANALYSIS_ENGINE_PROFILE_ID,
        INACTIVE_ENGINE_PROFILE_ID,
    }
    include_milestones = engine_profile_id in {
        MILESTONE_ENGINE_PROFILE_ID,
        NEGATIVE_LAG_ENGINE_PROFILE_ID,
        CALENDAR_EXCEPTIONS_ENGINE_PROFILE_ID,
        MULTI_CALENDAR_ENGINE_PROFILE_ID,
        CONSTRAINTS_ENGINE_PROFILE_ID,
        COMPLETED_PROGRESS_ENGINE_PROFILE_ID,
        IN_PROGRESS_ENGINE_PROFILE_ID,
        RESOURCE_ANALYSIS_ENGINE_PROFILE_ID,
        INACTIVE_ENGINE_PROFILE_ID,
    }
    include_negative_lag = engine_profile_id in {
        NEGATIVE_LAG_ENGINE_PROFILE_ID,
        CALENDAR_EXCEPTIONS_ENGINE_PROFILE_ID,
        MULTI_CALENDAR_ENGINE_PROFILE_ID,
        CONSTRAINTS_ENGINE_PROFILE_ID,
        COMPLETED_PROGRESS_ENGINE_PROFILE_ID,
        IN_PROGRESS_ENGINE_PROFILE_ID,
        RESOURCE_ANALYSIS_ENGINE_PROFILE_ID,
        INACTIVE_ENGINE_PROFILE_ID,
    }
    include_calendar_exceptions = engine_profile_id in {
        CALENDAR_EXCEPTIONS_ENGINE_PROFILE_ID,
        MULTI_CALENDAR_ENGINE_PROFILE_ID,
        CONSTRAINTS_ENGINE_PROFILE_ID,
        COMPLETED_PROGRESS_ENGINE_PROFILE_ID,
        IN_PROGRESS_ENGINE_PROFILE_ID,
        RESOURCE_ANALYSIS_ENGINE_PROFILE_ID,
        INACTIVE_ENGINE_PROFILE_ID,
    }
    include_multiple_calendars = engine_profile_id in {
        MULTI_CALENDAR_ENGINE_PROFILE_ID,
        CONSTRAINTS_ENGINE_PROFILE_ID,
        COMPLETED_PROGRESS_ENGINE_PROFILE_ID,
        IN_PROGRESS_ENGINE_PROFILE_ID,
        RESOURCE_ANALYSIS_ENGINE_PROFILE_ID,
        INACTIVE_ENGINE_PROFILE_ID,
    }
    include_hard_constraints = engine_profile_id in {
        CONSTRAINTS_ENGINE_PROFILE_ID,
        COMPLETED_PROGRESS_ENGINE_PROFILE_ID,
        IN_PROGRESS_ENGINE_PROFILE_ID,
        RESOURCE_ANALYSIS_ENGINE_PROFILE_ID,
        INACTIVE_ENGINE_PROFILE_ID,
    }
    include_completed_progress = engine_profile_id in {
        COMPLETED_PROGRESS_ENGINE_PROFILE_ID,
        IN_PROGRESS_ENGINE_PROFILE_ID,
    }
    include_in_progress = engine_profile_id == IN_PROGRESS_ENGINE_PROFILE_ID
    include_inactive_exclusion = engine_profile_id == INACTIVE_ENGINE_PROFILE_ID
    blockers = _support_blockers(
        source,
        include_summary_rollup=include_summary_rollup,
        include_milestones=include_milestones,
        include_negative_lag=include_negative_lag,
        include_calendar_exceptions=include_calendar_exceptions,
        include_multiple_calendars=include_multiple_calendars,
        include_hard_constraints=include_hard_constraints,
        include_completed_progress=include_completed_progress,
        include_in_progress=include_in_progress,
        include_inactive_exclusion=include_inactive_exclusion,
    )
    if blockers:
        return _blocked_result(blockers, engine_profile_id, engine_version)

    locked_task_ids = locked_task_ids or set()
    time_zone = ZoneInfo(source.semantics.time_zone)
    if include_multiple_calendars:
        work_calendars = build_effective_work_calendars(source.calendars, time_zone)
    else:
        calendar = source.calendars[0]
        work_calendars = {calendar.calendar_id: EffectiveWorkCalendar(calendar, time_zone)}
    default_work_calendar = work_calendars[source.project.default_calendar_id]
    schedulable_types = {"activity", "milestone"} if include_milestones else {"activity"}
    activities = {
        task.task_id: task
        for task in source.tasks
        if task.task_type in schedulable_types
        and (task.active or not include_inactive_exclusion)
    }
    unknown_locked_task_ids = locked_task_ids - activities.keys()
    if unknown_locked_task_ids:
        return _blocked_result(
            [
                EngineBlocker(
                    "LOCKED_TASK_UNKNOWN",
                    tuple(sorted(unknown_locked_task_ids)),
                    "locked_task_ids 必须引用当前来源中的活动任务",
                )
            ],
            engine_profile_id,
            engine_version,
        )
    incoming = {task_id: [] for task_id in activities}
    outgoing = {task_id: [] for task_id in activities}
    indegree = {task_id: 0 for task_id in activities}
    for dependency in source.dependencies:
        incoming[dependency.successor_task_id].append(
            (dependency.predecessor_task_id, dependency.type, dependency.lag_minutes)
        )
        outgoing[dependency.predecessor_task_id].append(
            (dependency.successor_task_id, dependency.type, dependency.lag_minutes)
        )
        indegree[dependency.successor_task_id] += 1

    ready = sorted(task_id for task_id, count in indegree.items() if count == 0)
    order: list[str] = []
    while ready:
        task_id = ready.pop(0)
        order.append(task_id)
        for successor_id, _, _ in sorted(outgoing[task_id]):
            indegree[successor_id] -= 1
            if indegree[successor_id] == 0:
                ready.append(successor_id)
                ready.sort()
    if len(order) != len(activities):
        return _blocked_result(
            [EngineBlocker("DEPENDENCY_CYCLE", tuple(sorted(activities)), "任务依赖网络存在环路")],
            engine_profile_id,
            engine_version,
        )

    project_start = default_work_calendar.next_working_instant(source.project.planned_start)
    calculated: dict[str, tuple[datetime, datetime]] = {}
    remaining_starts: dict[str, datetime] = {}
    conflicts = []
    constraint_issues = []
    for task_id in order:
        task = activities[task_id]
        work_calendar = work_calendars[task.effective_calendar_id]
        predecessor_bounds = []
        finish_bounds = []
        for predecessor_id, dependency_type, lag_minutes in incoming[task_id]:
            predecessor_start, predecessor_finish = calculated[predecessor_id]
            anchor = predecessor_finish if dependency_type in {"FS", "FF"} else predecessor_start
            bound = work_calendar.shift_working_minutes(anchor, lag_minutes) if lag_minutes else anchor
            if dependency_type in {"FF", "SF"}:
                finish_bounds.append(bound)
                if task.duration_minutes:
                    bound = work_calendar.subtract_working_minutes(bound, task.duration_minutes)
            predecessor_bounds.append(bound)
        constraint = task.constraint
        if constraint.type == "START_NO_EARLIER_THAN":
            predecessor_bounds.append(constraint.date)
        elif constraint.type == "FINISH_NO_EARLIER_THAN":
            finish_bounds.append(constraint.date)
            predecessor_bounds.append(
                work_calendar.subtract_working_minutes(constraint.date, task.duration_minutes)
            )
        earliest_start = max(
            [
                project_start,
                *predecessor_bounds,
                *([task.planned_start] if task.scheduling_mode == "manual" else []),
            ]
        )
        required_start = (
            earliest_start if task.task_type == "milestone" else work_calendar.next_working_instant(earliest_start)
        )
        required_finish = (
            required_start
            if task.task_type == "milestone"
            else max(
                work_calendar.add_working_minutes(required_start, task.duration_minutes),
                max(finish_bounds, default=required_start),
            )
        )
        if include_completed_progress and task.status == "COMPLETED":
            start = task.actual_start
            finish = task.actual_finish
            if start < required_start:
                constraint_issues.append(
                    _engine_issue(
                        "ACTUAL_START_NETWORK_CONFLICT",
                        task.task_id,
                        "已完成任务实际开始早于依赖网络要求；保留实际开始、完成事实并报告冲突。",
                        {
                            "actual_start": start.isoformat(),
                            "actual_finish": finish.isoformat(),
                            "network_required_start": required_start.isoformat(),
                            "status_date": source.project.status_date.isoformat(),
                        },
                    )
                )
        elif include_in_progress and task.status == "IN_PROGRESS":
            remaining_start = work_calendar.next_working_instant(
                max(source.project.status_date, required_start)
            )
            predicted_finish = max(
                work_calendar.add_working_minutes(
                    remaining_start,
                    task.remaining_duration_minutes,
                ),
                max(finish_bounds, default=remaining_start),
            )
            start = task.actual_start
            finish = predicted_finish
            remaining_starts[task.task_id] = remaining_start
            if start < required_start:
                constraint_issues.append(
                    _engine_issue(
                        "ACTUAL_START_NETWORK_CONFLICT",
                        task.task_id,
                        "任务实际开始早于依赖网络要求；保留实际事实，并从状态日期之后排剩余工作。",
                        {
                            "actual_start": start.isoformat(),
                            "network_required_start": required_start.isoformat(),
                            "status_date": source.project.status_date.isoformat(),
                            "remaining_start": remaining_start.isoformat(),
                            "predicted_finish": predicted_finish.isoformat(),
                        },
                    )
                )
        elif include_hard_constraints and constraint.type == "MUST_START_ON":
            start = constraint.date
            finish = (
                start
                if task.task_type == "milestone"
                else work_calendar.add_working_minutes(start, task.duration_minutes)
            )
            if required_start > start or required_finish > finish:
                constraint_issues.append(
                    _engine_issue(
                        "HARD_CONSTRAINT_NETWORK_CONFLICT",
                        task.task_id,
                        "MSO 固定开始早于依赖网络要求，任务保留固定日期。",
                        {
                            "constraint_type": constraint.type,
                            "constraint_date": constraint.date.isoformat(),
                            "fixed_start": start.isoformat(),
                            "fixed_finish": finish.isoformat(),
                            "network_required_start": required_start.isoformat(),
                            "network_required_finish": required_finish.isoformat(),
                        },
                    )
                )
        elif task_id in locked_task_ids:
            start = task.planned_start
            finish = task.planned_finish
            if start < required_start or finish < required_finish:
                conflicts.append(
                    {
                        "code": "LOCKED_TASK_DEPENDENCY_CONFLICT",
                        "task_id": task_id,
                        "fixed_start": start.isoformat(),
                        "fixed_finish": finish.isoformat(),
                        "required_start": required_start.isoformat(),
                        "required_finish": required_finish.isoformat(),
                    }
                )
        else:
            start = required_start
            finish = required_finish
        if (
            include_hard_constraints
            and constraint.type == "FINISH_NO_LATER_THAN"
            and finish > constraint.date
        ):
            constraint_issues.append(
                _engine_issue(
                    "FINISH_CONSTRAINT_VIOLATED",
                    task.task_id,
                    "任务计算完成时间晚于 FNLT 上界，保留计算日期并报告偏差。",
                    {
                        "constraint_type": constraint.type,
                        "constraint_date": constraint.date.isoformat(),
                        "calculated_finish": finish.isoformat(),
                        "variance_minutes": work_calendar.working_minutes_between(
                            constraint.date,
                            finish,
                        ),
                    },
                )
            )
        if include_hard_constraints and task.deadline is not None and finish > task.deadline:
            constraint_issues.append(
                _engine_issue(
                    "DEADLINE_MISSED",
                    task.task_id,
                    "任务计算完成时间晚于 Deadline，Deadline 不移动任务。",
                    {
                        "deadline": task.deadline.isoformat(),
                        "calculated_finish": finish.isoformat(),
                        "variance_minutes": work_calendar.working_minutes_between(
                            task.deadline,
                            finish,
                        ),
                    },
                )
            )
        calculated[task_id] = (start, finish)

    reverse_dates: dict[str, tuple[datetime, datetime]] = {}
    float_values: dict[str, tuple[int, int, bool]] = {}
    if include_reverse_float:
        calculated_project_finish = max(finish for _, finish in calculated.values())
        project_finish = (
            min(calculated_project_finish, source.project.required_finish)
            if include_hard_constraints and source.project.required_finish is not None
            else calculated_project_finish
        )
        for task_id in reversed(order):
            task = activities[task_id]
            task_calendar = work_calendars[task.effective_calendar_id]
            if include_in_progress and task.status == "COMPLETED":
                reverse_dates[task_id] = (task.actual_start, task.actual_finish)
                float_values[task_id] = (0, 0, False)
                continue
            reverse_duration = (
                task.remaining_duration_minutes
                if include_in_progress and task.status == "IN_PROGRESS"
                else task.duration_minutes
            )
            late_start_candidates = []
            for successor_id, dependency_type, lag_minutes in outgoing[task_id]:
                successor_calendar = work_calendars[activities[successor_id].effective_calendar_id]
                successor_late_start, successor_late_finish = reverse_dates[successor_id]
                successor_boundary = (
                    successor_late_start if dependency_type in {"FS", "SS"} else successor_late_finish
                )
                dependency_boundary = (
                    successor_calendar.shift_working_minutes(successor_boundary, -lag_minutes)
                    if lag_minutes
                    else successor_boundary
                )
                if dependency_type in {"FS", "FF"} and reverse_duration:
                    dependency_boundary = task_calendar.subtract_working_minutes(
                        dependency_boundary,
                        reverse_duration,
                    )
                late_start_candidates.append(dependency_boundary)
            if include_hard_constraints and task.constraint.type == "FINISH_NO_LATER_THAN":
                late_start_candidates.append(
                    task_calendar.subtract_working_minutes(
                        task.constraint.date,
                        reverse_duration,
                    )
                )
            late_start = min(late_start_candidates) if late_start_candidates else project_finish
            if include_hard_constraints and task.constraint.type == "MUST_START_ON":
                late_start = task.constraint.date
            if reverse_duration:
                if not late_start_candidates and not (
                    include_hard_constraints and task.constraint.type == "MUST_START_ON"
                ):
                    late_start = task_calendar.subtract_working_minutes(project_finish, reverse_duration)
                late_finish = task_calendar.add_working_minutes(late_start, reverse_duration)
            else:
                late_finish = late_start
            reverse_dates[task_id] = (
                (task.actual_start, late_finish)
                if include_in_progress and task.status == "IN_PROGRESS"
                else (late_start, late_finish)
            )

            early_start, early_finish = calculated[task_id]
            free_slack_candidates = []
            for successor_id, dependency_type, lag_minutes in outgoing[task_id]:
                successor_calendar = work_calendars[activities[successor_id].effective_calendar_id]
                successor_start, successor_finish = calculated[successor_id]
                source_boundary = early_finish if dependency_type in {"FS", "FF"} else early_start
                constrained_boundary = (
                    successor_calendar.shift_working_minutes(source_boundary, lag_minutes)
                    if lag_minutes
                    else source_boundary
                )
                successor_boundary = successor_start if dependency_type in {"FS", "SS"} else successor_finish
                free_slack_candidates.append(
                    successor_calendar.working_minutes_between(constrained_boundary, successor_boundary)
                )
            float_start = (
                remaining_starts[task_id]
                if include_in_progress and task.status == "IN_PROGRESS"
                else early_start
            )
            total_slack = task_calendar.working_minutes_between(float_start, late_start)
            free_slack = (
                min(free_slack_candidates)
                if free_slack_candidates
                else task_calendar.working_minutes_between(early_finish, project_finish)
            )
            float_values[task_id] = (total_slack, free_slack, total_slack <= 0)

    if include_summary_rollup:
        children_by_summary = {
            task.task_id: [child.task_id for child in source.tasks if child.parent_task_id == task.task_id]
            for task in source.tasks
            if task.task_type == "summary"
        }
        for summary in sorted(
            (task for task in source.tasks if task.task_type == "summary"),
            key=lambda task: task.outline_level,
            reverse=True,
        ):
            calculated_child_ids = [
                child_id
                for child_id in children_by_summary[summary.task_id]
                if child_id in calculated
            ]
            child_dates = [calculated[child_id] for child_id in calculated_child_ids]
            if not summary.active or not child_dates:
                continue
            calculated[summary.task_id] = (
                min(start for start, _ in child_dates),
                max(finish for _, finish in child_dates),
            )
            if include_reverse_float:
                child_reverse_dates = [
                    reverse_dates[child_id] for child_id in calculated_child_ids
                ]
                reverse_dates[summary.task_id] = (
                    min(start for start, _ in child_reverse_dates),
                    max(finish for _, finish in child_reverse_dates),
                )
                child_float_values = [
                    float_values[child_id] for child_id in calculated_child_ids
                ]
                total_slack = min(total for total, _, _ in child_float_values)
                free_slack = min(free for _, free, _ in child_float_values)
                float_values[summary.task_id] = (
                    total_slack,
                    free_slack,
                    any(critical for _, _, critical in child_float_values),
                )

    task_dates = []
    baseline_variances = []
    output_tasks = (
        source.tasks if include_summary_rollup else [activities[id] for id in order]
    )
    for task in output_tasks:
        task_id = task.task_id
        if task_id not in calculated:
            task_dates.append(
                {
                    "task_id": task_id,
                    "source_start": task.planned_start.isoformat(),
                    "source_finish": task.planned_finish.isoformat(),
                    "early_start": task.planned_start.isoformat(),
                    "early_finish": task.planned_finish.isoformat(),
                    "start_changed": False,
                    "finish_changed": False,
                    "parent_task_id": task.parent_task_id,
                    "outline_level": task.outline_level,
                    "task_type": task.task_type,
                    "summary": task.task_type == "summary",
                    "active": task.active,
                    "calculation_status": (
                        "excluded_inactive"
                        if not task.active
                        else "source_only_no_active_descendants"
                    ),
                    "late_start": None,
                    "late_finish": None,
                    "total_slack_minutes": None,
                    "free_slack_minutes": None,
                    "critical": False,
                }
            )
            continue
        early_start, early_finish = calculated[task_id]
        task_date = {
            "task_id": task_id,
            "source_start": task.planned_start.isoformat(),
            "source_finish": task.planned_finish.isoformat(),
            "early_start": early_start.isoformat(),
            "early_finish": early_finish.isoformat(),
            "start_changed": early_start != task.planned_start,
            "finish_changed": early_finish != task.planned_finish,
        }
        if include_summary_rollup:
            task_date.update(
                {
                    "parent_task_id": task.parent_task_id,
                    "outline_level": task.outline_level,
                    "task_type": task.task_type,
                    "summary": task.task_type == "summary",
                    "active": task.active,
                    "calculation_status": "calculated",
                }
            )
        if include_reverse_float:
            late_start, late_finish = reverse_dates[task_id]
            total_slack, free_slack, critical = float_values[task_id]
            task_date.update(
                {
                    "late_start": late_start.isoformat(),
                    "late_finish": late_finish.isoformat(),
                    "total_slack_minutes": total_slack,
                    "free_slack_minutes": free_slack,
                    "critical": critical,
                }
            )
        if include_completed_progress:
            task_date.update(
                {
                    "status": task.status,
                    "actual_start": task.actual_start.isoformat() if task.actual_start is not None else None,
                    "actual_finish": task.actual_finish.isoformat() if task.actual_finish is not None else None,
                    "baseline_start": (
                        task.baseline_0.start.isoformat() if task.baseline_0.start is not None else None
                    ),
                    "baseline_finish": (
                        task.baseline_0.finish.isoformat() if task.baseline_0.finish is not None else None
                    ),
                    "remaining_duration_minutes": task.remaining_duration_minutes,
                    "remaining_start": (
                        remaining_starts[task.task_id].isoformat()
                        if include_in_progress and task.status == "IN_PROGRESS"
                        else None
                    ),
                }
            )
        task_dates.append(task_date)
        if include_completed_progress and task.task_type != "summary" and task.baseline_0.exists:
            task_calendar = work_calendars[task.effective_calendar_id]
            baseline_variances.append(
                {
                    "task_id": task_id,
                    "start_variance_minutes": task_calendar.working_minutes_between(
                        task.baseline_0.start,
                        early_start,
                    ),
                    "finish_variance_minutes": task_calendar.working_minutes_between(
                        task.baseline_0.finish,
                        early_finish,
                    ),
                }
            )

    source_finish = source.project.planned_finish
    calculated_finish = max(finish for _, finish in calculated.values())
    if (
        include_hard_constraints
        and source.project.required_finish is not None
        and calculated_finish > source.project.required_finish
    ):
        constraint_issues.append(
            _engine_issue(
                "PROJECT_REQUIRED_FINISH_MISSED",
                "project",
                "项目计算完成时间晚于要求完成日期，管理目标不移动任务。",
                {
                    "required_finish": source.project.required_finish.isoformat(),
                    "calculated_finish": calculated_finish.isoformat(),
                    "negative_float_minutes": -default_work_calendar.working_minutes_between(
                        source.project.required_finish,
                        calculated_finish,
                    ),
                },
            )
        )
    resource_analysis = (
        analyze_resources(source, task_dates)
        if isinstance(source, CanonicalScheduleV27)
        and engine_profile_id
        in {RESOURCE_ANALYSIS_ENGINE_PROFILE_ID, INACTIVE_ENGINE_PROFILE_ID}
        else {"issues": [], "resource_conflicts": [], "assignment_costs": []}
    )
    result_issues = [*constraint_issues, *resource_analysis["issues"]]
    result_issues.sort(key=lambda item: (item["code"], item["object_ref"]))
    return {
        "status": "invalid" if conflicts else "calculated",
        "engine_profile_id": engine_profile_id,
        "engine_version": engine_version,
        "support": {"supported": True, "blockers": []},
        "conflicts": conflicts,
        "issues": result_issues,
        "issue_summary": {
            "total": len(result_issues),
            "blocker": 0,
            "warning": len(result_issues),
            "info": 0,
        },
        "project_start": project_start.isoformat(),
        "finish_before": source_finish.isoformat(),
        "finish_after": calculated_finish.isoformat(),
        "affected_task_count": sum(item["start_changed"] or item["finish_changed"] for item in task_dates),
        "task_dates": task_dates,
        "baseline_variances": baseline_variances,
        "resource_conflicts": resource_analysis["resource_conflicts"],
        "assignment_costs": resource_analysis["assignment_costs"],
    }


def _support_blockers(
    source: CanonicalSchedule,
    *,
    include_summary_rollup: bool,
    include_milestones: bool = False,
    include_negative_lag: bool = False,
    include_calendar_exceptions: bool = False,
    include_multiple_calendars: bool = False,
    include_hard_constraints: bool = False,
    include_completed_progress: bool = False,
    include_in_progress: bool = False,
    include_inactive_exclusion: bool = False,
) -> list[EngineBlocker]:
    blockers: list[EngineBlocker] = []
    try:
        time_zone = ZoneInfo(source.semantics.time_zone)
    except ZoneInfoNotFoundError:
        blockers.append(EngineBlocker("TIME_ZONE_UNSUPPORTED", (), "项目时区不是当前运行环境可识别的 IANA 时区"))
        time_zone = None

    if len(source.calendars) != 1 and not include_multiple_calendars:
        blockers.append(
            EngineBlocker(
                "MULTIPLE_CALENDARS_UNSUPPORTED",
                tuple(calendar.calendar_id for calendar in source.calendars),
                "当前 Profile 只支持一个统一项目日历",
            )
        )
    else:
        calendars = source.calendars if include_multiple_calendars else source.calendars[:1]
        if not include_multiple_calendars and calendars and (
            calendars[0].calendar_id != source.project.default_calendar_id
            or calendars[0].parent_calendar_id
        ):
            blockers.append(
                EngineBlocker(
                    "CALENDAR_INHERITANCE_UNSUPPORTED",
                    (calendars[0].calendar_id,),
                    "当前 Profile 只支持无继承的项目默认日历",
                )
            )
        for calendar in calendars:
            if calendar.exceptions and not include_calendar_exceptions:
                blockers.append(
                    EngineBlocker(
                        "CALENDAR_EXCEPTIONS_UNSUPPORTED",
                        (calendar.calendar_id,),
                        "当前 Profile 未启用结构化日历例外语义，不做近似计算",
                    )
                )
            elif calendar.exceptions and source.schema_version not in {
                "canonical_schedule_v2.4",
                "canonical_schedule_v2.5",
                "canonical_schedule_v2.6",
                "canonical_schedule_v2.7",
                "canonical_schedule_v2.8",
            }:
                blockers.append(
                    EngineBlocker(
                        "CALENDAR_EXCEPTIONS_SCHEMA_UNSUPPORTED",
                        (calendar.calendar_id,),
                        "日历例外必须通过 Canonical v2.4 结构化契约提交",
                    )
                )
            elif calendar.exceptions and time_zone:
                conflicts = calendar_exception_conflicts(calendar.exceptions, time_zone)
                if conflicts:
                    blockers.append(
                        EngineBlocker(
                            "CALENDAR_EXCEPTIONS_CONFLICT",
                            tuple(sorted({exception_id for pair in conflicts for exception_id in pair})),
                            "同一项目日历的例外日期范围不能重叠",
                        )
                    )
                time_zone_mismatches = tuple(
                    exception.exception_id
                    for exception in calendar.exceptions
                    if exception.start_date.utcoffset()
                    != exception.start_date.astimezone(time_zone).utcoffset()
                    or exception.finish_date.utcoffset()
                    != exception.finish_date.astimezone(time_zone).utcoffset()
                )
                if time_zone_mismatches:
                    blockers.append(
                        EngineBlocker(
                            "CALENDAR_EXCEPTION_TIME_ZONE_MISMATCH",
                            time_zone_mismatches,
                            "日历例外日期与项目时区不一致",
                        )
                    )
            if not _valid_weekly_pattern(calendar, allow_inherited=bool(calendar.parent_calendar_id)):
                blockers.append(
                    EngineBlocker(
                        "CALENDAR_INTERVALS_INVALID",
                        (calendar.calendar_id,),
                        "项目日历必须包含合法工作时段，继承日历可使用 INHERITED 工作日",
                    )
                )
        if include_multiple_calendars:
            if source.schema_version not in {
                "canonical_schedule_v2.4",
                "canonical_schedule_v2.5",
                "canonical_schedule_v2.6",
                "canonical_schedule_v2.7",
                "canonical_schedule_v2.8",
            }:
                blockers.append(
                    EngineBlocker(
                        "MULTI_CALENDAR_SCHEMA_UNSUPPORTED",
                        tuple(calendar.calendar_id for calendar in calendars),
                        "多/任务日历必须通过 Canonical v2.4 或更新版本提交",
                    )
                )
            if source.semantics.lag_calendar_policy != LAG_CALENDAR_POLICY_SUCCESSOR:
                blockers.append(
                    EngineBlocker(
                        "LAG_CALENDAR_POLICY_UNSUPPORTED",
                        (),
                        "多/任务日历依赖必须显式使用 SUCCESSOR_TASK_CALENDAR",
                    )
                )
            if time_zone and not blockers:
                try:
                    build_effective_work_calendars(calendars, time_zone)
                except CalendarResolutionError as exc:
                    blockers.append(EngineBlocker(exc.code, exc.object_refs, exc.message))

    schedulable_types = {"activity", "milestone"} if include_milestones else {"activity"}
    tasks_by_id = {task.task_id: task for task in source.tasks}
    activity_ids = {
        task.task_id
        for task in source.tasks
        if task.task_type in schedulable_types
        and (task.active or not include_inactive_exclusion)
    }
    if not activity_ids:
        blockers.append(EngineBlocker("NO_ACTIVITY_TASKS", (), "当前来源没有可重算的活动任务"))
    for task in source.tasks:
        if task.task_type == "summary":
            continue
        if not task.active:
            if include_inactive_exclusion:
                continue
            blockers.append(EngineBlocker("INACTIVE_TASK_UNSUPPORTED", (task.task_id,), "不支持非活动任务"))
        if task.task_type == "milestone" and not include_milestones:
            blockers.append(
                EngineBlocker(
                    "MILESTONE_UNSUPPORTED",
                    (task.task_id,),
                    "当前 Profile 尚未冻结零工期里程碑的边界时刻语义",
                )
            )
        elif task.task_type == "milestone" and task.duration_minutes != 0:
            blockers.append(
                EngineBlocker(
                    "MILESTONE_DURATION_INVALID",
                    (task.task_id,),
                    "里程碑工期必须为零",
                )
            )
        elif task.task_type == "activity" and task.duration_minutes == 0:
            blockers.append(
                EngineBlocker(
                    "MILESTONE_UNSUPPORTED" if not include_milestones else "ZERO_DURATION_ACTIVITY_UNSUPPORTED",
                    (task.task_id,),
                    (
                        "当前 Profile 尚未冻结零工期里程碑的边界时刻语义"
                        if not include_milestones
                        else "零工期任务必须在 Canonical 中明确标记为 milestone"
                    ),
                )
            )
        if task.constraint.type == "AS_SOON_AS_POSSIBLE" and task.constraint.date is not None:
            blockers.append(EngineBlocker("TASK_CONSTRAINT_INVALID", (task.task_id,), "ASAP 任务不能包含约束日期"))
        if task.constraint.type != "AS_SOON_AS_POSSIBLE" and task.constraint.date is None:
            blockers.append(
                EngineBlocker("TASK_CONSTRAINT_DATE_REQUIRED", (task.task_id,), "非 ASAP 约束必须包含约束日期")
            )
        if task.constraint.type in {"MUST_START_ON", "FINISH_NO_LATER_THAN"} and not include_hard_constraints:
            blockers.append(
                EngineBlocker(
                    "HARD_CONSTRAINT_UNSUPPORTED",
                    (task.task_id,),
                    "当前 Profile 未启用 MSO/FNLT 硬约束语义",
                )
            )
        if include_completed_progress:
            if task.status == "IN_PROGRESS" and not include_in_progress:
                blockers.append(
                    EngineBlocker(
                        "IN_PROGRESS_UNSUPPORTED",
                        (task.task_id,),
                        "阶段 5A 尚未启用进行中任务的剩余工作排程",
                    )
                )
        elif task.actual_start is not None or task.actual_finish is not None or task.percent_complete:
            blockers.append(EngineBlocker("ACTUAL_PROGRESS_UNSUPPORTED", (task.task_id,), "不支持包含实际进度的任务"))
        if include_multiple_calendars:
            expected_calendar_id = task.calendar_id or source.project.default_calendar_id
            if task.effective_calendar_id != expected_calendar_id:
                blockers.append(
                    EngineBlocker(
                        "TASK_EFFECTIVE_CALENDAR_MISMATCH",
                        (task.task_id, task.effective_calendar_id, expected_calendar_id),
                        "任务有效日历必须由任务日历或项目默认日历确定",
                    )
                )
        elif (
            task.calendar_id not in {None, source.project.default_calendar_id}
            or task.effective_calendar_id != source.project.default_calendar_id
        ):
            blockers.append(EngineBlocker("TASK_CALENDAR_UNSUPPORTED", (task.task_id,), "任务必须使用统一项目日历"))
        if time_zone and (
            task.planned_start.tzinfo is None
            or task.planned_finish.tzinfo is None
            or task.planned_start.utcoffset() != task.planned_start.astimezone(time_zone).utcoffset()
            or task.planned_finish.utcoffset() != task.planned_finish.astimezone(time_zone).utcoffset()
        ):
            blockers.append(EngineBlocker("TASK_TIME_ZONE_MISMATCH", (task.task_id,), "任务日期与项目时区不一致"))

    for dependency in source.dependencies:
        predecessor = tasks_by_id[dependency.predecessor_task_id]
        successor = tasks_by_id[dependency.successor_task_id]
        if include_inactive_exclusion and (not predecessor.active or not successor.active):
            blockers.append(
                EngineBlocker(
                    "INACTIVE_TASK_DEPENDENCY_REQUIRES_DECISION",
                    (dependency.dependency_id,),
                    "依赖关系涉及 inactive 任务，必须显式选择替代叶子关系",
                )
            )
        elif dependency.predecessor_task_id not in activity_ids or dependency.successor_task_id not in activity_ids:
            blockers.append(
                EngineBlocker(
                    "SUMMARY_DEPENDENCY_UNSUPPORTED",
                    (dependency.dependency_id,),
                    "当前 Profile 不支持汇总任务参与依赖",
                )
            )
        if dependency.lag_minutes < 0 and not include_negative_lag:
            blockers.append(
                EngineBlocker(
                    "NEGATIVE_DEPENDENCY_LAG_UNSUPPORTED",
                    (dependency.dependency_id,),
                    "当前 Profile 只支持零 Lag 和正 Lag",
                )
            )
        if include_multiple_calendars and dependency.lag_calendar_policy != LAG_CALENDAR_POLICY_SUCCESSOR:
            blockers.append(
                EngineBlocker(
                    "DEPENDENCY_LAG_CALENDAR_POLICY_MISMATCH",
                    (dependency.dependency_id,),
                    "多/任务日历依赖必须使用后续任务有效日历计算 Lag",
                )
            )
    if include_summary_rollup:
        tasks_by_id = {task.task_id: task for task in source.tasks}
        child_count = {task.task_id: 0 for task in source.tasks if task.task_type == "summary"}
        for task in source.tasks:
            if task.parent_task_id is None:
                continue
            parent = tasks_by_id[task.parent_task_id]
            if parent.task_type != "summary":
                blockers.append(
                    EngineBlocker(
                        "TASK_PARENT_NOT_SUMMARY",
                        (task.task_id, parent.task_id),
                        "当前 Profile 要求有子任务的父任务必须是汇总任务",
                    )
                )
            else:
                child_count[parent.task_id] += 1
            if task.outline_level <= parent.outline_level:
                blockers.append(
                    EngineBlocker(
                        "TASK_OUTLINE_HIERARCHY_INVALID",
                        (task.task_id, parent.task_id),
                        "子任务的层级必须深于父任务",
                    )
                )
        for summary_id, count in child_count.items():
            if count == 0:
                blockers.append(
                    EngineBlocker(
                        "SUMMARY_WITHOUT_CHILDREN",
                        (summary_id,),
                        "汇总任务必须至少包含一个直接子任务",
                    )
                )
    return blockers


def _valid_weekly_pattern(calendar: CanonicalCalendar, *, allow_inherited: bool = False) -> bool:
    has_interval = False
    for _, day in calendar.weekly_pattern:
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


def _engine_issue(
    code: str,
    object_ref: str,
    message: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "severity": "warning",
        "code": code,
        "object_ref": object_ref,
        "object_refs": [object_ref],
        "evidence": evidence,
        "message": message,
    }


def _blocked_result(blockers: list[EngineBlocker], engine_profile_id: str, engine_version: str) -> dict[str, Any]:
    grouped: dict[tuple[str, str], set[str]] = {}
    for blocker in blockers:
        grouped.setdefault((blocker.code, blocker.message), set()).update(blocker.object_refs)
    return {
        "status": "blocked",
        "engine_profile_id": engine_profile_id,
        "engine_version": engine_version,
        "support": {
            "supported": False,
            "blockers": [
                EngineBlocker(code, tuple(sorted(object_refs)), message).as_dict()
                for (code, message), object_refs in sorted(grouped.items())
            ],
        },
        "task_dates": [],
    }
