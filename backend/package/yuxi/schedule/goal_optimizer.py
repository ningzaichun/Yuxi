"""Deterministic, authorization-bounded project-finish optimization."""

from __future__ import annotations

from datetime import datetime
from itertools import combinations
from typing import Any

from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22
from yuxi.schedule.contracts.optimization import GoalOptimizationRequest
from yuxi.schedule.forward_engine import (
    REVERSE_FLOAT_ENGINE_PROFILE_ID,
    calculate_minimal_forward_schedule,
)

GOAL_OPTIMIZER_PROFILE_ID = "yuxi-authorized-duration-goal-optimizer-v1"
GOAL_OPTIMIZER_VERSION = "1.0.0"


def optimize_project_finish(
    source: CanonicalScheduleV22,
    request: GoalOptimizationRequest,
) -> dict[str, Any]:
    """Evaluate only explicitly authorized duration alternatives."""
    tasks = {task.task_id: task for task in source.tasks}
    blockers = []
    locked_task_ids = set(request.locked_task_ids)
    for option in request.authorized_duration_options:
        task = tasks.get(option.task_id)
        if task is None:
            blockers.append(_blocker("AUTHORIZED_TASK_UNKNOWN", option.task_id, "授权项引用了未知任务"))
        elif task.task_type != "activity":
            blockers.append(_blocker("AUTHORIZED_TASK_NOT_ACTIVITY", option.task_id, "只能授权活动任务工期"))
        elif task.scheduling_mode != "automatic":
            blockers.append(_blocker("AUTHORIZED_TASK_NOT_AUTOMATIC", option.task_id, "只能优化自动任务工期"))
        elif option.task_id in locked_task_ids:
            blockers.append(_blocker("AUTHORIZED_TASK_LOCKED", option.task_id, "锁定任务不能同时授权修改"))
        elif option.duration_minutes >= task.duration_minutes:
            blockers.append(
                _blocker(
                    "AUTHORIZED_DURATION_NOT_SHORTER",
                    option.task_id,
                    "授权工期必须短于来源工期且大于零",
                )
            )
    if blockers:
        return _blocked_result(request, blockers)

    baseline = calculate_minimal_forward_schedule(
        source,
        locked_task_ids=locked_task_ids,
        engine_profile_id=REVERSE_FLOAT_ENGINE_PROFILE_ID,
    )
    if baseline["status"] != "calculated":
        return {
            "status": baseline["status"],
            "optimizer_profile_id": GOAL_OPTIMIZER_PROFILE_ID,
            "optimizer_version": GOAL_OPTIMIZER_VERSION,
            "objective": request.objective,
            "target_finish": _isoformat(request.target_finish),
            "support": baseline["support"],
            "conflicts": baseline.get("conflicts", []),
            "evaluated_strategy_count": 0,
            "selected_strategy": None,
            "baseline_result": baseline,
        }

    options = sorted(request.authorized_duration_options, key=lambda item: item.task_id)
    evaluated = [_strategy_result(source, request, baseline, ())]
    for size in range(1, len(options) + 1):
        for selected in combinations(options, size):
            evaluated.append(_strategy_result(source, request, baseline, selected))

    if request.objective == "MEET_TARGET_FINISH":
        target_finish = request.target_finish
        feasible = [item for item in evaluated if _finish(item) <= target_finish]
        if not feasible:
            best = min(evaluated, key=_minimize_rank)
            return {
                "status": "blocked",
                "optimizer_profile_id": GOAL_OPTIMIZER_PROFILE_ID,
                "optimizer_version": GOAL_OPTIMIZER_VERSION,
                "objective": request.objective,
                "target_finish": target_finish.isoformat(),
                "support": {
                    "supported": False,
                    "blockers": [
                        {
                            "code": "TARGET_FINISH_UNACHIEVABLE",
                            "object_refs": [item.task_id for item in options],
                            "message": "所有已授权组合均无法满足目标完成日期",
                        }
                    ],
                },
                "evaluated_strategy_count": len(evaluated),
                "selected_strategy": None,
                "best_achievable_strategy": best,
                "baseline_result": baseline,
            }
        selected_strategy = min(feasible, key=lambda item: _target_rank(item, target_finish))
    else:
        selected_strategy = min(evaluated, key=_minimize_rank)

    return {
        "status": "calculated",
        "optimizer_profile_id": GOAL_OPTIMIZER_PROFILE_ID,
        "optimizer_version": GOAL_OPTIMIZER_VERSION,
        "objective": request.objective,
        "target_finish": _isoformat(request.target_finish),
        "support": {"supported": True, "blockers": []},
        "evaluated_strategy_count": len(evaluated),
        "selected_strategy": selected_strategy,
        "baseline_result": baseline,
        "finish_before": baseline["finish_after"],
        "finish_after": selected_strategy["engine_result"]["finish_after"],
        "target_met": (
            _finish(selected_strategy) <= request.target_finish if request.target_finish is not None else None
        ),
    }


def _strategy_result(
    source: CanonicalScheduleV22,
    request: GoalOptimizationRequest,
    baseline: dict[str, Any],
    selected: tuple[Any, ...],
) -> dict[str, Any]:
    if not selected:
        return {
            "strategy_id": "baseline",
            "duration_changes": [],
            "total_reduction_minutes": 0,
            "engine_result": baseline,
        }

    payload = source.model_dump(mode="json", exclude_none=False)
    tasks = {task["task_id"]: task for task in payload["tasks"]}
    duration_changes = []
    for option in selected:
        task = tasks[option.task_id]
        duration_changes.append(
            {
                "task_id": option.task_id,
                "before_duration_minutes": task["duration_minutes"],
                "after_duration_minutes": option.duration_minutes,
            }
        )
        task["duration_minutes"] = option.duration_minutes
    variant = CanonicalScheduleV22.model_validate(payload)
    result = calculate_minimal_forward_schedule(
        variant,
        locked_task_ids=set(request.locked_task_ids),
        engine_profile_id=REVERSE_FLOAT_ENGINE_PROFILE_ID,
    )
    return {
        "strategy_id": "duration:"
        + ",".join(f"{item['task_id']}={item['after_duration_minutes']}" for item in duration_changes),
        "duration_changes": duration_changes,
        "total_reduction_minutes": sum(
            item["before_duration_minutes"] - item["after_duration_minutes"] for item in duration_changes
        ),
        "engine_result": result,
    }


def _finish(strategy: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(strategy["engine_result"]["finish_after"])


def _minimize_rank(strategy: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _finish(strategy),
        strategy["total_reduction_minutes"],
        len(strategy["duration_changes"]),
        strategy["strategy_id"],
    )


def _target_rank(strategy: dict[str, Any], target_finish: datetime) -> tuple[Any, ...]:
    return (
        strategy["total_reduction_minutes"],
        len(strategy["duration_changes"]),
        target_finish - _finish(strategy),
        strategy["strategy_id"],
    )


def _blocker(code: str, object_ref: str, message: str) -> dict[str, Any]:
    return {"code": code, "object_refs": [object_ref], "message": message}


def _blocked_result(request: GoalOptimizationRequest, blockers: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "blocked",
        "optimizer_profile_id": GOAL_OPTIMIZER_PROFILE_ID,
        "optimizer_version": GOAL_OPTIMIZER_VERSION,
        "objective": request.objective,
        "target_finish": _isoformat(request.target_finish),
        "support": {"supported": False, "blockers": blockers},
        "evaluated_strategy_count": 0,
        "selected_strategy": None,
    }


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
