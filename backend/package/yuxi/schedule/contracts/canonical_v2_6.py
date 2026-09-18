"""Canonical Schedule v2.6 contract for status date, actual facts and Baseline 0."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from .canonical_v2_2 import MAX_TASKS, TaskBaseline
from .canonical_v2_5 import CanonicalProjectV25, CanonicalScheduleV25, CanonicalTaskV25


class CanonicalProjectV26(CanonicalProjectV25):
    status_date: AwareDatetime


class TaskBaselineV26(TaskBaseline):
    @model_validator(mode="after")
    def validate_baseline(self) -> Self:
        if self.exists != (self.start is not None and self.finish is not None):
            raise ValueError("Baseline 0 exists must match complete start and finish facts")
        if self.start is not None and self.finish is not None and self.finish < self.start:
            raise ValueError("Baseline 0 finish must not precede start")
        return self


class CanonicalTaskV26(CanonicalTaskV25):
    status: Literal["NOT_STARTED", "IN_PROGRESS", "COMPLETED"]
    remaining_duration_minutes: int | None = Field(default=None, ge=0)
    baseline_0: TaskBaselineV26

    @model_validator(mode="after")
    def validate_progress_facts(self) -> Self:
        if self.task_type == "summary":
            if self.status != "NOT_STARTED" or any(
                value is not None
                for value in (self.actual_start, self.actual_finish, self.remaining_duration_minutes)
            ):
                raise ValueError("summary tasks cannot carry actual progress facts")
            return self
        if self.status == "NOT_STARTED":
            if self.percent_complete != 0 or any(
                value is not None
                for value in (self.actual_start, self.actual_finish, self.remaining_duration_minutes)
            ):
                raise ValueError("NOT_STARTED tasks cannot carry actual progress facts")
        elif self.status == "COMPLETED":
            if self.percent_complete != 100 or self.actual_start is None or self.actual_finish is None:
                raise ValueError("COMPLETED tasks require 100 percent and actual start/finish")
            if self.actual_finish < self.actual_start:
                raise ValueError("actual finish must not precede actual start")
            if self.remaining_duration_minutes not in {None, 0}:
                raise ValueError("COMPLETED tasks cannot have remaining duration")
        else:
            if (
                self.percent_complete >= 100
                or self.actual_start is None
                or self.actual_finish is not None
                or not self.remaining_duration_minutes
            ):
                raise ValueError(
                    "IN_PROGRESS tasks require actual start, positive remaining duration and no actual finish"
                )
        if self.task_type == "milestone":
            if self.actual_start is not None and self.actual_start != self.actual_finish:
                raise ValueError("completed milestone actual start and finish must match")
            if self.baseline_0.exists and self.baseline_0.start != self.baseline_0.finish:
                raise ValueError("milestone Baseline 0 start and finish must match")
        return self


class CanonicalScheduleV26(CanonicalScheduleV25):
    schema_version: Literal["canonical_schedule_v2.6"]
    project: CanonicalProjectV26
    tasks: list[CanonicalTaskV26] = Field(max_length=MAX_TASKS)

    @model_validator(mode="after")
    def validate_actual_facts_against_status_date(self) -> Self:
        for task in self.tasks:
            if task.actual_start is not None and task.actual_start > self.project.status_date:
                raise ValueError(f"task {task.task_id} actual start is after project status date")
            if task.actual_finish is not None and task.actual_finish > self.project.status_date:
                raise ValueError(f"task {task.task_id} actual finish is after project status date")
        return self
