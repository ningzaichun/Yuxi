from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from scripts.generate_synthetic_schedule_case import build_synthetic_schedule_case
from yuxi.repositories.schedule_repository import ScheduleRepository
from yuxi.schedule.audit.engine import audit_schedule
from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22
from yuxi.schedule.contracts.envelope import ScheduleSnapshotSubmission
from yuxi.schedule.delivery_adapter import apply_delivery_to_source_copy
from yuxi.schedule.forward_engine import REVERSE_FLOAT_ENGINE_PROFILE_ID
from yuxi.schedule.importers.canonical_v2_2 import import_canonical_schedule_v2_2
from yuxi.schedule.storage import SCHEDULE_BUCKET
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "data" / "schedule" / "schedule_v2_2_sanitized.json"


def _submission(request_id: str) -> dict:
    return {
        "request_id": request_id,
        "external_project_id": f"pytest-schedule-{request_id}",
        "external_snapshot_id": f"pytest-snapshot-{request_id}",
        "external_revision": "V2.2",
        "snapshot": json.loads(FIXTURE_PATH.read_text(encoding="utf-8")),
    }


def _forward_submission(request_id: str) -> dict:
    source = build_synthetic_schedule_case()
    source["dependencies"] = [
        {
            "dependency_id": "dependency:design-build-a",
            "predecessor_task_id": "synthetic-task:design",
            "successor_task_id": "synthetic-task:build-a",
            "type": "FS",
            "source_type_code": 1,
            "lag_minutes": 0,
            "lag_calendar_policy": "UNSPECIFIED_REQUIRES_ENGINE_PROFILE",
        }
    ]
    contract = CanonicalScheduleV22.model_validate(source)
    execution = audit_schedule(
        import_canonical_schedule_v2_2(contract),
        schedule_snapshot_id=source["snapshot_id"],
        audit_run_id="audit:forward-integration",
    )
    source["statistics"] = execution.result.statistics
    source["capabilities"] = {
        name: value.model_dump(mode="json") for name, value in execution.result.capabilities.items()
    }
    return {
        "request_id": request_id,
        "external_project_id": f"pytest-forward-{request_id}",
        "external_snapshot_id": f"pytest-forward-snapshot-{request_id}",
        "external_revision": "V2.2-forward",
        "snapshot": source,
    }


async def test_schedule_http_idempotency_audit_and_owner_isolation(
    test_client,
    standard_user,
    admin_headers,
):
    headers = standard_user["headers"]
    request_id = f"pytest-{uuid.uuid4().hex}"
    submission = _submission(request_id)

    created = await test_client.post("/api/schedule/snapshots", json=submission, headers=headers)
    replay = await test_client.post("/api/schedule/snapshots", json=submission, headers=headers)

    assert created.status_code == 201, created.text
    assert replay.status_code == 200, replay.text
    snapshot_id = created.json()["schedule_snapshot_id"]
    assert replay.json()["schedule_snapshot_id"] == snapshot_id
    assert replay.json()["idempotent_replay"] is True

    conflict_submission = copy.deepcopy(submission)
    conflict_submission["snapshot"]["snapshot_id"] = f"changed-{uuid.uuid4().hex}"
    conflict = await test_client.post("/api/schedule/snapshots", json=conflict_submission, headers=headers)
    assert conflict.status_code == 409, conflict.text

    detail = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}", headers=headers)
    audit = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}/audit", headers=headers)
    issues = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}/issues", headers=headers)
    hidden = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}", headers=admin_headers)

    assert detail.status_code == 200, detail.text
    assert audit.status_code == 200, audit.text
    assert audit.json()["dependency_date_checks"]["checked"] == 68
    assert audit.json()["dependency_date_checks"]["skipped"] == 22
    assert issues.status_code == 200, issues.text
    assert hidden.status_code == 404, hidden.text


