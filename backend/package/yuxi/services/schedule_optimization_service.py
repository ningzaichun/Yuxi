"""S2 dependency-normalization Candidate, review, and Delivery use cases."""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from collections import Counter
from typing import Any

from yuxi.repositories.schedule_repository import ScheduleRepository
from yuxi.schedule.audit.engine import audit_schedule
from yuxi.schedule.contracts.canonical_v2_2 import MAX_DEPENDENCIES, CanonicalScheduleV22
from yuxi.schedule.contracts.optimization import (
    CandidateDecisionRequest,
    DependencyOptimizationRequest,
    ForwardRecalculationRequest,
    GoalOptimizationRequest,
)
from yuxi.schedule.forward_engine import REVERSE_FLOAT_ENGINE_PROFILE_ID, calculate_minimal_forward_schedule
from yuxi.schedule.goal_optimizer import optimize_project_finish
from yuxi.schedule.importers.canonical_v2_2 import import_canonical_schedule_v2_2
from yuxi.schedule.storage import SCHEDULE_BUCKET, ScheduleSnapshotStore

CANDIDATE_SCHEMA_VERSION = "schedule_candidate_draft_v0"
CANDIDATE_KIND = "dependency_normalization"
FORWARD_CANDIDATE_KIND = "automatic_forward_recalculation"
GOAL_CANDIDATE_KIND = "goal_duration_optimization"
STRATEGY_ID = "replace-summary-dependency-v1"
FORWARD_STRATEGY_ID = "recalculate-automatic-downstream-v1"
GOAL_STRATEGY_ID = "authorized-duration-goal-v1"
SOURCE_TYPE_CODES = {"FF": 0, "FS": 1, "SF": 2, "SS": 3}


class ScheduleOptimizationNotFoundError(Exception):
    pass


class ScheduleOptimizationConflictError(Exception):
    pass


class ScheduleOptimizationInvalidError(Exception):
    pass


class ScheduleOptimizationInProgressError(Exception):
    pass


class ScheduleOptimizationDependencyError(Exception):
    pass


