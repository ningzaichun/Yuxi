from __future__ import annotations

import importlib
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.utils.auth_middleware import get_required_user
from yuxi.services.schedule_audit_service import ScheduleConflictError, ScheduleNotFoundError

schedule_module = importlib.import_module("server.routers.schedule_router")


class FakeService:
    def __init__(self) -> None:
        self.replay = False
        self.conflict = False

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

    async def get_snapshot(self, owner_uid, snapshot_id):
        raise ScheduleNotFoundError


def _client(monkeypatch, service: FakeService) -> TestClient:
    monkeypatch.setattr(schedule_module, "schedule_service", service)
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
