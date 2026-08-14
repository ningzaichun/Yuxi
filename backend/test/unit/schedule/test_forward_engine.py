from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.generate_synthetic_schedule_case import build_synthetic_schedule_case
from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22
from scripts.verify_schedule_golden_case import _build_canonical_source, evaluate_golden_gate
from yuxi.schedule.forward_engine import (
    ENGINE_PROFILE_ID,
    SUMMARY_ROLLUP_ENGINE_PROFILE_ID,
    UnifiedWorkCalendar,
    calculate_minimal_forward_schedule,
)

POSITIVE_LAG_CASE_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "schedule" / "microsoft_project_s4_positive_lag_golden_case.json"
)
RELATION_TYPES_CASE_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "schedule" / "microsoft_project_s4_relation_types_golden_case.json"
)
CONSTRAINTS_CASE_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "schedule" / "microsoft_project_s4_constraints_golden_case.json"
)
MANUAL_LOCKED_CASE_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "schedule" / "microsoft_project_s4_manual_locked_golden_case.json"
)
SUMMARY_ROLLUP_CASE_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "schedule" / "microsoft_project_s4_summary_rollup_golden_case.json"
)


def _supported_source() -> dict:
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
        },
        {
            "dependency_id": "dependency:build-a-build-b",
            "predecessor_task_id": "synthetic-task:build-a",
            "successor_task_id": "synthetic-task:build-b",
            "type": "FS",
            "source_type_code": 1,
            "lag_minutes": 0,
            "lag_calendar_policy": "UNSPECIFIED_REQUIRES_ENGINE_PROFILE",
        },
        {
            "dependency_id": "dependency:design-verify",
            "predecessor_task_id": "synthetic-task:design",
            "successor_task_id": "synthetic-task:verify",
            "type": "FS",
            "source_type_code": 1,
            "lag_minutes": 0,
            "lag_calendar_policy": "UNSPECIFIED_REQUIRES_ENGINE_PROFILE",
        },
        {
            "dependency_id": "dependency:build-b-handover",
            "predecessor_task_id": "synthetic-task:build-b",
            "successor_task_id": "synthetic-task:handover",
            "type": "FS",
            "source_type_code": 1,
            "lag_minutes": 0,
            "lag_calendar_policy": "UNSPECIFIED_REQUIRES_ENGINE_PROFILE",
        },
        {
            "dependency_id": "dependency:verify-handover",
            "predecessor_task_id": "synthetic-task:verify",
            "successor_task_id": "synthetic-task:handover",
            "type": "FS",
            "source_type_code": 1,
            "lag_minutes": 0,
            "lag_calendar_policy": "UNSPECIFIED_REQUIRES_ENGINE_PROFILE",
        },
    ]
    return source


def test_work_calendar_moves_across_lunch_weekend_and_non_working_time() -> None:
    source = CanonicalScheduleV22.model_validate(_supported_source())
    calendar = UnifiedWorkCalendar(source.calendars[0], ZoneInfo("Asia/Shanghai"))

    assert calendar.next_working_instant(datetime.fromisoformat("2026-09-01T12:30:00+08:00")) == datetime.fromisoformat(
        "2026-09-01T13:00:00+08:00"
    )
    assert calendar.next_working_instant(datetime.fromisoformat("2026-09-05T09:00:00+08:00")) == datetime.fromisoformat(
        "2026-09-07T08:00:00+08:00"
    )
    assert calendar.add_working_minutes(
        datetime.fromisoformat("2026-09-04T13:00:00+08:00"), 480
    ) == datetime.fromisoformat("2026-09-07T12:00:00+08:00")
    assert calendar.subtract_working_minutes(
        datetime.fromisoformat("2026-09-03T10:00:00+08:00"), 240
    ) == datetime.fromisoformat("2026-09-02T15:00:00+08:00")


