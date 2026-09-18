from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.generate_synthetic_schedule_case import build_synthetic_schedule_case
from test.support.schedule_suite import normalize_suite_document
from yuxi.schedule.audit.engine import audit_schedule
from yuxi.schedule.contracts.canonical import parse_canonical_schedule
from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22
from yuxi.schedule.contracts.optimization import (
    CandidateDecisionRequest,
    DependencyOptimizationRequest,
    ForwardRecalculationRequest,
    GoalOptimizationRequest,
)
from yuxi.schedule.delivery_adapter import apply_delivery_to_source_copy
from yuxi.schedule.forward_engine import (
    CALENDAR_EXCEPTIONS_ENGINE_PROFILE_ID,
    CONSTRAINTS_ENGINE_PROFILE_ID,
    MILESTONE_ENGINE_PROFILE_ID,
    MULTI_CALENDAR_ENGINE_PROFILE_ID,
    NEGATIVE_LAG_ENGINE_PROFILE_ID,
    REVERSE_FLOAT_ENGINE_PROFILE_ID,
)
from yuxi.schedule.importers import import_canonical_schedule
from yuxi.schedule.importers.canonical_v2_2 import import_canonical_schedule_v2_2
from yuxi.services.schedule_optimization_service import (
    ScheduleOptimizationConflictError,
    ScheduleOptimizationDependencyError,
    ScheduleOptimizationInvalidError,
    ScheduleOptimizationService,
    _apply_decision,
    _canonical_content_sha256,
)


class OptimizationStore:
    def __init__(self, source: dict) -> None:
        self.objects = {"owner-1/snapshot-1/snapshot.json": json.dumps(source).encode()}

    async def download(self, object_name: str) -> bytes:
        return self.objects[object_name]

    async def upload(self, object_name: str, data: bytes) -> None:
        self.objects[object_name] = data


class OptimizationRepository:
    def __init__(self, source: dict) -> None:
        tasks = {task["task_id"]: task for task in source["tasks"]}
        dependency = (
            next(
                item
                for item in source["dependencies"]
                if tasks[item["predecessor_task_id"]]["task_type"] == "summary"
                or tasks[item["successor_task_id"]]["task_type"] == "summary"
            )
            if any(
                tasks[item["predecessor_task_id"]]["task_type"] == "summary"
                or tasks[item["successor_task_id"]]["task_type"] == "summary"
                for item in source["dependencies"]
            )
            else source["dependencies"][0]
        )
        predecessor_ids = _subtree_ids(tasks, dependency["predecessor_task_id"])
        successor_ids = _subtree_ids(tasks, dependency["successor_task_id"])
        predecessor_candidates = _boundary_ids(source, tasks, predecessor_ids, "exit")
        successor_candidates = _boundary_ids(source, tasks, successor_ids, "entry")
        self.snapshot = SimpleNamespace(
            schedule_snapshot_id="snapshot-1",
            request_id="submission-request-1",
            external_project_id="project-1",
            external_snapshot_id="external-snapshot-1",
            external_revision="revision-1",
            source_snapshot_id=source["snapshot_id"],
            schema_version=source["schema_version"],
            adapter_id="test-adapter",
            adapter_version="1.2.3",
            snapshot_content_sha256="sha256:" + "a" * 64,
            minio_object="owner-1/snapshot-1/snapshot.json",
        )
        self.issue = SimpleNamespace(
            issue_id="issue-1",
            rule_id="SUMMARY_TASK_DEPENDENCY",
            object_refs=[dependency["dependency_id"]],
        )
        self.dependency_decision = SimpleNamespace(
            decision_id="dependency-decision-1",
            issue_id="issue-1",
            schedule_snapshot_id="snapshot-1",
            status="confirmed",
            resolution="replace_with_leaf_tasks",
            predecessor_task_ids=[predecessor_candidates[0]],
            successor_task_ids=[successor_candidates[0]],
            dependency_type="FS",
            lag_minutes=480,
            reason="业务确认替代关系",
        )
        execution = audit_schedule(
            import_canonical_schedule(parse_canonical_schedule(source)),
            schedule_snapshot_id="snapshot-1",
            audit_run_id="audit-1",
        )
        self.base_issues = [
            SimpleNamespace(
                rule_id=item.rule_id,
                severity=item.severity,
                object_refs=item.object_refs,
                evidence=item.evidence,
            )
            for item in execution.findings
        ]
        self.optimization = None
        self.candidate = None
        self.candidate_decisions = []
        self.latest_snapshot = None
        self.latest_issues = None

    async def get_ready(self, owner_uid, snapshot_id):
        if owner_uid != "owner-1":
            return None
        if snapshot_id == "snapshot-1":
            return self.snapshot
        if self.latest_snapshot and snapshot_id == self.latest_snapshot.schedule_snapshot_id:
            return self.latest_snapshot
        return None

    async def get_dependency_decision_by_id(self, owner_uid, decision_id):
        if owner_uid == "owner-1" and decision_id == self.dependency_decision.decision_id:
            return self.dependency_decision
        return None

    async def get_latest_ready_for_project(self, owner_uid, external_project_id):
        if owner_uid != "owner-1" or external_project_id != "project-1":
            return None
        return self.latest_snapshot or self.snapshot

    async def get_issue(self, owner_uid, issue_id):
        return self.issue if owner_uid == "owner-1" and issue_id == "issue-1" else None

    async def reserve_optimization(self, values):
        if self.optimization is None:
            self.optimization = SimpleNamespace(**values)
            return self.optimization, True
        return self.optimization, False

    async def get_candidate_by_dependency_decision(self, owner_uid, dependency_decision_id):
        return self.candidate

    async def list_issues(self, owner_uid, snapshot_id, **kwargs):
        return self.base_issues

    async def list_all_issues(self, owner_uid, snapshot_id):
        if self.latest_snapshot and snapshot_id == self.latest_snapshot.schedule_snapshot_id:
            return self.latest_issues
        return self.base_issues

    async def finalize_optimization(self, optimization_id, requested_patch, candidate_values):
        self.optimization.status = candidate_values["candidate_status"]
        self.optimization.requested_patch = requested_patch
        self.candidate = SimpleNamespace(created_at=None, **candidate_values)
        return self.candidate

    async def mark_optimization_failed(self, optimization_id, failure_code):
        self.optimization.status = "failed"
        self.optimization.failure_code = failure_code

    async def get_candidate_by_optimization(self, owner_uid, optimization_id):
        return self.candidate

    async def get_candidate(self, owner_uid, candidate_snapshot_id):
        if self.candidate and self.candidate.candidate_snapshot_id == candidate_snapshot_id:
            return self.candidate
        return None

    async def list_candidates(self, owner_uid, base_schedule_snapshot_id):
        if (
            owner_uid == "owner-1"
            and self.candidate
            and self.candidate.base_schedule_snapshot_id == base_schedule_snapshot_id
        ):
            return [self.candidate]
        return []

    async def list_candidate_decisions(self, owner_uid, candidate_snapshot_ids):
        return [
            item for item in self.candidate_decisions if item.candidate_snapshot_id in candidate_snapshot_ids
        ]

    async def get_latest_candidate_decision(self, owner_uid, candidate_snapshot_id):
        return self.candidate_decisions[-1] if self.candidate_decisions else None

    async def save_candidate_decision(self, owner_uid, candidate_snapshot_id, values):
        existing = next(
            (item for item in self.candidate_decisions if item.request_id == values["request_id"]),
            None,
        )
        if existing:
            return existing, False
        record = SimpleNamespace(
            candidate_decision_id=f"candidate-decision-{len(self.candidate_decisions) + 1}",
            candidate_snapshot_id=candidate_snapshot_id,
            created_at=None,
            **values,
        )
        self.candidate_decisions.append(record)
        return record, True


