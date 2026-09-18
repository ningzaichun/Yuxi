"""Canonical Schedule v2.3 contract for milestones and explicit network boundaries."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from .canonical_v2_2 import MAX_TASKS, CanonicalScheduleV22, CanonicalTask


class CanonicalTaskV23(CanonicalTask):
    task_type: Literal["activity", "milestone", "summary"]
    boundary_role: Literal["PROJECT_START", "PROJECT_FINISH"] | None = None

    @model_validator(mode="after")
    def validate_task_kind(self) -> Self:
        if self.task_type == "milestone" and self.duration_minutes != 0:
            raise ValueError(f"milestone task {self.task_id} must have zero duration")
        if self.task_type == "activity" and self.duration_minutes == 0:
            raise ValueError(f"activity task {self.task_id} must have positive duration")
        if self.task_type == "summary" and self.boundary_role is not None:
            raise ValueError(f"summary task {self.task_id} cannot be a network boundary")
        return self


class CanonicalScheduleV23(CanonicalScheduleV22):
    schema_version: Literal["canonical_schedule_v2.3"]
    tasks: list[CanonicalTaskV23] = Field(max_length=MAX_TASKS)
