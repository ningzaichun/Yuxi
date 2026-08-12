from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from yuxi.repositories.schedule_repository import ScheduleRepository
from yuxi.schedule.contracts.envelope import ScheduleSnapshotSubmission
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
