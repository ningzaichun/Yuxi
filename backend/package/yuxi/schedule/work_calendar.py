"""Deterministic working-time arithmetic and calendar inheritance resolution."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from yuxi.schedule.contracts.canonical_v2_2 import CanonicalCalendar

WEEKDAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")


class EffectiveWorkCalendar:
    """Working-time primitives for one weekly calendar plus dated exceptions."""

    def __init__(self, calendar: CanonicalCalendar, time_zone: ZoneInfo) -> None:
        self._time_zone = time_zone
        self._intervals = _calendar_weekly_intervals(calendar)
        self._exceptions = _normalize_exceptions(calendar.exceptions, time_zone)
        if not any(self._intervals.values()):
            raise ValueError("calendar has no recurring working time")

    @classmethod
    def from_calendar_chain(
        cls,
        chain: tuple[Any, ...],
        time_zone: ZoneInfo,
    ) -> EffectiveWorkCalendar:
        calendar = cls.__new__(cls)
        calendar._time_zone = time_zone
        intervals = {weekday: () for weekday in WEEKDAYS}
        exception_layers = []
        for item in chain:
            item_intervals = _calendar_weekly_intervals(item)
            day_types = _calendar_day_types(item)
            for weekday in WEEKDAYS:
                if day_types[weekday] != "INHERITED":
                    intervals[weekday] = item_intervals[weekday]
            exception_layers.extend(_normalize_exceptions(item.exceptions, time_zone))
        calendar._intervals = intervals
        calendar._exceptions = tuple(reversed(exception_layers))
        if not any(intervals.values()):
            raise ValueError("calendar has no recurring working time")
        return calendar

    @classmethod
    def from_weekday_intervals(
        cls,
        intervals: dict[str, tuple[tuple[time, time], ...]],
        time_zone: ZoneInfo,
        exceptions: tuple[Any, ...] = (),
    ) -> EffectiveWorkCalendar:
        calendar = cls.__new__(cls)
        calendar._time_zone = time_zone
        calendar._intervals = intervals
        calendar._exceptions = _normalize_exceptions(exceptions, time_zone)
        if not any(intervals.values()):
            raise ValueError("calendar has no recurring working time")
        return calendar

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
        if minutes < 0:
            return self.subtract_working_minutes(value, -minutes)
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
        if minutes < 0:
            return self.add_working_minutes(value, -minutes)
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

    def shift_working_minutes(self, value: datetime, minutes: int) -> datetime:
        if minutes >= 0:
            return self.add_working_minutes(value, minutes)
        return self.subtract_working_minutes(value, -minutes)

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
        for start_date, finish_date, intervals in self._exceptions:
            if start_date <= value <= finish_date:
                return intervals
        return self._intervals[WEEKDAYS[value.weekday()]]


class CalendarResolutionError(ValueError):
    def __init__(self, code: str, object_refs: tuple[str, ...], message: str) -> None:
        super().__init__(message)
        self.code = code
        self.object_refs = object_refs
        self.message = message


def build_effective_work_calendars(
    calendars: list[Any] | tuple[Any, ...],
    time_zone: ZoneInfo,
) -> dict[str, EffectiveWorkCalendar]:
    by_id = {calendar.calendar_id: calendar for calendar in calendars}
    if len(by_id) != len(calendars):
        raise CalendarResolutionError("DUPLICATE_CALENDAR_ID", (), "日历 ID 必须唯一")

    chains: dict[str, tuple[Any, ...]] = {}

    def resolve(calendar_id: str, path: tuple[str, ...] = ()) -> tuple[Any, ...]:
        if calendar_id in chains:
            return chains[calendar_id]
        if calendar_id in path:
            cycle = path[path.index(calendar_id) :] + (calendar_id,)
            raise CalendarResolutionError("CALENDAR_INHERITANCE_CYCLE", cycle, "日历继承关系存在环路")
        calendar = by_id[calendar_id]
        if calendar.parent_calendar_id is None:
            chain = (calendar,)
        else:
            if calendar.parent_calendar_id not in by_id:
                raise CalendarResolutionError(
                    "CALENDAR_PARENT_NOT_FOUND",
                    (calendar.calendar_id, calendar.parent_calendar_id),
                    "日历引用了不存在的父日历",
                )
            chain = resolve(calendar.parent_calendar_id, path + (calendar_id,)) + (calendar,)
        chains[calendar_id] = chain
        return chain

    result = {}
    for calendar_id in by_id:
        try:
            result[calendar_id] = EffectiveWorkCalendar.from_calendar_chain(resolve(calendar_id), time_zone)
        except CalendarResolutionError:
            raise
        except ValueError as exc:
            raise CalendarResolutionError(
                "CALENDAR_NO_WORKING_TIME",
                (calendar_id,),
                "日历继承解析后没有可用的周期工作时间",
            ) from exc
    return result


def calendar_exception_conflicts(
    exceptions: list[Any] | tuple[Any, ...],
    time_zone: ZoneInfo,
) -> tuple[tuple[str, str], ...]:
    ranges = sorted(
        (
            exception.start_date.astimezone(time_zone).date(),
            exception.finish_date.astimezone(time_zone).date(),
            exception.exception_id,
        )
        for exception in exceptions
    )
    conflicts = []
    active_ranges = []
    for current in ranges:
        active_ranges = [previous for previous in active_ranges if current[0] <= previous[1]]
        conflicts.extend((previous[2], current[2]) for previous in active_ranges)
        active_ranges.append(current)
    return tuple(conflicts)


def _normalize_exceptions(
    exceptions: list[Any] | tuple[Any, ...],
    time_zone: ZoneInfo,
) -> tuple[tuple[date, date, tuple[tuple[time, time], ...]], ...]:
    conflicts = calendar_exception_conflicts(exceptions, time_zone)
    if conflicts:
        raise ValueError("calendar exceptions overlap")
    return tuple(
        (
            exception.start_date.astimezone(time_zone).date(),
            exception.finish_date.astimezone(time_zone).date(),
            tuple(
                (interval.start, interval.finish)
                if hasattr(interval, "start")
                else interval
                for interval in exception.intervals
            ),
        )
        for exception in exceptions
    )


def _calendar_weekly_intervals(calendar: Any) -> dict[str, tuple[tuple[time, time], ...]]:
    if hasattr(calendar, "weekly_pattern"):
        return {
            weekday: tuple((item.start, item.finish) for item in day.intervals)
            for weekday, day in calendar.weekly_pattern
        }
    return calendar.working_intervals


def _calendar_day_types(calendar: Any) -> dict[str, str]:
    if hasattr(calendar, "weekly_pattern"):
        return {weekday: day.day_type for weekday, day in calendar.weekly_pattern}
    return calendar.day_types


# Compatibility name retained for callers of the pre-v10 calendar primitive.
UnifiedWorkCalendar = EffectiveWorkCalendar