async def test_schedule_http_contract_error_has_stable_pointer(test_client, standard_user):
    submission = _submission(f"pytest-{uuid.uuid4().hex}")
    submission["snapshot"]["tasks"][0]["planned_start"] = "not-a-date"

    response = await test_client.post(
        "/api/schedule/snapshots",
        json=submission,
        headers=standard_user["headers"],
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["errors"][0]["path"] == "/snapshot/tasks/0/planned_start"
    assert "not-a-date" not in response.text


async def test_schedule_http_concurrency_body_limit_and_private_object(test_client, standard_user):
    headers = standard_user["headers"]
    submission = _submission(f"pytest-{uuid.uuid4().hex}")

    first, second = await asyncio.gather(
        test_client.post("/api/schedule/snapshots", json=submission, headers=headers),
        test_client.post("/api/schedule/snapshots", json=submission, headers=headers),
    )

    assert sorted((first.status_code, second.status_code)) == [200, 201]
    assert first.json()["schedule_snapshot_id"] == second.json()["schedule_snapshot_id"]

    oversized = await test_client.post(
        "/api/schedule/snapshots",
        content=b"{" + b" " * (10 * 1024 * 1024) + b"}",
        headers={**headers, "content-type": "application/json"},
    )
    assert oversized.status_code == 413, oversized.text

    snapshot_id = first.json()["schedule_snapshot_id"]
    owner_uid = standard_user["user"]["uid"]
    object_name = f"{owner_uid}/{snapshot_id}/snapshot.json"
    minio_origin = (os.getenv("MINIO_PUBLIC_URI") or os.getenv("MINIO_URI") or "").rstrip("/")
    assert minio_origin, "MINIO_PUBLIC_URI or MINIO_URI must be configured"
    unauthenticated = await test_client.get(f"{minio_origin}/schedule-snapshots/{object_name}")
    assert unauthenticated.status_code in {401, 403}, unauthenticated.text


async def test_schedule_http_recovers_a_failed_reservation(test_client, standard_user):
    owner_uid = standard_user["user"]["uid"]
    submission = _submission(f"pytest-{uuid.uuid4().hex}")
    contract = ScheduleSnapshotSubmission.model_validate(submission)
    canonical_bytes = json.dumps(
        contract.snapshot.model_dump(mode="json", exclude_none=False),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    content_sha256 = f"sha256:{hashlib.sha256(canonical_bytes).hexdigest()}"
    snapshot_id = uuid.uuid4().hex
    execution_token = uuid.uuid4().hex
    database_url = os.environ["POSTGRES_URL"]
    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def session_factory():
        session = session_maker()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    # This test injects a failed row directly. Its engine must belong to the
    # current pytest event loop instead of reusing the application singleton.
    repository = ScheduleRepository(session_factory)
    try:
        await repository.reserve(
            {
                "schedule_snapshot_id": snapshot_id,
                "owner_uid": owner_uid,
                "request_id": contract.request_id,
                "external_project_id": contract.external_project_id,
                "external_snapshot_id": contract.external_snapshot_id,
                "external_revision": contract.external_revision,
                "source_snapshot_id": contract.snapshot.snapshot_id,
                "schema_version": contract.snapshot.schema_version,
                "snapshot_content_sha256": content_sha256,
                "minio_bucket": SCHEDULE_BUCKET,
                "minio_object": f"{owner_uid}/{snapshot_id}/snapshot.json",
                "submission_status": "creating",
                "execution_token": execution_token,
                "execution_started_at": utc_now_naive(),
            }
        )
        await repository.mark_failed(snapshot_id, execution_token, "PYTEST_INJECTED_FAILURE")
    finally:
        await engine.dispose()

    recovered = await test_client.post(
        "/api/schedule/snapshots",
        json=submission,
        headers=standard_user["headers"],
    )

    assert recovered.status_code == 201, recovered.text
    assert recovered.json()["schedule_snapshot_id"] == snapshot_id


async def test_schedule_dependency_workbench_draft_confirm_and_owner_isolation(
    test_client,
    standard_user,
    admin_headers,
):
    headers = standard_user["headers"]
    submission = _submission(f"pytest-{uuid.uuid4().hex}")
    created = await test_client.post("/api/schedule/snapshots", json=submission, headers=headers)
    assert created.status_code == 201, created.text
    snapshot_id = created.json()["schedule_snapshot_id"]

    issues = await test_client.get(
        f"/api/schedule/snapshots/{snapshot_id}/issues?category=dependency",
        headers=headers,
    )
    issue = next(item for item in issues.json()["items"] if item["rule_id"] == "SUMMARY_TASK_DEPENDENCY")
    workbench = await test_client.get(
        f"/api/schedule/issues/{issue['issue_id']}/dependency-workbench",
        headers=headers,
    )
    assert workbench.status_code == 200, workbench.text
    context = workbench.json()
    assert context["predecessor"]["candidate_task_ids"]
    assert context["successor"]["candidate_task_ids"]
    source_before = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}", headers=headers)
    assert source_before.status_code == 200, source_before.text

    payload = {
        "resolution": "replace_with_leaf_tasks",
        "predecessor_task_ids": [context["predecessor"]["candidate_task_ids"][0]],
        "successor_task_ids": [context["successor"]["candidate_task_ids"][0]],
        "dependency_type": context["source_dependency"]["type"],
        "lag_minutes": context["source_dependency"]["lag_minutes"],
        "reason": "集成测试确认阶段出口和入口",
    }
    saved = await test_client.put(
        f"/api/schedule/issues/{issue['issue_id']}/dependency-decision",
        json=payload,
        headers=headers,
    )
    confirmed = await test_client.post(
        f"/api/schedule/issues/{issue['issue_id']}/dependency-decision/confirm",
        headers=headers,
    )
    immutable = await test_client.put(
        f"/api/schedule/issues/{issue['issue_id']}/dependency-decision",
        json={**payload, "reason": "确认后尝试修改"},
        headers=headers,
    )
    hidden = await test_client.get(
        f"/api/schedule/issues/{issue['issue_id']}/dependency-workbench",
        headers=admin_headers,
    )
    source_after = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}", headers=headers)

    assert saved.status_code == 200 and saved.json()["status"] == "draft"
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "confirmed"
    assert immutable.status_code == 409
    assert hidden.status_code == 404
    assert source_after.json()["snapshot_content_sha256"] == source_before.json()["snapshot_content_sha256"]
    assert source_after.json()["snapshot"] == source_before.json()["snapshot"]