def _service(source: dict):
    repository = OptimizationRepository(source)
    return ScheduleOptimizationService(repository, OptimizationStore(source)), repository


def _request(repository: OptimizationRepository, request_id: str = "optimization-request-1"):
    return DependencyOptimizationRequest(
        request_id=request_id,
        dependency_decision_id=repository.dependency_decision.decision_id,
        base_snapshot_content_sha256=repository.snapshot.snapshot_content_sha256,
    )


def _supported_forward_source() -> dict:
    source = build_synthetic_schedule_case()
    source["tasks"] = [task for task in source["tasks"] if task["task_type"] == "activity"]
    for task in source["tasks"]:
        task["parent_task_id"] = None
    source["dependencies"] = [
        {
            "dependency_id": "dependency:design-build-a",
            "predecessor_task_id": "synthetic-task:design",
            "successor_task_id": "synthetic-task:build-a",
            "type": "FS",
            "source_type_code": 1,
            "lag_minutes": 120,
            "lag_calendar_policy": "UNSPECIFIED_REQUIRES_ENGINE_PROFILE",
        }
    ]
    source["statistics"]["tasks"] = 5
    source["statistics"]["summary_tasks"] = 0
    source["statistics"]["leaf_tasks"] = 5
    source["statistics"]["dependencies"] = 1
    source["statistics"]["dependency_types"] = {"FS": 1, "SS": 0, "FF": 0, "SF": 0}
    source["statistics"]["positive_lag_dependencies"] = 1
    source["statistics"]["summary_task_dependencies"] = 0
    contract = CanonicalScheduleV22.model_validate(source)
    execution = audit_schedule(
        import_canonical_schedule_v2_2(contract),
        schedule_snapshot_id=source["snapshot_id"],
        audit_run_id="audit:forward-service-test",
    )
    source["statistics"] = execution.result.statistics
    source["capabilities"] = {
        name: value.model_dump(mode="json") for name, value in execution.result.capabilities.items()
    }
    return source


@pytest.mark.asyncio
async def test_forward_candidate_keeps_source_immutable_and_delivers_engine_result() -> None:
    source = _supported_forward_source()
    service, repository = _service(source)
    repository.snapshot.snapshot_content_sha256 = _canonical_content_sha256(source)
    before = json.loads(json.dumps(source))

    candidate, created = await service.create_forward_candidate(
        "owner-1",
        "snapshot-1",
        ForwardRecalculationRequest(
            request_id="forward-request-1",
            base_snapshot_content_sha256=repository.snapshot.snapshot_content_sha256,
        ),
    )
    await service.record_decision(
        "owner-1",
        candidate["candidate_snapshot_id"],
        CandidateDecisionRequest(request_id="forward-accept-1", attitude="accepted", comment="确认日期差异"),
    )
    delivery = await service.get_delivery("owner-1", candidate["candidate_snapshot_id"])

    assert created is True
    assert candidate["candidate_kind"] == "automatic_forward_recalculation"
    assert candidate["candidate_snapshot"]["engine_result"]["status"] == "calculated"
    assert candidate["candidate_snapshot"]["engine_profile_id"] == REVERSE_FLOAT_ENGINE_PROFILE_ID
    assert candidate["canonical_schema_version"] == "canonical_schedule_v2.2"
    assert candidate["adapter_id"] == "test-adapter"
    assert candidate["adapter_version"] == "1.2.3"
    assert candidate["engine_profile_id"] == REVERSE_FLOAT_ENGINE_PROFILE_ID
    assert candidate["engine_version"] == "7.0.0"
    dates = {item["task_id"]: item for item in candidate["candidate_snapshot"]["engine_result"]["task_dates"]}
    assert dates["synthetic-task:build-a"]["early_start"] == "2026-09-02T10:00:00+08:00"
    assert {
        "late_start",
        "late_finish",
        "total_slack_minutes",
        "free_slack_minutes",
        "critical",
    } <= dates["synthetic-task:build-a"].keys()
    assert candidate["candidate_snapshot"]["candidate_schedule"] == before
    assert delivery["simulation_result"]["status"] == "calculated"
    assert delivery["application_allowed"] is False
    assert delivery["application_blocking_reasons"] == ["DELIVERY_ADAPTER_UNAVAILABLE"]


@pytest.mark.asyncio
async def test_forward_candidate_supports_relation_types_in_main_flow() -> None:
    source = _supported_forward_source()
    source["dependencies"][0]["type"] = "SS"
    source["statistics"]["dependency_types"] = {"FS": 0, "SS": 1, "FF": 0, "SF": 0}
    service, repository = _service(source)
    repository.snapshot.snapshot_content_sha256 = _canonical_content_sha256(source)

    candidate, created = await service.create_forward_candidate(
        "owner-1",
        "snapshot-1",
        ForwardRecalculationRequest(
            request_id="forward-relation-request-1",
            base_snapshot_content_sha256=repository.snapshot.snapshot_content_sha256,
        ),
    )

    assert created is True
    assert candidate["candidate_status"] == "valid"
    assert candidate["candidate_snapshot"]["engine_result"]["status"] == "calculated"


