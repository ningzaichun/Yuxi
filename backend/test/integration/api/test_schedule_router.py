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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from scripts.export_schedule_suite_canonical import project_suite_reference
from scripts.generate_synthetic_schedule_case import build_synthetic_schedule_case
from test.support.schedule_suite import execute_suite_case, normalize_suite_document
from yuxi.repositories.schedule_repository import ScheduleRepository
from yuxi.schedule.audit.engine import audit_schedule
from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22
from yuxi.schedule.contracts.envelope import ScheduleSnapshotSubmission
from yuxi.schedule.delivery_adapter import apply_delivery_to_source_copy
from yuxi.schedule.forward_engine import (
    COMPLETED_PROGRESS_ENGINE_PROFILE_ID,
    CONSTRAINTS_ENGINE_PROFILE_ID,
    IN_PROGRESS_ENGINE_PROFILE_ID,
    MILESTONE_ENGINE_PROFILE_ID,
    MULTI_CALENDAR_ENGINE_PROFILE_ID,
    RESOURCE_ANALYSIS_ENGINE_PROFILE_ID,
    REVERSE_FLOAT_ENGINE_PROFILE_ID,
)
from yuxi.schedule.importers.canonical_v2_2 import import_canonical_schedule_v2_2
from yuxi.schedule.storage import SCHEDULE_BUCKET, ScheduleSnapshotStore
from yuxi.storage.postgres.models_schedule import ScheduleCandidateRecord, ScheduleSnapshotRecord
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "data" / "schedule" / "schedule_v2_2_sanitized.json"
IMPORT_CASE_PATH = (
    Path(__file__).resolve().parents[4]
    / "Microsoft_Project_水泵站排期_MOCK_v1.1"
    / "Microsoft_Project_水泵站排期_MOCK_v1.1.json"
)
SUITE_C04_PATH = (
    Path(__file__).resolve().parents[4] / "Yuxi_复杂排期测试套件_v1" / "C04_CONSTRAINTS_DEADLINES" / "input.json"
)
SUITE_C03_PATH = (
    Path(__file__).resolve().parents[4] / "Yuxi_复杂排期测试套件_v1" / "C03_NESTED_SUMMARY_BRANCHES" / "input.json"
)
SUITE_C02_PATH = (
    Path(__file__).resolve().parents[4] / "Yuxi_复杂排期测试套件_v1" / "C02_MULTI_CALENDAR_EXCEPTIONS" / "input.json"
)
SUITE_C05_PATH = (
    Path(__file__).resolve().parents[4] / "Yuxi_复杂排期测试套件_v1" / "C05_BASELINE_PROGRESS" / "input.json"
)
SUITE_C06_PATH = (
    Path(__file__).resolve().parents[4] / "Yuxi_复杂排期测试套件_v1" / "C06_RESOURCE_OVERALLOCATION" / "input.json"
)
SUITE_C01_PATH = Path(__file__).resolve().parents[4] / "Yuxi_复杂排期测试套件_v1" / "C01_RELATION_MATRIX" / "input.json"


def _submission(request_id: str) -> dict:
    return {
        "request_id": request_id,
        "external_project_id": f"pytest-schedule-{request_id}",
        "external_snapshot_id": f"pytest-snapshot-{request_id}",
        "external_revision": "V2.2",
        "snapshot": json.loads(FIXTURE_PATH.read_text(encoding="utf-8")),
    }


