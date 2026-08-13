from yuxi.storage.postgres.models_business import Base
from sqlalchemy import Text

from yuxi.storage.postgres.models_schedule import (
    ScheduleCandidateDecisionRecord,
    ScheduleCandidateRecord,
    ScheduleDependencyDecisionRecord,
    ScheduleIssueRecord,
    ScheduleOptimizationRunRecord,
    ScheduleSnapshotRecord,
)


def test_schedule_tables_share_business_metadata() -> None:
    assert {
        "schedule_snapshots",
        "schedule_audit_runs",
        "schedule_issues",
        "schedule_dependency_decisions",
        "schedule_optimization_runs",
        "schedule_candidates",
        "schedule_candidate_decisions",
    } <= set(Base.metadata.tables)
    assert any(
        constraint.name == "uq_schedule_snapshots_owner_request"
        for constraint in ScheduleSnapshotRecord.__table__.constraints
    )
    assert any(
        constraint.name == "uq_schedule_optimization_owner_request"
        for constraint in ScheduleOptimizationRunRecord.__table__.constraints
    )
    assert any(
        constraint.name == "uq_schedule_optimization_dependency_decision"
        for constraint in ScheduleOptimizationRunRecord.__table__.constraints
    )
    assert ScheduleCandidateRecord.__table__.columns["optimization_id"].unique is True
    assert any(
        constraint.name == "uq_schedule_candidate_decision_request"
        for constraint in ScheduleCandidateDecisionRecord.__table__.constraints
    )
    assert any(
        constraint.name == "uq_schedule_issues_run_key" for constraint in ScheduleIssueRecord.__table__.constraints
    )
    assert isinstance(ScheduleSnapshotRecord.source_snapshot_id.type, Text)
    assert isinstance(ScheduleIssueRecord.sort_key.type, Text)
    assert any(
        constraint.name is None and constraint.columns.keys() == ["issue_id"]
        for constraint in ScheduleDependencyDecisionRecord.__table__.constraints
        if hasattr(constraint, "columns")
    )
