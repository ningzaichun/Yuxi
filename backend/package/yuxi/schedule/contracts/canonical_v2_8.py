"""Canonical Schedule v2.8 contract for inactive task source facts."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from .canonical_v2_2 import MAX_TASKS
from .canonical_v2_5 import CanonicalTaskV25
from .canonical_v2_7 import CanonicalScheduleV27


class CanonicalTaskV28(CanonicalTaskV25):
    @model_validator(mode="after")
    def validate_inactive_facts(self) -> Self:
        if not self.active and (
            self.percent_complete != 0
            or self.actual_start is not None
            or self.actual_finish is not None
        ):
            raise ValueError(
                f"inactive task {self.task_id} cannot carry actual or progress facts"
            )
        return self


class CanonicalScheduleV28(CanonicalScheduleV27):
    schema_version: Literal["canonical_schedule_v2.8"]
    tasks: list[CanonicalTaskV28] = Field(max_length=MAX_TASKS)

    @model_validator(mode="after")
    def validate_inactive_assignments(self) -> Self:
        tasks_by_id = {task.task_id: task for task in self.tasks}
        for assignment in self.assignments:
            if not tasks_by_id[assignment.task_id].active:
                raise ValueError(
                    f"assignment {assignment.assignment_id} cannot reference an inactive task"
                )
        return self
