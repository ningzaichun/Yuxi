from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.utils.auth_middleware import get_required_user
from yuxi.schedule.importers.registry import UnsupportedScheduleImportVersionError
from yuxi.services.schedule_audit_service import ScheduleConflictError, ScheduleNotFoundError
from yuxi.services.schedule_optimization_service import ScheduleOptimizationConflictError

schedule_module = importlib.import_module("server.routers.schedule_router")
IMPORT_CASE_PATH = (
    Path(__file__).resolve().parents[4]
    / "Microsoft_Project_水泵站排期_MOCK_v1.1"
    / "Microsoft_Project_水泵站排期_MOCK_v1.1.json"
)


class FakeService:
    def __init__(self) -> None:
        self.replay = False
        self.conflict = False
        self.unsupported = False

    async def submit(self, owner_uid, submission):
        assert owner_uid == "owner-1"
        if self.conflict:
            raise ScheduleConflictError
        return {
            "schedule_snapshot_id": "snapshot-1",
            "audit_run_id": "audit-1",
            "idempotent_replay": self.replay,
            "snapshot_content_sha256": "sha256:test",
            "capabilities": {},
            "dependency_date_checks": {},
            "issue_summary": {},
        }

    async def submit_import(self, owner_uid, submission):
        assert owner_uid == "owner-1"
        if self.unsupported:
            raise UnsupportedScheduleImportVersionError
        if self.conflict:
            raise ScheduleConflictError
        return {
            "schedule_snapshot_id": "snapshot-import-1",
            "audit_run_id": "audit-import-1",
            "idempotent_replay": self.replay,
            "snapshot_content_sha256": "sha256:canonical",
            "canonical_snapshot_sha256": "sha256:canonical",
            "source_document_sha256": "sha256:source",
            "adapter_id": "microsoft_project_interchange_v1_1",
            "adapter_version": "1.0.0",
            "normalization_report": {},
            "capabilities": {},
            "dependency_date_checks": {},
            "issue_summary": {},
        }

    async def get_snapshot(self, owner_uid, snapshot_id):
        raise ScheduleNotFoundError

    async def save_dependency_decision(self, owner_uid, issue_id, draft):
        return {
            "decision_id": "decision-1",
            "issue_id": issue_id,
            "status": "draft",
            **draft.model_dump(mode="json"),
        }

    async def confirm_dependency_decision(self, owner_uid, issue_id):
        return {"decision_id": "decision-1", "issue_id": issue_id, "status": "confirmed"}


class FakeOptimizationService:
    def __init__(self) -> None:
        self.replay = False
        self.conflict = False

    async def create_candidate(self, owner_uid, snapshot_id, request):
        if self.conflict:
            raise ScheduleOptimizationConflictError
        return (
            {
                "candidate_snapshot_id": "candidate-1",
                "base_schedule_snapshot_id": snapshot_id,
                "dependency_decision_id": request.dependency_decision_id,
                "candidate_status": "valid",
            },
            not self.replay,
        )

    async def create_forward_candidate(self, owner_uid, snapshot_id, request):
        if self.conflict:
            raise ScheduleOptimizationConflictError
        return (
            {
                "candidate_snapshot_id": "candidate-forward-1",
                "base_schedule_snapshot_id": snapshot_id,
                "candidate_status": "valid",
                "candidate_kind": "automatic_forward_recalculation",
                "engine_result": {"status": "calculated"},
            },
            not self.replay,
        )

    async def create_goal_candidate(self, owner_uid, snapshot_id, request):
        if self.conflict:
            raise ScheduleOptimizationConflictError
        return (
            {
                "candidate_snapshot_id": "candidate-goal-1",
                "base_schedule_snapshot_id": snapshot_id,
                "candidate_status": "valid",
                "candidate_kind": "goal_duration_optimization",
                "comparison": {
                    "objective": request.objective,
                    "target_met": True,
                },
            },
            not self.replay,
        )

    async def get_candidate(self, owner_uid, candidate_snapshot_id):
        return {"candidate_snapshot_id": candidate_snapshot_id, "candidate_status": "valid"}

    async def record_decision(self, owner_uid, candidate_snapshot_id, request):
        return {"candidate_snapshot_id": candidate_snapshot_id, **request.model_dump(mode="json")}, True

    async def get_delivery(self, owner_uid, candidate_snapshot_id):
        return {"candidate_snapshot_id": candidate_snapshot_id, "application_allowed": True}

    async def get_acceptance_evidence(self, owner_uid, candidate_snapshot_id):
        return {"candidate_snapshot_id": candidate_snapshot_id, "status": "passed", "checks": []}