@pytest.mark.asyncio
async def test_forward_candidate_routes_v23_milestones_to_engine_v8() -> None:
    suite_root = Path(__file__).resolve().parents[4] / "Yuxi_复杂排期测试套件_v1"
    document = json.loads(
        (suite_root / "C03_NESTED_SUMMARY_BRANCHES" / "input.json").read_text(encoding="utf-8")
    )
    canonical, _ = normalize_suite_document(document)
    source = canonical.model_dump(mode="json", exclude_none=False)
    service, repository = _service(source)
    repository.snapshot.snapshot_content_sha256 = _canonical_content_sha256(source)

    candidate, created = await service.create_forward_candidate(
        "owner-1",
        "snapshot-1",
        ForwardRecalculationRequest(
            request_id="forward-v23-milestone-request",
            base_snapshot_content_sha256=repository.snapshot.snapshot_content_sha256,
        ),
    )

    result = candidate["candidate_snapshot"]["engine_result"]
    milestone_ids = {task["task_id"] for task in source["tasks"] if task["task_type"] == "milestone"}
    milestone_dates = [item for item in result["task_dates"] if item["task_id"] in milestone_ids]
    assert created is True
    assert candidate["canonical_schema_version"] == "canonical_schedule_v2.3"
    assert candidate["engine_profile_id"] == MILESTONE_ENGINE_PROFILE_ID
    assert result["status"] == "calculated"
    assert milestone_dates
    assert all(item["early_start"] == item["early_finish"] for item in milestone_dates)


@pytest.mark.asyncio
async def test_forward_candidate_persists_negative_lag_engine_v9() -> None:
    source = _supported_forward_source()
    source["dependencies"][0]["lag_minutes"] = -240
    service, repository = _service(source)
    repository.snapshot.snapshot_content_sha256 = _canonical_content_sha256(source)

    candidate, created = await service.create_forward_candidate(
        "owner-1",
        "snapshot-1",
        ForwardRecalculationRequest(
            request_id="forward-negative-lag-request",
            base_snapshot_content_sha256=repository.snapshot.snapshot_content_sha256,
        ),
    )

    result = candidate["candidate_snapshot"]["engine_result"]
    assert created is True
    assert candidate["candidate_status"] == "valid"
    assert candidate["engine_profile_id"] == NEGATIVE_LAG_ENGINE_PROFILE_ID
    assert candidate["engine_version"] == "9.0.0"
    assert candidate["candidate_snapshot"]["engine_profile_id"] == NEGATIVE_LAG_ENGINE_PROFILE_ID
    assert candidate["candidate_snapshot"]["engine_version"] == "9.0.0"
    assert result["status"] == "calculated"


@pytest.mark.asyncio
async def test_forward_candidate_routes_v24_calendar_exceptions_to_engine_v10() -> None:
    suite_root = Path(__file__).resolve().parents[4] / "Yuxi_复杂排期测试套件_v1"
    document = json.loads(
        (suite_root / "C02_MULTI_CALENDAR_EXCEPTIONS" / "input.json").read_text(encoding="utf-8")
    )
    document["case_id"] = "C02_SINGLE_CALENDAR_EXCEPTIONS"
    document["calendars"] = [
        calendar
        for calendar in document["calendars"]
        if calendar["calendar_id"] == "calendar:standard"
    ]
    document["tasks"] = [task for task in document["tasks"] if task["task_id"] != "task:site"]
    document["dependencies"] = [
        dependency
        for dependency in document["dependencies"]
        if "task:site"
        not in {dependency["predecessor_task_id"], dependency["successor_task_id"]}
    ]
    canonical, _ = normalize_suite_document(document)
    source = canonical.model_dump(mode="json", exclude_none=False)
    service, repository = _service(source)
    repository.snapshot.snapshot_content_sha256 = _canonical_content_sha256(source)

    candidate, created = await service.create_forward_candidate(
        "owner-1",
        "snapshot-1",
        ForwardRecalculationRequest(
            request_id="forward-v24-calendar-exception-request",
            base_snapshot_content_sha256=repository.snapshot.snapshot_content_sha256,
        ),
    )

    result = candidate["candidate_snapshot"]["engine_result"]
    assert created is True
    assert candidate["candidate_status"] == "valid"
    assert candidate["canonical_schema_version"] == "canonical_schedule_v2.4"
    assert candidate["engine_profile_id"] == CALENDAR_EXCEPTIONS_ENGINE_PROFILE_ID
    assert candidate["engine_version"] == "10.0.0"
    assert result["status"] == "calculated"
    assert result["finish_after"] == "2026-09-13T17:00:00+08:00"


@pytest.mark.asyncio
async def test_forward_candidate_routes_v24_multi_calendar_to_engine_v11() -> None:
    suite_root = Path(__file__).resolve().parents[4] / "Yuxi_复杂排期测试套件_v1"
    document = json.loads(
        (suite_root / "C02_MULTI_CALENDAR_EXCEPTIONS" / "input.json").read_text(encoding="utf-8")
    )
    canonical, _ = normalize_suite_document(document)
    source = canonical.model_dump(mode="json", exclude_none=False)
    service, repository = _service(source)
    repository.snapshot.snapshot_content_sha256 = _canonical_content_sha256(source)

    candidate, created = await service.create_forward_candidate(
        "owner-1",
        "snapshot-1",
        ForwardRecalculationRequest(
            request_id="forward-v24-multi-calendar-request",
            base_snapshot_content_sha256=repository.snapshot.snapshot_content_sha256,
        ),
    )

    result = candidate["candidate_snapshot"]["engine_result"]
    assert created is True
    assert candidate["candidate_status"] == "valid"
    assert candidate["canonical_schema_version"] == "canonical_schedule_v2.4"
    assert candidate["engine_profile_id"] == MULTI_CALENDAR_ENGINE_PROFILE_ID
    assert candidate["engine_version"] == "11.0.0"
    assert result["status"] == "calculated"
    assert result["finish_after"] == "2026-09-14T17:00:00+08:00"