def _import_submission(request_id: str) -> dict:
    return {
        "request_id": request_id,
        "external_project_id": f"pytest-water-pump-{request_id}",
        "external_snapshot_id": f"pytest-water-pump-source-{request_id}",
        "external_revision": "v1.1",
        "document": json.loads(IMPORT_CASE_PATH.read_text(encoding="utf-8")),
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


def _completed_progress_document() -> dict:
    document = json.loads(SUITE_C05_PATH.read_text(encoding="utf-8"))
    kept_task_ids = {"task:root", "task:start", "task:a", "task:c", "task:finish"}
    document["case_id"] = "C05_COMPLETED_NOT_STARTED_API"
    document["tasks"] = [task for task in document["tasks"] if task["task_id"] in kept_task_ids]
    dependencies = {item["dependency_id"]: item for item in document["dependencies"]}
    a_to_c = copy.deepcopy(dependencies["dep:a:b:FS:0"])
    a_to_c.update({"dependency_id": "dep:a:c:FS:0", "successor_task_id": "task:c"})
    document["dependencies"] = [
        dependencies["dep:start:a:FS:0"],
        a_to_c,
        dependencies["dep:c:finish:FS:0"],
    ]
    return document


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


async def test_suite_reference_http_flow_preserves_verified_engine_dates(
    test_client,
    standard_user,
):
    headers = standard_user["headers"]
    document = json.loads(SUITE_C01_PATH.read_text(encoding="utf-8"))
    execution = execute_suite_case(document)
    canonical = project_suite_reference(execution.canonical, execution.engine_result)
    request_id = f"pytest-suite-reference-{uuid.uuid4().hex}"
    created = await test_client.post(
        "/api/schedule/snapshots",
        json={
            "request_id": request_id,
            "external_project_id": canonical.project.project_id,
            "external_snapshot_id": f"suite-reference-{request_id}",
            "external_revision": "Y2-engine-reference",
            "snapshot": canonical.model_dump(mode="json", exclude_none=False),
        },
        headers=headers,
    )

    assert created.status_code == 201, created.text
    snapshot_id = created.json()["schedule_snapshot_id"]
    detail = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}", headers=headers)
    audit = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}/audit", headers=headers)
    issues = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}/issues", headers=headers)

    assert detail.status_code == 200, detail.text
    snapshot = detail.json()["snapshot"]
    assert snapshot["source"]["extraction_method"] == "YUXI_TEST_SUITE_ENGINE_REFERENCE"
    assert snapshot["semantics"]["source_dates_preserved"] is False
    assert snapshot["project"]["planned_finish"] == execution.engine_result["finish_after"]
    assert len({task["planned_finish"] for task in snapshot["tasks"]}) > 1
    assert audit.status_code == 200, audit.text
    assert audit.json()["dependency_date_checks"]["violation_count"] == 0
    assert issues.status_code == 200, issues.text
    assert "STATISTICS_MISMATCH" not in {item["rule_id"] for item in issues.json()["items"]}

    candidate = await test_client.post(
        f"/api/schedule/snapshots/{snapshot_id}/recalculate-automatic-downstream",
        json={
            "request_id": f"pytest-suite-reference-candidate-{uuid.uuid4().hex}",
            "base_snapshot_content_sha256": created.json()["snapshot_content_sha256"],
            "locked_task_ids": [],
        },
        headers=headers,
    )
    assert candidate.status_code == 201, candidate.text
    assert candidate.json()["comparison"]["finish_before"] == execution.engine_result["finish_after"]
    assert candidate.json()["comparison"]["finish_after"] == execution.engine_result["finish_after"]