def test_forward_engine_calculates_fs_chain_and_latest_predecessor_without_mutating_source() -> None:
    payload = _supported_source()
    before = copy.deepcopy(payload)

    result = calculate_minimal_forward_schedule(CanonicalScheduleV22.model_validate(payload))

    dates = {item["task_id"]: item for item in result["task_dates"]}
    assert result["status"] == "calculated"
    assert dates["synthetic-task:design"]["early_start"] == "2026-09-01T08:00:00+08:00"
    assert dates["synthetic-task:design"]["early_finish"] == "2026-09-01T17:00:00+08:00"
    assert dates["synthetic-task:build-a"]["early_start"] == "2026-09-02T08:00:00+08:00"
    assert dates["synthetic-task:build-b"]["early_finish"] == "2026-09-03T17:00:00+08:00"
    assert dates["synthetic-task:handover"]["early_start"] == "2026-09-04T08:00:00+08:00"
    assert payload == before


def test_forward_engine_blocks_unsupported_semantics_instead_of_approximating() -> None:
    payload = _supported_source()
    payload["dependencies"][1]["lag_minutes"] = -480

    result = calculate_minimal_forward_schedule(CanonicalScheduleV22.model_validate(payload))

    codes = {item["code"] for item in result["support"]["blockers"]}
    assert result["status"] == "blocked"
    assert result["task_dates"] == []
    assert codes == {"NEGATIVE_DEPENDENCY_LAG_UNSUPPORTED"}


def test_forward_engine_matches_confirmed_microsoft_project_positive_lag_dates() -> None:
    case = json.loads(POSITIVE_LAG_CASE_PATH.read_text(encoding="utf-8"))

    result = evaluate_golden_gate(case)

    assert result["gate_status"] == "PASSED"
    assert result["external_observation_status"] == "PASSED"
    assert result["engine_profile_id"] == ENGINE_PROFILE_ID


def test_forward_engine_matches_confirmed_microsoft_project_relation_type_dates() -> None:
    case = json.loads(RELATION_TYPES_CASE_PATH.read_text(encoding="utf-8"))

    result = evaluate_golden_gate(case)

    assert result["gate_status"] == "PASSED"
    assert result["external_observation_status"] == "PASSED"
    assert result["engine_profile_id"] == ENGINE_PROFILE_ID


def test_forward_engine_matches_confirmed_microsoft_project_constraint_dates() -> None:
    case = json.loads(CONSTRAINTS_CASE_PATH.read_text(encoding="utf-8"))

    result = evaluate_golden_gate(case)

    assert result["gate_status"] == "PASSED"
    assert result["external_observation_status"] == "PASSED"
    assert result["engine_profile_id"] == ENGINE_PROFILE_ID


def test_forward_engine_matches_confirmed_microsoft_project_manual_dates() -> None:
    case = json.loads(MANUAL_LOCKED_CASE_PATH.read_text(encoding="utf-8"))

    result = evaluate_golden_gate(case)

    assert result["gate_status"] == "PASSED"
    assert result["external_observation_status"] == "PASSED"
    assert result["engine_profile_id"] == ENGINE_PROFILE_ID


def test_forward_engine_rolls_up_nested_summaries_without_using_source_summary_dates() -> None:
    case = json.loads(SUMMARY_ROLLUP_CASE_PATH.read_text(encoding="utf-8"))
    payload = _build_canonical_source(case).model_dump(mode="json")
    for task in payload["tasks"]:
        if task["task_type"] == "summary":
            task["planned_start"] = "2026-09-30T08:00:00+08:00"
            task["planned_finish"] = "2026-09-30T17:00:00+08:00"
    source = CanonicalScheduleV22.model_validate(payload)
    before = source.model_dump(mode="json")

    result = calculate_minimal_forward_schedule(source, engine_profile_id=SUMMARY_ROLLUP_ENGINE_PROFILE_ID)

    dates = {item["task_id"]: item for item in result["task_dates"]}
    assert result["status"] == "calculated"
    assert result["engine_profile_id"] == SUMMARY_ROLLUP_ENGINE_PROFILE_ID
    assert set(dates) == {"task:1", "task:2", "task:3", "task:4", "task:5"}
    assert dates["task:2"]["early_start"] == dates["task:3"]["early_start"]
    assert dates["task:2"]["early_finish"] == dates["task:4"]["early_finish"]
    assert dates["task:1"]["early_start"] == dates["task:5"]["early_start"]
    assert dates["task:1"]["early_finish"] == dates["task:2"]["early_finish"]
    assert dates["task:1"]["summary"] is True
    assert source.model_dump(mode="json") == before