@pytest.mark.asyncio
async def test_forward_candidate_keeps_v25_constraint_issues_reviewable() -> None:
    suite_root = Path(__file__).resolve().parents[4] / "Yuxi_复杂排期测试套件_v1"
    document = json.loads(
        (suite_root / "C04_CONSTRAINTS_DEADLINES" / "input.json").read_text(encoding="utf-8")
    )
    canonical, _ = normalize_suite_document(document)
    source = canonical.model_dump(mode="json", exclude_none=False)
    service, repository = _service(source)
    repository.snapshot.snapshot_content_sha256 = _canonical_content_sha256(source)

    candidate, created = await service.create_forward_candidate(
        "owner-1",
        "snapshot-1",
        ForwardRecalculationRequest(
            request_id="forward-v25-constraints-request",
            base_snapshot_content_sha256=repository.snapshot.snapshot_content_sha256,
        ),
    )

    result = candidate["candidate_snapshot"]["engine_result"]
    assert created is True
    assert candidate["candidate_status"] == "valid"
    assert candidate["canonical_schema_version"] == "canonical_schedule_v2.5"
    assert candidate["engine_profile_id"] == CONSTRAINTS_ENGINE_PROFILE_ID
    assert candidate["engine_version"] == "12.0.0"
    assert candidate["candidate_audit"]["issue_summary"] == {
        "total": 4,
        "blocker": 0,
        "warning": 4,
        "info": 0,
    }
    assert [issue["rule_id"] for issue in candidate["candidate_audit"]["issues"]] == [
        "DEADLINE_MISSED",
        "FINISH_CONSTRAINT_VIOLATED",
        "HARD_CONSTRAINT_NETWORK_CONFLICT",
        "PROJECT_REQUIRED_FINISH_MISSED",
    ]
    fixed = next(item for item in result["task_dates"] if item["task_id"] == "task:c")
    assert fixed["early_start"] == "2026-09-15T08:00:00+08:00"


@pytest.mark.asyncio
async def test_goal_candidate_persists_selected_duration_strategy_and_delivers_before_review() -> None:
    source = _supported_forward_source()
    service, repository = _service(source)
    repository.snapshot.snapshot_content_sha256 = _canonical_content_sha256(source)
    before = json.loads(json.dumps(source))
    request = GoalOptimizationRequest(
        request_id="goal-request-1",
        base_snapshot_content_sha256=repository.snapshot.snapshot_content_sha256,
        objective="MINIMIZE_PROJECT_FINISH",
        authorized_duration_options=[{"task_id": "synthetic-task:build-a", "duration_minutes": 240}],
        authorization_confirmed=True,
    )

    candidate, created = await service.create_goal_candidate("owner-1", "snapshot-1", request)
    replay, replay_created = await service.create_goal_candidate("owner-1", "snapshot-1", request)
    delivery = await service.get_delivery("owner-1", candidate["candidate_snapshot_id"])

    assert created is True
    assert replay_created is False
    assert replay["candidate_snapshot_id"] == candidate["candidate_snapshot_id"]
    assert candidate["candidate_kind"] == "goal_duration_optimization"
    assert candidate["candidate_status"] == "valid"
    assert candidate["comparison"]["finish_after"] < candidate["comparison"]["finish_before"]
    assert candidate["effective_patch"]["duration_changes"] == [
        {
            "task_id": "synthetic-task:build-a",
            "before_duration_minutes": 480,
            "after_duration_minutes": 240,
        }
    ]
    candidate_tasks = {task["task_id"]: task for task in candidate["candidate_snapshot"]["candidate_schedule"]["tasks"]}
    assert candidate_tasks["synthetic-task:build-a"]["duration_minutes"] == 240
    assert source == before
    assert delivery["user_attitude"] == "not_reviewed"
    assert delivery["application_allowed"] is False
    assert delivery["application_blocking_reasons"] == ["DELIVERY_ADAPTER_UNAVAILABLE"]

    with pytest.raises(ScheduleOptimizationConflictError):
        await service.create_goal_candidate(
            "owner-1",
            "snapshot-1",
            GoalOptimizationRequest(
                request_id="goal-request-1",
                base_snapshot_content_sha256=repository.snapshot.snapshot_content_sha256,
                objective="MINIMIZE_PROJECT_FINISH",
                authorized_duration_options=[{"task_id": "synthetic-task:build-a", "duration_minutes": 120}],
                authorization_confirmed=True,
            ),
        )


@pytest.mark.asyncio
async def test_list_candidates_returns_summaries_with_attitude() -> None:
    source = _supported_forward_source()
    service, repository = _service(source)
    repository.snapshot.snapshot_content_sha256 = _canonical_content_sha256(source)
    request = GoalOptimizationRequest(
        request_id="goal-request-list",
        base_snapshot_content_sha256=repository.snapshot.snapshot_content_sha256,
        objective="MINIMIZE_PROJECT_FINISH",
        authorized_duration_options=[{"task_id": "synthetic-task:build-a", "duration_minutes": 240}],
        authorization_confirmed=True,
    )

    candidate, _ = await service.create_goal_candidate("owner-1", "snapshot-1", request)
    await service.record_decision(
        "owner-1",
        candidate["candidate_snapshot_id"],
        CandidateDecisionRequest(request_id="decision-list-1", attitude="accepted"),
    )

    listed = await service.list_candidates("owner-1", "snapshot-1")

    assert len(listed["items"]) == 1
    item = listed["items"][0]
    assert item["candidate_snapshot_id"] == candidate["candidate_snapshot_id"]
    assert item["candidate_kind"] == "goal_duration_optimization"
    assert item["candidate_status"] == "valid"
    assert item["canonical_schema_version"] == "canonical_schedule_v2.2"
    assert item["adapter_id"] == "test-adapter"
    assert item["engine_profile_id"] == REVERSE_FLOAT_ENGINE_PROFILE_ID
    assert item["user_attitude"] == "accepted"
    assert item["comparison"]["finish_after"] < item["comparison"]["finish_before"]
    assert await service.list_candidates("owner-2", "snapshot-1") == {"items": []}