async def test_schedule_dependency_candidate_review_delivery_and_source_immutability(
    test_client,
    standard_user,
    admin_headers,
):
    headers = standard_user["headers"]
    submission = _submission(f"pytest-{uuid.uuid4().hex}")
    created = await test_client.post("/api/schedule/snapshots", json=submission, headers=headers)
    assert created.status_code == 201, created.text
    snapshot_id = created.json()["schedule_snapshot_id"]
    source_before = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}", headers=headers)

    issues = await test_client.get(
        f"/api/schedule/snapshots/{snapshot_id}/issues?category=dependency",
        headers=headers,
    )
    base_all_issues = await test_client.get(
        f"/api/schedule/snapshots/{snapshot_id}/issues?limit=500",
        headers=headers,
    )
    issue = next(item for item in issues.json()["items"] if item["rule_id"] == "SUMMARY_TASK_DEPENDENCY")
    workbench = await test_client.get(
        f"/api/schedule/issues/{issue['issue_id']}/dependency-workbench",
        headers=headers,
    )
    context = workbench.json()
    decision_payload = {
        "resolution": "replace_with_leaf_tasks",
        "predecessor_task_ids": [context["predecessor"]["candidate_task_ids"][0]],
        "successor_task_ids": [context["successor"]["candidate_task_ids"][0]],
        "dependency_type": "FS",
        "lag_minutes": 480,
        "reason": "集成测试确认依赖规范化",
    }
    saved = await test_client.put(
        f"/api/schedule/issues/{issue['issue_id']}/dependency-decision",
        json=decision_payload,
        headers=headers,
    )
    confirmed = await test_client.post(
        f"/api/schedule/issues/{issue['issue_id']}/dependency-decision/confirm",
        headers=headers,
    )
    assert saved.status_code == 200 and confirmed.status_code == 200

    optimization_payload = {
        "request_id": f"pytest-optimization-{uuid.uuid4().hex}",
        "dependency_decision_id": confirmed.json()["decision_id"],
        "base_snapshot_content_sha256": source_before.json()["snapshot_content_sha256"],
    }
    candidate = await test_client.post(
        f"/api/schedule/snapshots/{snapshot_id}/optimizations",
        json=optimization_payload,
        headers=headers,
    )
    replay = await test_client.post(
        f"/api/schedule/snapshots/{snapshot_id}/optimizations",
        json=optimization_payload,
        headers=headers,
    )
    replay_with_new_request = await test_client.post(
        f"/api/schedule/snapshots/{snapshot_id}/optimizations",
        json={**optimization_payload, "request_id": f"pytest-optimization-{uuid.uuid4().hex}"},
        headers=headers,
    )
    assert candidate.status_code == 201, candidate.text
    assert replay.status_code == 200, replay.text
    assert replay_with_new_request.status_code == 200, replay_with_new_request.text
    candidate_payload = candidate.json()
    candidate_id = candidate_payload["candidate_snapshot_id"]
    assert replay.json()["candidate_snapshot_id"] == candidate_id
    assert replay_with_new_request.json()["candidate_snapshot_id"] == candidate_id
    assert candidate_payload["candidate_status"] == "valid"
    assert candidate_payload["comparison"]["target_issue_resolved"] is True
    assert candidate_payload["candidate_snapshot"]["engine_result"] is None

    hidden = await test_client.get(f"/api/schedule/candidates/{candidate_id}", headers=admin_headers)
    rejected = await test_client.post(
        f"/api/schedule/candidates/{candidate_id}/decisions",
        json={
            "request_id": f"pytest-reject-{uuid.uuid4().hex}",
            "attitude": "rejected",
            "comment": "先验证拒绝历史",
        },
        headers=headers,
    )
    accepted = await test_client.post(
        f"/api/schedule/candidates/{candidate_id}/decisions",
        json={
            "request_id": f"pytest-accept-{uuid.uuid4().hex}",
            "attitude": "accepted",
            "comment": "确认进入 Delivery",
        },
        headers=headers,
    )
    delivery = await test_client.get(
        f"/api/schedule/candidates/{candidate_id}/delivery",
        headers=headers,
    )
    source_after = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}", headers=headers)

    assert hidden.status_code == 404
    assert rejected.status_code == 201 and accepted.status_code == 201
    assert delivery.status_code == 200, delivery.text
    assert delivery.json()["application_allowed"] is True
    assert delivery.json()["simulation_result"] is None
    assert delivery.json()["requested_patch"] == candidate_payload["requested_patch"]
    assert source_after.json()["snapshot_content_sha256"] == source_before.json()["snapshot_content_sha256"]
    assert source_after.json()["snapshot"] == source_before.json()["snapshot"]

    new_source_snapshot_id = f"source-copy-{uuid.uuid4().hex}"
    source_copy = apply_delivery_to_source_copy(
        source_before.json()["snapshot"],
        delivery.json(),
        new_source_snapshot_id=new_source_snapshot_id,
        generated_at=datetime.now(UTC),
    )
    applied = await test_client.post(
        "/api/schedule/snapshots",
        json={
            "request_id": f"pytest-delivery-application-{uuid.uuid4().hex}",
            "external_project_id": submission["external_project_id"],
            "external_snapshot_id": new_source_snapshot_id,
            "external_revision": "V2.2-delivery-1",
            "snapshot": source_copy,
        },
        headers=headers,
    )
    assert applied.status_code == 201, applied.text
    applied_snapshot_id = applied.json()["schedule_snapshot_id"]
    applied_detail = await test_client.get(f"/api/schedule/snapshots/{applied_snapshot_id}", headers=headers)
    applied_issues = await test_client.get(
        f"/api/schedule/snapshots/{applied_snapshot_id}/issues?limit=500", headers=headers
    )
    original_unchanged = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}", headers=headers)
    acceptance_evidence = await test_client.get(
        f"/api/schedule/candidates/{candidate_id}/acceptance-evidence", headers=headers
    )
    hidden_evidence = await test_client.get(
        f"/api/schedule/candidates/{candidate_id}/acceptance-evidence", headers=admin_headers
    )

    assert applied_detail.status_code == 200, applied_detail.text
    assert applied_issues.status_code == 200, applied_issues.text
    assert acceptance_evidence.status_code == 200, acceptance_evidence.text
    assert acceptance_evidence.json()["status"] == "passed"
    assert len(acceptance_evidence.json()["checks"]) == 16
    assert all(item["passed"] for item in acceptance_evidence.json()["checks"])
    assert hidden_evidence.status_code == 404
    assert applied_detail.json()["external_revision"] == "V2.2-delivery-1"
    assert not any(
        item["dependency_id"] == context["source_dependency"]["dependency_id"]
        for item in applied_detail.json()["snapshot"]["dependencies"]
    )
    added_dependency = candidate_payload["effective_patch"]["added_dependencies"][0]
    assert added_dependency in applied_detail.json()["snapshot"]["dependencies"]
    assert not any(
        item["rule_id"] == "SUMMARY_TASK_DEPENDENCY"
        and context["source_dependency"]["dependency_id"] in item["object_refs"]
        for item in applied_issues.json()["items"]
    )
    assert not any(item["rule_id"] == "STATISTICS_MISMATCH" for item in applied_issues.json()["items"])
    base_blocker_rules = {item["rule_id"] for item in base_all_issues.json()["items"] if item["severity"] == "blocker"}
    applied_blocker_rules = {
        item["rule_id"] for item in applied_issues.json()["items"] if item["severity"] == "blocker"
    }
    assert applied_blocker_rules <= base_blocker_rules
    assert original_unchanged.json()["snapshot_content_sha256"] == source_before.json()["snapshot_content_sha256"]
    assert original_unchanged.json()["snapshot"] == source_before.json()["snapshot"]


