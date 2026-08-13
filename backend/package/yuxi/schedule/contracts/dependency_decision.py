"""Contracts for the S1 summary-dependency confirmation workbench."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DependencyDecisionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: Literal["replace_with_leaf_tasks", "add_milestone", "defer"]
    predecessor_task_ids: list[str] = Field(default_factory=list)
    successor_task_ids: list[str] = Field(default_factory=list)
    dependency_type: Literal["FS", "SS", "FF", "SF"] | None = None
    lag_minutes: int | None = None
    reason: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_resolution_shape(self):
        if len(self.predecessor_task_ids) != len(set(self.predecessor_task_ids)) or len(
            self.successor_task_ids
        ) != len(set(self.successor_task_ids)):
            raise ValueError("替代依赖任务不能重复")
        if self.resolution != "replace_with_leaf_tasks" and (
            self.predecessor_task_ids
            or self.successor_task_ids
            or self.dependency_type is not None
            or self.lag_minutes is not None
        ):
            raise ValueError("新增里程碑或暂不处理时不能提交替代依赖字段")
        return self