@pytest.mark.asyncio
async def test_goal_candidate_does_not_leave_creating_record_when_candidate_audit_fails(monkeypatch) -> None:
    source = _supported_forward_source()
    service, repository = _service(source)
    repository.snapshot.snapshot_content_sha256 = _canonical_content_sha256(source)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("candidate audit failed")

    monkeypatch.setattr("yuxi.services.schedule_optimization_service.audit_schedule", fail_audit)

    with pytest.raises(ScheduleOptimizationDependencyError):
        await service.create_goal_candidate(
            "owner-1",
            "snapshot-1",
            GoalOptimizationRequest(
                request_id="goal-audit-failure-1",
                base_snapshot_content_sha256=repository.snapshot.snapshot_content_sha256,
                objective="MINIMIZE_PROJECT_FINISH",
                authorized_duration_options=[{"task_id": "synthetic-task:build-a", "duration_minutes": 240}],
                authorization_confirmed=True,
            ),
        )

    assert repository.optimization is None


@pytest.mark.asyncio
async def test_forward_candidate_persists_invalid_locked_conflict_without_allowing_delivery() -> None:
    source = _supported_forward_source()
    target = next(task for task in source["tasks"] if task["task_id"] == "synthetic-task:build-a")
    target["planned_start"] = "2026-09-01T08:00:00+08:00"
    target["planned_finish"] = "2026-09-01T17:00:00+08:00"
    service, repository = _service(source)
    repository.snapshot.snapshot_content_sha256 = _canonical_content_sha256(source)

    candidate, created = await service.create_forward_candidate(
        "owner-1",
        "snapshot-1",
        ForwardRecalculationRequest(
            request_id="forward-locked-conflict-1",
            base_snapshot_content_sha256=repository.snapshot.snapshot_content_sha256,
            locked_task_ids=["synthetic-task:build-a"],
        ),
    )

    assert created is True
    assert candidate["candidate_status"] == "invalid"
    assert candidate["candidate_snapshot"]["engine_result"]["status"] == "invalid"
    assert candidate["candidate_snapshot"]["engine_result"]["conflicts"][0]["code"] == (
        "LOCKED_TASK_DEPENDENCY_CONFLICT"
    )


@pytest.mark.asyncio
async def test_forward_candidate_keeps_summary_dependency_blocked_under_v9() -> None:
    source = build_synthetic_schedule_case()
    source["dependencies"][0]["lag_minutes"] = -120
    service, repository = _service(source)

    blocked, created = await service.create_forward_candidate(
        "owner-1",
        "snapshot-1",
        ForwardRecalculationRequest(
            request_id="forward-blocked-1",
            base_snapshot_content_sha256=repository.snapshot.snapshot_content_sha256,
        ),
    )

    assert created is False
    assert blocked["candidate_status"] == "blocked"
    assert repository.optimization is None
    assert blocked["engine_result"]["task_dates"] == []
    assert "SUMMARY_DEPENDENCY_UNSUPPORTED" in {
        item["code"] for item in blocked["engine_result"]["support"]["blockers"]
    }


@pytest.mark.asyncio
async def test_confirmed_decision_generates_valid_candidate_without_changing_source(
    canonical_schedule_payload: dict,
) -> None:
    service, repository = _service(canonical_schedule_payload)
    source_before = json.dumps(canonical_schedule_payload, sort_keys=True)

    candidate, created = await service.create_candidate("owner-1", "snapshot-1", _request(repository))

    assert created is True
    assert candidate["candidate_status"] == "valid"
    assert candidate["candidate_kind"] == "dependency_normalization"
    assert candidate["comparison"]["target_issue_resolved"] is True
    assert candidate["candidate_snapshot"]["engine_result"] is None
    assert len(candidate["effective_patch"]["removed_dependencies"]) == 1
    assert len(candidate["effective_patch"]["added_dependencies"]) == 1
    candidate_rules = {issue["rule_id"] for issue in candidate["candidate_snapshot"]["candidate_audit"]["issues"]}
    assert "STATISTICS_MISMATCH" not in candidate_rules
    assert "SOURCE_CAPABILITY_MISMATCH" not in candidate_rules
    assert (
        candidate["candidate_snapshot"]["candidate_schedule"]["statistics"]
        == candidate["candidate_snapshot"]["candidate_audit"]["statistics"]
    )
    assert json.dumps(canonical_schedule_payload, sort_keys=True) == source_before


@pytest.mark.asyncio
async def test_optimization_request_is_idempotent_and_rejects_hash_conflict(
    canonical_schedule_payload: dict,
) -> None:
    service, repository = _service(canonical_schedule_payload)
    request = _request(repository)

    first, created = await service.create_candidate("owner-1", "snapshot-1", request)
    replay, replay_created = await service.create_candidate("owner-1", "snapshot-1", request)

    assert created is True
    assert replay_created is False
    assert replay["candidate_snapshot_id"] == first["candidate_snapshot_id"]

    with pytest.raises(ScheduleOptimizationConflictError):
        await service.create_candidate(
            "owner-1",
            "snapshot-1",
            request.model_copy(update={"base_snapshot_content_sha256": "sha256:" + "b" * 64}),
        )


@pytest.mark.asyncio
async def test_delivery_is_available_for_every_attitude_without_changing_technical_status(
    canonical_schedule_payload: dict,
) -> None:
    service, repository = _service(canonical_schedule_payload)
    candidate, _ = await service.create_candidate("owner-1", "snapshot-1", _request(repository))
    candidate_id = candidate["candidate_snapshot_id"]

    initial_delivery = await service.get_delivery("owner-1", candidate_id)
    assert initial_delivery["user_attitude"] == "not_reviewed"
    assert initial_delivery["application_allowed"] is True

    rejected, _ = await service.record_decision(
        "owner-1",
        candidate_id,
        CandidateDecisionRequest(request_id="reject-1", attitude="rejected", comment="需要复核"),
    )
    after_rejection = await service.get_candidate("owner-1", candidate_id)
    rejected_delivery = await service.get_delivery("owner-1", candidate_id)
    assert rejected["attitude"] == "rejected"
    assert after_rejection["candidate_status"] == "valid"
    assert rejected_delivery["user_attitude"] == "rejected"
    assert rejected_delivery["application_allowed"] is True

    await service.record_decision(
        "owner-1",
        candidate_id,
        CandidateDecisionRequest(request_id="accept-1", attitude="accepted", comment="确认交付"),
    )
    delivery = await service.get_delivery("owner-1", candidate_id)

    assert delivery["application_allowed"] is True
    assert delivery["simulation_result"] is None
    assert delivery["user_attitude"] == "accepted"
    assert delivery["base_snapshot_content_sha256"] == repository.snapshot.snapshot_content_sha256


