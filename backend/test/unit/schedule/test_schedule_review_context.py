from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from yuxi.services.schedule_audit_service import (
    REVIEW_CONTEXT_ISSUE_LIMIT,
    REVIEW_CONTEXT_OBJECT_REF_LIMIT,
    ScheduleAuditService,
    ScheduleNotFoundError,
)


class FakeReviewRepository:
    def __init__(self) -> None:
        self.owner_uid = "owner-1"
        self.snapshot_id = "snapshot-1"
        self.record = SimpleNamespace(
            schedule_snapshot_id=self.snapshot_id,
            external_project_id="project-1",
            external_snapshot_id="external-snapshot-1",
            external_revision="V3",
            source_snapshot_id="source-snapshot-1",
            schema_version="canonical_schedule_v2.2",
            snapshot_content_sha256="sha256:canonical",
            source_schema_version="source_v1",
            adapter_id="adapter-1",
            adapter_version="1.2.3",
            normalization_report={"unknown_field": "must-not-leak"},
        )
        self.audit = SimpleNamespace(
            audit_run_id="audit-1",
            schedule_snapshot_id=self.snapshot_id,
            rule_set_version="rules-v1",
            statistics={"tasks": 60, "dependencies": 55},
            capabilities={
                "source_schedule_review": {"allowed": True, "reasons": []},
                "cpm_recalculation": {"allowed": False, "reasons": ["MILESTONE_UNSUPPORTED"]},
            },
            dependency_date_checks={"checked": 10, "skipped": 2},
            issue_summary={"total": 51, "blocker": 1, "warning": 50, "info": 0},
            created_at=None,
        )
        self.issues = [
            SimpleNamespace(
                issue_id=f"issue-{index}",
                schedule_snapshot_id=self.snapshot_id,
                rule_id="MILESTONE_UNSUPPORTED" if index == 0 else "OPEN_FINISH",
                category="calculation_capability" if index == 0 else "management_quality",
                severity="blocker" if index == 0 else "warning",
                object_refs=[f"task-{item}" for item in range(REVIEW_CONTEXT_OBJECT_REF_LIMIT + 2)],
                evidence={"private_note": "must-not-leak"},
                message=f"issue message {index}",
                recommendation=f"issue recommendation {index}",
            )
            for index in range(REVIEW_CONTEXT_ISSUE_LIMIT + 1)
        ]

    async def get_ready(self, owner_uid, snapshot_id):
        if owner_uid != self.owner_uid or snapshot_id != self.snapshot_id:
            return None
        return self.record

    async def get_audit(self, owner_uid, snapshot_id):
        if owner_uid != self.owner_uid or snapshot_id != self.snapshot_id:
            return None
        return self.audit

    async def list_issues(self, owner_uid, snapshot_id, *, category, severity, limit, offset):
        assert owner_uid == self.owner_uid
        assert snapshot_id == self.snapshot_id
        assert category is None
        assert severity is None
        assert limit == REVIEW_CONTEXT_ISSUE_LIMIT + 1
        assert offset == 0
        return self.issues[:limit]


class FakeReviewStore:
    async def download(self, object_name: str) -> bytes:
        assert object_name == "owner-1/snapshot-1/snapshot.json"
        return json.dumps(
            {
                "tasks": [
                    {
                        "task_id": "task-1",
                        "wbs": "1.1",
                        "name": "可授权任务",
                        "task_type": "activity",
                        "scheduling_mode": "automatic",
                        "duration_minutes": 480,
                        "notes": "must-not-leak",
                    },
                    {
                        "task_id": "task-2",
                        "wbs": "1.2",
                        "name": "手工任务",
                        "task_type": "activity",
                        "scheduling_mode": "manual",
                        "duration_minutes": 240,
                    },
                    {
                        "task_id": "task-3",
                        "wbs": "1.3",
                        "name": "inactive 任务",
                        "task_type": "activity",
                        "active": False,
                        "scheduling_mode": "automatic",
                        "duration_minutes": 240,
                    },
                ]
            }
        ).encode()


@pytest.mark.asyncio
async def test_review_context_returns_bounded_evidence_projection() -> None:
    service = ScheduleAuditService(repository=FakeReviewRepository())

    result = await service.get_review_context("owner-1", "snapshot-1")

    assert result["evidence_source"] == "YUXI_AUDIT"
    assert result["snapshot"] == {
        "schedule_snapshot_id": "snapshot-1",
        "snapshot_content_sha256": "sha256:canonical",
        "external_project_id": "project-1",
        "external_snapshot_id": "external-snapshot-1",
        "external_revision": "V3",
        "source_snapshot_id": "source-snapshot-1",
        "schema_version": "canonical_schedule_v2.2",
        "source_schema_version": "source_v1",
        "adapter_id": "adapter-1",
        "adapter_version": "1.2.3",
    }
    assert result["audit"]["audit_run_id"] == "audit-1"
    assert result["capabilities"]["cpm_recalculation"]["reasons"] == ["MILESTONE_UNSUPPORTED"]
    assert result["issue_summary"]["total"] == 51
    assert result["issue_projection"]["returned"] == REVIEW_CONTEXT_ISSUE_LIMIT
    assert result["issue_projection"]["truncated"] is True
    first_issue = result["issue_projection"]["items"][0]
    assert first_issue["severity"] == "blocker"
    assert first_issue["object_ref_count"] == REVIEW_CONTEXT_OBJECT_REF_LIMIT + 2
    assert len(first_issue["object_refs_preview"]) == REVIEW_CONTEXT_OBJECT_REF_LIMIT
    assert first_issue["evidence_locator"] == {
        "tool": "get_schedule_issue_context",
        "issue_id": "issue-0",
        "url": "/schedule?schedule_snapshot_id=snapshot-1&schedule_issue_id=issue-0",
    }
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    assert "must-not-leak" not in serialized
    assert "normalization_report" not in serialized
    assert '"evidence"' not in serialized


@pytest.mark.asyncio
async def test_review_context_hides_snapshot_owned_by_another_user() -> None:
    service = ScheduleAuditService(repository=FakeReviewRepository())

    with pytest.raises(ScheduleNotFoundError):
        await service.get_review_context("other-owner", "snapshot-1")


@pytest.mark.asyncio
async def test_goal_optimization_context_returns_only_explicit_authorization_projection() -> None:
    repository = FakeReviewRepository()
    repository.record.minio_object = "owner-1/snapshot-1/snapshot.json"
    service = ScheduleAuditService(repository=repository, store=FakeReviewStore())

    result = await service.get_goal_optimization_context("owner-1", "snapshot-1")

    assert result["supported_objectives"] == [
        "MINIMIZE_PROJECT_FINISH",
        "MEET_TARGET_FINISH",
    ]
    assert result["authorization_policy"]["authorization_confirmation_required"] is True
    assert result["eligible_tasks"] == [
        {
            "task_id": "task-1",
            "wbs": "1.1",
            "name": "可授权任务",
            "duration_minutes": 480,
        }
    ]
    assert "must-not-leak" not in json.dumps(result, ensure_ascii=False)
