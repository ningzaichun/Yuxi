"""Stable output contracts shared by audit, HTTP, and Agent tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class OutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Capability(OutputModel):
    allowed: bool
    reasons: list[str] = Field(default_factory=list)


class DependencyDateChecks(OutputModel):
    checked: int = Field(ge=0)
    skipped: int = Field(ge=0)
    violation_count: int = Field(ge=0)
    skipped_reasons: dict[str, int] = Field(default_factory=dict)


class IssueSummary(OutputModel):
    total: int = Field(ge=0)
    blocker: int = Field(ge=0)
    warning: int = Field(ge=0)
    info: int = Field(ge=0)


class Issue(OutputModel):
    issue_id: str
    issue_key: str
    audit_run_id: str
    schedule_snapshot_id: str
    rule_id: str
    rule_version: str
    origin: Literal["YUXI_AUDIT"] = "YUXI_AUDIT"
    category: str
    severity: Literal["blocker", "warning", "info"]
    object_refs: list[str]
    evidence: dict[str, Any]
    message: str
    recommendation: str
    status: Literal["open"] = "open"


class AuditResult(OutputModel):
    audit_run_id: str
    schedule_snapshot_id: str
    rule_set_version: str
    statistics: dict[str, Any]
    capabilities: dict[str, Capability]
    dependency_date_checks: DependencyDateChecks
    issue_summary: IssueSummary
    created_at: AwareDatetime | None = None


class ScheduleFieldError(OutputModel):
    path: str
    code: str
    message: str


class ScheduleErrorDetail(OutputModel):
    code: str
    message: str
    errors: list[ScheduleFieldError] = Field(default_factory=list)
