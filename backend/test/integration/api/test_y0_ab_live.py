from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from yuxi.schedule.storage import SCHEDULE_BUCKET
from yuxi.storage.minio.client import get_minio_client
from yuxi.storage.postgres.models_schedule import ScheduleSnapshotRecord

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

CASES = (
    (
        "A",
        os.getenv("YUXI_Y0_A_INTERCHANGE"),
        "canonical_schedule_v2.4",
        46,
        48,
        2,
    ),
    (
        "B",
        os.getenv("YUXI_Y0_B_INTERCHANGE"),
        "canonical_schedule_v2.5",
        62,
        65,
        2,
    ),
)


async def test_y0_real_projects_import_idempotency_dual_objects_and_hashes(
    test_client,
    standard_user,
) -> None:
    owner_uid = standard_user["user"]["uid"]
    headers = standard_user["headers"]
    created_snapshot_ids: list[str] = []

    if any(path_value is None for _, path_value, *_ in CASES):
        pytest.skip(
            "Set YUXI_Y0_A_INTERCHANGE and YUXI_Y0_B_INTERCHANGE to projected A/B files."
        )

    try:
        for case_id, path_value, schema_version, tasks, dependencies, calendars in CASES:
            path = Path(path_value)
            document = json.loads(path.read_text(encoding="utf-8"))
            request_id = f"pytest-y0-{case_id.lower()}-{uuid.uuid4().hex}"
            submission = {
                "request_id": request_id,
                "external_project_id": f"mpp-clean-{case_id.lower()}",
                "external_snapshot_id": document["source"]["mpp_sha256"],
                "external_revision": "bridge-0.5.0-y0",
                "document": document,
            }

            created = await test_client.post(
                "/api/schedule/imports", json=submission, headers=headers
            )
            replay = await test_client.post(
                "/api/schedule/imports", json=submission, headers=headers
            )

            assert created.status_code == 201, created.text
            result = created.json()
            snapshot_id = result["schedule_snapshot_id"]
            created_snapshot_ids.append(snapshot_id)
            assert replay.status_code == 200, replay.text
            assert replay.json()["schedule_snapshot_id"] == snapshot_id
            assert replay.json()["idempotent_replay"] is True
            assert result["source_schema_version"] == "microsoft_project_interchange_v1.1"
            assert result["adapter_id"] == "microsoft_project_interchange_v1_1"
            assert result["adapter_version"] == "1.6.0"

            minio = get_minio_client()
            source_object = f"{owner_uid}/{snapshot_id}/source-document.json"
            canonical_object = f"{owner_uid}/{snapshot_id}/snapshot.json"
            source_bytes = await minio.adownload_file(SCHEDULE_BUCKET, source_object)
            canonical_bytes = await minio.adownload_file(SCHEDULE_BUCKET, canonical_object)
            canonical = json.loads(canonical_bytes)

            assert json.loads(source_bytes) == document
            assert result["source_document_sha256"] == (
                f"sha256:{hashlib.sha256(source_bytes).hexdigest()}"
            )
            assert result["canonical_snapshot_sha256"] == (
                f"sha256:{hashlib.sha256(canonical_bytes).hexdigest()}"
            )
            assert canonical["schema_version"] == schema_version
            assert canonical["statistics"]["tasks"] == tasks
            assert canonical["statistics"]["dependencies"] == dependencies
            assert canonical["statistics"]["calendars"] == calendars
            assert canonical["validation"]["summary"]["source_fidelity_valid"] is True
    finally:
        minio = get_minio_client()
        for snapshot_id in created_snapshot_ids:
            assert await minio.adelete_file(
                SCHEDULE_BUCKET, f"{owner_uid}/{snapshot_id}/source-document.json"
            )
            assert await minio.adelete_file(
                SCHEDULE_BUCKET, f"{owner_uid}/{snapshot_id}/snapshot.json"
            )

        if created_snapshot_ids:
            engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
            session_maker = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            try:
                async with session_maker.begin() as session:
                    deleted = await session.execute(
                        delete(ScheduleSnapshotRecord).where(
                            ScheduleSnapshotRecord.schedule_snapshot_id.in_(
                                created_snapshot_ids
                            )
                        )
                    )
                    assert deleted.rowcount == len(created_snapshot_ids)
            finally:
                await engine.dispose()