@pytest.mark.parametrize(
    ("case_path", "expected_schema", "expected_profile"),
    [
        (SUITE_C03_PATH, "canonical_schedule_v2.3", MILESTONE_ENGINE_PROFILE_ID),
        (SUITE_C02_PATH, "canonical_schedule_v2.4", MULTI_CALENDAR_ENGINE_PROFILE_ID),
    ],
)
async def test_schedule_v23_v24_http_audit_optimization_and_candidate_read(
    test_client,
    standard_user,
    case_path: Path,
    expected_schema: str,
    expected_profile: str,
):
    headers = standard_user["headers"]
    canonical, _ = normalize_suite_document(json.loads(case_path.read_text(encoding="utf-8")))
    request_id = f"pytest-versioned-{uuid.uuid4().hex}"
    submission = {
        "request_id": request_id,
        "external_project_id": f"pytest-versioned-{request_id}",
        "external_snapshot_id": f"pytest-versioned-snapshot-{request_id}",
        "external_revision": expected_schema,
        "snapshot": canonical.model_dump(mode="json", exclude_none=False),
    }

    created = await test_client.post("/api/schedule/snapshots", json=submission, headers=headers)

    assert created.status_code == 201, created.text
    snapshot_id = created.json()["schedule_snapshot_id"]
    audit = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}/audit", headers=headers)
    assert audit.status_code == 200, audit.text
    assert audit.json()["capabilities"]["cpm_recalculation"] == {
        "allowed": True,
        "reasons": [],
    }
    candidate = await test_client.post(
        f"/api/schedule/snapshots/{snapshot_id}/recalculate-automatic-downstream",
        json={
            "request_id": f"pytest-versioned-candidate-{uuid.uuid4().hex}",
            "base_snapshot_content_sha256": created.json()["snapshot_content_sha256"],
            "locked_task_ids": [],
        },
        headers=headers,
    )

    assert candidate.status_code == 201, candidate.text
    candidate_id = candidate.json()["candidate_snapshot_id"]
    detail = await test_client.get(f"/api/schedule/candidates/{candidate_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["candidate_status"] == "valid"
    assert detail.json()["canonical_schema_version"] == expected_schema
    assert detail.json()["engine_profile_id"] == expected_profile
    assert detail.json()["base_schedule_snapshot_id"] == snapshot_id
    assert detail.json()["base_snapshot_content_sha256"] == created.json()["snapshot_content_sha256"]


async def test_schedule_v25_http_constraint_candidate_keeps_warning_evidence(
    test_client,
    standard_user,
):
    headers = standard_user["headers"]
    document = json.loads(SUITE_C04_PATH.read_text(encoding="utf-8"))
    canonical, _ = normalize_suite_document(document)
    request_id = f"pytest-v25-{uuid.uuid4().hex}"
    submission = {
        "request_id": request_id,
        "external_project_id": f"pytest-v25-{request_id}",
        "external_snapshot_id": f"pytest-v25-snapshot-{request_id}",
        "external_revision": "V2.5-constraints",
        "snapshot": canonical.model_dump(mode="json", exclude_none=False),
    }

    created = await test_client.post("/api/schedule/snapshots", json=submission, headers=headers)

    assert created.status_code == 201, created.text
    snapshot_id = created.json()["schedule_snapshot_id"]
    candidate = await test_client.post(
        f"/api/schedule/snapshots/{snapshot_id}/recalculate-automatic-downstream",
        json={
            "request_id": f"pytest-v25-candidate-{uuid.uuid4().hex}",
            "base_snapshot_content_sha256": created.json()["snapshot_content_sha256"],
            "locked_task_ids": [],
        },
        headers=headers,
    )

    assert candidate.status_code == 201, candidate.text
    payload = candidate.json()
    assert payload["candidate_status"] == "valid"
    assert payload["canonical_schema_version"] == "canonical_schedule_v2.5"
    assert payload["engine_profile_id"] == CONSTRAINTS_ENGINE_PROFILE_ID
    assert payload["candidate_audit"]["issue_summary"]["warning"] == 4
    assert [item["rule_id"] for item in payload["candidate_audit"]["issues"]] == [
        "DEADLINE_MISSED",
        "FINISH_CONSTRAINT_VIOLATED",
        "HARD_CONSTRAINT_NETWORK_CONFLICT",
        "PROJECT_REQUIRED_FINISH_MISSED",
    ]


async def test_schedule_v26_http_completed_progress_candidate_keeps_actual_and_baseline_facts(
    test_client,
    standard_user,
):
    headers = standard_user["headers"]
    canonical, _ = normalize_suite_document(_completed_progress_document())
    request_id = f"pytest-v26-{uuid.uuid4().hex}"
    submission = {
        "request_id": request_id,
        "external_project_id": f"pytest-v26-{request_id}",
        "external_snapshot_id": f"pytest-v26-snapshot-{request_id}",
        "external_revision": "V2.6-completed-progress",
        "snapshot": canonical.model_dump(mode="json", exclude_none=False),
    }

    created = await test_client.post("/api/schedule/snapshots", json=submission, headers=headers)

    assert created.status_code == 201, created.text
    snapshot_id = created.json()["schedule_snapshot_id"]
    candidate = await test_client.post(
        f"/api/schedule/snapshots/{snapshot_id}/recalculate-automatic-downstream",
        json={
            "request_id": f"pytest-v26-candidate-{uuid.uuid4().hex}",
            "base_snapshot_content_sha256": created.json()["snapshot_content_sha256"],
            "locked_task_ids": [],
        },
        headers=headers,
    )

    assert candidate.status_code == 201, candidate.text
    payload = candidate.json()
    assert payload["candidate_status"] == "valid"
    assert payload["canonical_schema_version"] == "canonical_schedule_v2.6"
    assert payload["engine_profile_id"] == COMPLETED_PROGRESS_ENGINE_PROFILE_ID
    engine = payload["candidate_snapshot"]["engine_result"]
    task_a = next(item for item in engine["task_dates"] if item["task_id"] == "task:a")
    assert task_a["actual_start"] == "2026-09-08T08:00:00+08:00"
    assert task_a["actual_finish"] == "2026-09-10T17:00:00+08:00"
    assert next(item for item in engine["baseline_variances"] if item["task_id"] == "task:a") == {
        "task_id": "task:a",
        "start_variance_minutes": 480,
        "finish_variance_minutes": 480,
    }


async def test_schedule_v26_http_in_progress_audit_and_candidate_forecasts_remaining_work(
    test_client,
    standard_user,
):
    headers = standard_user["headers"]
    document = json.loads(SUITE_C05_PATH.read_text(encoding="utf-8"))
    canonical, _ = normalize_suite_document(document)
    request_id = f"pytest-v26-in-progress-{uuid.uuid4().hex}"
    submission = {
        "request_id": request_id,
        "external_project_id": f"pytest-v26-{request_id}",
        "external_snapshot_id": f"pytest-v26-snapshot-{request_id}",
        "external_revision": "V2.6-in-progress",
        "snapshot": canonical.model_dump(mode="json", exclude_none=False),
    }

    created = await test_client.post("/api/schedule/snapshots", json=submission, headers=headers)

    assert created.status_code == 201, created.text
    snapshot_id = created.json()["schedule_snapshot_id"]
    audit = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}/audit", headers=headers)
    assert audit.status_code == 200, audit.text
    assert audit.json()["capabilities"]["cpm_recalculation"] == {
        "allowed": True,
        "reasons": [],
    }
    candidate = await test_client.post(
        f"/api/schedule/snapshots/{snapshot_id}/recalculate-automatic-downstream",
        json={
            "request_id": f"pytest-v26-candidate-{uuid.uuid4().hex}",
            "base_snapshot_content_sha256": created.json()["snapshot_content_sha256"],
            "locked_task_ids": [],
        },
        headers=headers,
    )

    assert candidate.status_code == 201, candidate.text
    candidate_id = candidate.json()["candidate_snapshot_id"]
    detail = await test_client.get(f"/api/schedule/candidates/{candidate_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["candidate_status"] == "valid"
    assert payload["canonical_schema_version"] == "canonical_schedule_v2.6"
    assert payload["engine_profile_id"] == IN_PROGRESS_ENGINE_PROFILE_ID
    engine = payload["candidate_snapshot"]["engine_result"]
    task_b = next(item for item in engine["task_dates"] if item["task_id"] == "task:b")
    assert task_b["actual_start"] == "2026-09-11T08:00:00+08:00"
    assert task_b["remaining_start"] == "2026-09-17T08:00:00+08:00"
    assert task_b["remaining_duration_minutes"] == 1440
    assert task_b["early_finish"] == "2026-09-21T17:00:00+08:00"
    assert engine["finish_after"] == "2026-09-24T17:00:00+08:00"
    assert payload["candidate_audit"]["issues"] == []


async def test_schedule_v27_http_candidate_exposes_resource_conflicts_and_costs(
    test_client,
    standard_user,
):
    headers = standard_user["headers"]
    canonical, _ = normalize_suite_document(json.loads(SUITE_C06_PATH.read_text(encoding="utf-8")))
    request_id = f"pytest-v27-resource-{uuid.uuid4().hex}"
    submission = {
        "request_id": request_id,
        "external_project_id": f"pytest-v27-{request_id}",
        "external_snapshot_id": f"pytest-v27-snapshot-{request_id}",
        "external_revision": "V2.7-resource-analysis",
        "snapshot": canonical.model_dump(mode="json", exclude_none=False),
    }

    created = await test_client.post("/api/schedule/snapshots", json=submission, headers=headers)

    assert created.status_code == 201, created.text
    snapshot_id = created.json()["schedule_snapshot_id"]
    audit = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}/audit", headers=headers)
    assert audit.status_code == 200, audit.text
    assert audit.json()["capabilities"]["resource_leveling"] == {
        "allowed": False,
        "reasons": ["RESOURCE_LEVELING_NOT_IMPLEMENTED"],
    }

    candidate = await test_client.post(
        f"/api/schedule/snapshots/{snapshot_id}/recalculate-automatic-downstream",
        json={
            "request_id": f"pytest-v27-candidate-{uuid.uuid4().hex}",
            "base_snapshot_content_sha256": created.json()["snapshot_content_sha256"],
            "locked_task_ids": [],
        },
        headers=headers,
    )

    assert candidate.status_code == 201, candidate.text
    payload = candidate.json()
    assert payload["candidate_status"] == "valid"
    assert payload["canonical_schema_version"] == "canonical_schedule_v2.7"
    assert payload["engine_profile_id"] == RESOURCE_ANALYSIS_ENGINE_PROFILE_ID
    engine = payload["candidate_snapshot"]["engine_result"]
    assert [item["resource_id"] for item in engine["resource_conflicts"]] == [
        "resource:crane",
        "resource:crew",
    ]
    assert [item["cost"] for item in engine["assignment_costs"]] == [7200, 2880, 2880, 4800]
    assert [item["category"] for item in payload["candidate_audit"]["issues"]] == [
        "resource",
        "resource",
    ]