def _client(monkeypatch, service: FakeService) -> TestClient:
    monkeypatch.setattr(schedule_module, "schedule_service", service)
    monkeypatch.setattr(schedule_module, "optimization_service", FakeOptimizationService())
    app = FastAPI()
    app.include_router(schedule_module.schedule_router, prefix="/api")

    async def user():
        return SimpleNamespace(uid="owner-1")

    app.dependency_overrides[get_required_user] = user
    return TestClient(app)


def _request(canonical_schedule_payload: dict) -> dict:
    return {
        "request_id": "request-1",
        "external_project_id": "external-project",
        "external_snapshot_id": "external-snapshot",
        "external_revision": "V2.2",
        "snapshot": canonical_schedule_payload,
    }


def _import_request() -> dict:
    return {
        "request_id": "import-request-1",
        "external_project_id": "water-pump-project",
        "external_snapshot_id": "water-pump-source-v1.1",
        "external_revision": "v1.1",
        "document": json.loads(IMPORT_CASE_PATH.read_text(encoding="utf-8")),
    }


def test_schedule_post_statuses_and_error_contract(monkeypatch, canonical_schedule_payload: dict) -> None:
    service = FakeService()
    client = _client(monkeypatch, service)

    created = client.post("/api/schedule/snapshots", json=_request(canonical_schedule_payload))
    service.replay = True
    replay = client.post("/api/schedule/snapshots", json=_request(canonical_schedule_payload))
    service.conflict = True
    conflict = client.post("/api/schedule/snapshots", json=_request(canonical_schedule_payload))

    assert created.status_code == 201
    assert replay.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "SCHEDULE_IDEMPOTENCY_CONFLICT"


def test_schedule_import_statuses_and_version_error(monkeypatch) -> None:
    service = FakeService()
    client = _client(monkeypatch, service)

    created = client.post("/api/schedule/imports", json=_import_request())
    service.replay = True
    replay = client.post("/api/schedule/imports", json=_import_request())
    service.conflict = True
    conflict = client.post("/api/schedule/imports", json=_import_request())
    service.conflict = False
    service.unsupported = True
    unsupported = client.post("/api/schedule/imports", json=_import_request())

    assert created.status_code == 201
    assert replay.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "SCHEDULE_IDEMPOTENCY_CONFLICT"
    assert unsupported.status_code == 422
    assert unsupported.json()["detail"]["code"] == "SCHEDULE_IMPORT_VERSION_UNSUPPORTED"