async def test_schedule_forward_recalculation_candidate_decision_delivery_and_blocked_input(
    test_client,
    standard_user,
):
    headers = standard_user["headers"]
    submission = _forward_submission(f"pytest-{uuid.uuid4().hex}")
    created = await test_client.post("/api/schedule/snapshots", json=submission, headers=headers)
    assert created.status_code == 201, created.text
    snapshot_id = created.json()["schedule_snapshot_id"]
    source_before = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}", headers=headers)

    candidate = await test_client.post(
        f"/api/schedule/snapshots/{snapshot_id}/recalculate-automatic-downstream",
        json={
            "request_id": f"pytest-forward-{uuid.uuid4().hex}",
            "base_snapshot_content_sha256": source_before.json()["snapshot_content_sha256"],
        },
        headers=headers,
    )
    assert candidate.status_code == 201, candidate.text
    candidate_payload = candidate.json()
    candidate_id = candidate_payload["candidate_snapshot_id"]
    reviewed = await test_client.post(
        f"/api/schedule/candidates/{candidate_id}/decisions",
        json={
            "request_id": f"pytest-forward-review-{uuid.uuid4().hex}",
            "attitude": "accepted",
            "comment": "确认最小正向日期差异",
        },
        headers=headers,
    )
    delivery = await test_client.get(f"/api/schedule/candidates/{candidate_id}/delivery", headers=headers)
    source_after = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}", headers=headers)

    assert reviewed.status_code == 201, reviewed.text
    assert delivery.status_code == 200, delivery.text
    assert candidate_payload["candidate_kind"] == "automatic_forward_recalculation"
    assert candidate_payload["candidate_snapshot"]["engine_result"]["status"] == "calculated"
    assert candidate_payload["candidate_snapshot"]["engine_profile_id"] == REVERSE_FLOAT_ENGINE_PROFILE_ID
    task_dates = candidate_payload["candidate_snapshot"]["engine_result"]["task_dates"]
    assert {item["task_id"] for item in task_dates} == {
        item["task_id"] for item in source_before.json()["snapshot"]["tasks"]
    }
    assert all(
        {
            "late_start",
            "late_finish",
            "total_slack_minutes",
            "free_slack_minutes",
            "critical",
        }
        <= item.keys()
        for item in task_dates
    )
    assert any(item["summary"] for item in task_dates)
    assert delivery.json()["simulation_result"]["status"] == "calculated"
    assert source_after.json()["snapshot_content_sha256"] == source_before.json()["snapshot_content_sha256"]
    assert source_after.json()["snapshot"] == source_before.json()["snapshot"]

    blocked_submission = _submission(f"pytest-{uuid.uuid4().hex}")
    blocked_created = await test_client.post("/api/schedule/snapshots", json=blocked_submission, headers=headers)
    blocked_snapshot_id = blocked_created.json()["schedule_snapshot_id"]
    blocked_detail = await test_client.get(f"/api/schedule/snapshots/{blocked_snapshot_id}", headers=headers)
    blocked = await test_client.post(
        f"/api/schedule/snapshots/{blocked_snapshot_id}/recalculate-automatic-downstream",
        json={
            "request_id": f"pytest-forward-blocked-{uuid.uuid4().hex}",
            "base_snapshot_content_sha256": blocked_detail.json()["snapshot_content_sha256"],
        },
        headers=headers,
    )

    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["candidate_status"] == "blocked"
    assert blocked.json()["engine_result"]["task_dates"] == []
