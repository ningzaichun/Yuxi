"""Canonical Schedule v2.7 contract for resources and assignments."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, Self

from pydantic import Field, model_validator

from .canonical_v2_2 import MAX_TASKS, CanonicalResource, ContractModel
from .canonical_v2_5 import CanonicalScheduleV25


class CanonicalResourceV27(CanonicalResource):
    resource_type: Literal["WORK", "EQUIPMENT"]
    max_units: Decimal = Field(gt=0)
    standard_rate_per_hour: Decimal = Field(ge=0)


class CanonicalAssignmentV27(ContractModel):
    assignment_id: str
    task_id: str
    resource_id: str
    units: Decimal = Field(gt=0)


class CanonicalScheduleV27(CanonicalScheduleV25):
    schema_version: Literal["canonical_schedule_v2.7"]
    resources: list[CanonicalResourceV27] = Field(max_length=MAX_TASKS)
    assignments: list[CanonicalAssignmentV27] = Field(max_length=MAX_TASKS)

    @model_validator(mode="after")
    def validate_assignment_references(self) -> Self:
        assignment_ids = [assignment.assignment_id for assignment in self.assignments]
        if len(assignment_ids) != len(set(assignment_ids)):
            raise ValueError("duplicate assignment_id")

        tasks_by_id = {task.task_id: task for task in self.tasks}
        resource_ids = {resource.resource_id for resource in self.resources}
        for assignment in self.assignments:
            task = tasks_by_id.get(assignment.task_id)
            if task is None:
                raise ValueError(f"assignment {assignment.assignment_id} references an unknown task")
            if assignment.resource_id not in resource_ids:
                raise ValueError(f"assignment {assignment.assignment_id} references an unknown resource")
            if task.task_type != "activity":
                raise ValueError(f"assignment {assignment.assignment_id} must reference an activity task")
        return self
