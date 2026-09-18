"""Schedule snapshot submission and authenticated read use cases."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

from yuxi.repositories.schedule_repository import ScheduleRepository
from yuxi.schedule.audit.engine import audit_schedule
from yuxi.schedule.contracts.dependency_decision import DependencyDecisionDraft
from yuxi.schedule.contracts.envelope import ScheduleSnapshotSubmission
from yuxi.schedule.contracts.import_v1 import ScheduleImportEnvelope
from yuxi.schedule.importers import build_default_schedule_import_registry, import_canonical_schedule
from yuxi.schedule.importers.registry import ScheduleImportAdapterRegistry
from yuxi.schedule.storage import SCHEDULE_BUCKET, ScheduleSnapshotStore
from yuxi.utils.datetime_utils import utc_now_naive

SUBMISSION_LEASE_SECONDS = 60
SUBMISSION_POLL_INTERVAL_SECONDS = 0.05
REVIEW_CONTEXT_ISSUE_LIMIT = 50
REVIEW_CONTEXT_OBJECT_REF_LIMIT = 10
DEPENDENCY_DECISION_RULE_IDS = {
    "SUMMARY_TASK_DEPENDENCY",
    "INACTIVE_TASK_DEPENDENCY",
}


class ScheduleConflictError(Exception):
    pass


class ScheduleNotFoundError(Exception):
    pass


class ScheduleSubmissionInProgressError(Exception):
    pass


class ScheduleDependencyError(Exception):
    pass


class ScheduleDecisionConflictError(Exception):
    pass


class ScheduleDecisionInvalidError(Exception):
    pass


class ScheduleAuditService:
    def __init__(
        self,
        repository: ScheduleRepository | None = None,
        store: ScheduleSnapshotStore | None = None,
        import_registry: ScheduleImportAdapterRegistry | None = None,
    ) -> None:
        self._repository = repository or ScheduleRepository()
        self._store = store or ScheduleSnapshotStore()
        self._import_registry = import_registry or build_default_schedule_import_registry()

    async def submit(self, owner_uid: str, submission: ScheduleSnapshotSubmission) -> dict[str, Any]:
        canonical_bytes = _canonical_json_bytes(submission.snapshot.model_dump(mode="json", exclude_none=False))
        return await self._persist_submission(
            owner_uid,
            submission,
            canonical_bytes=canonical_bytes,
            content_sha256=_sha256(canonical_bytes),
        )

    async def submit_import(self, owner_uid: str, submission: ScheduleImportEnvelope) -> dict[str, Any]:
        normalized = self._import_registry.normalize(submission.document)
        canonical_bytes = _canonical_json_bytes(normalized.canonical.model_dump(mode="json", exclude_none=False))
        source_document_bytes = _canonical_json_bytes(submission.document)
        canonical_submission = ScheduleSnapshotSubmission(
            request_id=submission.request_id,
            external_project_id=submission.external_project_id,
            external_snapshot_id=submission.external_snapshot_id,
            external_revision=submission.external_revision,
            snapshot=normalized.canonical,
        )
        report = normalized.normalization_report.model_dump(mode="json")
        return await self._persist_submission(
            owner_uid,
            canonical_submission,
            canonical_bytes=canonical_bytes,
            content_sha256=_sha256(canonical_bytes),
            source_document_bytes=source_document_bytes,
            source_document_sha256=_sha256(source_document_bytes),
            source_schema_version=normalized.normalization_report.source_schema_version,
            adapter_id=normalized.normalization_report.adapter_id,
            adapter_version=normalized.normalization_report.adapter_version,
            normalization_report=report,
        )

    async def _persist_submission(
        self,
        owner_uid: str,
        submission: ScheduleSnapshotSubmission,
        *,
        canonical_bytes: bytes,
        content_sha256: str,
        source_document_bytes: bytes | None = None,
        source_document_sha256: str | None = None,
        source_schema_version: str | None = None,
        adapter_id: str | None = None,
        adapter_version: str | None = None,
        normalization_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        proposed_snapshot_id = uuid.uuid4().hex
        execution_token = uuid.uuid4().hex
        execution_started_at = utc_now_naive()
        object_name = f"{owner_uid}/{proposed_snapshot_id}/snapshot.json"
        source_object_name = (
            f"{owner_uid}/{proposed_snapshot_id}/source-document.json" if source_document_bytes is not None else None
        )

        # Audit runs before external writes so a deterministic domain failure
        # cannot leave a reserved database row or an orphaned object.
        schedule = import_canonical_schedule(submission.snapshot)
        execution = audit_schedule(
            schedule,
            schedule_snapshot_id=proposed_snapshot_id,
            audit_run_id=uuid.uuid4().hex,
        )
        record, created = await self._repository.reserve(
            {
                "schedule_snapshot_id": proposed_snapshot_id,
                "owner_uid": owner_uid,
                "request_id": submission.request_id,
                "external_project_id": submission.external_project_id,
                "external_snapshot_id": submission.external_snapshot_id,
                "external_revision": submission.external_revision,
                "source_snapshot_id": submission.snapshot.snapshot_id,
                "schema_version": submission.snapshot.schema_version,
                "snapshot_content_sha256": content_sha256,
                "source_schema_version": source_schema_version,
                "adapter_id": adapter_id,
                "adapter_version": adapter_version,
                "source_document_sha256": source_document_sha256,
                "source_document_object": source_object_name,
                "normalization_report": normalization_report,
                "minio_bucket": SCHEDULE_BUCKET,
                "minio_object": object_name,
                "submission_status": "creating",
                "execution_token": execution_token,
                "execution_started_at": execution_started_at,
            }
        )
        if record.snapshot_content_sha256 != content_sha256:
            raise ScheduleConflictError
        record_source_sha256 = getattr(record, "source_document_sha256", None)
        if (record_source_sha256 is None) != (source_document_sha256 is None):
            raise ScheduleConflictError
        if source_document_sha256 is not None and record_source_sha256 != source_document_sha256:
            raise ScheduleConflictError
        if (
            record.external_project_id != submission.external_project_id
            or record.external_snapshot_id != submission.external_snapshot_id
            or record.external_revision != submission.external_revision
        ):
            raise ScheduleConflictError
        if not created:
            replay = await self._resolve_existing(
                owner_uid,
                submission.request_id,
                record,
                execution_token,
            )
            if replay is not None:
                return replay
            proposed_snapshot_id = record.schedule_snapshot_id
            object_name = record.minio_object
            source_object_name = getattr(record, "source_document_object", None)
            execution = audit_schedule(
                schedule,
                schedule_snapshot_id=proposed_snapshot_id,
                audit_run_id=uuid.uuid4().hex,
            )

        try:
            if source_document_bytes is not None and source_object_name is not None:
                await self._store.upload(source_object_name, source_document_bytes)
            await self._store.upload(object_name, canonical_bytes)
            await self._repository.finalize(proposed_snapshot_id, execution_token, execution)
        except asyncio.CancelledError:
            # A cancelled HTTP task must release its execution lease so the
            # same idempotency key can resume immediately instead of waiting.
            await asyncio.shield(
                self._repository.mark_failed(
                    proposed_snapshot_id,
                    execution_token,
                    "SCHEDULE_REQUEST_CANCELLED",
                )
            )
            raise
        except Exception as exc:
            await self._repository.mark_failed(proposed_snapshot_id, execution_token, "SCHEDULE_DEPENDENCY_FAILURE")
            raise ScheduleDependencyError from exc
        return _submission_response(
            content_sha256,
            execution,
            idempotent_replay=False,
            import_metadata=(
                {
                    "source_schema_version": source_schema_version,
                    "adapter_id": adapter_id,
                    "adapter_version": adapter_version,
                    "source_document_sha256": source_document_sha256,
                    "normalization_report": normalization_report,
                }
                if source_document_sha256 is not None
                else None
            ),
        )

    async def _resolve_existing(
        self,
        owner_uid: str,
        request_id: str,
        record: Any,
        execution_token: str,
    ) -> dict[str, Any] | None:
        if record.submission_status == "ready":
            return await self._ready_submission_response(owner_uid, record, idempotent_replay=True)
        if await self._claim_recoverable(record.schedule_snapshot_id, execution_token):
            return None

        # Wait through the execution lease so a concurrent identical request
        # can reuse the owner's result or atomically reclaim an expired lease.
        deadline = asyncio.get_running_loop().time() + SUBMISSION_LEASE_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(SUBMISSION_POLL_INTERVAL_SECONDS)
            current = await self._repository.get_submission(owner_uid, request_id)
            if current is None:
                raise ScheduleDependencyError
            if current.submission_status == "ready":
                return await self._ready_submission_response(owner_uid, current, idempotent_replay=True)
            if await self._claim_recoverable(current.schedule_snapshot_id, execution_token):
                return None
        raise ScheduleSubmissionInProgressError

    async def _claim_recoverable(self, schedule_snapshot_id: str, execution_token: str) -> bool:
        now = utc_now_naive()
        return await self._repository.claim_recoverable(
            schedule_snapshot_id,
            execution_token,
            execution_started_at=now,
            stale_before=now - timedelta(seconds=SUBMISSION_LEASE_SECONDS),
        )

    async def _ready_submission_response(
        self, owner_uid: str, record: Any, *, idempotent_replay: bool
    ) -> dict[str, Any]:
        audit = await self._repository.get_audit(owner_uid, record.schedule_snapshot_id)
        if audit is None:
            raise ScheduleDependencyError
        response = {
            "schedule_snapshot_id": record.schedule_snapshot_id,
            "audit_run_id": audit.audit_run_id,
            "idempotent_replay": idempotent_replay,
            "snapshot_content_sha256": record.snapshot_content_sha256,
            "capabilities": audit.capabilities,
            "dependency_date_checks": audit.dependency_date_checks,
            "issue_summary": audit.issue_summary,
        }
        if getattr(record, "source_document_sha256", None) is not None:
            response.update(_import_metadata(record))
        return response

    async def list_snapshots(self, owner_uid: str, limit: int, offset: int) -> dict[str, Any]:
        records = await self._repository.list_ready(owner_uid, limit, offset)
        return {"items": [_snapshot_summary(record) for record in records], "limit": limit, "offset": offset}

    async def get_snapshot(self, owner_uid: str, snapshot_id: str) -> dict[str, Any]:
        record = await self._repository.get_ready(owner_uid, snapshot_id)
        if record is None:
            raise ScheduleNotFoundError
        try:
            snapshot = json.loads((await self._store.download(record.minio_object)).decode("utf-8"))
        except Exception as exc:
            raise ScheduleDependencyError from exc
        return {**_snapshot_summary(record), "snapshot": snapshot}

    async def get_audit(self, owner_uid: str, snapshot_id: str) -> dict[str, Any]:
        audit = await self._repository.get_audit(owner_uid, snapshot_id)
        if audit is None:
            raise ScheduleNotFoundError
        return _audit_record(audit)

    async def get_review_context(self, owner_uid: str, snapshot_id: str) -> dict[str, Any]:
        record = await self._repository.get_ready(owner_uid, snapshot_id)
        if record is None:
            raise ScheduleNotFoundError
        audit = await self._repository.get_audit(owner_uid, snapshot_id)
        if audit is None:
            raise ScheduleDependencyError
        issues = await self._repository.list_issues(
            owner_uid,
            snapshot_id,
            category=None,
            severity=None,
            limit=REVIEW_CONTEXT_ISSUE_LIMIT + 1,
            offset=0,
        )
        if issues is None:
            raise ScheduleNotFoundError

        visible_issues = issues[:REVIEW_CONTEXT_ISSUE_LIMIT]
        issue_total = int((audit.issue_summary or {}).get("total", 0))
        return {
            "evidence_source": "YUXI_AUDIT",
            "review_scope": {
                "basis": ["persisted_audit", "persisted_issues", "persisted_capabilities"],
                "excluded": [
                    "raw_source_reinterpretation",
                    "unsupported_field_inference",
                    "unversioned_domain_judgment",
                    "date_cost_or_patch_calculation",
                ],
            },
            "priority_policy": {
                "first": "capability_reasons_blocking_the_user_goal",
                "then": ["blocker", "warning", "info"],
                "same_severity": "advisory_only_without_quantified_evidence",
            },
            "snapshot": {
                "schedule_snapshot_id": record.schedule_snapshot_id,
                "snapshot_content_sha256": record.snapshot_content_sha256,
                "external_project_id": record.external_project_id,
                "external_snapshot_id": record.external_snapshot_id,
                "external_revision": record.external_revision,
                "source_snapshot_id": record.source_snapshot_id,
                "schema_version": record.schema_version,
                "source_schema_version": getattr(record, "source_schema_version", None),
                "adapter_id": getattr(record, "adapter_id", None),
                "adapter_version": getattr(record, "adapter_version", None),
            },
            "audit": {
                "audit_run_id": audit.audit_run_id,
                "rule_set_version": audit.rule_set_version,
                "statistics": audit.statistics,
                "dependency_date_checks": audit.dependency_date_checks,
                "created_at": audit.created_at,
            },
            "capabilities": audit.capabilities,
            "issue_summary": audit.issue_summary,
            "issue_projection": {
                "limit": REVIEW_CONTEXT_ISSUE_LIMIT,
                "returned": len(visible_issues),
                "truncated": issue_total > len(visible_issues) or len(issues) > REVIEW_CONTEXT_ISSUE_LIMIT,
                "items": [_review_issue_record(issue) for issue in visible_issues],
            },
        }

    async def get_goal_optimization_context(self, owner_uid: str, snapshot_id: str) -> dict[str, Any]:
        record = await self._repository.get_ready(owner_uid, snapshot_id)
        if record is None:
            raise ScheduleNotFoundError
        audit = await self._repository.get_audit(owner_uid, snapshot_id)
        if audit is None:
            raise ScheduleDependencyError
        try:
            source = json.loads((await self._store.download(record.minio_object)).decode("utf-8"))
        except Exception as exc:
            raise ScheduleDependencyError from exc

        return {
            "evidence_source": "YUXI_SCHEDULE_SNAPSHOT",
            "snapshot": {
                "schedule_snapshot_id": record.schedule_snapshot_id,
                "snapshot_content_sha256": record.snapshot_content_sha256,
                "external_project_id": record.external_project_id,
                "external_revision": record.external_revision,
            },
            "cpm_capability": (audit.capabilities or {}).get("cpm_recalculation"),
            "supported_objectives": ["MINIMIZE_PROJECT_FINISH", "MEET_TARGET_FINISH"],
            "authorization_policy": {
                "allowed_change": "explicitly_authorized_automatic_activity_duration_only",
                "authorization_confirmation_required": True,
                "max_authorized_duration_options": 10,
                "hard_constraints": {
                    "preserve_dependencies": True,
                    "preserve_lag": True,
                    "preserve_calendars": True,
                    "preserve_milestones": True,
                    "preserve_task_modes": True,
                },
            },
            "eligible_tasks": [
                {
                    "task_id": task["task_id"],
                    "wbs": task["wbs"],
                    "name": task["name"],
                    "duration_minutes": task["duration_minutes"],
                }
                for task in source["tasks"]
                if task["task_type"] == "activity"
                and task.get("active", True)
                and task["scheduling_mode"] == "automatic"
                and task["duration_minutes"] > 0
            ],
            "submission": {
                "method": "POST",
                "url": f"/schedule?schedule_snapshot_id={record.schedule_snapshot_id}",
                "final_confirmation_surface": "schedule_goal_optimization_dialog",
            },
        }

    async def list_issues(
        self,
        owner_uid: str,
        snapshot_id: str,
        *,
        category: str | None,
        severity: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        issues = await self._repository.list_issues(
            owner_uid,
            snapshot_id,
            category=category,
            severity=severity,
            limit=limit,
            offset=offset,
        )
        if issues is None:
            raise ScheduleNotFoundError
        return {"items": [_issue_record(issue) for issue in issues], "limit": limit, "offset": offset}

    async def get_issue(self, owner_uid: str, issue_id: str) -> dict[str, Any]:
        issue = await self._repository.get_issue(owner_uid, issue_id)
        if issue is None:
            raise ScheduleNotFoundError
        return _issue_record(issue)

    async def get_issue_context(self, owner_uid: str, issue_id: str) -> dict[str, Any]:
        issue = await self.get_issue(owner_uid, issue_id)
        record = await self._repository.get_ready(owner_uid, issue["schedule_snapshot_id"])
        if record is None:
            raise ScheduleNotFoundError
        try:
            snapshot = json.loads((await self._store.download(record.minio_object)).decode("utf-8"))
        except Exception as exc:
            raise ScheduleDependencyError from exc

        tasks_by_id = {task["task_id"]: task for task in snapshot["tasks"]}
        dependencies_by_id = {item["dependency_id"]: item for item in snapshot["dependencies"]}
        involved_task_ids = {ref for ref in issue["object_refs"] if ref in tasks_by_id}
        involved_dependencies = [dependencies_by_id[ref] for ref in issue["object_refs"] if ref in dependencies_by_id]
        for dependency in involved_dependencies:
            involved_task_ids.update({dependency["predecessor_task_id"], dependency["successor_task_id"]})
        neighboring_dependencies = [
            dependency
            for dependency in snapshot["dependencies"]
            if dependency["predecessor_task_id"] in involved_task_ids
            or dependency["successor_task_id"] in involved_task_ids
        ]
        return {
            "issue": issue,
            "rule": _rule_context(issue["rule_id"]),
            "tasks": [_task_context(tasks_by_id[task_id]) for task_id in sorted(involved_task_ids)],
            "dependencies": sorted(
                (_dependency_context(item) for item in involved_dependencies),
                key=lambda item: item["dependency_id"],
            ),
            "direct_network": sorted(
                (_dependency_context(item) for item in neighboring_dependencies),
                key=lambda item: item["dependency_id"],
            ),
            "date_check_scope": {
                "checked_rule": "零 Lag 与冻结策略下的有符号 Lag FS/SS/FF/SF 来源日期关系",
                "non_zero_lag": "正负 Lag 均按统一项目日历工作分钟检查；未冻结策略保持跳过",
            },
        }

    async def get_dependency_workbench(self, owner_uid: str, issue_id: str) -> dict[str, Any]:
        issue, snapshot = await self._load_dependency_issue(owner_uid, issue_id)
        scope = _dependency_workbench_scope(issue, snapshot)
        decision = await self._repository.get_dependency_decision(owner_uid, issue_id)
        candidate = (
            await self._repository.get_candidate_by_dependency_decision(owner_uid, decision.decision_id)
            if decision and decision.status == "confirmed"
            else None
        )
        return {
            "issue": _issue_record(issue),
            "source_dependency": scope["source_dependencies"][0],
            "source_dependencies": scope["source_dependencies"],
            "predecessor": {
                "root_task_id": scope["predecessor_root_task_id"],
                "tasks": [
                    _workbench_task(task)
                    for task in snapshot["tasks"]
                    if task["task_id"] in scope["predecessor_ids"]
                ],
                "candidate_task_ids": scope["predecessor_candidates"],
            },
            "successor": {
                "root_task_id": scope["successor_root_task_id"],
                "tasks": [
                    _workbench_task(task)
                    for task in snapshot["tasks"]
                    if task["task_id"] in scope["successor_ids"]
                ],
                "candidate_task_ids": scope["successor_candidates"],
            },
            "direct_network": [
                item
                for item in snapshot["dependencies"]
                if item["dependency_id"] not in scope["source_dependency_ids"]
                and (
                    item["predecessor_task_id"] in scope["context_ids"]
                    or item["successor_task_id"] in scope["context_ids"]
                )
            ],
            "decision": _dependency_decision_record(decision) if decision else None,
            "candidate": _dependency_candidate_summary(candidate) if candidate else None,
        }

    async def save_dependency_decision(
        self, owner_uid: str, issue_id: str, draft: DependencyDecisionDraft
    ) -> dict[str, Any]:
        issue, snapshot = await self._load_dependency_issue(owner_uid, issue_id)
        self._validate_dependency_decision(issue, snapshot, draft, require_complete=False)
        try:
            record = await self._repository.save_dependency_decision(
                owner_uid,
                issue,
                draft.model_dump(mode="json"),
            )
        except ValueError as exc:
            raise ScheduleDecisionConflictError from exc
        return _dependency_decision_record(record)

    async def confirm_dependency_decision(self, owner_uid: str, issue_id: str) -> dict[str, Any]:
        issue, snapshot = await self._load_dependency_issue(owner_uid, issue_id)
        record = await self._repository.get_dependency_decision(owner_uid, issue_id)
        if record is None:
            raise ScheduleDecisionInvalidError
        draft = DependencyDecisionDraft.model_validate(
            {
                "resolution": record.resolution,
                "predecessor_task_ids": record.predecessor_task_ids,
                "successor_task_ids": record.successor_task_ids,
                "dependency_type": record.dependency_type,
                "lag_minutes": record.lag_minutes,
                "reason": record.reason,
            }
        )
        self._validate_dependency_decision(issue, snapshot, draft, require_complete=True)
        confirmed = await self._repository.confirm_dependency_decision(owner_uid, issue_id)
        if confirmed is None:
            raise ScheduleDecisionInvalidError
        return _dependency_decision_record(confirmed)

    async def _load_dependency_issue(self, owner_uid: str, issue_id: str) -> tuple[Any, dict[str, Any]]:
        issue = await self._repository.get_issue(owner_uid, issue_id)
        if issue is None or issue.rule_id not in DEPENDENCY_DECISION_RULE_IDS or not issue.object_refs:
            raise ScheduleNotFoundError
        record = await self._repository.get_ready(owner_uid, issue.schedule_snapshot_id)
        if record is None:
            raise ScheduleNotFoundError
        try:
            snapshot = json.loads((await self._store.download(record.minio_object)).decode("utf-8"))
        except Exception as exc:
            raise ScheduleDependencyError from exc
        dependency_ids = {item["dependency_id"] for item in snapshot["dependencies"]}
        if not set(issue.object_refs) <= dependency_ids:
            raise ScheduleDependencyError
        return issue, snapshot

    def _validate_dependency_decision(
        self,
        issue: Any,
        snapshot: dict[str, Any],
        draft: DependencyDecisionDraft,
        *,
        require_complete: bool,
    ) -> None:
        if draft.resolution != "replace_with_leaf_tasks":
            if require_complete and not draft.reason.strip():
                raise ScheduleDecisionInvalidError
            return
        scope = _dependency_workbench_scope(issue, snapshot)
        allowed_predecessors = set(scope["predecessor_candidates"])
        allowed_successors = set(scope["successor_candidates"])
        if (
            not set(draft.predecessor_task_ids) <= allowed_predecessors
            or not set(draft.successor_task_ids) <= allowed_successors
        ):
            raise ScheduleDecisionInvalidError
        if require_complete and (
            not draft.predecessor_task_ids
            or not draft.successor_task_ids
            or draft.dependency_type is None
            or draft.lag_minutes is None
            or not draft.reason.strip()
        ):
            raise ScheduleDecisionInvalidError


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _submission_response(
    content_sha256: str,
    execution,
    *,
    idempotent_replay: bool,
    import_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = execution.result
    response = {
        "schedule_snapshot_id": result.schedule_snapshot_id,
        "audit_run_id": result.audit_run_id,
        "idempotent_replay": idempotent_replay,
        "snapshot_content_sha256": content_sha256,
        "capabilities": {key: value.model_dump(mode="json") for key, value in result.capabilities.items()},
        "dependency_date_checks": result.dependency_date_checks.model_dump(mode="json"),
        "issue_summary": result.issue_summary.model_dump(mode="json"),
    }
    if import_metadata is not None:
        response.update(import_metadata)
        response["canonical_snapshot_sha256"] = content_sha256
    return response


def _import_metadata(record: Any) -> dict[str, Any]:
    return {
        "source_schema_version": record.source_schema_version,
        "adapter_id": record.adapter_id,
        "adapter_version": record.adapter_version,
        "source_document_sha256": record.source_document_sha256,
        "canonical_snapshot_sha256": record.snapshot_content_sha256,
        "normalization_report": record.normalization_report,
    }


def _snapshot_summary(record: Any) -> dict[str, Any]:
    summary = {
        "schedule_snapshot_id": record.schedule_snapshot_id,
        "external_project_id": record.external_project_id,
        "external_snapshot_id": record.external_snapshot_id,
        "external_revision": record.external_revision,
        "source_snapshot_id": record.source_snapshot_id,
        "schema_version": record.schema_version,
        "snapshot_content_sha256": record.snapshot_content_sha256,
        "created_at": record.created_at,
        "ready_at": record.ready_at,
    }
    if getattr(record, "source_document_sha256", None) is not None:
        summary.update(_import_metadata(record))
    return summary


def _audit_record(record: Any) -> dict[str, Any]:
    return {
        "audit_run_id": record.audit_run_id,
        "schedule_snapshot_id": record.schedule_snapshot_id,
        "rule_set_version": record.rule_set_version,
        "statistics": record.statistics,
        "capabilities": record.capabilities,
        "dependency_date_checks": record.dependency_date_checks,
        "issue_summary": record.issue_summary,
        "created_at": record.created_at,
    }


def _issue_record(record: Any) -> dict[str, Any]:
    return {
        "issue_id": record.issue_id,
        "issue_key": record.issue_key,
        "audit_run_id": record.audit_run_id,
        "schedule_snapshot_id": record.schedule_snapshot_id,
        "rule_id": record.rule_id,
        "rule_version": record.rule_version,
        "origin": "YUXI_AUDIT",
        "category": record.category,
        "severity": record.severity,
        "object_refs": record.object_refs,
        "evidence": record.evidence,
        "message": record.message,
        "recommendation": record.recommendation,
        "status": "open",
    }


def _review_issue_record(record: Any) -> dict[str, Any]:
    object_refs = list(record.object_refs or [])
    query = urlencode(
        {
            "schedule_snapshot_id": record.schedule_snapshot_id,
            "schedule_issue_id": record.issue_id,
        }
    )
    return {
        "issue_id": record.issue_id,
        "rule_id": record.rule_id,
        "category": record.category,
        "severity": record.severity,
        "message": record.message,
        "recommendation": record.recommendation,
        "object_ref_count": len(object_refs),
        "object_refs_preview": object_refs[:REVIEW_CONTEXT_OBJECT_REF_LIMIT],
        "evidence_locator": {
            "tool": "get_schedule_issue_context",
            "issue_id": record.issue_id,
            "url": f"/schedule?{query}",
        },
    }


def _task_context(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task["task_id"],
        "name": task["name"],
        "task_type": task["task_type"],
        "parent_task_id": task["parent_task_id"],
        "planned_start": task["planned_start"],
        "planned_finish": task["planned_finish"],
        "duration_minutes": task["duration_minutes"],
    }


def _dependency_context(dependency: dict[str, Any]) -> dict[str, Any]:
    return {
        "dependency_id": dependency["dependency_id"],
        "predecessor_task_id": dependency["predecessor_task_id"],
        "successor_task_id": dependency["successor_task_id"],
        "type": dependency["type"],
        "lag_minutes": dependency["lag_minutes"],
    }


def _rule_context(rule_id: str) -> dict[str, str]:
    descriptions = {
        "OPEN_START": "叶子任务没有前置关系。",
        "OPEN_FINISH": "叶子任务没有后续关系。",
        "SUMMARY_TASK_DEPENDENCY": "依赖涉及汇总任务。",
        "INACTIVE_TASK_DEPENDENCY": "依赖涉及 inactive 任务，必须显式选择活动叶子关系。",
        "ZERO_LAG_DATE_VIOLATION": "零 Lag 关系不满足来源日期锚点。",
        "LAG_DATE_VIOLATION": "非零 Lag 关系不满足统一项目日历工作分钟锚点。",
        "LAG_CALENDAR_POLICY_UNSPECIFIED": (
            "存在未检查的非零 Lag 关系（策略未冻结或日历口径不支持），未执行日期合规检查。"
        ),
    }
    return {
        "rule_id": rule_id,
        "description": descriptions.get(rule_id, "确定性 Schedule 审查规则。"),
        "authority": "YUXI_AUDIT",
    }


def _subtree_task_ids(tasks_by_id: dict[str, dict[str, Any]], root_task_id: str) -> set[str]:
    task_ids = {root_task_id}
    while True:
        children = {
            task_id
            for task_id, task in tasks_by_id.items()
            if task["parent_task_id"] in task_ids and task_id not in task_ids
        }
        if not children:
            return task_ids
        task_ids.update(children)


def _dependency_workbench_scope(issue: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    tasks_by_id = {task["task_id"]: task for task in snapshot["tasks"]}
    source_dependency_ids = set(issue.object_refs)
    source_dependencies = [
        dependency
        for dependency in snapshot["dependencies"]
        if dependency["dependency_id"] in source_dependency_ids
    ]
    if issue.rule_id == "SUMMARY_TASK_DEPENDENCY":
        dependency = source_dependencies[0]
        predecessor_roots = [dependency["predecessor_task_id"]]
        successor_roots = [dependency["successor_task_id"]]
    else:
        inactive_task_ids = {
            task_id
            for task_id in issue.evidence.get("inactive_task_ids", [])
            if task_id in tasks_by_id and not tasks_by_id[task_id].get("active", True)
        }
        predecessor_roots = sorted(
            {
                dependency["predecessor_task_id"]
                for dependency in source_dependencies
                if dependency["successor_task_id"] in inactive_task_ids
                and tasks_by_id[dependency["predecessor_task_id"]].get("active", True)
            }
        )
        successor_roots = sorted(
            {
                dependency["successor_task_id"]
                for dependency in source_dependencies
                if dependency["predecessor_task_id"] in inactive_task_ids
                and tasks_by_id[dependency["successor_task_id"]].get("active", True)
            }
        )
        if not predecessor_roots or not successor_roots:
            predecessor_roots = predecessor_roots or sorted(inactive_task_ids)
            successor_roots = successor_roots or sorted(inactive_task_ids)

    predecessor_ids = set().union(
        *(_subtree_task_ids(tasks_by_id, task_id) for task_id in predecessor_roots)
    )
    successor_ids = set().union(
        *(_subtree_task_ids(tasks_by_id, task_id) for task_id in successor_roots)
    )
    predecessor_candidates = sorted(
        {
            task_id
            for root_id in predecessor_roots
            for task_id in _boundary_leaf_ids(
                tasks_by_id,
                snapshot["dependencies"],
                _subtree_task_ids(tasks_by_id, root_id),
                boundary="exit",
            )
        }
    )
    successor_candidates = sorted(
        {
            task_id
            for root_id in successor_roots
            for task_id in _boundary_leaf_ids(
                tasks_by_id,
                snapshot["dependencies"],
                _subtree_task_ids(tasks_by_id, root_id),
                boundary="entry",
            )
        }
    )
    inactive_context_ids = {
        task_id
        for task_id in issue.evidence.get("inactive_task_ids", [])
        if task_id in tasks_by_id
    }
    return {
        "source_dependency_ids": source_dependency_ids,
        "source_dependencies": source_dependencies,
        "predecessor_root_task_id": predecessor_roots[0],
        "successor_root_task_id": successor_roots[0],
        "predecessor_ids": predecessor_ids,
        "successor_ids": successor_ids,
        "predecessor_candidates": predecessor_candidates,
        "successor_candidates": successor_candidates,
        "context_ids": predecessor_ids | successor_ids | inactive_context_ids,
    }


def _boundary_leaf_ids(
    tasks_by_id: dict[str, dict[str, Any]],
    dependencies: list[dict[str, Any]],
    task_ids: set[str],
    *,
    boundary: str,
) -> list[str]:
    leaf_ids = {
        task_id
        for task_id in task_ids
        if tasks_by_id[task_id]["task_type"] != "summary"
        and tasks_by_id[task_id].get("active", True)
    }
    if boundary == "entry":
        connected = {
            item["successor_task_id"]
            for item in dependencies
            if item["predecessor_task_id"] in task_ids and item["successor_task_id"] in task_ids
        }
    else:
        connected = {
            item["predecessor_task_id"]
            for item in dependencies
            if item["predecessor_task_id"] in task_ids and item["successor_task_id"] in task_ids
        }
    return sorted(leaf_ids - connected)


def _workbench_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task["task_id"],
        "parent_task_id": task["parent_task_id"],
        "name": task["name"],
        "wbs": task["wbs"],
        "outline_level": task["outline_level"],
        "task_type": task["task_type"],
        "active": task.get("active", True),
    }


def _dependency_decision_record(record: Any) -> dict[str, Any]:
    return {
        "decision_id": record.decision_id,
        "issue_id": record.issue_id,
        "schedule_snapshot_id": record.schedule_snapshot_id,
        "status": record.status,
        "resolution": record.resolution,
        "predecessor_task_ids": record.predecessor_task_ids,
        "successor_task_ids": record.successor_task_ids,
        "dependency_type": record.dependency_type,
        "lag_minutes": record.lag_minutes,
        "reason": record.reason,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "confirmed_at": record.confirmed_at,
    }


def _dependency_candidate_summary(record: Any) -> dict[str, Any]:
    return {
        "candidate_snapshot_id": record.candidate_snapshot_id,
        "candidate_status": record.candidate_status,
        "candidate_kind": record.candidate_kind,
        "created_at": record.created_at,
    }


async def list_schedule_capable_agents(user: Any, *, review_scope: str = "issue") -> list[dict[str, Any]]:
    """Project visible agents with the Schedule tools required by the requested entry."""
    from yuxi.agents.context import normalize_agent_context_config
    from yuxi.repositories.agent_repository import AgentRepository
    from yuxi.storage.postgres.manager import pg_manager

    required_tools = {"get_schedule_audit", "get_schedule_issue_context"}
    if review_scope == "snapshot":
        required_tools.add("get_schedule_review_context")
    elif review_scope == "goal":
        required_tools.update({"get_schedule_review_context", "get_schedule_goal_optimization_context"})
    async with pg_manager.get_async_session_context() as db:
        repository = AgentRepository(db)
        agents = await repository.list_visible(user=user)
        eligible: list[dict[str, Any]] = []
        for agent in agents:
            context = (agent.config_json or {}).get("context")
            normalized = await normalize_agent_context_config(context, db=db, user=user)
            if required_tools <= set(normalized.get("tools") or []):
                eligible.append({"agent_id": agent.slug, "name": agent.name, "description": agent.description})
        return eligible