async def test_schedule_v27_http_preflight_rejects_before_snapshot_or_candidate_persistence(
    test_client,
    standard_user,
):
    owner_uid = standard_user["user"]["uid"]
    canonical, _ = normalize_suite_document(json.loads(SUITE_C06_PATH.read_text(encoding="utf-8")))
    request_id = f"pytest-v27-invalid-{uuid.uuid4().hex}"
    snapshot = canonical.model_dump(mode="json", exclude_none=False)
    snapshot["assignments"][0]["resource_id"] = "resource:missing"
    snapshot["tasks"][0]["calendar_id"] = "calendar:missing"
    submission = {
        "request_id": request_id,
        "external_project_id": f"pytest-v27-invalid-{request_id}",
        "external_snapshot_id": f"pytest-v27-invalid-snapshot-{request_id}",
        "external_revision": "V2.7-invalid-preflight",
        "snapshot": snapshot,
    }

    response = await test_client.post(
        "/api/schedule/snapshots",
        json=submission,
        headers=standard_user["headers"],
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "SCHEDULE_PREFLIGHT_FAILED"
    assert [item["code"] for item in response.json()["detail"]["errors"]] == [
        "ASSIGNMENT_RESOURCE_NOT_FOUND",
        "TASK_CALENDAR_NOT_FOUND",
    ]

    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_maker() as session:
            snapshot_record = await session.scalar(
                select(ScheduleSnapshotRecord).where(
                    ScheduleSnapshotRecord.owner_uid == owner_uid,
                    ScheduleSnapshotRecord.request_id == request_id,
                )
            )
            candidate_record = await session.scalar(
                select(ScheduleCandidateRecord)
                .join(
                    ScheduleSnapshotRecord,
                    ScheduleCandidateRecord.base_schedule_snapshot_id == ScheduleSnapshotRecord.schedule_snapshot_id,
                )
                .where(
                    ScheduleSnapshotRecord.owner_uid == owner_uid,
                    ScheduleSnapshotRecord.request_id == request_id,
                )
            )
    finally:
        await engine.dispose()

    assert snapshot_record is None
    assert candidate_record is None


async def test_schedule_import_http_dual_storage_hashes_and_source_idempotency(
    test_client,
    standard_user,
    admin_headers,
):
    headers = standard_user["headers"]
    owner_uid = standard_user["user"]["uid"]
    submission = _import_submission(f"pytest-import-{uuid.uuid4().hex}")

    first, second = await asyncio.gather(
        test_client.post("/api/schedule/imports", json=submission, headers=headers),
        test_client.post("/api/schedule/imports", json=submission, headers=headers),
    )
    created = first if first.status_code == 201 else second
    replay = second if first.status_code == 201 else first

    assert sorted((first.status_code, second.status_code)) == [200, 201], (first.text, second.text)
    result = created.json()
    snapshot_id = result["schedule_snapshot_id"]
    assert replay.json()["schedule_snapshot_id"] == snapshot_id
    assert result["adapter_id"] == "microsoft_project_interchange_v1_1"
    assert result["adapter_version"] == "1.6.0"
    assert result["capabilities"]["cpm_recalculation"] == {"allowed": True, "reasons": []}
    assert result["dependency_date_checks"]["checked"] == 19
    assert result["dependency_date_checks"]["skipped"] == 0

    store = ScheduleSnapshotStore()
    source_object = f"{owner_uid}/{snapshot_id}/source-document.json"
    canonical_object = f"{owner_uid}/{snapshot_id}/snapshot.json"
    source_bytes = await store.download(source_object)
    canonical_bytes = await store.download(canonical_object)
    assert json.loads(source_bytes) == submission["document"]
    assert result["source_document_sha256"] == f"sha256:{hashlib.sha256(source_bytes).hexdigest()}"
    assert result["canonical_snapshot_sha256"] == f"sha256:{hashlib.sha256(canonical_bytes).hexdigest()}"

    changed = copy.deepcopy(submission)
    changed["document"]["vendor_extension"] = {"display_only": True}
    conflict = await test_client.post("/api/schedule/imports", json=changed, headers=headers)
    assert conflict.status_code == 409, conflict.text

    invalid = copy.deepcopy(submission)
    invalid["request_id"] = f"pytest-import-invalid-{uuid.uuid4().hex}"
    del invalid["document"]["project"]["default_calendar_id"]
    rejected = await test_client.post("/api/schedule/imports", json=invalid, headers=headers)
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["detail"]["errors"][0]["path"] == "/document/project/default_calendar_id"

    detail = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}", headers=headers)
    hidden = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}", headers=admin_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["source_document_sha256"] == result["source_document_sha256"]
    assert detail.json()["canonical_snapshot_sha256"] == result["canonical_snapshot_sha256"]
    assert hidden.status_code == 404, hidden.text

    minio_origin = (os.getenv("MINIO_PUBLIC_URI") or os.getenv("MINIO_URI") or "").rstrip("/")
    assert minio_origin, "MINIO_PUBLIC_URI or MINIO_URI must be configured"
    unauthenticated = await test_client.get(f"{minio_origin}/{SCHEDULE_BUCKET}/{source_object}")
    assert unauthenticated.status_code in {401, 403}, unauthenticated.text


