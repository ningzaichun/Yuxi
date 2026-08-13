"""Persistence boundary for Schedule snapshots, audits, and issues."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from yuxi.schedule.domain.models import AuditExecution
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_schedule import (
    ScheduleAuditRunRecord,
    ScheduleCandidateDecisionRecord,
    ScheduleCandidateRecord,
    ScheduleDependencyDecisionRecord,
    ScheduleIssueRecord,
    ScheduleOptimizationRunRecord,
    ScheduleSnapshotRecord,
)
from yuxi.utils.datetime_utils import utc_now_naive


class ScheduleRepository:
    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory or pg_manager.get_async_session_context

    async def reserve(self, values: dict[str, Any]) -> tuple[ScheduleSnapshotRecord, bool]:
        """Use PostgreSQL conflict arbitration instead of a racy read-before-insert."""
        async with self._session_factory() as session:
            statement = (
                insert(ScheduleSnapshotRecord)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["owner_uid", "request_id"])
                .returning(ScheduleSnapshotRecord)
            )
            created = (await session.execute(statement)).scalar_one_or_none()
            if created is not None:
                return created, True
            existing = await session.scalar(
                select(ScheduleSnapshotRecord).where(
                    ScheduleSnapshotRecord.owner_uid == values["owner_uid"],
                    ScheduleSnapshotRecord.request_id == values["request_id"],
                )
            )
            if existing is None:
                raise RuntimeError("idempotency reservation disappeared")
            return existing, False

    async def claim_recoverable(
        self,
        schedule_snapshot_id: str,
        execution_token: str,
        *,
        execution_started_at,
        stale_before,
    ) -> bool:
        """Claim a failed submission or a creating submission whose execution lease expired."""
        async with self._session_factory() as session:
            result = await session.execute(
                update(ScheduleSnapshotRecord)
                .where(
                    ScheduleSnapshotRecord.schedule_snapshot_id == schedule_snapshot_id,
                    or_(
                        ScheduleSnapshotRecord.submission_status == "failed",
                        and_(
                            ScheduleSnapshotRecord.submission_status == "creating",
                            or_(
                                ScheduleSnapshotRecord.execution_started_at.is_(None),
                                ScheduleSnapshotRecord.execution_started_at <= stale_before,
                            ),
                        ),
                    ),
                )
                .values(
                    submission_status="creating",
                    execution_token=execution_token,
                    execution_started_at=execution_started_at,
                    failure_code=None,
                )
            )
            return result.rowcount == 1

    async def get_submission(self, owner_uid: str, request_id: str) -> ScheduleSnapshotRecord | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(ScheduleSnapshotRecord).where(
                    ScheduleSnapshotRecord.owner_uid == owner_uid,
                    ScheduleSnapshotRecord.request_id == request_id,
                )
            )

    async def finalize(
        self,
        schedule_snapshot_id: str,
        execution_token: str,
        execution: AuditExecution,
    ) -> None:
        async with self._session_factory() as session:
            snapshot = await session.scalar(
                select(ScheduleSnapshotRecord).where(
                    ScheduleSnapshotRecord.schedule_snapshot_id == schedule_snapshot_id,
                    ScheduleSnapshotRecord.submission_status == "creating",
                    ScheduleSnapshotRecord.execution_token == execution_token,
                )
            )
            if snapshot is None:
                raise RuntimeError("schedule submission ownership was lost")
            audit = ScheduleAuditRunRecord(
                audit_run_id=execution.result.audit_run_id,
                schedule_snapshot_id=schedule_snapshot_id,
                rule_set_version=execution.result.rule_set_version,
                statistics=execution.result.statistics,
                capabilities={
                    key: value.model_dump(mode="json") for key, value in execution.result.capabilities.items()
                },
                dependency_date_checks=execution.result.dependency_date_checks.model_dump(mode="json"),
                issue_summary=execution.result.issue_summary.model_dump(mode="json"),
            )
            session.add(audit)
            for finding in execution.findings:
                primary_ref = finding.object_refs[0] if finding.object_refs else ""
                session.add(
                    ScheduleIssueRecord(
                        issue_id=uuid.uuid4().hex,
                        audit_run_id=audit.audit_run_id,
                        schedule_snapshot_id=schedule_snapshot_id,
                        issue_key=finding.issue_key,
                        rule_id=finding.rule_id,
                        rule_version=finding.rule_version,
                        category=finding.category,
                        severity=finding.severity,
                        object_refs=list(finding.object_refs),
                        evidence=finding.evidence,
                        message=finding.message,
                        recommendation=finding.recommendation,
                        sort_key=f"{_severity_order(finding.severity)}|{finding.category}|{finding.rule_id}|{primary_ref}|{finding.issue_key}",
                    )
                )
            snapshot.submission_status = "ready"
            snapshot.execution_token = None
            snapshot.execution_started_at = None
            snapshot.ready_at = utc_now_naive()

    async def mark_failed(self, schedule_snapshot_id: str, execution_token: str, failure_code: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(ScheduleSnapshotRecord)
                .where(
                    ScheduleSnapshotRecord.schedule_snapshot_id == schedule_snapshot_id,
                    ScheduleSnapshotRecord.execution_token == execution_token,
                )
                .values(
                    submission_status="failed",
                    execution_token=None,
                    execution_started_at=None,
                    failure_code=failure_code,
                )
            )

    async def list_ready(self, owner_uid: str, limit: int, offset: int) -> list[ScheduleSnapshotRecord]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ScheduleSnapshotRecord)
                .where(
                    ScheduleSnapshotRecord.owner_uid == owner_uid,
                    ScheduleSnapshotRecord.submission_status == "ready",
                )
                .order_by(ScheduleSnapshotRecord.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return list(result.scalars())

    async def get_ready(self, owner_uid: str, snapshot_id: str) -> ScheduleSnapshotRecord | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(ScheduleSnapshotRecord).where(
                    ScheduleSnapshotRecord.owner_uid == owner_uid,
                    ScheduleSnapshotRecord.schedule_snapshot_id == snapshot_id,
                    ScheduleSnapshotRecord.submission_status == "ready",
                )
            )

    async def get_audit(self, owner_uid: str, snapshot_id: str) -> ScheduleAuditRunRecord | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(ScheduleAuditRunRecord)
                .join(ScheduleSnapshotRecord)
                .where(
                    ScheduleSnapshotRecord.owner_uid == owner_uid,
                    ScheduleSnapshotRecord.schedule_snapshot_id == snapshot_id,
                    ScheduleSnapshotRecord.submission_status == "ready",
                )
            )

    async def list_issues(
        self,
        owner_uid: str,
        snapshot_id: str,
        *,
        category: str | None,
        severity: str | None,
        limit: int,
        offset: int,
    ) -> list[ScheduleIssueRecord] | None:
        if await self.get_ready(owner_uid, snapshot_id) is None:
            return None
        async with self._session_factory() as session:
            statement = select(ScheduleIssueRecord).where(ScheduleIssueRecord.schedule_snapshot_id == snapshot_id)
            if category:
                statement = statement.where(ScheduleIssueRecord.category == category)
            if severity:
                statement = statement.where(ScheduleIssueRecord.severity == severity)
            result = await session.execute(statement.order_by(ScheduleIssueRecord.sort_key).limit(limit).offset(offset))
            return list(result.scalars())

    async def list_all_issues(self, owner_uid: str, snapshot_id: str) -> list[ScheduleIssueRecord] | None:
        if await self.get_ready(owner_uid, snapshot_id) is None:
            return None
        async with self._session_factory() as session:
            result = await session.execute(
                select(ScheduleIssueRecord)
                .where(ScheduleIssueRecord.schedule_snapshot_id == snapshot_id)
                .order_by(ScheduleIssueRecord.sort_key)
            )
            return list(result.scalars())

    async def get_issue(self, owner_uid: str, issue_id: str) -> ScheduleIssueRecord | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(ScheduleIssueRecord)
                .join(
                    ScheduleSnapshotRecord,
                    ScheduleSnapshotRecord.schedule_snapshot_id == ScheduleIssueRecord.schedule_snapshot_id,
                )
                .where(
                    ScheduleIssueRecord.issue_id == issue_id,
                    ScheduleSnapshotRecord.owner_uid == owner_uid,
                    ScheduleSnapshotRecord.submission_status == "ready",
                )
            )

    async def get_dependency_decision(self, owner_uid: str, issue_id: str) -> ScheduleDependencyDecisionRecord | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(ScheduleDependencyDecisionRecord).where(
                    ScheduleDependencyDecisionRecord.owner_uid == owner_uid,
                    ScheduleDependencyDecisionRecord.issue_id == issue_id,
                )
            )

    async def save_dependency_decision(
        self,
        owner_uid: str,
        issue: ScheduleIssueRecord,
        values: dict[str, Any],
    ) -> ScheduleDependencyDecisionRecord:
        async with self._session_factory() as session:
            existing = await session.scalar(
                select(ScheduleDependencyDecisionRecord).where(
                    ScheduleDependencyDecisionRecord.issue_id == issue.issue_id
                )
            )
            if existing is not None:
                if existing.owner_uid != owner_uid or existing.status == "confirmed":
                    raise ValueError("dependency decision is not editable")
                for key, value in values.items():
                    setattr(existing, key, value)
                existing.updated_at = utc_now_naive()
                await session.flush()
                return existing

            record = ScheduleDependencyDecisionRecord(
                decision_id=uuid.uuid4().hex,
                owner_uid=owner_uid,
                issue_id=issue.issue_id,
                schedule_snapshot_id=issue.schedule_snapshot_id,
                status="draft",
                **values,
            )
            session.add(record)
            await session.flush()
            return record

    async def confirm_dependency_decision(
        self, owner_uid: str, issue_id: str
    ) -> ScheduleDependencyDecisionRecord | None:
        async with self._session_factory() as session:
            record = await session.scalar(
                select(ScheduleDependencyDecisionRecord).where(
                    ScheduleDependencyDecisionRecord.owner_uid == owner_uid,
                    ScheduleDependencyDecisionRecord.issue_id == issue_id,
                )
            )
            if record is None:
                return None
            if record.status == "confirmed":
                return record
            record.status = "confirmed"
            record.confirmed_at = utc_now_naive()
            record.updated_at = record.confirmed_at
            await session.flush()
            return record

    async def get_dependency_decision_by_id(
        self, owner_uid: str, decision_id: str
    ) -> ScheduleDependencyDecisionRecord | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(ScheduleDependencyDecisionRecord).where(
                    ScheduleDependencyDecisionRecord.owner_uid == owner_uid,
                    ScheduleDependencyDecisionRecord.decision_id == decision_id,
                )
            )

    async def get_latest_ready_for_project(
        self, owner_uid: str, external_project_id: str
    ) -> ScheduleSnapshotRecord | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(ScheduleSnapshotRecord)
                .where(
                    ScheduleSnapshotRecord.owner_uid == owner_uid,
                    ScheduleSnapshotRecord.external_project_id == external_project_id,
                    ScheduleSnapshotRecord.submission_status == "ready",
                )
                .order_by(
                    ScheduleSnapshotRecord.created_at.desc(),
                    ScheduleSnapshotRecord.schedule_snapshot_id.desc(),
                )
                .limit(1)
            )

    async def reserve_optimization(self, values: dict[str, Any]) -> tuple[ScheduleOptimizationRunRecord, bool]:
        async with self._session_factory() as session:
            statement = (
                insert(ScheduleOptimizationRunRecord)
                .values(**values)
                .on_conflict_do_nothing()
                .returning(ScheduleOptimizationRunRecord)
            )
            created = (await session.execute(statement)).scalar_one_or_none()
            if created is not None:
                return created, True
            identity = ScheduleOptimizationRunRecord.request_id == values["request_id"]
            if values.get("dependency_decision_id") is not None:
                identity = or_(
                    identity,
                    ScheduleOptimizationRunRecord.dependency_decision_id == values["dependency_decision_id"],
                )
            existing = await session.scalar(
                select(ScheduleOptimizationRunRecord).where(
                    ScheduleOptimizationRunRecord.owner_uid == values["owner_uid"],
                    identity,
                )
            )
            if existing is None:
                raise RuntimeError("optimization idempotency reservation disappeared")
            return existing, False

    async def finalize_optimization(
        self,
        optimization_id: str,
        requested_patch: dict[str, Any],
        candidate_values: dict[str, Any],
    ) -> ScheduleCandidateRecord:
        async with self._session_factory() as session:
            optimization = await session.get(ScheduleOptimizationRunRecord, optimization_id)
            if optimization is None or optimization.status != "creating":
                raise RuntimeError("optimization is not finalizable")
            optimization.status = candidate_values["candidate_status"]
            optimization.requested_patch = requested_patch
            optimization.updated_at = utc_now_naive()
            candidate = ScheduleCandidateRecord(**candidate_values)
            session.add(candidate)
            await session.flush()
            return candidate

    async def mark_optimization_failed(self, optimization_id: str, failure_code: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(ScheduleOptimizationRunRecord)
                .where(ScheduleOptimizationRunRecord.optimization_id == optimization_id)
                .values(status="failed", failure_code=failure_code, updated_at=utc_now_naive())
            )

    async def get_optimization(self, owner_uid: str, optimization_id: str) -> ScheduleOptimizationRunRecord | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(ScheduleOptimizationRunRecord).where(
                    ScheduleOptimizationRunRecord.owner_uid == owner_uid,
                    ScheduleOptimizationRunRecord.optimization_id == optimization_id,
                )
            )

    async def get_candidate(self, owner_uid: str, candidate_snapshot_id: str) -> ScheduleCandidateRecord | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(ScheduleCandidateRecord).where(
                    ScheduleCandidateRecord.owner_uid == owner_uid,
                    ScheduleCandidateRecord.candidate_snapshot_id == candidate_snapshot_id,
                )
            )

    async def get_candidate_by_optimization(
        self, owner_uid: str, optimization_id: str
    ) -> ScheduleCandidateRecord | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(ScheduleCandidateRecord).where(
                    ScheduleCandidateRecord.owner_uid == owner_uid,
                    ScheduleCandidateRecord.optimization_id == optimization_id,
                )
            )

    async def get_candidate_by_dependency_decision(
        self, owner_uid: str, dependency_decision_id: str
    ) -> ScheduleCandidateRecord | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(ScheduleCandidateRecord).where(
                    ScheduleCandidateRecord.owner_uid == owner_uid,
                    ScheduleCandidateRecord.dependency_decision_id == dependency_decision_id,
                )
            )

    async def save_candidate_decision(
        self,
        owner_uid: str,
        candidate_snapshot_id: str,
        values: dict[str, Any],
    ) -> tuple[ScheduleCandidateDecisionRecord, bool]:
        async with self._session_factory() as session:
            statement = (
                insert(ScheduleCandidateDecisionRecord)
                .values(
                    candidate_decision_id=uuid.uuid4().hex,
                    owner_uid=owner_uid,
                    candidate_snapshot_id=candidate_snapshot_id,
                    **values,
                )
                .on_conflict_do_nothing(index_elements=["owner_uid", "candidate_snapshot_id", "request_id"])
                .returning(ScheduleCandidateDecisionRecord)
            )
            created = (await session.execute(statement)).scalar_one_or_none()
            if created is not None:
                return created, True
            existing = await session.scalar(
                select(ScheduleCandidateDecisionRecord).where(
                    ScheduleCandidateDecisionRecord.owner_uid == owner_uid,
                    ScheduleCandidateDecisionRecord.candidate_snapshot_id == candidate_snapshot_id,
                    ScheduleCandidateDecisionRecord.request_id == values["request_id"],
                )
            )
            if existing is None:
                raise RuntimeError("candidate decision idempotency reservation disappeared")
            return existing, False

    async def get_latest_candidate_decision(
        self, owner_uid: str, candidate_snapshot_id: str
    ) -> ScheduleCandidateDecisionRecord | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(ScheduleCandidateDecisionRecord)
                .where(
                    ScheduleCandidateDecisionRecord.owner_uid == owner_uid,
                    ScheduleCandidateDecisionRecord.candidate_snapshot_id == candidate_snapshot_id,
                )
                .order_by(
                    ScheduleCandidateDecisionRecord.created_at.desc(),
                    ScheduleCandidateDecisionRecord.candidate_decision_id.desc(),
                )
                .limit(1)
            )


def _severity_order(severity: str) -> int:
    return {"blocker": 0, "warning": 1, "info": 2}[severity]
