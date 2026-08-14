"""Deterministic forward scheduling for the supported S3/S4 profile."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from yuxi.schedule.contracts.canonical_v2_2 import CanonicalCalendar, CanonicalScheduleV22

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
WEEKDAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")


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


class UnifiedWorkCalendar:
    """Working-time primitives for one project calendar and one time zone."""

    def __init__(self, calendar: CanonicalCalendar, time_zone: ZoneInfo) -> None:
        self._time_zone = time_zone
        self._intervals = {
            weekday: tuple((item.start, item.finish) for item in day.intervals)
            for weekday, day in calendar.weekly_pattern
        }

    def next_working_instant(self, value: datetime) -> datetime:
        current = value.astimezone(self._time_zone)
        current_date = current.date()
        while True:
            for interval_start, interval_finish in self._intervals_for(current_date):
                start = datetime.combine(current_date, interval_start, self._time_zone)
                finish = datetime.combine(current_date, interval_finish, self._time_zone)
                if current <= start:
                    return start
                if start <= current < finish:
                    return current
            current_date += timedelta(days=1)
            current = datetime.combine(current_date, time.min, self._time_zone)

    def add_working_minutes(self, value: datetime, minutes: int) -> datetime:
        current = self.next_working_instant(value)
        remaining = minutes
        while remaining:
            current_date = current.date()
            active_finish = next(
                datetime.combine(current_date, finish, self._time_zone)
                for start, finish in self._intervals_for(current_date)
                if datetime.combine(current_date, start, self._time_zone)
                <= current
                < datetime.combine(current_date, finish, self._time_zone)
            )
            available = int((active_finish - current).total_seconds() // 60)
            if remaining <= available:
                return current + timedelta(minutes=remaining)
            remaining -= available
            current = self.next_working_instant(active_finish)
        return current

    def subtract_working_minutes(self, value: datetime, minutes: int) -> datetime:
        current = self._previous_working_instant(value)
        remaining = minutes
        while remaining:
            current_date = current.date()
            active_start = next(
                datetime.combine(current_date, start, self._time_zone)
                for start, finish in reversed(self._intervals_for(current_date))
                if datetime.combine(current_date, start, self._time_zone)
                < current
                <= datetime.combine(current_date, finish, self._time_zone)
            )
            available = int((current - active_start).total_seconds() // 60)
            if remaining <= available:
                return current - timedelta(minutes=remaining)
            remaining -= available
            current = self._previous_working_instant(active_start)
        return current

    def working_minutes_between(self, start: datetime, finish: datetime) -> int:
        if finish < start:
            return -self.working_minutes_between(finish, start)
        current_date = start.astimezone(self._time_zone).date()
        finish_date = finish.astimezone(self._time_zone).date()
        total = 0
        while current_date <= finish_date:
            for interval_start, interval_finish in self._intervals_for(current_date):
                interval_start_at = datetime.combine(current_date, interval_start, self._time_zone)
                interval_finish_at = datetime.combine(current_date, interval_finish, self._time_zone)
                overlap_start = max(start, interval_start_at)
                overlap_finish = min(finish, interval_finish_at)
                if overlap_start < overlap_finish:
                    total += int((overlap_finish - overlap_start).total_seconds() // 60)
            current_date += timedelta(days=1)
        return total

    def _previous_working_instant(self, value: datetime) -> datetime:
        current = value.astimezone(self._time_zone)
        current_date = current.date()
        while True:
            for interval_start, interval_finish in reversed(self._intervals_for(current_date)):
                start = datetime.combine(current_date, interval_start, self._time_zone)
                finish = datetime.combine(current_date, interval_finish, self._time_zone)
                if current >= finish:
                    return finish
                if start < current <= finish:
                    return current
            current_date -= timedelta(days=1)
            current = datetime.combine(current_date, time.max, self._time_zone)

    def _intervals_for(self, value: date) -> tuple[tuple[time, time], ...]:
        return self._intervals[WEEKDAYS[value.weekday()]]


def calculate_minimal_forward_schedule(
    source: CanonicalScheduleV22,
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
    else:
        raise ValueError(f"unsupported schedule engine profile: {engine_profile_id}")

    include_summary_rollup = engine_profile_id in {
        SUMMARY_ROLLUP_ENGINE_PROFILE_ID,
        REVERSE_FLOAT_ENGINE_PROFILE_ID,
    }
    include_reverse_float = engine_profile_id == REVERSE_FLOAT_ENGINE_PROFILE_ID
    blockers = _support_blockers(source, include_summary_rollup=include_summary_rollup)
    if blockers:
        return _blocked_result(blockers, engine_profile_id, engine_version)

    locked_task_ids = locked_task_ids or set()
    calendar = source.calendars[0]
    work_calendar = UnifiedWorkCalendar(calendar, ZoneInfo(source.semantics.time_zone))
    activities = {task.task_id: task for task in source.tasks if task.task_type == "activity"}
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

    project_start = work_calendar.next_working_instant(source.project.planned_start)
    calculated: dict[str, tuple[datetime, datetime]] = {}
    conflicts = []
    for task_id in order:
        predecessor_bounds = []
        finish_bounds = []
        for predecessor_id, dependency_type, lag_minutes in incoming[task_id]:
            predecessor_start, predecessor_finish = calculated[predecessor_id]
            anchor = predecessor_finish if dependency_type in {"FS", "FF"} else predecessor_start
            bound = work_calendar.add_working_minutes(anchor, lag_minutes) if lag_minutes else anchor
            if dependency_type in {"FF", "SF"}:
                finish_bounds.append(bound)
                bound = work_calendar.subtract_working_minutes(bound, activities[task_id].duration_minutes)
            predecessor_bounds.append(bound)
        constraint = activities[task_id].constraint
        if constraint.type == "START_NO_EARLIER_THAN":
            predecessor_bounds.append(constraint.date)
        elif constraint.type == "FINISH_NO_EARLIER_THAN":
            finish_bounds.append(constraint.date)
            predecessor_bounds.append(
                work_calendar.subtract_working_minutes(constraint.date, activities[task_id].duration_minutes)
            )
        task = activities[task_id]
        earliest_start = max(
            [
                project_start,
                *predecessor_bounds,
                *([task.planned_start] if task.scheduling_mode == "manual" else []),
            ]
        )
        required_start = work_calendar.next_working_instant(earliest_start)
        required_finish = max(
            work_calendar.add_working_minutes(required_start, task.duration_minutes),
            max(finish_bounds, default=required_start),
        )
        if task_id in locked_task_ids:
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
        calculated[task_id] = (start, finish)

    reverse_dates: dict[str, tuple[datetime, datetime]] = {}
    float_values: dict[str, tuple[int, int, bool]] = {}
    if include_reverse_float:
        project_finish = max(finish for _, finish in calculated.values())
        for task_id in reversed(order):
            task = activities[task_id]
            late_start_candidates = []
            for successor_id, dependency_type, lag_minutes in outgoing[task_id]:
                successor_late_start, successor_late_finish = reverse_dates[successor_id]
                successor_boundary = (
                    successor_late_start if dependency_type in {"FS", "SS"} else successor_late_finish
                )
                dependency_boundary = (
                    work_calendar.subtract_working_minutes(successor_boundary, lag_minutes)
                    if lag_minutes
                    else successor_boundary
                )
                if dependency_type in {"FS", "FF"}:
                    dependency_boundary = work_calendar.subtract_working_minutes(
                        dependency_boundary,
                        task.duration_minutes,
                    )
                late_start_candidates.append(dependency_boundary)
            late_start = (
                min(late_start_candidates)
                if late_start_candidates
                else work_calendar.subtract_working_minutes(project_finish, task.duration_minutes)
            )
            late_finish = work_calendar.add_working_minutes(late_start, task.duration_minutes)
            reverse_dates[task_id] = (late_start, late_finish)

            early_start, early_finish = calculated[task_id]
            free_slack_candidates = []
            for successor_id, dependency_type, lag_minutes in outgoing[task_id]:
                successor_start, successor_finish = calculated[successor_id]
                source_boundary = early_finish if dependency_type in {"FS", "FF"} else early_start
                constrained_boundary = (
                    work_calendar.add_working_minutes(source_boundary, lag_minutes)
                    if lag_minutes
                    else source_boundary
                )
                successor_boundary = successor_start if dependency_type in {"FS", "SS"} else successor_finish
                free_slack_candidates.append(
                    work_calendar.working_minutes_between(constrained_boundary, successor_boundary)
                )
            total_slack = work_calendar.working_minutes_between(early_start, late_start)
            free_slack = (
                min(free_slack_candidates)
                if free_slack_candidates
                else work_calendar.working_minutes_between(early_finish, project_finish)
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
            child_dates = [calculated[child_id] for child_id in children_by_summary[summary.task_id]]
            calculated[summary.task_id] = (
                min(start for start, _ in child_dates),
                max(finish for _, finish in child_dates),
            )
            if include_reverse_float:
                child_reverse_dates = [reverse_dates[child_id] for child_id in children_by_summary[summary.task_id]]
                reverse_dates[summary.task_id] = (
                    min(start for start, _ in child_reverse_dates),
                    max(finish for _, finish in child_reverse_dates),
                )
                child_float_values = [float_values[child_id] for child_id in children_by_summary[summary.task_id]]
                total_slack = min(total for total, _, _ in child_float_values)
                free_slack = min(free for _, free, _ in child_float_values)
                float_values[summary.task_id] = (
                    total_slack,
                    free_slack,
                    any(critical for _, _, critical in child_float_values),
                )

    task_dates = []
    output_tasks = (
        source.tasks if include_summary_rollup else [activities[id] for id in order]
    )
    for task in output_tasks:
        task_id = task.task_id
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
        task_dates.append(task_date)

    source_finish = source.project.planned_finish
    calculated_finish = max(finish for _, finish in calculated.values())
    return {
        "status": "invalid" if conflicts else "calculated",
        "engine_profile_id": engine_profile_id,
        "engine_version": engine_version,
        "support": {"supported": True, "blockers": []},
        "conflicts": conflicts,
        "project_start": project_start.isoformat(),
        "finish_before": source_finish.isoformat(),
        "finish_after": calculated_finish.isoformat(),
        "affected_task_count": sum(item["start_changed"] or item["finish_changed"] for item in task_dates),
        "task_dates": task_dates,
    }


def _support_blockers(source: CanonicalScheduleV22, *, include_summary_rollup: bool) -> list[EngineBlocker]:
    blockers: list[EngineBlocker] = []
    try:
        time_zone = ZoneInfo(source.semantics.time_zone)
    except ZoneInfoNotFoundError:
        blockers.append(EngineBlocker("TIME_ZONE_UNSUPPORTED", (), "项目时区不是当前运行环境可识别的 IANA 时区"))
        time_zone = None

    if len(source.calendars) != 1:
        blockers.append(
            EngineBlocker(
                "MULTIPLE_CALENDARS_UNSUPPORTED",
                tuple(calendar.calendar_id for calendar in source.calendars),
                "当前 Profile 只支持一个统一项目日历",
            )
        )
    else:
        calendar = source.calendars[0]
        if calendar.calendar_id != source.project.default_calendar_id or calendar.parent_calendar_id:
            blockers.append(
                EngineBlocker(
                    "CALENDAR_INHERITANCE_UNSUPPORTED",
                    (calendar.calendar_id,),
                    "当前 Profile 只支持无继承的项目默认日历",
                )
            )
        if calendar.exceptions:
            blockers.append(
                EngineBlocker(
                    "CALENDAR_EXCEPTIONS_UNSUPPORTED",
                    (calendar.calendar_id,),
                    "v2.2 尚未冻结日历例外语义，当前 Profile 不做近似计算",
                )
            )
        if not _valid_weekly_pattern(calendar):
            blockers.append(
                EngineBlocker(
                    "CALENDAR_INTERVALS_INVALID",
                    (calendar.calendar_id,),
                    "项目日历必须至少包含一个不重叠且不跨日的工作时段",
                )
            )

    activity_ids = {task.task_id for task in source.tasks if task.task_type == "activity"}
    if not activity_ids:
        blockers.append(EngineBlocker("NO_ACTIVITY_TASKS", (), "当前来源没有可重算的活动任务"))
    for task in source.tasks:
        if task.task_type != "activity":
            continue
        if not task.active:
            blockers.append(EngineBlocker("INACTIVE_TASK_UNSUPPORTED", (task.task_id,), "不支持非活动任务"))
        if task.duration_minutes == 0:
            blockers.append(
                EngineBlocker(
                    "MILESTONE_UNSUPPORTED",
                    (task.task_id,),
                    "当前 Profile 尚未冻结零工期里程碑的边界时刻语义",
                )
            )
        if task.constraint.type == "AS_SOON_AS_POSSIBLE" and task.constraint.date is not None:
            blockers.append(EngineBlocker("TASK_CONSTRAINT_INVALID", (task.task_id,), "ASAP 任务不能包含约束日期"))
        if task.constraint.type != "AS_SOON_AS_POSSIBLE" and task.constraint.date is None:
            blockers.append(
                EngineBlocker("TASK_CONSTRAINT_DATE_REQUIRED", (task.task_id,), "SNET/FNET 必须包含约束日期")
            )
        if task.actual_start is not None or task.actual_finish is not None or task.percent_complete:
            blockers.append(EngineBlocker("ACTUAL_PROGRESS_UNSUPPORTED", (task.task_id,), "不支持包含实际进度的任务"))
        if task.calendar_id is not None or task.effective_calendar_id != source.project.default_calendar_id:
            blockers.append(EngineBlocker("TASK_CALENDAR_UNSUPPORTED", (task.task_id,), "任务必须使用统一项目日历"))
        if time_zone and (
            task.planned_start.tzinfo is None
            or task.planned_finish.tzinfo is None
            or task.planned_start.utcoffset() != task.planned_start.astimezone(time_zone).utcoffset()
            or task.planned_finish.utcoffset() != task.planned_finish.astimezone(time_zone).utcoffset()
        ):
            blockers.append(EngineBlocker("TASK_TIME_ZONE_MISMATCH", (task.task_id,), "任务日期与项目时区不一致"))

    for dependency in source.dependencies:
        if dependency.predecessor_task_id not in activity_ids or dependency.successor_task_id not in activity_ids:
            blockers.append(
                EngineBlocker(
                    "SUMMARY_DEPENDENCY_UNSUPPORTED",
                    (dependency.dependency_id,),
                    "当前 Profile 不支持汇总任务参与依赖",
                )
            )
        if dependency.lag_minutes < 0:
            blockers.append(
                EngineBlocker(
                    "NEGATIVE_DEPENDENCY_LAG_UNSUPPORTED",
                    (dependency.dependency_id,),
                    "当前 Profile 只支持零 Lag 和正 Lag",
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


def _valid_weekly_pattern(calendar: CanonicalCalendar) -> bool:
    has_interval = False
    for _, day in calendar.weekly_pattern:
        if (day.day_type == "WORKING") != bool(day.intervals):
            return False
        previous_finish: time | None = None
        for interval in day.intervals:
            has_interval = True
            if interval.start >= interval.finish or (previous_finish is not None and interval.start < previous_finish):
                return False
            previous_finish = interval.finish
    return has_interval


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
