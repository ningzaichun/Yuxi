from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.verify_schedule_golden_case import _build_canonical_source
from yuxi.schedule.contracts.optimization import GoalOptimizationRequest
from yuxi.schedule.goal_optimizer import optimize_project_finish

CASE_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "schedule"
    / "microsoft_project_s4_reverse_float_critical_golden_case.json"
)


def _source():
    case = json.loads(CASE_PATH.read_text(encoding="utf-8"))
    source = _build_canonical_source(case)
    expected_dates = {item["task_id"]: item for item in case["expected"]["task_dates"]}
    for task in source.tasks:
        task.planned_start = datetime.fromisoformat(expected_dates[task.task_id]["early_start"])
        task.planned_finish = datetime.fromisoformat(expected_dates[task.task_id]["early_finish"])
    source.project.planned_finish = max(task.planned_finish for task in source.tasks)
    return source


def _request(**updates) -> GoalOptimizationRequest:
    values = {
        "request_id": "goal-optimization-1",
        "base_snapshot_content_sha256": "sha256:" + "a" * 64,
        "objective": "MEET_TARGET_FINISH",
        "target_finish": "2026-09-03T17:00:00+08:00",
        "authorized_duration_options": [{"task_id": "synthetic-task:long-work", "duration_minutes": 480}],
        "locked_task_ids": ["synthetic-task:kickoff"],
        "authorization_confirmed": True,
    }
    values.update(updates)
    return GoalOptimizationRequest.model_validate(values)


def test_goal_optimizer_meets_target_with_least_authorized_change() -> None:
    result = optimize_project_finish(_source(), _request())

    assert result["status"] == "calculated"
    assert result["evaluated_strategy_count"] == 2
    assert result["finish_before"] == "2026-09-04T17:00:00+08:00"
    assert result["finish_after"] == "2026-09-03T17:00:00+08:00"
    assert result["target_met"] is True
    assert result["selected_strategy"]["duration_changes"] == [
        {
            "task_id": "synthetic-task:long-work",
            "before_duration_minutes": 960,
            "after_duration_minutes": 480,
        }
    ]


def test_goal_optimizer_combines_multiple_authorized_tasks_when_target_requires_both() -> None:
    result = optimize_project_finish(
        _source(),
        _request(
            target_finish="2026-09-03T15:00:00+08:00",
            authorized_duration_options=[
                {"task_id": "synthetic-task:long-work", "duration_minutes": 240},
                {"task_id": "synthetic-task:short-review", "duration_minutes": 120},
            ],
        ),
    )

    assert result["status"] == "calculated"
    assert result["evaluated_strategy_count"] == 4
    assert result["finish_after"] == "2026-09-03T15:00:00+08:00"
    assert result["selected_strategy"]["duration_changes"] == [
        {
            "task_id": "synthetic-task:long-work",
            "before_duration_minutes": 960,
            "after_duration_minutes": 240,
        },
        {
            "task_id": "synthetic-task:short-review",
            "before_duration_minutes": 240,
            "after_duration_minutes": 120,
        },
    ]


def test_goal_optimizer_returns_blocked_when_authorized_options_cannot_meet_target() -> None:
    result = optimize_project_finish(
        _source(),
        _request(target_finish="2026-09-02T17:00:00+08:00"),
    )

    assert result["status"] == "blocked"
    assert result["support"]["blockers"][0]["code"] == "TARGET_FINISH_UNACHIEVABLE"
    assert result["best_achievable_strategy"]["engine_result"]["finish_after"] == ("2026-09-03T17:00:00+08:00")
    assert result["selected_strategy"] is None


def test_goal_optimizer_rejects_unknown_locked_or_non_shorter_authorization() -> None:
    result = optimize_project_finish(
        _source(),
        _request(
            authorized_duration_options=[
                {"task_id": "synthetic-task:long-work", "duration_minutes": 960},
                {"task_id": "missing-task", "duration_minutes": 120},
            ],
        ),
    )

    assert result["status"] == "blocked"
    assert {item["code"] for item in result["support"]["blockers"]} == {
        "AUTHORIZED_DURATION_NOT_SHORTER",
        "AUTHORIZED_TASK_UNKNOWN",
    }
    assert result["evaluated_strategy_count"] == 0


def test_goal_optimizer_rejects_inactive_authorized_task() -> None:
    source = _source()
    task = next(
        item for item in source.tasks if item.task_id == "synthetic-task:long-work"
    )
    task.active = False

    result = optimize_project_finish(source, _request())

    assert result["status"] == "blocked"
    assert result["support"]["blockers"] == [
        {
            "code": "AUTHORIZED_TASK_INACTIVE",
            "object_refs": ["synthetic-task:long-work"],
            "message": "inactive 任务不参与工期优化",
        }
    ]


def test_goal_contract_requires_matching_target_and_unique_authorizations() -> None:
    with pytest.raises(ValidationError, match="requires target_finish"):
        _request(target_finish=None)

    with pytest.raises(ValidationError, match="does not accept target_finish"):
        _request(objective="MINIMIZE_PROJECT_FINISH")

    with pytest.raises(ValidationError, match="duplicate task_id"):
        _request(
            authorized_duration_options=[
                {"task_id": "synthetic-task:long-work", "duration_minutes": 480},
                {"task_id": "synthetic-task:long-work", "duration_minutes": 240},
            ]
        )

    with pytest.raises(ValidationError, match="cannot overlap locked_task_ids"):
        _request(locked_task_ids=["synthetic-task:long-work"])
