from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from yuxi.services import schedule_audit_service as schedule_audit_service_module
from yuxi.schedule.contracts.envelope import ScheduleSnapshotSubmission
from yuxi.services.schedule_audit_service import (
    SUBMISSION_LEASE_SECONDS,
    ScheduleAuditService,
    ScheduleConflictError,
    ScheduleDependencyError,
)
from yuxi.utils.datetime_utils import utc_now_naive


class FakeScheduleRepository:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], SimpleNamespace] = {}
        self.audits: dict[str, SimpleNamespace] = {}
        self._lock = asyncio.Lock()

    async def reserve(self, values):
        async with self._lock:
            key = (values["owner_uid"], values["request_id"])
            if key in self.records:
                return self.records[key], False
            record = SimpleNamespace(**values, created_at=None, ready_at=None, failure_code=None)
            self.records[key] = record
            return record, True

    async def claim_recoverable(
        self,
        snapshot_id,
        execution_token,
        *,
        execution_started_at,
        stale_before,
    ):
        async with self._lock:
            record = next(item for item in self.records.values() if item.schedule_snapshot_id == snapshot_id)
            recoverable = record.submission_status == "failed" or (
                record.submission_status == "creating"
                and (record.execution_started_at is None or record.execution_started_at <= stale_before)
            )
            if not recoverable:
                return False
            record.submission_status = "creating"
            record.execution_token = execution_token
            record.execution_started_at = execution_started_at
            record.failure_code = None
            return True

    async def get_submission(self, owner_uid, request_id):
        return self.records.get((owner_uid, request_id))

    async def finalize(self, snapshot_id, execution_token, execution):
        record = next(item for item in self.records.values() if item.schedule_snapshot_id == snapshot_id)
        assert record.execution_token == execution_token
        result = execution.result
        self.audits[snapshot_id] = SimpleNamespace(
            audit_run_id=result.audit_run_id,
            schedule_snapshot_id=snapshot_id,
            rule_set_version=result.rule_set_version,
            statistics=result.statistics,
            capabilities={key: value.model_dump(mode="json") for key, value in result.capabilities.items()},
            dependency_date_checks=result.dependency_date_checks.model_dump(mode="json"),
            issue_summary=result.issue_summary.model_dump(mode="json"),
            created_at=None,
        )
        record.submission_status = "ready"
        record.execution_token = None
        record.execution_started_at = None

    async def mark_failed(self, snapshot_id, execution_token, failure_code):
        record = next(item for item in self.records.values() if item.schedule_snapshot_id == snapshot_id)
        if record.execution_token == execution_token:
            record.submission_status = "failed"
            record.execution_token = None
            record.execution_started_at = None
            record.failure_code = failure_code

    async def get_audit(self, owner_uid, snapshot_id):
        record = next(
            (
                item
                for (uid, _), item in self.records.items()
                if uid == owner_uid and item.schedule_snapshot_id == snapshot_id and item.submission_status == "ready"
            ),
            None,
        )
        return self.audits.get(snapshot_id) if record else None


class FakeScheduleStore:
    def __init__(self, *, fail_once: bool = False, delay: float = 0) -> None:
        self.fail_once = fail_once
        self.delay = delay
        self.uploads: list[tuple[str, bytes]] = []

    async def upload(self, object_name: str, data: bytes) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("storage unavailable")
        self.uploads.append((object_name, data))


class CancellableScheduleStore(FakeScheduleStore):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def upload(self, object_name: str, data: bytes) -> None:
        self.started.set()
        await self.release.wait()
        await super().upload(object_name, data)


def _submission(payload: dict, request_id: str = "request-1") -> ScheduleSnapshotSubmission:
    return ScheduleSnapshotSubmission.model_validate(
        {
            "request_id": request_id,
            "external_project_id": "external-project",
            "external_snapshot_id": "external-snapshot",
            "external_revision": "V2.2",
            "snapshot": payload,
        }
    )


