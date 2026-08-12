"""Authenticated HTTP adapter for Schedule audit use cases."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import ValidationError

from server.utils.auth_middleware import get_required_user
from yuxi.schedule.contracts.envelope import ScheduleSnapshotSubmission
from yuxi.schedule.contracts.errors import validation_error_to_schedule_detail
from yuxi.services.schedule_audit_service import (
    ScheduleAuditService,
    ScheduleConflictError,
    ScheduleDependencyError,
    ScheduleNotFoundError,
    ScheduleSubmissionInProgressError,
    list_schedule_capable_agents,
)
from yuxi.storage.postgres.models_business import User

MAX_BODY_BYTES = 10 * 1024 * 1024
schedule_router = APIRouter(prefix="/schedule", tags=["schedule"])
schedule_service = ScheduleAuditService()


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