async def test_schedule_formal_import_preserves_complex_fields_and_candidate_provenance(
    test_client,
    standard_user,
):
    headers = standard_user["headers"]
    submission = _import_submission(f"pytest-formal-import-{uuid.uuid4().hex}")
    submission["document"]["schema_version"] = "microsoft_project_interchange_v1.1"
    submission["document"]["project"]["required_finish"] = submission["document"]["project"]["planned_finish"]
    activity = next(task for task in submission["document"]["tasks"] if task["task_type"] == "TASK")
    activity["deadline"] = activity["finish"]

    created = await test_client.post("/api/schedule/imports", json=submission, headers=headers)

    assert created.status_code == 201, created.text
    assert created.json()["source_schema_version"] == "microsoft_project_interchange_v1.1"
    assert created.json()["adapter_id"] == "microsoft_project_interchange_v1_1"
    assert created.json()["adapter_version"] == "1.6.0"
    snapshot_id = created.json()["schedule_snapshot_id"]
    detail = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}", headers=headers)
    audit = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}/audit", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["snapshot"]["schema_version"] == "canonical_schedule_v2.5"
    imported_activity = next(
        task for task in detail.json()["snapshot"]["tasks"] if task["task_id"] == activity["task_id"]
    )
    assert imported_activity["deadline"] == activity["finish"]
    assert audit.status_code == 200, audit.text
    assert audit.json()["capabilities"]["cpm_recalculation"] == {
        "allowed": True,
        "reasons": [],
    }
    candidate = await test_client.post(
        f"/api/schedule/snapshots/{snapshot_id}/recalculate-automatic-downstream",
        json={
            "request_id": f"pytest-formal-candidate-{uuid.uuid4().hex}",
            "base_snapshot_content_sha256": created.json()["snapshot_content_sha256"],
            "locked_task_ids": [],
        },
        headers=headers,
    )
    assert candidate.status_code == 201, candidate.text
    candidate_detail = await test_client.get(
        f"/api/schedule/candidates/{candidate.json()['candidate_snapshot_id']}",
        headers=headers,
    )
    assert candidate_detail.status_code == 200, candidate_detail.text
    assert candidate_detail.json()["adapter_id"] == "microsoft_project_interchange_v1_1"
    assert candidate_detail.json()["adapter_version"] == "1.6.0"
    assert candidate_detail.json()["canonical_schema_version"] == "canonical_schedule_v2.5"
    assert candidate_detail.json()["base_schedule_snapshot_id"] == snapshot_id


