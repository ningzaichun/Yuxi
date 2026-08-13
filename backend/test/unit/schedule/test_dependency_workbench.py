from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from yuxi.schedule.contracts.dependency_decision import DependencyDecisionDraft
from yuxi.services.schedule_audit_service import ScheduleAuditService, ScheduleDecisionInvalidError


class WorkbenchRepository:
    def __init__(self, issue) -> None:
        self.issue = issue
        self.decision = None

    async def get_issue(self, owner_uid, issue_id):
        return self.issue if owner_uid == "owner-1" and issue_id == self.issue.issue_id else None

    async def get_ready(self, owner_uid, snapshot_id):
        if owner_uid != "owner-1" or snapshot_id != self.issue.schedule_snapshot_id:
            return None
        return SimpleNamespace(minio_object="owner-1/snapshot-1/snapshot.json")

    async def get_dependency_decision(self, owner_uid, issue_id):
        return self.decision if owner_uid == "owner-1" and issue_id == self.issue.issue_id else None

    async def save_dependency_decision(self, owner_uid, issue, values):
        self.decision = SimpleNamespace(
            decision_id="decision-1",
            issue_id=issue.issue_id,
            schedule_snapshot_id=issue.schedule_snapshot_id,
            status="draft",
            created_at=None,
            updated_at=None,
            confirmed_at=None,
            **values,
        )
        return self.decision

    async def confirm_dependency_decision(self, owner_uid, issue_id):
        self.decision.status = "confirmed"
        return self.decision

    async def get_candidate_by_dependency_decision(self, owner_uid, dependency_decision_id):
        return None


class WorkbenchStore:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def download(self, object_name: str) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode()


def _service(canonical_schedule_payload: dict, dependency_id: str | None = None):
    tasks = {task["task_id"]: task for task in canonical_schedule_payload["tasks"]}
    dependency = (
        next(
            item
            for item in canonical_schedule_payload["dependencies"]
            if item["dependency_id"] == dependency_id
        )
        if dependency_id
        else next(
            item
            for item in canonical_schedule_payload["dependencies"]
            if tasks[item["predecessor_task_id"]]["task_type"] == "summary"
            or tasks[item["successor_task_id"]]["task_type"] == "summary"
        )
    )
    issue = SimpleNamespace(
        issue_id="issue-1",
        issue_key="issue-key-1",
        audit_run_id="audit-1",
        schedule_snapshot_id="snapshot-1",
        rule_id="SUMMARY_TASK_DEPENDENCY",
        rule_version="1",
        category="dependency",
        severity="warning",
        object_refs=[dependency["dependency_id"]],
        evidence={},
        message="依赖关系涉及汇总任务。",
        recommendation="业务确认后将关系下沉到合适的叶子任务。",
    )
    repository = WorkbenchRepository(issue)
    return ScheduleAuditService(repository, WorkbenchStore(canonical_schedule_payload)), repository


@pytest.mark.asyncio
async def test_workbench_returns_leaf_candidates_and_never_changes_source(
    canonical_schedule_payload: dict,
) -> None:
    service, _ = _service(canonical_schedule_payload)
    before = json.dumps(canonical_schedule_payload, sort_keys=True)

    workbench = await service.get_dependency_workbench("owner-1", "issue-1")

    task_types = {
        task["task_id"]: task["task_type"] for task in canonical_schedule_payload["tasks"]
    }
    assert workbench["issue"]["rule_id"] == "SUMMARY_TASK_DEPENDENCY"
    assert workbench["predecessor"]["candidate_task_ids"]
    assert workbench["successor"]["candidate_task_ids"]
    assert all(task_types[task_id] != "summary" for task_id in workbench["predecessor"]["candidate_task_ids"])
    assert all(task_types[task_id] != "summary" for task_id in workbench["successor"]["candidate_task_ids"])
    assert json.dumps(canonical_schedule_payload, sort_keys=True) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency_id", ["dependency:41", "dependency:61", "dependency:68", "dependency:81"])