@pytest.mark.asyncio
async def test_acceptance_evidence_is_pending_before_new_source_returns(
    canonical_schedule_payload: dict,
) -> None:
    service, repository = _service(canonical_schedule_payload)
    repository.snapshot.snapshot_content_sha256 = _canonical_content_sha256(canonical_schedule_payload)
    candidate, _ = await service.create_candidate("owner-1", "snapshot-1", _request(repository))
    await service.record_decision(
        "owner-1",
        candidate["candidate_snapshot_id"],
        CandidateDecisionRequest(request_id="accept-evidence", attitude="accepted", comment="确认"),
    )

    evidence = await service.get_acceptance_evidence("owner-1", candidate["candidate_snapshot_id"])

    assert evidence["status"] == "pending"
    checks = {item["code"]: item["passed"] for item in evidence["checks"]}
    assert checks["NEW_SNAPSHOT_AVAILABLE"] is False
    assert checks["BASE_CANDIDATE_OUTDATED"] is False


@pytest.mark.asyncio
async def test_acceptance_evidence_passes_after_controlled_source_return(
    canonical_schedule_payload: dict,
) -> None:
    service, repository = _service(canonical_schedule_payload)
    repository.snapshot.snapshot_content_sha256 = _canonical_content_sha256(canonical_schedule_payload)
    candidate, _ = await service.create_candidate("owner-1", "snapshot-1", _request(repository))
    await service.record_decision(
        "owner-1",
        candidate["candidate_snapshot_id"],
        CandidateDecisionRequest(request_id="accept-return", attitude="accepted", comment="确认回流"),
    )
    delivery = await service.get_delivery("owner-1", candidate["candidate_snapshot_id"])
    returned_source = apply_delivery_to_source_copy(
        canonical_schedule_payload,
        delivery,
        new_source_snapshot_id="source-copy-2",
        generated_at=datetime.now(UTC),
    )
    latest_object = "owner-1/snapshot-2/snapshot.json"
    service._store.objects[latest_object] = json.dumps(returned_source).encode()
    repository.latest_snapshot = SimpleNamespace(
        schedule_snapshot_id="snapshot-2",
        request_id="submission-request-2",
        external_project_id="project-1",
        external_snapshot_id="external-snapshot-2",
        external_revision="revision-2",
        source_snapshot_id=returned_source["snapshot_id"],
        snapshot_content_sha256=_canonical_content_sha256(returned_source),
        minio_object=latest_object,
    )
    execution = audit_schedule(
        import_canonical_schedule_v2_2(CanonicalScheduleV22.model_validate(returned_source)),
        schedule_snapshot_id="snapshot-2",
        audit_run_id="audit-2",
    )
    repository.latest_issues = [
        SimpleNamespace(
            rule_id=item.rule_id,
            severity=item.severity,
            object_refs=item.object_refs,
            evidence=item.evidence,
        )
        for item in execution.findings
    ]

    evidence = await service.get_acceptance_evidence("owner-1", candidate["candidate_snapshot_id"])

    assert evidence["status"] == "passed"
    assert len(evidence["checks"]) == 16
    assert all(item["passed"] for item in evidence["checks"])


@pytest.mark.asyncio
async def test_synthetic_case_completes_reusable_delivery_return_with_sixteen_checks() -> None:
    source = build_synthetic_schedule_case()
    service, repository = _service(source)
    repository.snapshot.snapshot_content_sha256 = _canonical_content_sha256(source)
    candidate, _ = await service.create_candidate("owner-1", "snapshot-1", _request(repository))
    await service.record_decision(
        "owner-1",
        candidate["candidate_snapshot_id"],
        CandidateDecisionRequest(
            request_id="accept-synthetic-return",
            attitude="accepted",
            comment="合成协议回归",
        ),
    )
    delivery = await service.get_delivery("owner-1", candidate["candidate_snapshot_id"])
    returned_source = apply_delivery_to_source_copy(
        source,
        delivery,
        new_source_snapshot_id="synthetic-source-copy-2",
        generated_at=datetime.now(UTC),
    )
    latest_object = "owner-1/synthetic-snapshot-2/snapshot.json"
    service._store.objects[latest_object] = json.dumps(returned_source).encode()
    repository.latest_snapshot = SimpleNamespace(
        schedule_snapshot_id="synthetic-snapshot-2",
        request_id="synthetic-submission-request-2",
        external_project_id="project-1",
        external_snapshot_id="synthetic-external-snapshot-2",
        external_revision="synthetic-revision-2",
        source_snapshot_id=returned_source["snapshot_id"],
        snapshot_content_sha256=_canonical_content_sha256(returned_source),
        minio_object=latest_object,
    )
    execution = audit_schedule(
        import_canonical_schedule_v2_2(CanonicalScheduleV22.model_validate(returned_source)),
        schedule_snapshot_id="synthetic-snapshot-2",
        audit_run_id="synthetic-audit-2",
    )
    repository.latest_issues = [
        SimpleNamespace(
            rule_id=item.rule_id,
            severity=item.severity,
            object_refs=item.object_refs,
            evidence=item.evidence,
        )
        for item in execution.findings
    ]

    evidence = await service.get_acceptance_evidence("owner-1", candidate["candidate_snapshot_id"])

    assert candidate["candidate_status"] == "valid"
    assert evidence["status"] == "passed"
    assert len(evidence["checks"]) == 16
    assert all(item["passed"] for item in evidence["checks"])
    assert len(source["tasks"]) == 9
    assert source["statistics"]["summary_task_dependencies"] == 1
    assert returned_source["statistics"]["summary_task_dependencies"] == 0