async def test_schedule_v28_inactive_dependency_workbench_candidate_and_delivery_gate(
    test_client,
    standard_user,
):
    headers = standard_user["headers"]
    submission = _import_submission(f"pytest-v28-inactive-{uuid.uuid4().hex}")
    submission["document"]["schema_version"] = "microsoft_project_interchange_v1.1"
    tasks = {task["task_id"]: task for task in submission["document"]["tasks"]}
    incoming = {task_id: [] for task_id in tasks}
    outgoing = {task_id: [] for task_id in tasks}
    for dependency in submission["document"]["dependencies"]:
        incoming[dependency["successor_task_id"]].append(dependency)
        outgoing[dependency["predecessor_task_id"]].append(dependency)
    inactive = next(
        task
        for task_id, task in tasks.items()
        if task["task_type"] == "TASK"
        and incoming[task_id]
        and outgoing[task_id]
        and tasks[incoming[task_id][0]["predecessor_task_id"]]["task_type"] != "SUMMARY"
        and tasks[outgoing[task_id][0]["successor_task_id"]]["task_type"] != "SUMMARY"
    )
    inactive["active"] = False
    inactive["percent_complete"] = 0

    created = await test_client.post("/api/schedule/imports", json=submission, headers=headers)

    assert created.status_code == 201, created.text
    assert created.json()["adapter_version"] == "1.6.0"
    snapshot_id = created.json()["schedule_snapshot_id"]
    detail = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}", headers=headers)
    assert detail.json()["snapshot"]["schema_version"] == "canonical_schedule_v2.8"
    assert created.json()["capabilities"]["cpm_recalculation"] == {
        "allowed": False,
        "reasons": ["INACTIVE_TASK_DEPENDENCIES"],
    }

    issues = await test_client.get(
        f"/api/schedule/snapshots/{snapshot_id}/issues?category=dependency",
        headers=headers,
    )
    issue = next(item for item in issues.json()["items"] if item["rule_id"] == "INACTIVE_TASK_DEPENDENCY")
    workbench = await test_client.get(
        f"/api/schedule/issues/{issue['issue_id']}/dependency-workbench",
        headers=headers,
    )
    assert workbench.status_code == 200, workbench.text
    context = workbench.json()
    assert len(context["source_dependencies"]) >= 2
    assert inactive["task_id"] not in context["predecessor"]["candidate_task_ids"]
    assert inactive["task_id"] not in context["successor"]["candidate_task_ids"]

    decision = await test_client.put(
        f"/api/schedule/issues/{issue['issue_id']}/dependency-decision",
        json={
            "resolution": "replace_with_leaf_tasks",
            "predecessor_task_ids": [context["predecessor"]["candidate_task_ids"][0]],
            "successor_task_ids": [context["successor"]["candidate_task_ids"][0]],
            "dependency_type": "FS",
            "lag_minutes": 0,
            "reason": "集成测试确认 inactive 前后活动叶子关系",
        },
        headers=headers,
    )
    assert decision.status_code == 200, decision.text
    confirmed = await test_client.post(
        f"/api/schedule/issues/{issue['issue_id']}/dependency-decision/confirm",
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text

    candidate = await test_client.post(
        f"/api/schedule/snapshots/{snapshot_id}/optimizations",
        json={
            "request_id": f"pytest-v28-candidate-{uuid.uuid4().hex}",
            "dependency_decision_id": confirmed.json()["decision_id"],
            "base_snapshot_content_sha256": created.json()["snapshot_content_sha256"],
        },
        headers=headers,
    )
    assert candidate.status_code == 201, candidate.text
    assert candidate.json()["candidate_status"] == "valid"
    assert candidate.json()["comparison"]["target_issue_resolved"] is True
    assert len(candidate.json()["effective_patch"]["removed_dependencies"]) >= 2

    candidate_id = candidate.json()["candidate_snapshot_id"]
    accepted = await test_client.post(
        f"/api/schedule/candidates/{candidate_id}/decisions",
        json={
            "request_id": f"pytest-v28-accept-{uuid.uuid4().hex}",
            "attitude": "accepted",
            "comment": "确认 inactive 依赖替代方案",
        },
        headers=headers,
    )
    assert accepted.status_code == 201, accepted.text
    delivery = await test_client.get(
        f"/api/schedule/candidates/{candidate_id}/delivery",
        headers=headers,
    )
    assert delivery.status_code == 200, delivery.text
    assert delivery.json()["application_allowed"] is False
    assert "DELIVERY_ADAPTER_UNAVAILABLE" in delivery.json()["application_blocking_reasons"]
    assert (
        detail.json()["snapshot"]["tasks"]
        == (await test_client.get(f"/api/schedule/snapshots/{snapshot_id}", headers=headers)).json()["snapshot"][
            "tasks"
        ]
    )


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
    assert delivery.json()["application_allowed"] is False
    assert delivery.json()["application_blocking_reasons"] == ["DELIVERY_ADAPTER_UNAVAILABLE"]
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


async def test_schedule_goal_optimization_is_idempotent_deliverable_and_source_immutable(
    test_client,
    standard_user,
):
    headers = standard_user["headers"]
    submission = _forward_submission(f"pytest-goal-{uuid.uuid4().hex}")
    created = await test_client.post("/api/schedule/snapshots", json=submission, headers=headers)
    assert created.status_code == 201, created.text
    snapshot_id = created.json()["schedule_snapshot_id"]
    source_before = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}", headers=headers)
    payload = {
        "request_id": f"pytest-goal-{uuid.uuid4().hex}",
        "base_snapshot_content_sha256": source_before.json()["snapshot_content_sha256"],
        "objective": "MINIMIZE_PROJECT_FINISH",
        "target_finish": None,
        "authorized_duration_options": [{"task_id": "synthetic-task:build-a", "duration_minutes": 240}],
        "locked_task_ids": [],
        "authorization_confirmed": True,
    }

    candidate = await test_client.post(
        f"/api/schedule/snapshots/{snapshot_id}/goal-optimizations",
        json=payload,
        headers=headers,
    )
    replay = await test_client.post(
        f"/api/schedule/snapshots/{snapshot_id}/goal-optimizations",
        json=payload,
        headers=headers,
    )
    assert candidate.status_code == 201, candidate.text
    assert replay.status_code == 200, replay.text
    candidate_payload = candidate.json()
    candidate_id = candidate_payload["candidate_snapshot_id"]
    delivery = await test_client.get(f"/api/schedule/candidates/{candidate_id}/delivery", headers=headers)
    source_after = await test_client.get(f"/api/schedule/snapshots/{snapshot_id}", headers=headers)

    assert replay.json()["candidate_snapshot_id"] == candidate_id
    assert candidate_payload["candidate_kind"] == "goal_duration_optimization"
    assert candidate_payload["candidate_status"] == "valid"
    assert candidate_payload["comparison"]["finish_after"] < candidate_payload["comparison"]["finish_before"]
    assert candidate_payload["comparison"]["evaluated_strategy_count"] == 2
    assert delivery.status_code == 200, delivery.text
    assert delivery.json()["user_attitude"] == "not_reviewed"
    assert delivery.json()["application_allowed"] is False
    assert delivery.json()["application_blocking_reasons"] == ["DELIVERY_ADAPTER_UNAVAILABLE"]
    assert source_after.json()["snapshot_content_sha256"] == source_before.json()["snapshot_content_sha256"]
    assert source_after.json()["snapshot"] == source_before.json()["snapshot"]
