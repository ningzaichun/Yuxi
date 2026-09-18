"""Canonical Schedule v2.4 contract for structured project-calendar exceptions."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from .canonical_v2_2 import (
    MAX_DEPENDENCIES,
    CanonicalCalendar,
    CanonicalDependency,
    ContractModel,
    ScheduleSemantics,
    WorkingInterval,
)
from .canonical_v2_3 import CanonicalScheduleV23

LAG_CALENDAR_POLICY_SUCCESSOR = "SUCCESSOR_TASK_CALENDAR"


class ScheduleSemanticsV24(ScheduleSemantics):
    lag_calendar_policy: Literal[
        "UNSPECIFIED_REQUIRES_ENGINE_PROFILE",
        "UNIFIED_PROJECT_CALENDAR_WORKING_MINUTES",
        "SUCCESSOR_TASK_CALENDAR",
    ]


class CanonicalDependencyV24(CanonicalDependency):
    lag_calendar_policy: Literal[
        "UNSPECIFIED_REQUIRES_ENGINE_PROFILE",
        "UNIFIED_PROJECT_CALENDAR_WORKING_MINUTES",
        "SUCCESSOR_TASK_CALENDAR",
    ]


class CalendarExceptionV24(ContractModel):
    exception_id: str
    name: str
    start_date: AwareDatetime
    finish_date: AwareDatetime
    working: bool
    intervals: list[WorkingInterval]

    @model_validator(mode="after")
    def validate_range_and_intervals(self) -> Self:
        if self.finish_date < self.start_date:
            raise ValueError(f"calendar exception {self.exception_id} finish precedes start")
        if self.working != bool(self.intervals):
            raise ValueError(
                f"calendar exception {self.exception_id} working flag must match intervals"
            )
        previous_finish = None
        for interval in self.intervals:
            if interval.start >= interval.finish or (
                previous_finish is not None and interval.start < previous_finish
            ):
                raise ValueError(f"calendar exception {self.exception_id} has invalid intervals")
            previous_finish = interval.finish
        return self


class CanonicalCalendarV24(CanonicalCalendar):
    exceptions: list[CalendarExceptionV24]

    @model_validator(mode="after")
    def validate_exception_identity(self) -> Self:
        exception_ids = [exception.exception_id for exception in self.exceptions]
        if len(exception_ids) != len(set(exception_ids)):
            raise ValueError(f"calendar {self.calendar_id} has duplicate exception_id")
        return self


class CanonicalScheduleV24(CanonicalScheduleV23):
    schema_version: Literal["canonical_schedule_v2.4"]
    semantics: ScheduleSemanticsV24
    calendars: list[CanonicalCalendarV24] = Field()
    dependencies: list[CanonicalDependencyV24] = Field(max_length=MAX_DEPENDENCIES)