def test_schedule_import_requires_document_schema_version(monkeypatch) -> None:
    client = _client(monkeypatch, FakeService())
    payload = _import_request()
    del payload["document"]["schema_version"]

    response = client.post("/api/schedule/imports", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "SCHEDULE_IMPORT_CONTRACT_INVALID"
    assert response.json()["detail"]["errors"][0]["path"] == "/document"


def test_schedule_post_returns_stable_json_pointer(monkeypatch, canonical_schedule_payload: dict) -> None:
    client = _client(monkeypatch, FakeService())
    payload = _request(canonical_schedule_payload)
    payload["snapshot"]["tasks"][0]["planned_start"] = "not-a-date"

    response = client.post("/api/schedule/snapshots", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"]["errors"][0]["path"] == "/snapshot/tasks/0/planned_start"
    assert "not-a-date" not in response.text


def test_schedule_post_rejects_oversized_body(monkeypatch) -> None:
    client = _client(monkeypatch, FakeService())
    body = b"{" + b" " * schedule_module.MAX_BODY_BYTES + b"}"

    response = client.post("/api/schedule/snapshots", content=body, headers={"content-type": "application/json"})

    assert response.status_code == 413


def test_schedule_read_hides_missing_and_unauthorized_resources(monkeypatch) -> None:
    client = _client(monkeypatch, FakeService())

    response = client.get("/api/schedule/snapshots/not-visible")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "SCHEDULE_NOT_FOUND"


def test_dependency_decision_save_and_confirm_contract(monkeypatch) -> None:
    client = _client(monkeypatch, FakeService())
    payload = {
        "resolution": "replace_with_leaf_tasks",
        "predecessor_task_ids": ["task:1"],
        "successor_task_ids": ["task:2"],
        "dependency_type": "FS",
        "lag_minutes": 0,
        "reason": "业务已确认阶段出口和入口",
    }

    saved = client.put("/api/schedule/issues/issue-1/dependency-decision", json=payload)
    confirmed = client.post("/api/schedule/issues/issue-1/dependency-decision/confirm")

    assert saved.status_code == 200
    assert saved.json()["status"] == "draft"
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"


def test_dependency_decision_rejects_fields_for_defer(monkeypatch) -> None:
    client = _client(monkeypatch, FakeService())

    response = client.put(
        "/api/schedule/issues/issue-1/dependency-decision",
        json={
            "resolution": "defer",
            "predecessor_task_ids": ["task:1"],
            "reason": "暂不处理",
        },
    )

    assert response.status_code == 422


def test_dependency_optimization_candidate_decision_and_delivery_contract(monkeypatch) -> None:
    client = _client(monkeypatch, FakeService())
    payload = {
        "request_id": "optimization-request-1",
        "dependency_decision_id": "decision-1",
        "base_snapshot_content_sha256": "sha256:" + "a" * 64,
    }

    created = client.post("/api/schedule/snapshots/snapshot-1/optimizations", json=payload)
    candidate = client.get("/api/schedule/candidates/candidate-1")
    reviewed = client.post(
        "/api/schedule/candidates/candidate-1/decisions",
        json={"request_id": "candidate-review-1", "attitude": "accepted", "comment": "同意交付"},
    )
    delivery = client.get("/api/schedule/candidates/candidate-1/delivery")
    evidence = client.get("/api/schedule/candidates/candidate-1/acceptance-evidence")

    assert created.status_code == 201
    assert candidate.json()["candidate_status"] == "valid"
    assert reviewed.status_code == 201
    assert reviewed.json()["attitude"] == "accepted"
    assert delivery.json()["application_allowed"] is True
    assert evidence.status_code == 200
    assert evidence.json()["status"] == "passed"


def test_forward_recalculation_candidate_contract(monkeypatch) -> None:
    client = _client(monkeypatch, FakeService())

    response = client.post(
        "/api/schedule/snapshots/snapshot-1/recalculate-automatic-downstream",
        json={
            "request_id": "forward-request-1",
            "base_snapshot_content_sha256": "sha256:" + "a" * 64,
        },
    )

    assert response.status_code == 201
    assert response.json()["candidate_status"] == "valid"
    assert response.json()["candidate_kind"] == "automatic_forward_recalculation"


def test_goal_optimization_candidate_contract(monkeypatch) -> None:
    client = _client(monkeypatch, FakeService())

    response = client.post(
        "/api/schedule/snapshots/snapshot-1/goal-optimizations",
        json={
            "request_id": "goal-request-1",
            "base_snapshot_content_sha256": "sha256:" + "a" * 64,
            "objective": "MEET_TARGET_FINISH",
            "target_finish": "2026-09-03T17:00:00+08:00",
            "authorized_duration_options": [{"task_id": "synthetic-task:long-work", "duration_minutes": 480}],
            "locked_task_ids": ["synthetic-task:kickoff"],
            "authorization_confirmed": True,
        },
    )

    assert response.status_code == 201
    assert response.json()["candidate_status"] == "valid"
    assert response.json()["candidate_kind"] == "goal_duration_optimization"
    assert response.json()["comparison"]["target_met"] is True