async def test_workbench_supports_summary_on_predecessor_or_successor(
    canonical_schedule_payload: dict,
    dependency_id: str,
) -> None:
    service, _ = _service(canonical_schedule_payload, dependency_id)

    workbench = await service.get_dependency_workbench("owner-1", "issue-1")

    tasks = {task["task_id"]: task for task in canonical_schedule_payload["tasks"]}
    assert workbench["source_dependency"]["dependency_id"] == dependency_id
    assert all(
        tasks[task_id]["task_type"] != "summary"
        for task_id in workbench["predecessor"]["candidate_task_ids"]
    )
    assert all(
        tasks[task_id]["task_type"] != "summary"
        for task_id in workbench["successor"]["candidate_task_ids"]
    )


@pytest.mark.asyncio
async def test_workbench_supports_summary_tasks_on_both_endpoints(
    canonical_schedule_payload: dict,
) -> None:
    source = json.loads(json.dumps(canonical_schedule_payload))
    dependency = next(item for item in source["dependencies"] if item["dependency_id"] == "dependency:41")
    dependency["predecessor_task_id"] = "task:56"
    dependency["successor_task_id"] = "task:89"
    service, _ = _service(source, dependency["dependency_id"])

    workbench = await service.get_dependency_workbench("owner-1", "issue-1")

    assert len(workbench["predecessor"]["candidate_task_ids"]) > 1
    assert workbench["successor"]["candidate_task_ids"] == ["task:310"]


@pytest.mark.asyncio
async def test_dependency_decision_requires_complete_confirmed_leaf_relation(
    canonical_schedule_payload: dict,
) -> None:
    service, repository = _service(canonical_schedule_payload)
    workbench = await service.get_dependency_workbench("owner-1", "issue-1")
    draft = DependencyDecisionDraft(
        resolution="replace_with_leaf_tasks",
        predecessor_task_ids=[workbench["predecessor"]["candidate_task_ids"][0]],
        successor_task_ids=[workbench["successor"]["candidate_task_ids"][0]],
        dependency_type="FS",
        lag_minutes=0,
        reason="以已确认的阶段出口和入口替代汇总依赖",
    )

    saved = await service.save_dependency_decision("owner-1", "issue-1", draft)
    confirmed = await service.confirm_dependency_decision("owner-1", "issue-1")

    assert saved["status"] == "draft"
    assert confirmed["status"] == "confirmed"
    assert repository.decision.predecessor_task_ids == draft.predecessor_task_ids


@pytest.mark.asyncio
async def test_dependency_decision_rejects_task_outside_candidates(
    canonical_schedule_payload: dict,
) -> None:
    service, _ = _service(canonical_schedule_payload)
    draft = DependencyDecisionDraft(
        resolution="replace_with_leaf_tasks",
        predecessor_task_ids=["task:not-allowed"],
        successor_task_ids=["task:not-allowed"],
        dependency_type="FS",
        lag_minutes=0,
        reason="非法范围",
    )

    with pytest.raises(ScheduleDecisionInvalidError):
        await service.save_dependency_decision("owner-1", "issue-1", draft)


def test_dependency_decision_rejects_duplicate_leaf_ids() -> None:
    with pytest.raises(ValueError, match="替代依赖任务不能重复"):
        DependencyDecisionDraft(
            resolution="replace_with_leaf_tasks",
            predecessor_task_ids=["task:1", "task:1"],
            successor_task_ids=["task:2"],
            dependency_type="FS",
            lag_minutes=0,
            reason="重复任务",
        )


@pytest.mark.asyncio
async def test_defer_requires_reason_before_confirmation(canonical_schedule_payload: dict) -> None:
    service, _ = _service(canonical_schedule_payload)
    await service.save_dependency_decision(
        "owner-1", "issue-1", DependencyDecisionDraft(resolution="defer")
    )

    with pytest.raises(ScheduleDecisionInvalidError):
        await service.confirm_dependency_decision("owner-1", "issue-1")