@pytest.mark.asyncio
async def test_acceptance_evidence_rejects_new_snapshot_with_reused_submission_provenance(
    canonical_schedule_payload: dict,
) -> None:
    service, repository = _service(canonical_schedule_payload)
    repository.snapshot.snapshot_content_sha256 = _canonical_content_sha256(canonical_schedule_payload)
    candidate, _ = await service.create_candidate("owner-1", "snapshot-1", _request(repository))
    await service.record_decision(
        "owner-1",
        candidate["candidate_snapshot_id"],
        CandidateDecisionRequest(request_id="accept-reused", attitude="accepted", comment="确认"),
    )
    delivery = await service.get_delivery("owner-1", candidate["candidate_snapshot_id"])
    returned_source = apply_delivery_to_source_copy(
        canonical_schedule_payload,
        delivery,
        new_source_snapshot_id="source-copy-2",
        generated_at=datetime.now(UTC),
    )
    latest_object = "owner-1/snapshot-2/snapshot.json"
    service._store.objects[latest_object] = json.dumps(returned_source).encode()
    repository.latest_snapshot = SimpleNamespace(
        schedule_snapshot_id="snapshot-2",
        request_id=repository.snapshot.request_id,
        external_project_id="project-1",
        external_snapshot_id=repository.snapshot.external_snapshot_id,
        external_revision="revision-2",
        source_snapshot_id=repository.snapshot.source_snapshot_id,
        snapshot_content_sha256=_canonical_content_sha256(returned_source),
        minio_object=latest_object,
    )
    execution = audit_schedule(
        import_canonical_schedule_v2_2(CanonicalScheduleV22.model_validate(returned_source)),
        schedule_snapshot_id="snapshot-2",
        audit_run_id="audit-2",
    )
    repository.latest_issues = [
        SimpleNamespace(
            rule_id=item.rule_id,
            severity=item.severity,
            object_refs=item.object_refs,
            evidence=item.evidence,
        )
        for item in execution.findings
    ]

    evidence = await service.get_acceptance_evidence("owner-1", candidate["candidate_snapshot_id"])

    assert evidence["status"] == "failed"
    checks = {item["code"]: item["passed"] for item in evidence["checks"]}
    assert checks["PATCH_APPLIED_EXACTLY"] is True
    assert checks["SUBMISSION_REQUEST_IS_NEW"] is False
    assert checks["EXTERNAL_SNAPSHOT_IS_NEW"] is False
    assert checks["EXTERNAL_REVISION_IS_NEW"] is True
    assert checks["SOURCE_SNAPSHOT_IS_NEW"] is False


@pytest.mark.asyncio
async def test_acceptance_evidence_rejects_latest_object_hash_mismatch(
    canonical_schedule_payload: dict,
) -> None:
    service, repository = _service(canonical_schedule_payload)
    repository.snapshot.snapshot_content_sha256 = _canonical_content_sha256(canonical_schedule_payload)
    candidate, _ = await service.create_candidate("owner-1", "snapshot-1", _request(repository))
    await service.record_decision(
        "owner-1",
        candidate["candidate_snapshot_id"],
        CandidateDecisionRequest(request_id="accept-hash", attitude="accepted", comment="确认"),
    )
    delivery = await service.get_delivery("owner-1", candidate["candidate_snapshot_id"])
    returned_source = apply_delivery_to_source_copy(
        canonical_schedule_payload,
        delivery,
        new_source_snapshot_id="source-copy-2",
        generated_at=datetime.now(UTC),
    )
    latest_object = "owner-1/snapshot-2/snapshot.json"
    service._store.objects[latest_object] = json.dumps(returned_source).encode()
    repository.latest_snapshot = SimpleNamespace(
        schedule_snapshot_id="snapshot-2",
        request_id="submission-request-2",
        external_project_id="project-1",
        external_snapshot_id="external-snapshot-2",
        external_revision="revision-2",
        source_snapshot_id=returned_source["snapshot_id"],
        snapshot_content_sha256="sha256:" + "f" * 64,
        minio_object=latest_object,
    )
    execution = audit_schedule(
        import_canonical_schedule_v2_2(CanonicalScheduleV22.model_validate(returned_source)),
        schedule_snapshot_id="snapshot-2",
        audit_run_id="audit-2",
    )
    repository.latest_issues = [
        SimpleNamespace(
            rule_id=item.rule_id,
            severity=item.severity,
            object_refs=item.object_refs,
            evidence=item.evidence,
        )
        for item in execution.findings
    ]

    evidence = await service.get_acceptance_evidence("owner-1", candidate["candidate_snapshot_id"])

    assert evidence["status"] == "failed"
    checks = {item["code"]: item["passed"] for item in evidence["checks"]}
    assert checks["BASE_OBJECT_HASH_MATCHES_RECORD"] is True
    assert checks["LATEST_OBJECT_HASH_MATCHES_RECORD"] is False


@pytest.mark.parametrize("dependency_id", ["dependency:41", "dependency:61", "dependency:68", "dependency:81"])
def test_patch_supports_summary_dependency_on_either_endpoint(
    canonical_schedule_payload: dict,
    dependency_id: str,
) -> None:
    source_dependency = next(
        item for item in canonical_schedule_payload["dependencies"] if item["dependency_id"] == dependency_id
    )
    tasks = {task["task_id"]: task for task in canonical_schedule_payload["tasks"]}
    predecessor_candidates = _boundary_ids(
        canonical_schedule_payload,
        tasks,
        _subtree_ids(tasks, source_dependency["predecessor_task_id"]),
        "exit",
    )
    successor_candidates = _boundary_ids(
        canonical_schedule_payload,
        tasks,
        _subtree_ids(tasks, source_dependency["successor_task_id"]),
        "entry",
    )
    decision = SimpleNamespace(
        decision_id=f"decision:{dependency_id}",
        schedule_snapshot_id="snapshot-1",
        predecessor_task_ids=predecessor_candidates[:2],
        successor_task_ids=successor_candidates[:2],
        dependency_type=source_dependency["type"],
        lag_minutes=source_dependency["lag_minutes"],
        reason="通用形态矩阵",
    )
    issue = SimpleNamespace(issue_id=f"issue:{dependency_id}", object_refs=[dependency_id])

    candidate, _, effective_patch, _ = _apply_decision(canonical_schedule_payload, issue, decision, "candidate-matrix")

    added = effective_patch["added_dependencies"]
    assert len(added) == len(decision.predecessor_task_ids) * len(decision.successor_task_ids)
    assert all(tasks[item["predecessor_task_id"]]["task_type"] != "summary" for item in added)
    assert all(tasks[item["successor_task_id"]]["task_type"] != "summary" for item in added)
    assert not any(item["dependency_id"] == dependency_id for item in candidate["dependencies"])
    CanonicalScheduleV22.model_validate(candidate)


