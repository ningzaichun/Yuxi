from yuxi.storage.postgres.models_business import Base
from sqlalchemy import Text

from yuxi.storage.postgres.models_schedule import ScheduleIssueRecord, ScheduleSnapshotRecord


def test_schedule_tables_share_business_metadata() -> None:
    assert {"schedule_snapshots", "schedule_audit_runs", "schedule_issues"} <= set(Base.metadata.tables)
    assert any(
        constraint.name == "uq_schedule_snapshots_owner_request"
        for constraint in ScheduleSnapshotRecord.__table__.constraints
    )
    assert any(
        constraint.name == "uq_schedule_issues_run_key" for constraint in ScheduleIssueRecord.__table__.constraints
    )
    assert isinstance(ScheduleSnapshotRecord.source_snapshot_id.type, Text)
    assert isinstance(ScheduleIssueRecord.sort_key.type, Text)
