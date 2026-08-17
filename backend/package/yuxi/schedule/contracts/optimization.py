"""Restricted contracts for deterministic schedule candidates."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class DependencyOptimizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    dependency_decision_id: str = Field(min_length=1, max_length=64)
    base_snapshot_content_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ForwardRecalculationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    base_snapshot_content_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    locked_task_ids: list[str] = Field(default_factory=list, max_length=10_000)


class AuthorizedDurationOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=256)
    duration_minutes: int = Field(ge=1)


class GoalOptimizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    base_snapshot_content_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    objective: Literal["MINIMIZE_PROJECT_FINISH", "MEET_TARGET_FINISH"]
    target_finish: AwareDatetime | None = None
    authorized_duration_options: list[AuthorizedDurationOption] = Field(min_length=1, max_length=10)
    locked_task_ids: list[str] = Field(default_factory=list, max_length=10_000)
    authorization_confirmed: Literal[True]

    @model_validator(mode="after")
    def validate_goal(self) -> "GoalOptimizationRequest":
        if self.objective == "MEET_TARGET_FINISH" and self.target_finish is None:
            raise ValueError("MEET_TARGET_FINISH requires target_finish")
        if self.objective == "MINIMIZE_PROJECT_FINISH" and self.target_finish is not None:
            raise ValueError("MINIMIZE_PROJECT_FINISH does not accept target_finish")
        task_ids = [item.task_id for item in self.authorized_duration_options]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("authorized_duration_options contains duplicate task_id")
        if len(self.locked_task_ids) != len(set(self.locked_task_ids)):
            raise ValueError("locked_task_ids contains duplicate task_id")
        if set(task_ids) & set(self.locked_task_ids):
            raise ValueError("authorized_duration_options cannot overlap locked_task_ids")
        return self


class CandidateDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    attitude: Literal["accepted", "rejected"]
    comment: str = Field(default="", max_length=2000)
