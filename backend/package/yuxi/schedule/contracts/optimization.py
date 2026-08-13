"""Restricted contracts for the S2 dependency-normalization slice."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


class CandidateDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    attitude: Literal["accepted", "rejected"]
    comment: str = Field(default="", max_length=2000)
