"""PostgreSQL models for immutable Schedule snapshots and audit results."""

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from yuxi.storage.postgres.models_business import Base
from yuxi.utils.datetime_utils import utc_now_naive


class ScheduleSnapshotRecord(Base):
    __tablename__ = "schedule_snapshots"

    schedule_snapshot_id = Column(String(64), primary_key=True)
    owner_uid = Column(String(64), nullable=False)
    request_id = Column(String(128), nullable=False)
    external_project_id = Column(String(256), nullable=False)
    external_snapshot_id = Column(String(256), nullable=False)
    external_revision = Column(String(256), nullable=False)
    source_snapshot_id = Column(Text, nullable=False)
    schema_version = Column(String(64), nullable=False)
    snapshot_content_sha256 = Column(String(80), nullable=False)
    source_schema_version = Column(String(128), nullable=True)
    adapter_id = Column(String(128), nullable=True)
    adapter_version = Column(String(64), nullable=True)
    source_document_sha256 = Column(String(80), nullable=True)
    source_document_object = Column(String(1024), nullable=True)
    normalization_report = Column(JSON, nullable=True)
    minio_bucket = Column(String(128), nullable=False)
    minio_object = Column(String(1024), nullable=False)
    submission_status = Column(String(16), nullable=False, default="creating")
    execution_token = Column(String(64), nullable=True)
    execution_started_at = Column(DateTime, nullable=True)
    failure_code = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    ready_at = Column(DateTime, nullable=True)

    audit_run = relationship(
        "ScheduleAuditRunRecord",
        back_populates="snapshot",
        uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("owner_uid", "request_id", name="uq_schedule_snapshots_owner_request"),
        Index("ix_schedule_snapshots_owner_project_created", "owner_uid", "external_project_id", "created_at"),
    )


class ScheduleAuditRunRecord(Base):
    __tablename__ = "schedule_audit_runs"

    audit_run_id = Column(String(64), primary_key=True)
    schedule_snapshot_id = Column(
        String(64),
        ForeignKey("schedule_snapshots.schedule_snapshot_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    rule_set_version = Column(String(64), nullable=False)
    statistics = Column(JSON, nullable=False)
    capabilities = Column(JSON, nullable=False)
    dependency_date_checks = Column(JSON, nullable=False)
    issue_summary = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)

    snapshot = relationship("ScheduleSnapshotRecord", back_populates="audit_run")
    issues = relationship("ScheduleIssueRecord", back_populates="audit_run", cascade="all, delete-orphan")


class ScheduleIssueRecord(Base):
    __tablename__ = "schedule_issues"

    issue_id = Column(String(64), primary_key=True)
    audit_run_id = Column(
        String(64),
        ForeignKey("schedule_audit_runs.audit_run_id", ondelete="CASCADE"),
        nullable=False,
    )
    schedule_snapshot_id = Column(String(64), nullable=False, index=True)
    issue_key = Column(String(80), nullable=False)
    rule_id = Column(String(64), nullable=False, index=True)
    rule_version = Column(String(32), nullable=False)
    category = Column(String(64), nullable=False, index=True)
    severity = Column(String(16), nullable=False, index=True)
    object_refs = Column(JSON, nullable=False)
    evidence = Column(JSON, nullable=False)
    message = Column(Text, nullable=False)
    recommendation = Column(Text, nullable=False)
    sort_key = Column(Text, nullable=False)

    audit_run = relationship("ScheduleAuditRunRecord", back_populates="issues")

    __table_args__ = (UniqueConstraint("audit_run_id", "issue_key", name="uq_schedule_issues_run_key"),)


class ScheduleDependencyDecisionRecord(Base):
    __tablename__ = "schedule_dependency_decisions"

    decision_id = Column(String(64), primary_key=True)
    owner_uid = Column(String(64), nullable=False, index=True)
    issue_id = Column(
        String(64),
        ForeignKey("schedule_issues.issue_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    schedule_snapshot_id = Column(String(64), nullable=False, index=True)
    status = Column(String(16), nullable=False, default="draft")
    resolution = Column(String(32), nullable=False)
    predecessor_task_ids = Column(JSON, nullable=False, default=list)
    successor_task_ids = Column(JSON, nullable=False, default=list)
    dependency_type = Column(String(2), nullable=True)
    lag_minutes = Column(Integer, nullable=True)
    reason = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)
    confirmed_at = Column(DateTime, nullable=True)


class ScheduleOptimizationRunRecord(Base):
    __tablename__ = "schedule_optimization_runs"

    optimization_id = Column(String(64), primary_key=True)
    candidate_snapshot_id = Column(String(64), nullable=False, unique=True)
    owner_uid = Column(String(64), nullable=False, index=True)
    request_id = Column(String(128), nullable=False)
    dependency_decision_id = Column(
        String(64),
        ForeignKey("schedule_dependency_decisions.decision_id", ondelete="RESTRICT"),
        nullable=True,
    )
    base_schedule_snapshot_id = Column(String(64), nullable=False, index=True)
    base_snapshot_content_sha256 = Column(String(80), nullable=False)
    strategy_id = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, default="creating")
    requested_patch = Column(JSON, nullable=True)
    failure_code = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)

    __table_args__ = (
        UniqueConstraint("owner_uid", "request_id", name="uq_schedule_optimization_owner_request"),
        UniqueConstraint("dependency_decision_id", name="uq_schedule_optimization_dependency_decision"),
    )


class ScheduleCandidateRecord(Base):
    __tablename__ = "schedule_candidates"

    candidate_snapshot_id = Column(String(64), primary_key=True)
    owner_uid = Column(String(64), nullable=False, index=True)
    optimization_id = Column(
        String(64),
        ForeignKey("schedule_optimization_runs.optimization_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    dependency_decision_id = Column(String(64), nullable=True)
    base_schedule_snapshot_id = Column(String(64), nullable=False, index=True)
    candidate_schema_version = Column(String(64), nullable=False)
    candidate_kind = Column(String(64), nullable=False)
    candidate_status = Column(String(16), nullable=False)
    minio_bucket = Column(String(128), nullable=False)
    minio_object = Column(String(1024), nullable=False)
    effective_patch = Column(JSON, nullable=False)
    comparison = Column(JSON, nullable=False)
    candidate_audit = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)


class ScheduleCandidateDecisionRecord(Base):
    __tablename__ = "schedule_candidate_decisions"

    candidate_decision_id = Column(String(64), primary_key=True)
    owner_uid = Column(String(64), nullable=False, index=True)
    request_id = Column(String(128), nullable=False)
    candidate_snapshot_id = Column(
        String(64),
        ForeignKey("schedule_candidates.candidate_snapshot_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attitude = Column(String(16), nullable=False)
    comment = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)

    __table_args__ = (
        UniqueConstraint(
            "owner_uid",
            "candidate_snapshot_id",
            "request_id",
            name="uq_schedule_candidate_decision_request",
        ),
    )