@pytest.mark.asyncio
async def test_submit_is_idempotent_for_same_owner_request_and_content(canonical_schedule_payload: dict) -> None:
    repository = FakeScheduleRepository()
    store = FakeScheduleStore()
    service = ScheduleAuditService(repository, store)
    submission = _submission(canonical_schedule_payload)

    created = await service.submit("owner-1", submission)
    replay = await service.submit("owner-1", submission)

    assert created["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert replay["schedule_snapshot_id"] == created["schedule_snapshot_id"]
    assert replay["audit_run_id"] == created["audit_run_id"]
    assert len(store.uploads) == 1


@pytest.mark.asyncio
async def test_same_idempotency_key_with_different_content_conflicts(canonical_schedule_payload: dict) -> None:
    repository = FakeScheduleRepository()
    service = ScheduleAuditService(repository, FakeScheduleStore())
    await service.submit("owner-1", _submission(canonical_schedule_payload))
    changed = {**canonical_schedule_payload, "snapshot_id": "different-source-snapshot"}

    with pytest.raises(ScheduleConflictError):
        await service.submit("owner-1", _submission(changed))


@pytest.mark.asyncio
async def test_failed_upload_can_recover_with_same_request(canonical_schedule_payload: dict) -> None:
    repository = FakeScheduleRepository()
    store = FakeScheduleStore(fail_once=True)
    service = ScheduleAuditService(repository, store)
    submission = _submission(canonical_schedule_payload)

    with pytest.raises(ScheduleDependencyError):
        await service.submit("owner-1", submission)
    recovered = await service.submit("owner-1", submission)

    record = repository.records[("owner-1", "request-1")]
    assert recovered["schedule_snapshot_id"] == record.schedule_snapshot_id
    assert record.submission_status == "ready"
    assert len(store.uploads) == 1


@pytest.mark.asyncio
async def test_stale_creating_submission_can_be_reclaimed(canonical_schedule_payload: dict) -> None:
    repository = FakeScheduleRepository()
    store = FakeScheduleStore()
    service = ScheduleAuditService(repository, store)
    submission = _submission(canonical_schedule_payload)
    snapshot_json = submission.snapshot.model_dump(mode="json", exclude_none=False)
    canonical_bytes = json.dumps(
        snapshot_json,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    content_sha256 = f"sha256:{hashlib.sha256(canonical_bytes).hexdigest()}"
    stale_started_at = utc_now_naive() - timedelta(seconds=SUBMISSION_LEASE_SECONDS + 1)
    await repository.reserve(
        {
            "schedule_snapshot_id": "stale-snapshot",
            "owner_uid": "owner-1",
            "request_id": submission.request_id,
            "external_project_id": submission.external_project_id,
            "external_snapshot_id": submission.external_snapshot_id,
            "external_revision": submission.external_revision,
            "source_snapshot_id": submission.snapshot.snapshot_id,
            "schema_version": submission.snapshot.schema_version,
            "snapshot_content_sha256": content_sha256,
            "minio_bucket": "schedule-snapshots",
            "minio_object": "owner-1/stale-snapshot/snapshot.json",
            "submission_status": "creating",
            "execution_token": "abandoned-token",
            "execution_started_at": stale_started_at,
        }
    )

    recovered = await service.submit("owner-1", submission)

    assert recovered["schedule_snapshot_id"] == "stale-snapshot"
    assert repository.records[("owner-1", submission.request_id)].submission_status == "ready"
    assert len(store.uploads) == 1


@pytest.mark.asyncio
async def test_cancelled_submission_is_released_for_immediate_retry(canonical_schedule_payload: dict) -> None:
    repository = FakeScheduleRepository()
    store = CancellableScheduleStore()
    service = ScheduleAuditService(repository, store)
    submission = _submission(canonical_schedule_payload)
    task = asyncio.create_task(service.submit("owner-1", submission))
    await store.started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    record = repository.records[("owner-1", submission.request_id)]
    assert record.submission_status == "failed"
    assert record.failure_code == "SCHEDULE_REQUEST_CANCELLED"

    store.release.set()
    recovered = await service.submit("owner-1", submission)
    assert recovered["schedule_snapshot_id"] == record.schedule_snapshot_id
    assert record.submission_status == "ready"


@pytest.mark.asyncio
async def test_concurrent_same_request_has_one_execution_owner(canonical_schedule_payload: dict) -> None:
    repository = FakeScheduleRepository()
    store = FakeScheduleStore(delay=0.1)
    service = ScheduleAuditService(repository, store)
    submission = _submission(canonical_schedule_payload)

    first, second = await asyncio.gather(
        service.submit("owner-1", submission),
        service.submit("owner-1", submission),
    )

    assert first["schedule_snapshot_id"] == second["schedule_snapshot_id"]
    assert {first["idempotent_replay"], second["idempotent_replay"]} == {False, True}
    assert len(store.uploads) == 1


@pytest.mark.asyncio
async def test_concurrent_wait_covers_more_than_the_previous_100_poll_limit(
    monkeypatch,
    canonical_schedule_payload: dict,
) -> None:
    repository = FakeScheduleRepository()
    service = ScheduleAuditService(repository, FakeScheduleStore())
    submission = _submission(canonical_schedule_payload)
    created = await service.submit("owner-1", submission)
    record = repository.records[("owner-1", submission.request_id)]
    record.submission_status = "creating"
    record.execution_token = "execution-owner"
    record.execution_started_at = utc_now_naive()

    polls = 0

    async def complete_after_101_polls(_seconds: float) -> None:
        nonlocal polls
        polls += 1
        if polls == 101:
            record.submission_status = "ready"
            record.execution_token = None
            record.execution_started_at = None

    monkeypatch.setattr(schedule_audit_service_module.asyncio, "sleep", complete_after_101_polls)

    replay = await service._resolve_existing(
        "owner-1",
        submission.request_id,
        record,
        "waiting-execution",
    )

    assert polls == 101
    assert replay is not None
    assert replay["idempotent_replay"] is True
    assert replay["schedule_snapshot_id"] == created["schedule_snapshot_id"]