def test_summary_rollup_profile_blocks_summary_without_direct_children() -> None:
    case = json.loads(SUMMARY_ROLLUP_CASE_PATH.read_text(encoding="utf-8"))
    payload = _build_canonical_source(case).model_dump(mode="json")
    for task in payload["tasks"]:
        if task["parent_task_id"] == "task:2":
            task["parent_task_id"] = "task:1"

    result = calculate_minimal_forward_schedule(
        CanonicalScheduleV22.model_validate(payload),
        engine_profile_id=SUMMARY_ROLLUP_ENGINE_PROFILE_ID,
    )

    assert result["status"] == "blocked"
    assert "SUMMARY_WITHOUT_CHILDREN" in {item["code"] for item in result["support"]["blockers"]}


def test_forward_engine_keeps_locked_task_fixed_and_reports_dependency_conflict() -> None:
    case = json.loads(MANUAL_LOCKED_CASE_PATH.read_text(encoding="utf-8"))
    source = _build_canonical_source(case)

    result = calculate_minimal_forward_schedule(source, locked_task_ids={"manual-task:fixed-conflict"})

    dates = {item["task_id"]: item for item in result["task_dates"]}
    assert result["status"] == "invalid"
    assert result["conflicts"] == [
        {
            "code": "LOCKED_TASK_DEPENDENCY_CONFLICT",
            "task_id": "manual-task:fixed-conflict",
            "fixed_start": "2026-09-01T08:00:00+08:00",
            "fixed_finish": "2026-09-01T12:00:00+08:00",
            "required_start": "2026-09-02T08:00:00+08:00",
            "required_finish": "2026-09-02T12:00:00+08:00",
        }
    ]
    assert dates["manual-task:fixed-conflict"]["early_start"] == "2026-09-01T08:00:00+08:00"


def test_forward_engine_uses_latest_start_bound_across_relation_types() -> None:
    payload = _supported_source()
    payload["dependencies"] = [
        {
            "dependency_id": "dependency:ss-bound",
            "predecessor_task_id": "synthetic-task:design",
            "successor_task_id": "synthetic-task:handover",
            "type": "SS",
            "source_type_code": 2,
            "lag_minutes": 120,
            "lag_calendar_policy": "UNSPECIFIED_REQUIRES_ENGINE_PROFILE",
        },
        {
            "dependency_id": "dependency:ff-bound",
            "predecessor_task_id": "synthetic-task:build-a",
            "successor_task_id": "synthetic-task:handover",
            "type": "FF",
            "source_type_code": 3,
            "lag_minutes": 120,
            "lag_calendar_policy": "UNSPECIFIED_REQUIRES_ENGINE_PROFILE",
        },
    ]

    result = calculate_minimal_forward_schedule(CanonicalScheduleV22.model_validate(payload))

    dates = {item["task_id"]: item for item in result["task_dates"]}
    assert result["status"] == "calculated"
    assert dates["synthetic-task:handover"]["early_start"] == "2026-09-01T10:00:00+08:00"
    assert dates["synthetic-task:handover"]["early_finish"] == "2026-09-02T10:00:00+08:00"


def test_forward_engine_blocks_empty_activity_set() -> None:
    payload = _supported_source()
    payload["tasks"] = [task for task in payload["tasks"] if task["task_type"] == "summary"]
    payload["dependencies"] = []
    for task in payload["tasks"]:
        if task["parent_task_id"] and not any(
            parent["task_id"] == task["parent_task_id"] for parent in payload["tasks"]
        ):
            task["parent_task_id"] = None

    result = calculate_minimal_forward_schedule(CanonicalScheduleV22.model_validate(payload))

    assert result["status"] == "blocked"
    assert result["support"]["blockers"][0]["code"] == "NO_ACTIVITY_TASKS"