@pytest.mark.parametrize(("dependency_type", "source_type_code"), [("FF", 0), ("FS", 1), ("SF", 2), ("SS", 3)])
def test_patch_preserves_confirmed_dependency_type_and_lag(
    canonical_schedule_payload: dict,
    dependency_type: str,
    source_type_code: int,
) -> None:
    source_dependency = next(
        item for item in canonical_schedule_payload["dependencies"] if item["dependency_id"] == "dependency:68"
    )
    issue = SimpleNamespace(issue_id="issue-1", object_refs=[source_dependency["dependency_id"]])
    decision = SimpleNamespace(
        decision_id="decision-1",
        schedule_snapshot_id="snapshot-1",
        predecessor_task_ids=["task:270", "task:271"],
        successor_task_ids=["task:236"],
        dependency_type=dependency_type,
        lag_minutes=-240,
        reason="关系类型矩阵",
    )

    _, _, effective_patch, _ = _apply_decision(canonical_schedule_payload, issue, decision, "candidate-type-matrix")

    assert len(effective_patch["added_dependencies"]) == 2
    assert all(item["type"] == dependency_type for item in effective_patch["added_dependencies"])
    assert all(item["source_type_code"] == source_type_code for item in effective_patch["added_dependencies"])
    assert all(item["lag_minutes"] == -240 for item in effective_patch["added_dependencies"])


def test_patch_rejects_multi_leaf_expansion_above_contract_capacity(
    canonical_schedule_payload: dict,
    monkeypatch,
) -> None:
    source_dependency = next(
        item for item in canonical_schedule_payload["dependencies"] if item["dependency_id"] == "dependency:61"
    )
    issue = SimpleNamespace(issue_id="issue-1", object_refs=[source_dependency["dependency_id"]])
    decision = SimpleNamespace(
        decision_id="decision-1",
        schedule_snapshot_id="snapshot-1",
        predecessor_task_ids=["task:139", "task:149"],
        successor_task_ids=["task:305"],
        dependency_type="FS",
        lag_minutes=0,
        reason="容量边界",
    )
    monkeypatch.setattr(
        "yuxi.services.schedule_optimization_service.MAX_DEPENDENCIES",
        len(canonical_schedule_payload["dependencies"]),
    )

    with pytest.raises(ScheduleOptimizationInvalidError):
        _apply_decision(canonical_schedule_payload, issue, decision, "candidate-overflow")


def test_inactive_dependency_decision_removes_chain_and_adds_explicit_leaf_relation(
    canonical_schedule_payload: dict,
) -> None:
    source = json.loads(json.dumps(canonical_schedule_payload))
    activities = [task for task in source["tasks"] if task["task_type"] == "activity"][:3]
    predecessor, inactive, successor = activities
    inactive["active"] = False
    source["dependencies"] = [
        {
            "dependency_id": "dependency:inactive-in",
            "predecessor_task_id": predecessor["task_id"],
            "successor_task_id": inactive["task_id"],
            "type": "FS",
            "source_type_code": 1,
            "lag_minutes": 0,
            "lag_calendar_policy": "UNSPECIFIED_REQUIRES_ENGINE_PROFILE",
        },
        {
            "dependency_id": "dependency:inactive-out",
            "predecessor_task_id": inactive["task_id"],
            "successor_task_id": successor["task_id"],
            "type": "FS",
            "source_type_code": 1,
            "lag_minutes": 0,
            "lag_calendar_policy": "UNSPECIFIED_REQUIRES_ENGINE_PROFILE",
        },
    ]
    issue = SimpleNamespace(
        issue_id="issue-inactive",
        object_refs=["dependency:inactive-in", "dependency:inactive-out"],
    )
    decision = SimpleNamespace(
        decision_id="decision-inactive",
        schedule_snapshot_id="snapshot-1",
        predecessor_task_ids=[predecessor["task_id"]],
        successor_task_ids=[successor["task_id"]],
        dependency_type="FS",
        lag_minutes=0,
        reason="确认绕过 inactive 任务的活动叶子关系",
    )

    candidate, requested_patch, effective_patch, added_ids = _apply_decision(
        source,
        issue,
        decision,
        "candidate-inactive",
    )

    assert {item["dependency_id"] for item in effective_patch["removed_dependencies"]} == {
        "dependency:inactive-in",
        "dependency:inactive-out",
    }
    assert len(effective_patch["added_dependencies"]) == 1
    added = effective_patch["added_dependencies"][0]
    assert added["predecessor_task_id"] == predecessor["task_id"]
    assert added["successor_task_id"] == successor["task_id"]
    assert set(added_ids) == {added["dependency_id"]}
    assert len(
        [
            item
            for item in requested_patch["operations"]
            if item["operation"] == "remove_dependency"
        ]
    ) == 2
    assert candidate["dependencies"] == effective_patch["added_dependencies"]


def _subtree_ids(tasks: dict[str, dict], root_id: str) -> set[str]:
    result = {root_id}
    while True:
        children = {
            task_id for task_id, task in tasks.items() if task["parent_task_id"] in result and task_id not in result
        }
        if not children:
            return result
        result.update(children)


def _boundary_ids(source: dict, tasks: dict[str, dict], task_ids: set[str], boundary: str) -> list[str]:
    leaf_ids = {task_id for task_id in task_ids if tasks[task_id]["task_type"] != "summary"}
    if boundary == "entry":
        connected = {
            item["successor_task_id"]
            for item in source["dependencies"]
            if item["predecessor_task_id"] in task_ids and item["successor_task_id"] in task_ids
        }
    else:
        connected = {
            item["predecessor_task_id"]
            for item in source["dependencies"]
            if item["predecessor_task_id"] in task_ids and item["successor_task_id"] in task_ids
        }
    return sorted(leaf_ids - connected)
