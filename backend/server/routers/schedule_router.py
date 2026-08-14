"""Authenticated HTTP adapter for Schedule audit use cases."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import ValidationError

from server.utils.auth_middleware import get_required_user
from yuxi.schedule.contracts.envelope import ScheduleSnapshotSubmission
from yuxi.schedule.contracts.dependency_decision import DependencyDecisionDraft
from yuxi.schedule.contracts.errors import (
    validation_error_to_schedule_detail,
    validation_error_to_schedule_import_detail,
)
from yuxi.schedule.contracts.import_v1 import ScheduleImportEnvelope
from yuxi.schedule.contracts.optimization import (
    CandidateDecisionRequest,
    DependencyOptimizationRequest,
    ForwardRecalculationRequest,
)
from yuxi.services.schedule_audit_service import (
    ScheduleAuditService,
    ScheduleConflictError,
    ScheduleDependencyError,
    ScheduleDecisionConflictError,
    ScheduleDecisionInvalidError,
    ScheduleNotFoundError,
    ScheduleSubmissionInProgressError,
    list_schedule_capable_agents,
)
from yuxi.services.schedule_optimization_service import (
    ScheduleOptimizationConflictError,
    ScheduleOptimizationDependencyError,
    ScheduleOptimizationInProgressError,
    ScheduleOptimizationInvalidError,
    ScheduleOptimizationNotFoundError,
    ScheduleOptimizationService,
)
from yuxi.schedule.importers.registry import UnsupportedScheduleImportVersionError
from yuxi.storage.postgres.models_business import User

MAX_BODY_BYTES = 10 * 1024 * 1024
schedule_router = APIRouter(prefix="/schedule", tags=["schedule"])
schedule_service = ScheduleAuditService()
optimization_service = ScheduleOptimizationService()


@schedule_router.post("/snapshots")
async def submit_snapshot(
    request: Request,
    response: Response,
    current_user: User = Depends(get_required_user),
):
    raw_body = await request.body()
    if len(raw_body) > MAX_BODY_BYTES:
        raise _error(413, "SCHEDULE_BODY_TOO_LARGE", "排期请求正文超过 10 MiB 限制")
    try:
        submission = ScheduleSnapshotSubmission.model_validate_json(raw_body)
    except (ValidationError, json.JSONDecodeError) as exc:
        if isinstance(exc, ValidationError):
            detail = validation_error_to_schedule_detail(exc).model_dump(mode="json")
            raise HTTPException(status_code=422, detail=detail) from exc
        raise _error(422, "SCHEDULE_CONTRACT_INVALID", "排期数据不是有效 JSON") from exc
    try:
        result = await schedule_service.submit(str(current_user.uid), submission)
    except ScheduleConflictError as exc:
        raise _error(409, "SCHEDULE_IDEMPOTENCY_CONFLICT", "相同 request_id 已用于不同的排期内容") from exc
    except ScheduleSubmissionInProgressError as exc:
        raise _error(409, "SCHEDULE_SUBMISSION_IN_PROGRESS", "相同请求正在处理中，请稍后重试") from exc
    except ScheduleDependencyError as exc:
        raise _error(500, "SCHEDULE_DEPENDENCY_FAILURE", "排期快照保存失败，可使用同一请求重试") from exc
    response.status_code = 200 if result["idempotent_replay"] else 201
    return result


@schedule_router.post("/imports")
async def submit_import(
    request: Request,
    response: Response,
    current_user: User = Depends(get_required_user),
):
    raw_body = await request.body()
    if len(raw_body) > MAX_BODY_BYTES:
        raise _error(413, "SCHEDULE_BODY_TOO_LARGE", "排期请求正文超过 10 MiB 限制")
    try:
        submission = ScheduleImportEnvelope.model_validate_json(raw_body)
    except (ValidationError, json.JSONDecodeError) as exc:
        if isinstance(exc, ValidationError):
            detail = validation_error_to_schedule_import_detail(exc).model_dump(mode="json")
            raise HTTPException(status_code=422, detail=detail) from exc
        raise _error(422, "SCHEDULE_IMPORT_CONTRACT_INVALID", "排期来源数据不是有效 JSON") from exc
    try:
        result = await schedule_service.submit_import(str(current_user.uid), submission)
    except ValidationError as exc:
        detail = validation_error_to_schedule_import_detail(exc, path_prefix=("document",)).model_dump(mode="json")
        raise HTTPException(status_code=422, detail=detail) from exc
    except UnsupportedScheduleImportVersionError as exc:
        raise _error(422, "SCHEDULE_IMPORT_VERSION_UNSUPPORTED", "不支持该排期来源格式版本") from exc
    except ValueError as exc:
        raise _error(422, "SCHEDULE_IMPORT_SEMANTICS_UNSUPPORTED", "排期来源包含当前适配器不支持的语义") from exc
    except ScheduleConflictError as exc:
        raise _error(409, "SCHEDULE_IDEMPOTENCY_CONFLICT", "相同 request_id 已用于不同的排期来源内容") from exc
    except ScheduleSubmissionInProgressError as exc:
        raise _error(409, "SCHEDULE_SUBMISSION_IN_PROGRESS", "相同请求正在处理中，请稍后重试") from exc
    except ScheduleDependencyError as exc:
        raise _error(500, "SCHEDULE_DEPENDENCY_FAILURE", "排期导入保存失败，可使用同一请求重试") from exc
    response.status_code = 200 if result["idempotent_replay"] else 201
    return result


@schedule_router.get("/snapshots")
async def list_snapshots(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_required_user),
):
    return await schedule_service.list_snapshots(str(current_user.uid), limit, offset)


@schedule_router.get("/snapshots/{snapshot_id}")
async def get_snapshot(snapshot_id: str, current_user: User = Depends(get_required_user)):
    return await _read(schedule_service.get_snapshot(str(current_user.uid), snapshot_id))


@schedule_router.get("/snapshots/{snapshot_id}/audit")
async def get_audit(snapshot_id: str, current_user: User = Depends(get_required_user)):
    return await _read(schedule_service.get_audit(str(current_user.uid), snapshot_id))


@schedule_router.get("/snapshots/{snapshot_id}/issues")
async def list_issues(
    snapshot_id: str,
    category: str | None = None,
    severity: str | None = Query(None, pattern="^(blocker|warning|info)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_required_user),
):
    return await _read(
        schedule_service.list_issues(
            str(current_user.uid),
            snapshot_id,
            category=category,
            severity=severity,
            limit=limit,
            offset=offset,
        )
    )


@schedule_router.get("/issues/{issue_id}")
async def get_issue(issue_id: str, current_user: User = Depends(get_required_user)):
    return await _read(schedule_service.get_issue_context(str(current_user.uid), issue_id))


@schedule_router.get("/issues/{issue_id}/dependency-workbench")
async def get_dependency_workbench(issue_id: str, current_user: User = Depends(get_required_user)):
    return await _read(schedule_service.get_dependency_workbench(str(current_user.uid), issue_id))


@schedule_router.put("/issues/{issue_id}/dependency-decision")
async def save_dependency_decision(
    issue_id: str,
    draft: DependencyDecisionDraft,
    current_user: User = Depends(get_required_user),
):
    try:
        return await schedule_service.save_dependency_decision(str(current_user.uid), issue_id, draft)
    except ScheduleNotFoundError as exc:
        raise _error(404, "SCHEDULE_NOT_FOUND", "排期资源不存在") from exc
    except ScheduleDecisionConflictError as exc:
        raise _error(409, "SCHEDULE_DECISION_CONFIRMED", "已确认的依赖决策不可修改") from exc
    except ScheduleDecisionInvalidError as exc:
        raise _error(422, "SCHEDULE_DECISION_INVALID", "依赖决策不符合当前工作台范围") from exc


@schedule_router.post("/issues/{issue_id}/dependency-decision/confirm")
async def confirm_dependency_decision(issue_id: str, current_user: User = Depends(get_required_user)):
    try:
        return await schedule_service.confirm_dependency_decision(str(current_user.uid), issue_id)
    except ScheduleNotFoundError as exc:
        raise _error(404, "SCHEDULE_NOT_FOUND", "排期资源不存在") from exc
    except ScheduleDecisionInvalidError as exc:
        raise _error(422, "SCHEDULE_DECISION_INVALID", "请补全替代关系和业务理由后再确认") from exc


@schedule_router.post("/snapshots/{snapshot_id}/optimizations")
async def create_optimization(
    snapshot_id: str,
    request: DependencyOptimizationRequest,
    response: Response,
    current_user: User = Depends(get_required_user),
):
    try:
        candidate, created = await optimization_service.create_candidate(str(current_user.uid), snapshot_id, request)
    except ScheduleOptimizationNotFoundError as exc:
        raise _error(404, "SCHEDULE_NOT_FOUND", "排期资源不存在") from exc
    except ScheduleOptimizationConflictError as exc:
        raise _error(409, "SCHEDULE_OPTIMIZATION_CONFLICT", "基础版本、Hash 或幂等请求不一致") from exc
    except ScheduleOptimizationInProgressError as exc:
        raise _error(409, "SCHEDULE_OPTIMIZATION_IN_PROGRESS", "相同优化请求正在处理中") from exc
    except ScheduleOptimizationInvalidError as exc:
        raise _error(422, "SCHEDULE_OPTIMIZATION_INVALID", "仅支持已确认的汇总依赖替代关系") from exc
    except ScheduleOptimizationDependencyError as exc:
        raise _error(500, "SCHEDULE_OPTIMIZATION_FAILURE", "候选方案生成失败") from exc
    response.status_code = 201 if created else 200
    return candidate


@schedule_router.post("/snapshots/{snapshot_id}/recalculate-automatic-downstream")
async def create_forward_recalculation(
    snapshot_id: str,
    request: ForwardRecalculationRequest,
    response: Response,
    current_user: User = Depends(get_required_user),
):
    try:
        candidate, created = await optimization_service.create_forward_candidate(
            str(current_user.uid), snapshot_id, request
        )
    except ScheduleOptimizationNotFoundError as exc:
        raise _error(404, "SCHEDULE_NOT_FOUND", "排期资源不存在") from exc
    except ScheduleOptimizationConflictError as exc:
        raise _error(409, "SCHEDULE_OPTIMIZATION_CONFLICT", "基础版本、Hash 或幂等请求不一致") from exc
    except ScheduleOptimizationInProgressError as exc:
        raise _error(409, "SCHEDULE_OPTIMIZATION_IN_PROGRESS", "相同重算请求正在处理中") from exc
    except ScheduleOptimizationDependencyError as exc:
        raise _error(500, "SCHEDULE_OPTIMIZATION_FAILURE", "正向重算候选生成失败") from exc
    response.status_code = 201 if created else 200
    return candidate


@schedule_router.get("/optimizations/{optimization_id}")
async def get_optimization(optimization_id: str, current_user: User = Depends(get_required_user)):
    try:
        return await optimization_service.get_optimization(str(current_user.uid), optimization_id)
    except ScheduleOptimizationNotFoundError as exc:
        raise _error(404, "SCHEDULE_NOT_FOUND", "排期资源不存在") from exc


@schedule_router.get("/candidates/{candidate_snapshot_id}")
async def get_candidate(candidate_snapshot_id: str, current_user: User = Depends(get_required_user)):
    try:
        return await optimization_service.get_candidate(str(current_user.uid), candidate_snapshot_id)
    except ScheduleOptimizationNotFoundError as exc:
        raise _error(404, "SCHEDULE_NOT_FOUND", "排期资源不存在") from exc
    except ScheduleOptimizationDependencyError as exc:
        raise _error(500, "SCHEDULE_OPTIMIZATION_FAILURE", "候选方案读取失败") from exc


@schedule_router.post("/candidates/{candidate_snapshot_id}/decisions")
async def record_candidate_decision(
    candidate_snapshot_id: str,
    request: CandidateDecisionRequest,
    response: Response,
    current_user: User = Depends(get_required_user),
):
    try:
        decision, created = await optimization_service.record_decision(
            str(current_user.uid), candidate_snapshot_id, request
        )
    except ScheduleOptimizationNotFoundError as exc:
        raise _error(404, "SCHEDULE_NOT_FOUND", "排期资源不存在") from exc
    except ScheduleOptimizationConflictError as exc:
        raise _error(409, "SCHEDULE_OPTIMIZATION_CONFLICT", "相同 request_id 已用于不同的候选态度") from exc
    response.status_code = 201 if created else 200
    return decision


@schedule_router.get("/candidates/{candidate_snapshot_id}/delivery")
async def get_candidate_delivery(
    candidate_snapshot_id: str,
    current_user: User = Depends(get_required_user),
):
    try:
        return await optimization_service.get_delivery(str(current_user.uid), candidate_snapshot_id)
    except ScheduleOptimizationNotFoundError as exc:
        raise _error(404, "SCHEDULE_NOT_FOUND", "排期资源不存在") from exc
    except ScheduleOptimizationInvalidError as exc:
        raise _error(409, "SCHEDULE_DELIVERY_NOT_READY", "候选尚未接受或不满足交付条件") from exc
    except ScheduleOptimizationDependencyError as exc:
        raise _error(500, "SCHEDULE_OPTIMIZATION_FAILURE", "交付包读取失败") from exc


@schedule_router.get("/candidates/{candidate_snapshot_id}/acceptance-evidence")
async def get_candidate_acceptance_evidence(
    candidate_snapshot_id: str,
    current_user: User = Depends(get_required_user),
):
    try:
        return await optimization_service.get_acceptance_evidence(str(current_user.uid), candidate_snapshot_id)
    except ScheduleOptimizationNotFoundError as exc:
        raise _error(404, "SCHEDULE_NOT_FOUND", "排期资源不存在") from exc
    except ScheduleOptimizationInvalidError as exc:
        raise _error(409, "SCHEDULE_ACCEPTANCE_EVIDENCE_NOT_APPLICABLE", "该 Candidate 不使用回流验收证据") from exc
    except ScheduleOptimizationDependencyError as exc:
        raise _error(500, "SCHEDULE_OPTIMIZATION_FAILURE", "验收证据读取失败") from exc


@schedule_router.get("/agents")
async def list_capable_agents(current_user: User = Depends(get_required_user)):
    return {"items": await list_schedule_capable_agents(current_user)}


async def _read(awaitable):
    try:
        return await awaitable
    except ScheduleNotFoundError as exc:
        raise _error(404, "SCHEDULE_NOT_FOUND", "排期资源不存在") from exc
    except ScheduleDependencyError as exc:
        raise _error(500, "SCHEDULE_DEPENDENCY_FAILURE", "排期数据读取失败") from exc


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, "errors": []})