class ScheduleOptimizationService:
    def __init__(
        self,
        repository: ScheduleRepository | None = None,
        store: ScheduleSnapshotStore | None = None,
    ) -> None:
        self._repository = repository or ScheduleRepository()
        self._store = store or ScheduleSnapshotStore()

    async def create_candidate(
        self,
        owner_uid: str,
        snapshot_id: str,
        request: DependencyOptimizationRequest,
    ) -> tuple[dict[str, Any], bool]:
        base = await self._repository.get_ready(owner_uid, snapshot_id)
        decision = await self._repository.get_dependency_decision_by_id(owner_uid, request.dependency_decision_id)
        if base is None or decision is None:
            raise ScheduleOptimizationNotFoundError
        if (
            decision.status != "confirmed"
            or decision.resolution != "replace_with_leaf_tasks"
            or decision.schedule_snapshot_id != snapshot_id
        ):
            raise ScheduleOptimizationInvalidError
        if base.snapshot_content_sha256 != request.base_snapshot_content_sha256:
            raise ScheduleOptimizationConflictError
        latest = await self._repository.get_latest_ready_for_project(owner_uid, base.external_project_id)
        if latest is None or latest.schedule_snapshot_id != snapshot_id:
            raise ScheduleOptimizationConflictError

        issue = await self._repository.get_issue(owner_uid, decision.issue_id)
        if issue is None or issue.rule_id != "SUMMARY_TASK_DEPENDENCY":
            raise ScheduleOptimizationInvalidError

        optimization_id = uuid.uuid4().hex
        candidate_snapshot_id = uuid.uuid4().hex
        optimization, created = await self._repository.reserve_optimization(
            {
                "optimization_id": optimization_id,
                "candidate_snapshot_id": candidate_snapshot_id,
                "owner_uid": owner_uid,
                "request_id": request.request_id,
                "dependency_decision_id": decision.decision_id,
                "base_schedule_snapshot_id": snapshot_id,
                "base_snapshot_content_sha256": base.snapshot_content_sha256,
                "strategy_id": STRATEGY_ID,
                "status": "creating",
            }
        )
        if not created:
            if (
                optimization.request_id == request.request_id
                and optimization.dependency_decision_id != decision.decision_id
            ):
                raise ScheduleOptimizationConflictError
            if (
                optimization.base_schedule_snapshot_id != snapshot_id
                or optimization.base_snapshot_content_sha256 != request.base_snapshot_content_sha256
            ):
                raise ScheduleOptimizationConflictError
            if optimization.status == "creating":
                raise ScheduleOptimizationInProgressError
            if optimization.status == "failed":
                raise ScheduleOptimizationDependencyError
            candidate = await self._repository.get_candidate_by_optimization(owner_uid, optimization.optimization_id)
            if candidate is None:
                raise ScheduleOptimizationDependencyError
            return await self._candidate_response(owner_uid, candidate), False

        object_name = f"{owner_uid}/candidates/{candidate_snapshot_id}/snapshot.json"
        try:
            source = json.loads((await self._store.download(base.minio_object)).decode("utf-8"))
            candidate_schedule, requested_patch, effective_patch, added_dependency_ids = _apply_decision(
                source, issue, decision, candidate_snapshot_id
            )
            contract = CanonicalScheduleV22.model_validate(candidate_schedule)
            projection = audit_schedule(
                import_canonical_schedule_v2_2(contract),
                schedule_snapshot_id=candidate_snapshot_id,
                audit_run_id=uuid.uuid4().hex,
            )
            candidate_schedule["statistics"] = projection.result.statistics
            candidate_schedule["capabilities"] = {
                name: capability.model_dump(mode="json") for name, capability in projection.result.capabilities.items()
            }
            contract = CanonicalScheduleV22.model_validate(candidate_schedule)
            execution = audit_schedule(
                import_canonical_schedule_v2_2(contract),
                schedule_snapshot_id=candidate_snapshot_id,
                audit_run_id=uuid.uuid4().hex,
            )
            base_issues = await self._repository.list_all_issues(owner_uid, snapshot_id)
            if base_issues is None:
                raise ScheduleOptimizationNotFoundError
            comparison, candidate_status = _compare_audits(
                base_issues,
                execution.findings,
                target_dependency_id=issue.object_refs[0],
                added_dependency_ids=added_dependency_ids,
            )
            candidate_document = {
                "candidate_schema_version": CANDIDATE_SCHEMA_VERSION,
                "candidate_snapshot_id": candidate_snapshot_id,
                "base_schedule_snapshot_id": snapshot_id,
                "base_snapshot_content_sha256": base.snapshot_content_sha256,
                "candidate_schedule": candidate_schedule,
                "engine_result": None,
                "requested_patch": requested_patch,
                "effective_patch": effective_patch,
                "engine_profile_id": None,
                "engine_version": None,
                "candidate_kind": CANDIDATE_KIND,
                "candidate_status": candidate_status,
                "comparison": comparison,
                "candidate_audit": _candidate_audit(execution),
            }
            await self._store.upload(
                object_name,
                json.dumps(
                    candidate_document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
            )
            candidate = await self._repository.finalize_optimization(
                optimization_id,
                requested_patch,
                {
                    "candidate_snapshot_id": candidate_snapshot_id,
                    "owner_uid": owner_uid,
                    "optimization_id": optimization_id,
                    "dependency_decision_id": decision.decision_id,
                    "base_schedule_snapshot_id": snapshot_id,
                    "candidate_schema_version": CANDIDATE_SCHEMA_VERSION,
                    "candidate_kind": CANDIDATE_KIND,
                    "candidate_status": candidate_status,
                    "minio_bucket": SCHEDULE_BUCKET,
                    "minio_object": object_name,
                    "effective_patch": effective_patch,
                    "comparison": comparison,
                    "candidate_audit": candidate_document["candidate_audit"],
                },
            )
        except (ScheduleOptimizationNotFoundError, ScheduleOptimizationInvalidError):
            await self._repository.mark_optimization_failed(optimization_id, "SCHEDULE_OPTIMIZATION_INVALID")
            raise
        except Exception as exc:
            await self._repository.mark_optimization_failed(optimization_id, "SCHEDULE_OPTIMIZATION_FAILURE")
            raise ScheduleOptimizationDependencyError from exc
        return await self._candidate_response(owner_uid, candidate), True

    async def create_forward_candidate(
        self,
        owner_uid: str,
        snapshot_id: str,
        request: ForwardRecalculationRequest,
    ) -> tuple[dict[str, Any], bool]:
        base = await self._repository.get_ready(owner_uid, snapshot_id)
        if base is None:
            raise ScheduleOptimizationNotFoundError
        if base.snapshot_content_sha256 != request.base_snapshot_content_sha256:
            raise ScheduleOptimizationConflictError
        latest = await self._repository.get_latest_ready_for_project(owner_uid, base.external_project_id)
        if latest is None or latest.schedule_snapshot_id != snapshot_id:
            raise ScheduleOptimizationConflictError

        try:
            source = json.loads((await self._store.download(base.minio_object)).decode("utf-8"))
            contract = CanonicalScheduleV22.model_validate(source)
            engine_result = calculate_minimal_forward_schedule(
                contract,
                locked_task_ids=set(request.locked_task_ids),
                engine_profile_id=REVERSE_FLOAT_ENGINE_PROFILE_ID,
            )
        except Exception as exc:
            raise ScheduleOptimizationDependencyError from exc
        if engine_result["status"] == "blocked":
            return {
                "candidate_status": "blocked",
                "candidate_kind": FORWARD_CANDIDATE_KIND,
                "base_schedule_snapshot_id": snapshot_id,
                "base_snapshot_content_sha256": base.snapshot_content_sha256,
                "engine_result": engine_result,
            }, False

        optimization_id = uuid.uuid4().hex
        candidate_snapshot_id = uuid.uuid4().hex
        optimization, created = await self._repository.reserve_optimization(
            {
                "optimization_id": optimization_id,
                "candidate_snapshot_id": candidate_snapshot_id,
                "owner_uid": owner_uid,
                "request_id": request.request_id,
                "dependency_decision_id": None,
                "base_schedule_snapshot_id": snapshot_id,
                "base_snapshot_content_sha256": base.snapshot_content_sha256,
                "strategy_id": FORWARD_STRATEGY_ID,
                "status": "creating",
            }
        )
        if not created:
            if (
                optimization.strategy_id != FORWARD_STRATEGY_ID
                or optimization.base_schedule_snapshot_id != snapshot_id
                or optimization.base_snapshot_content_sha256 != request.base_snapshot_content_sha256
            ):
                raise ScheduleOptimizationConflictError
            if optimization.status == "creating":
                raise ScheduleOptimizationInProgressError
            if optimization.status == "failed":
                raise ScheduleOptimizationDependencyError
            candidate = await self._repository.get_candidate_by_optimization(owner_uid, optimization.optimization_id)
            if candidate is None:
                raise ScheduleOptimizationDependencyError
            return await self._candidate_response(owner_uid, candidate), False

        object_name = f"{owner_uid}/candidates/{candidate_snapshot_id}/snapshot.json"
        requested_patch = {
            "strategy_id": FORWARD_STRATEGY_ID,
            "locked_task_ids": request.locked_task_ids,
            "operations": [
                {
                    "operation_id": uuid.uuid4().hex,
                    "operation": "recalculate_automatic_downstream",
                    "scope": "all_supported_tasks",
                    "source_fields_modified": False,
                }
            ],
        }
        effective_patch = {
            "task_date_changes": [
                {
                    "task_id": item["task_id"],
                    "start": {"before": item["source_start"], "after": item["early_start"]},
                    "finish": {"before": item["source_finish"], "after": item["early_finish"]},
                    "change_origin": "calculated",
                }
                for item in engine_result["task_dates"]
                if item["start_changed"] or item["finish_changed"]
            ]
        }
        candidate_document = {
            "candidate_schema_version": CANDIDATE_SCHEMA_VERSION,
            "candidate_snapshot_id": candidate_snapshot_id,
            "base_schedule_snapshot_id": snapshot_id,
            "base_snapshot_content_sha256": base.snapshot_content_sha256,
            "candidate_schedule": copy.deepcopy(source),
            "engine_result": engine_result,
            "requested_patch": requested_patch,
            "effective_patch": effective_patch,
            "engine_profile_id": engine_result["engine_profile_id"],
            "engine_version": engine_result["engine_version"],
            "candidate_kind": FORWARD_CANDIDATE_KIND,
            "candidate_status": "valid" if engine_result["status"] == "calculated" else "invalid",
            "comparison": {
                "affected_task_count": engine_result["affected_task_count"],
                "finish_before": engine_result["finish_before"],
                "finish_after": engine_result["finish_after"],
            },
            "candidate_audit": {},
        }
        try:
            await self._store.upload(
                object_name,
                json.dumps(
                    candidate_document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
            )
            candidate = await self._repository.finalize_optimization(
                optimization_id,
                requested_patch,
                {
                    "candidate_snapshot_id": candidate_snapshot_id,
                    "owner_uid": owner_uid,
                    "optimization_id": optimization_id,
                    "dependency_decision_id": None,
                    "base_schedule_snapshot_id": snapshot_id,
                    "candidate_schema_version": CANDIDATE_SCHEMA_VERSION,
                    "candidate_kind": FORWARD_CANDIDATE_KIND,
                    "candidate_status": "valid" if engine_result["status"] == "calculated" else "invalid",
                    "minio_bucket": SCHEDULE_BUCKET,
                    "minio_object": object_name,
                    "effective_patch": effective_patch,
                    "comparison": candidate_document["comparison"],
                    "candidate_audit": {},
                },
            )
        except Exception as exc:
            await self._repository.mark_optimization_failed(optimization_id, "SCHEDULE_FORWARD_RECALCULATION_FAILURE")
            raise ScheduleOptimizationDependencyError from exc
        return await self._candidate_response(owner_uid, candidate), True

    async def create_goal_candidate(
        self,
        owner_uid: str,
        snapshot_id: str,
        request: GoalOptimizationRequest,
    ) -> tuple[dict[str, Any], bool]:
        base = await self._repository.get_ready(owner_uid, snapshot_id)
        if base is None:
            raise ScheduleOptimizationNotFoundError
        if base.snapshot_content_sha256 != request.base_snapshot_content_sha256:
            raise ScheduleOptimizationConflictError
        latest = await self._repository.get_latest_ready_for_project(owner_uid, base.external_project_id)
        if latest is None or latest.schedule_snapshot_id != snapshot_id:
            raise ScheduleOptimizationConflictError

        try:
            source = json.loads((await self._store.download(base.minio_object)).decode("utf-8"))
            contract = CanonicalScheduleV22.model_validate(source)
            engine_result = optimize_project_finish(contract, request)
        except Exception as exc:
            raise ScheduleOptimizationDependencyError from exc
        if engine_result["status"] != "calculated":
            return {
                "candidate_status": engine_result["status"],
                "candidate_kind": GOAL_CANDIDATE_KIND,
                "base_schedule_snapshot_id": snapshot_id,
                "base_snapshot_content_sha256": base.snapshot_content_sha256,
                "engine_result": engine_result,
            }, False
        base_issues = await self._repository.list_all_issues(owner_uid, snapshot_id)
        if base_issues is None:
            raise ScheduleOptimizationNotFoundError

        optimization_id = uuid.uuid4().hex
        candidate_snapshot_id = uuid.uuid4().hex
        try:
            selected = engine_result["selected_strategy"]
            duration_changes = selected["duration_changes"]
            requested_patch = {
                "strategy_id": GOAL_STRATEGY_ID,
                "objective": request.objective,
                "target_finish": request.target_finish.isoformat() if request.target_finish else None,
                "authorization": {
                    "confirmed": request.authorization_confirmed,
                    "authorized_task_ids": [item.task_id for item in request.authorized_duration_options],
                    "authorized_duration_options": [
                        item.model_dump(mode="json") for item in request.authorized_duration_options
                    ],
                    "locked_task_ids": request.locked_task_ids,
                },
                "hard_constraints": {
                    "preserve_dependencies": True,
                    "preserve_lag": True,
                    "preserve_calendars": True,
                    "preserve_milestones": True,
                    "preserve_task_modes": True,
                },
                "operations": [
                    {
                        "operation_id": uuid.uuid4().hex,
                        "operation": "set_task_duration",
                        **change,
                        "change_origin": "explicit_authorization",
                    }
                    for change in duration_changes
                ],
            }
            effective_patch = {
                "duration_changes": duration_changes,
                "change_origin": "explicit_authorization",
            }
            candidate_schedule = copy.deepcopy(source)
            candidate_tasks = {task["task_id"]: task for task in candidate_schedule["tasks"]}
            for change in duration_changes:
                candidate_tasks[change["task_id"]]["duration_minutes"] = change["after_duration_minutes"]
            candidate_contract = CanonicalScheduleV22.model_validate(candidate_schedule)
            audit = audit_schedule(
                import_canonical_schedule_v2_2(candidate_contract),
                schedule_snapshot_id=candidate_snapshot_id,
                audit_run_id=uuid.uuid4().hex,
            )
            base_blockers = {_blocker_signature(item) for item in base_issues if item.severity == "blocker"}
            new_blockers = sorted(
                _blocker_signature(item)
                for item in audit.findings
                if item.severity == "blocker" and _blocker_signature(item) not in base_blockers
            )
            candidate_status = "invalid" if new_blockers else "valid"
            comparison = {
                "objective": request.objective,
                "target_finish": engine_result["target_finish"],
                "target_met": engine_result["target_met"],
                "finish_before": engine_result["finish_before"],
                "finish_after": engine_result["finish_after"],
                "evaluated_strategy_count": engine_result["evaluated_strategy_count"],
                "affected_task_count": len(duration_changes),
                "total_reduction_minutes": selected["total_reduction_minutes"],
                "new_blockers": new_blockers,
            }
            candidate_document = {
                "candidate_schema_version": CANDIDATE_SCHEMA_VERSION,
                "candidate_snapshot_id": candidate_snapshot_id,
                "base_schedule_snapshot_id": snapshot_id,
                "base_snapshot_content_sha256": base.snapshot_content_sha256,
                "candidate_schedule": candidate_schedule,
                "engine_result": engine_result,
                "requested_patch": requested_patch,
                "effective_patch": effective_patch,
                "engine_profile_id": engine_result["optimizer_profile_id"],
                "engine_version": engine_result["optimizer_version"],
                "candidate_kind": GOAL_CANDIDATE_KIND,
                "candidate_status": candidate_status,
                "comparison": comparison,
                "candidate_audit": _candidate_audit(audit),
            }
        except Exception as exc:
            raise ScheduleOptimizationDependencyError from exc

        object_name = f"{owner_uid}/candidates/{candidate_snapshot_id}/snapshot.json"
        optimization, created = await self._repository.reserve_optimization(
            {
                "optimization_id": optimization_id,
                "candidate_snapshot_id": candidate_snapshot_id,
                "owner_uid": owner_uid,
                "request_id": request.request_id,
                "dependency_decision_id": None,
                "base_schedule_snapshot_id": snapshot_id,
                "base_snapshot_content_sha256": base.snapshot_content_sha256,
                "strategy_id": GOAL_STRATEGY_ID,
                "status": "creating",
            }
        )
        if not created:
            if (
                optimization.strategy_id != GOAL_STRATEGY_ID
                or optimization.base_schedule_snapshot_id != snapshot_id
                or optimization.base_snapshot_content_sha256 != request.base_snapshot_content_sha256
            ):
                raise ScheduleOptimizationConflictError
            if optimization.status == "creating":
                raise ScheduleOptimizationInProgressError
            if optimization.status == "failed":
                raise ScheduleOptimizationDependencyError
            candidate = await self._repository.get_candidate_by_optimization(owner_uid, optimization.optimization_id)
            if candidate is None:
                raise ScheduleOptimizationDependencyError
            response = await self._candidate_response(owner_uid, candidate)
            requested_patch = response["requested_patch"]
            expected_options = [item.model_dump(mode="json") for item in request.authorized_duration_options]
            if (
                requested_patch.get("objective") != request.objective
                or requested_patch.get("target_finish")
                != (request.target_finish.isoformat() if request.target_finish else None)
                or requested_patch.get("authorization", {}).get("authorized_duration_options") != expected_options
                or requested_patch.get("authorization", {}).get("locked_task_ids") != request.locked_task_ids
            ):
                raise ScheduleOptimizationConflictError
            return response, False

        try:
            await self._store.upload(
                object_name,
                json.dumps(
                    candidate_document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
            )
            candidate = await self._repository.finalize_optimization(
                optimization_id,
                requested_patch,
                {
                    "candidate_snapshot_id": candidate_snapshot_id,
                    "owner_uid": owner_uid,
                    "optimization_id": optimization_id,
                    "dependency_decision_id": None,
                    "base_schedule_snapshot_id": snapshot_id,
                    "candidate_schema_version": CANDIDATE_SCHEMA_VERSION,
                    "candidate_kind": GOAL_CANDIDATE_KIND,
                    "candidate_status": candidate_status,
                    "minio_bucket": SCHEDULE_BUCKET,
                    "minio_object": object_name,
                    "effective_patch": effective_patch,
                    "comparison": comparison,
                    "candidate_audit": candidate_document["candidate_audit"],
                },
            )
        except Exception as exc:
            await self._repository.mark_optimization_failed(optimization_id, "SCHEDULE_GOAL_OPTIMIZATION_FAILURE")
            raise ScheduleOptimizationDependencyError from exc
        return await self._candidate_response(owner_uid, candidate), True

    async def get_optimization(self, owner_uid: str, optimization_id: str) -> dict[str, Any]:
        record = await self._repository.get_optimization(owner_uid, optimization_id)
        if record is None:
            raise ScheduleOptimizationNotFoundError
        return {
            "optimization_id": record.optimization_id,
            "candidate_snapshot_id": record.candidate_snapshot_id,
            "base_schedule_snapshot_id": record.base_schedule_snapshot_id,
            "dependency_decision_id": record.dependency_decision_id,
            "strategy_id": record.strategy_id,
            "status": record.status,
            "failure_code": record.failure_code,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }

    async def get_candidate(self, owner_uid: str, candidate_snapshot_id: str) -> dict[str, Any]:
        candidate = await self._repository.get_candidate(owner_uid, candidate_snapshot_id)
        if candidate is None:
            raise ScheduleOptimizationNotFoundError
        return await self._candidate_response(owner_uid, candidate)

    async def record_decision(
        self,
        owner_uid: str,
        candidate_snapshot_id: str,
        request: CandidateDecisionRequest,
    ) -> tuple[dict[str, Any], bool]:
        candidate = await self._repository.get_candidate(owner_uid, candidate_snapshot_id)
        if candidate is None:
            raise ScheduleOptimizationNotFoundError
        record, created = await self._repository.save_candidate_decision(
            owner_uid,
            candidate_snapshot_id,
            request.model_dump(mode="json"),
        )
        if not created and (record.attitude != request.attitude or record.comment != request.comment):
            raise ScheduleOptimizationConflictError
        return _candidate_decision_record(record), created

    async def get_delivery(self, owner_uid: str, candidate_snapshot_id: str) -> dict[str, Any]:
        candidate = await self.get_candidate(owner_uid, candidate_snapshot_id)
        attitude = candidate["user_attitude"]
        reasons = []
        if candidate["candidate_status"] != "valid":
            reasons.append("CANDIDATE_NOT_VALID")
        if candidate["base_snapshot_status"] != "current":
            reasons.append("BASE_SNAPSHOT_OUTDATED")
        return {
            "delivery_schema_version": "schedule_delivery_draft_v0",
            "candidate_snapshot_id": candidate_snapshot_id,
            "base_schedule_snapshot_id": candidate["base_schedule_snapshot_id"],
            "base_snapshot_content_sha256": candidate["base_snapshot_content_sha256"],
            "candidate_schema_version": candidate["candidate_schema_version"],
            "candidate_kind": candidate["candidate_kind"],
            "candidate_status": candidate["candidate_status"],
            "user_attitude": attitude,
            "requested_patch": candidate["requested_patch"],
            "effective_patch": candidate["effective_patch"],
            "simulation_result": candidate["candidate_snapshot"]["engine_result"],
            "candidate_snapshot": candidate["candidate_snapshot"],
            "base_snapshot_status": candidate["base_snapshot_status"],
            "latest_schedule_snapshot_id": candidate["latest_schedule_snapshot_id"],
            "application_allowed": not reasons,
            "application_blocking_reasons": reasons,
        }

    async def get_acceptance_evidence(
        self,
        owner_uid: str,
        candidate_snapshot_id: str,
    ) -> dict[str, Any]:
        candidate = await self.get_candidate(owner_uid, candidate_snapshot_id)
        if candidate["candidate_kind"] != CANDIDATE_KIND:
            raise ScheduleOptimizationInvalidError
        base = await self._repository.get_ready(owner_uid, candidate["base_schedule_snapshot_id"])
        if base is None:
            raise ScheduleOptimizationNotFoundError
        latest = await self._repository.get_latest_ready_for_project(owner_uid, base.external_project_id)
        if latest is None:
            raise ScheduleOptimizationNotFoundError

        base_source = json.loads((await self._store.download(base.minio_object)).decode("utf-8"))
        base_hash = _canonical_content_sha256(base_source)
        new_snapshot_available = latest.schedule_snapshot_id != base.schedule_snapshot_id
        latest_source = None
        base_issues = await self._repository.list_all_issues(owner_uid, base.schedule_snapshot_id)
        if base_issues is None:
            raise ScheduleOptimizationNotFoundError
        latest_issues = []
        latest_hash = None
        if new_snapshot_available:
            latest_source = json.loads((await self._store.download(latest.minio_object)).decode("utf-8"))
            latest_hash = _canonical_content_sha256(latest_source)
            latest_issues = await self._repository.list_all_issues(owner_uid, latest.schedule_snapshot_id)
            if latest_issues is None:
                raise ScheduleOptimizationNotFoundError

        checks = _acceptance_checks(
            candidate,
            base_hash=base_hash,
            base=base,
            latest=latest,
            latest_hash=latest_hash,
            latest_source=latest_source,
            base_issues=base_issues,
            latest_issues=latest_issues,
            new_snapshot_available=new_snapshot_available,
        )
        if not new_snapshot_available:
            status = "pending"
        else:
            status = "passed" if all(check["passed"] for check in checks) else "failed"
        return {
            "acceptance_evidence_schema_version": "schedule_acceptance_evidence_v0",
            "candidate_snapshot_id": candidate_snapshot_id,
            "base_schedule_snapshot_id": candidate["base_schedule_snapshot_id"],
            "latest_schedule_snapshot_id": latest.schedule_snapshot_id,
            "status": status,
            "checks": checks,
        }

    async def _candidate_response(self, owner_uid: str, candidate: Any) -> dict[str, Any]:
        try:
            document = json.loads((await self._store.download(candidate.minio_object)).decode("utf-8"))
        except Exception as exc:
            raise ScheduleOptimizationDependencyError from exc
        base = await self._repository.get_ready(owner_uid, candidate.base_schedule_snapshot_id)
        if base is None:
            raise ScheduleOptimizationNotFoundError
        latest = await self._repository.get_latest_ready_for_project(owner_uid, base.external_project_id)
        decision = await self._repository.get_latest_candidate_decision(owner_uid, candidate.candidate_snapshot_id)
        return {
            "candidate_snapshot_id": candidate.candidate_snapshot_id,
            "optimization_id": candidate.optimization_id,
            "dependency_decision_id": candidate.dependency_decision_id,
            "candidate_schema_version": candidate.candidate_schema_version,
            "candidate_kind": candidate.candidate_kind,
            "candidate_status": candidate.candidate_status,
            "base_schedule_snapshot_id": candidate.base_schedule_snapshot_id,
            "base_snapshot_content_sha256": document["base_snapshot_content_sha256"],
            "base_snapshot_status": (
                "current"
                if latest and latest.schedule_snapshot_id == candidate.base_schedule_snapshot_id
                else "outdated"
            ),
            "latest_schedule_snapshot_id": latest.schedule_snapshot_id if latest else None,
            "requested_patch": document["requested_patch"],
            "effective_patch": candidate.effective_patch,
            "comparison": candidate.comparison,
            "candidate_audit": candidate.candidate_audit,
            "user_attitude": decision.attitude if decision else "not_reviewed",
            "latest_decision": _candidate_decision_record(decision) if decision else None,
            "candidate_snapshot": document,
            "created_at": candidate.created_at,
        }


def _apply_decision(
    source: dict[str, Any],
    issue: Any,
    decision: Any,
    candidate_snapshot_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], set[str]]:
    target_dependency_id = issue.object_refs[0]
    source_dependency = next(
        (item for item in source["dependencies"] if item["dependency_id"] == target_dependency_id),
        None,
    )
    if source_dependency is None:
        raise ScheduleOptimizationInvalidError

    candidate = copy.deepcopy(source)
    added_count = len(decision.predecessor_task_ids) * len(decision.successor_task_ids)
    if len(candidate["dependencies"]) - 1 + added_count > MAX_DEPENDENCIES:
        raise ScheduleOptimizationInvalidError
    candidate["dependencies"] = [
        item for item in candidate["dependencies"] if item["dependency_id"] != target_dependency_id
    ]
    added = []
    for index, (predecessor_id, successor_id) in enumerate(
        (
            (predecessor_id, successor_id)
            for predecessor_id in decision.predecessor_task_ids
            for successor_id in decision.successor_task_ids
        ),
        start=1,
    ):
        added.append(
            {
                "dependency_id": f"candidate:{candidate_snapshot_id}:{index}",
                "predecessor_task_id": predecessor_id,
                "successor_task_id": successor_id,
                "type": decision.dependency_type,
                "source_type_code": SOURCE_TYPE_CODES[decision.dependency_type],
                "lag_minutes": decision.lag_minutes,
                "lag_calendar_policy": "UNSPECIFIED_REQUIRES_ENGINE_PROFILE",
            }
        )
    candidate["dependencies"].extend(added)
    operations = [
        {
            "operation_id": f"remove:{target_dependency_id}",
            "operation": "remove_dependency",
            "dependency_id": target_dependency_id,
            "expected_before": source_dependency,
            "reason_issue_id": issue.issue_id,
            "dependency_decision_id": decision.decision_id,
        },
        *[
            {
                "operation_id": f"add:{item['dependency_id']}",
                "operation": "add_dependency",
                "dependency": item,
                "expected_before": None,
                "reason_issue_id": issue.issue_id,
                "dependency_decision_id": decision.decision_id,
            }
            for item in added
        ],
    ]
    requested_patch = {
        "strategy_id": STRATEGY_ID,
        "base_schedule_snapshot_id": decision.schedule_snapshot_id,
        "target_issue_ids": [issue.issue_id],
        "dependency_decision_id": decision.decision_id,
        "reason": decision.reason,
        "operations": operations,
    }
    effective_patch = {
        "operations": operations,
        "removed_dependencies": [source_dependency],
        "added_dependencies": added,
        "change_origin": "requested",
    }
    return candidate, requested_patch, effective_patch, {item["dependency_id"] for item in added}


def _compare_audits(
    base_issues: list[Any],
    candidate_findings: tuple[Any, ...],
    *,
    target_dependency_id: str,
    added_dependency_ids: set[str],
) -> tuple[dict[str, Any], str]:
    before_counts = Counter(item.severity for item in base_issues)
    after_counts = Counter(item.severity for item in candidate_findings)
    before_rules = Counter(item.rule_id for item in base_issues)
    after_rules = Counter(item.rule_id for item in candidate_findings)
    target_resolved = not any(
        item.rule_id == "SUMMARY_TASK_DEPENDENCY" and target_dependency_id in item.object_refs
        for item in candidate_findings
    )
    duplicate_added = any(
        item.rule_id == "DUPLICATE_RELATION" and added_dependency_ids.intersection(item.object_refs)
        for item in candidate_findings
    )
    base_blockers = {_blocker_signature(item) for item in base_issues if item.severity == "blocker"}
    candidate_blockers = {_blocker_signature(item) for item in candidate_findings if item.severity == "blocker"}
    new_blockers = sorted(candidate_blockers - base_blockers)
    status = "valid" if target_resolved and not duplicate_added and not new_blockers else "invalid"
    return (
        {
            "target_issue_resolved": target_resolved,
            "duplicate_relation_added": duplicate_added,
            "new_blockers": new_blockers,
            "issue_summary_before": dict(before_counts),
            "issue_summary_after": dict(after_counts),
            "rule_count_changes": {
                rule_id: {"before": before_rules[rule_id], "after": after_rules[rule_id]}
                for rule_id in sorted(set(before_rules) | set(after_rules))
                if before_rules[rule_id] != after_rules[rule_id]
            },
        },
        status,
    )


def _blocker_signature(issue: Any) -> str:
    if issue.rule_id == "LAG_CALENDAR_POLICY_UNSPECIFIED":
        return issue.rule_id
    evidence = json.dumps(issue.evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{issue.rule_id}:{evidence}"


def _canonical_content_sha256(source: dict[str, Any]) -> str:
    contract = CanonicalScheduleV22.model_validate(source)
    payload = json.dumps(
        contract.model_dump(mode="json", exclude_none=False),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _acceptance_checks(
    candidate: dict[str, Any],
    *,
    base_hash: str,
    base: Any,
    latest: Any,
    latest_hash: str | None,
    latest_source: dict[str, Any] | None,
    base_issues: list[Any],
    latest_issues: list[Any],
    new_snapshot_available: bool,
) -> list[dict[str, Any]]:
    effective_patch = candidate["effective_patch"]
    latest_dependencies = (
        {dependency["dependency_id"]: dependency for dependency in latest_source["dependencies"]}
        if latest_source
        else {}
    )
    removed_ids = {dependency["dependency_id"] for dependency in effective_patch["removed_dependencies"]}
    patch_applied = (
        new_snapshot_available
        and all(dependency_id not in latest_dependencies for dependency_id in removed_ids)
        and all(
            latest_dependencies.get(dependency["dependency_id"]) == dependency
            for dependency in effective_patch["added_dependencies"]
        )
    )
    target_issue_resolved = new_snapshot_available and not any(
        issue.rule_id == "SUMMARY_TASK_DEPENDENCY" and removed_ids.intersection(issue.object_refs)
        for issue in latest_issues
    )
    no_statistics_mismatch = new_snapshot_available and not any(
        issue.rule_id in {"STATISTICS_MISMATCH", "SOURCE_CAPABILITY_MISMATCH"} for issue in latest_issues
    )
    base_blockers = {_blocker_signature(issue) for issue in base_issues if issue.severity == "blocker"}
    latest_blockers = {_blocker_signature(issue) for issue in latest_issues if issue.severity == "blocker"}
    no_new_blockers = new_snapshot_available and latest_blockers <= base_blockers
    values = [
        ("CANDIDATE_VALID", candidate["candidate_status"] == "valid"),
        ("CANDIDATE_ACCEPTED", candidate["user_attitude"] == "accepted"),
        ("ENGINE_NOT_USED", candidate["candidate_snapshot"]["engine_result"] is None),
        ("BASE_SOURCE_HASH_UNCHANGED", base_hash == candidate["base_snapshot_content_sha256"]),
        ("BASE_OBJECT_HASH_MATCHES_RECORD", base_hash == base.snapshot_content_sha256),
        ("NEW_SNAPSHOT_AVAILABLE", new_snapshot_available),
        (
            "LATEST_OBJECT_HASH_MATCHES_RECORD",
            new_snapshot_available and latest_hash == latest.snapshot_content_sha256,
        ),
        ("SUBMISSION_REQUEST_IS_NEW", new_snapshot_available and latest.request_id != base.request_id),
        (
            "EXTERNAL_SNAPSHOT_IS_NEW",
            new_snapshot_available and latest.external_snapshot_id != base.external_snapshot_id,
        ),
        (
            "EXTERNAL_REVISION_IS_NEW",
            new_snapshot_available and latest.external_revision != base.external_revision,
        ),
        (
            "SOURCE_SNAPSHOT_IS_NEW",
            new_snapshot_available and latest.source_snapshot_id != base.source_snapshot_id,
        ),
        ("PATCH_APPLIED_EXACTLY", patch_applied),
        ("TARGET_ISSUE_RESOLVED", target_issue_resolved),
        ("NO_STATISTICS_OR_CAPABILITY_MISMATCH", no_statistics_mismatch),
        ("NO_NEW_BLOCKER", no_new_blockers),
        ("BASE_CANDIDATE_OUTDATED", candidate["base_snapshot_status"] == "outdated"),
    ]
    return [{"code": code, "passed": passed} for code, passed in values]


def _candidate_decision_record(record: Any) -> dict[str, Any]:
    return {
        "candidate_decision_id": record.candidate_decision_id,
        "candidate_snapshot_id": record.candidate_snapshot_id,
        "request_id": record.request_id,
        "attitude": record.attitude,
        "comment": record.comment,
        "created_at": record.created_at,
    }


def _candidate_audit(execution: Any) -> dict[str, Any]:
    return {
        "audit_run_id": execution.result.audit_run_id,
        "rule_set_version": execution.result.rule_set_version,
        "statistics": execution.result.statistics,
        "capabilities": {key: value.model_dump(mode="json") for key, value in execution.result.capabilities.items()},
        "dependency_date_checks": execution.result.dependency_date_checks.model_dump(mode="json"),
        "issue_summary": execution.result.issue_summary.model_dump(mode="json"),
        "issues": [
            {
                "issue_key": finding.issue_key,
                "rule_id": finding.rule_id,
                "category": finding.category,
                "severity": finding.severity,
                "object_refs": list(finding.object_refs),
                "evidence": finding.evidence,
                "message": finding.message,
                "recommendation": finding.recommendation,
            }
            for finding in execution.findings
        ],
    }
