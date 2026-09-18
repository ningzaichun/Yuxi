"""Canonical Schedule v2.5 contract for hard constraints and management targets."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from .canonical_v2_2 import MAX_TASKS, CanonicalProject, TaskConstraint
from .canonical_v2_3 import CanonicalTaskV23
from .canonical_v2_4 import CanonicalScheduleV24


class CanonicalProjectV25(CanonicalProject):
    required_finish: AwareDatetime | None = None


class TaskConstraintV25(TaskConstraint):
    type: Literal[
        "AS_SOON_AS_POSSIBLE",
        "START_NO_EARLIER_THAN",
        "FINISH_NO_EARLIER_THAN",
        "MUST_START_ON",
        "FINISH_NO_LATER_THAN",
    ]

    @model_validator(mode="after")
    def validate_constraint_date(self) -> Self:
        if self.type == "AS_SOON_AS_POSSIBLE" and self.date is not None:
            raise ValueError("ASAP constraint must not contain a date")
        if self.type != "AS_SOON_AS_POSSIBLE" and self.date is None:
            raise ValueError(f"{self.type} constraint requires a date")
        return self


class CanonicalTaskV25(CanonicalTaskV23):
    constraint: TaskConstraintV25


class CanonicalScheduleV25(CanonicalScheduleV24):
    schema_version: Literal["canonical_schedule_v2.5"]
    project: CanonicalProjectV25
    tasks: list[CanonicalTaskV25] = Field(max_length=MAX_TASKS)
