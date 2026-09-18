from __future__ import annotations

import copy
import json
from pathlib import Path

from scripts.verify_schedule_golden_case import evaluate_external_observation, evaluate_golden_gate, load_golden_case
from yuxi.schedule.forward_engine import (
    CONSTRAINTS_ENGINE_PROFILE_ID,
    ENGINE_PROFILE_ID,
    NEGATIVE_LAG_ENGINE_PROFILE_ID,
    REVERSE_FLOAT_ENGINE_PROFILE_ID,
    SUMMARY_ROLLUP_ENGINE_PROFILE_ID,
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
REVERSE_FLOAT_CASE_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "schedule"
    / "microsoft_project_s4_reverse_float_critical_golden_case.json"
)
NEGATIVE_LAG_CASE_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "schedule"
    / "microsoft_project_s5_negative_lag_golden_case.json"
)
HARD_CONSTRAINTS_CASE_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "schedule"
    / "microsoft_project_v12_hard_constraints_golden_case.json"
)
STATUS_DATE_CASE_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "schedule"
    / "microsoft_project_v14_status_date_golden_case.json"
)


def test_microsoft_project_golden_gate_passes_confirmed_zero_lag_dates() -> None:
    result = evaluate_golden_gate(load_golden_case())

    assert result["gate_status"] == "PASSED"
    assert result["external_observation_status"] == "PASSED"
    assert result["expected_value_source"] == "MICROSOFT_PROJECT_MANUAL_CONFIRMATION"
    assert result["errors"] == []


def test_microsoft_project_golden_gate_rejects_unconfirmed_or_wrong_dates() -> None:
    case = copy.deepcopy(load_golden_case())
    case["expected"].update(
        {
            "confirmation_status": "CONFIRMED_BY_MS_PROJECT",
            "confirmed_by": "计划人员测试占位",
            "confirmed_at": "2026-08-13T12:00:00+08:00",
            "microsoft_project_version": "TEST_PLACEHOLDER",
            "task_dates": [
                {
                    "task_id": task["task_id"],
                    "early_start": "2000-01-01T08:00:00+08:00",
                    "early_finish": "2000-01-01T17:00:00+08:00",
                }
                for task in case["tasks"]
            ],
        }
    )

    result = evaluate_golden_gate(case)

    assert result["gate_status"] == "FAILED"
    assert any(error.startswith("CONFIRMED_DATE_MISMATCH:") for error in result["errors"])


def test_microsoft_project_golden_gate_rejects_external_observation_drift() -> None:
    case = copy.deepcopy(load_golden_case())
    case["external_observation"]["task_dates"][0]["early_finish"] = "2000-01-01T17:00:00+08:00"

    result = evaluate_golden_gate(case)

    assert result["gate_status"] == "FAILED"
    assert result["external_observation_status"] == "FAILED"
    assert result["errors"] == ["EXTERNAL_OBSERVATION_DATE_MISMATCH:synthetic-task:design:early_finish"]


def test_positive_lag_slice_is_confirmed_for_current_profile() -> None:
    case = json.loads(POSITIVE_LAG_CASE_PATH.read_text(encoding="utf-8"))

    assert case["external_observation"]["observation_status"] == "CAPTURED_BY_MS_PROJECT_COM"
    assert len(case["external_observation"]["task_dates"]) == 4
    assert case["expected"]["confirmation_status"] == "CONFIRMED_BY_MS_PROJECT"
    assert len(case["expected"]["task_dates"]) == 4
    assert case["engine_profile_id"] == ENGINE_PROFILE_ID
    assert evaluate_golden_gate(case)["gate_status"] == "PASSED"


def test_relation_types_slice_is_confirmed_for_current_profile() -> None:
    case = json.loads(RELATION_TYPES_CASE_PATH.read_text(encoding="utf-8"))

    assert case["engine_profile_id"] == ENGINE_PROFILE_ID
    assert {dependency["type"] for dependency in case["dependencies"]} == {"FS", "SS", "FF", "SF"}
    assert {dependency["lag_minutes"] for dependency in case["dependencies"]} == {0, 120}
    assert case["external_observation"]["observation_status"] == "CAPTURED_BY_MS_PROJECT_COM"
    assert len(case["external_observation"]["task_relations"]) == 7
    assert len(case["external_observation"]["task_dates"]) == 8
    assert len(case["expected"]["task_dates"]) == 8
    assert evaluate_external_observation(case) == {
        "case_id": "s4-ss-ff-sf-positive-lag-unified-calendar-v1",
        "verification_scope": "EXTERNAL_OBSERVATION_ONLY_NO_YUXI_ENGINE",
        "gate_status": "PASSED",
        "external_observation_status": "PASSED",
        "confirmation_status": "CONFIRMED_BY_MS_PROJECT",
        "engine_profile_id": ENGINE_PROFILE_ID,
        "errors": [],
    }
    assert evaluate_golden_gate(case)["gate_status"] == "PASSED"


def test_external_observation_gate_rejects_relation_evidence_drift() -> None:
    case = json.loads(RELATION_TYPES_CASE_PATH.read_text(encoding="utf-8"))
    case["external_observation"]["task_relations"].pop()

    result = evaluate_external_observation(case)

    assert result["gate_status"] == "FAILED"
    assert result["external_observation_status"] == "FAILED"
    assert result["errors"] == ["EXTERNAL_OBSERVATION_RELATION_SET_MISMATCH"]


def test_external_observation_gate_rejects_relation_type_drift() -> None:
    case = json.loads(RELATION_TYPES_CASE_PATH.read_text(encoding="utf-8"))
    case["external_observation"]["task_relations"][1]["microsoft_project_predecessors"] = "2FS"

    result = evaluate_external_observation(case)

    assert result["gate_status"] == "FAILED"
    assert result["errors"] == ["EXTERNAL_OBSERVATION_RELATION_TYPE_MISMATCH:relation-task:ss-zero"]


def test_constraints_slice_is_confirmed_for_current_profile() -> None:
    case = json.loads(CONSTRAINTS_CASE_PATH.read_text(encoding="utf-8"))

    assert case["engine_profile_id"] == ENGINE_PROFILE_ID
    assert {task["constraint"]["type"] for task in case["tasks"]} == {
        "AS_SOON_AS_POSSIBLE",
        "START_NO_EARLIER_THAN",
        "FINISH_NO_EARLIER_THAN",
    }
    assert len(case["expected"]["task_dates"]) == 6
    assert evaluate_external_observation(case) == {
        "case_id": "s4-snet-fnet-unified-calendar-v1",
        "verification_scope": "EXTERNAL_OBSERVATION_ONLY_NO_YUXI_ENGINE",
        "gate_status": "PASSED",
        "external_observation_status": "PASSED",
        "confirmation_status": "CONFIRMED_BY_MS_PROJECT",
        "engine_profile_id": ENGINE_PROFILE_ID,
        "errors": [],
    }
    assert evaluate_golden_gate(case)["gate_status"] == "PASSED"


def test_external_observation_gate_rejects_constraint_evidence_drift() -> None:
    case = json.loads(CONSTRAINTS_CASE_PATH.read_text(encoding="utf-8"))
    case["external_observation"]["task_constraints"][2]["microsoft_project_constraint_type"] = 0

    result = evaluate_external_observation(case)

    assert result["gate_status"] == "FAILED"
    assert result["external_observation_status"] == "FAILED"
    assert result["errors"] == ["EXTERNAL_OBSERVATION_CONSTRAINT_TYPE_MISMATCH:constraint-task:snet-later"]


def test_manual_locked_slice_observation_is_confirmed() -> None:
    case = json.loads(MANUAL_LOCKED_CASE_PATH.read_text(encoding="utf-8"))

    result = evaluate_external_observation(case)

    assert {task["scheduling_mode"] for task in case["tasks"]} == {"automatic", "manual"}
    assert result["gate_status"] == "PASSED"
    assert result["external_observation_status"] == "PASSED"
    assert result["confirmation_status"] == "CONFIRMED_BY_MS_PROJECT"
    assert result["errors"] == []


def test_external_observation_gate_rejects_manual_mode_evidence_drift() -> None:
    case = json.loads(MANUAL_LOCKED_CASE_PATH.read_text(encoding="utf-8"))
    case["external_observation"]["task_scheduling_modes"][1]["microsoft_project_manual"] = False

    result = evaluate_external_observation(case)

    assert result["gate_status"] == "FAILED"
    assert result["errors"] == ["EXTERNAL_OBSERVATION_SCHEDULING_MODE_MISMATCH:manual-task:fixed-later"]


def test_summary_rollup_slice_is_confirmed_for_v6_profile() -> None:
    case = json.loads(SUMMARY_ROLLUP_CASE_PATH.read_text(encoding="utf-8"))

    result = evaluate_golden_gate(case)

    assert case["engine_profile_id"] == SUMMARY_ROLLUP_ENGINE_PROFILE_ID
    assert len(case["tasks"]) == 5
    assert len(case["rollup_assertions"]) == 2
    assert result["gate_status"] == "PASSED"
    assert result["external_observation_status"] == "PASSED"
    assert result["errors"] == []


def test_reverse_float_slice_is_confirmed_for_v7_profile() -> None:
    case = json.loads(REVERSE_FLOAT_CASE_PATH.read_text(encoding="utf-8"))

    result = evaluate_golden_gate(case)

    assert case["engine_profile_id"] == REVERSE_FLOAT_ENGINE_PROFILE_ID
    assert case["external_observation"]["slack_numeric_unit"] == "working_minutes"
    assert case["external_observation"]["independent_recapture"]["result"] == (
        "MATCHED_ALL_TASK_DATES_SLACK_AND_CRITICAL_FIELDS"
    )
    assert result["gate_status"] == "PASSED"
    assert result["external_observation_status"] == "PASSED"
    assert result["errors"] == []


def test_reverse_float_gate_rejects_slack_or_critical_drift() -> None:
    case = json.loads(REVERSE_FLOAT_CASE_PATH.read_text(encoding="utf-8"))
    case["external_observation"]["task_dates"][1]["total_slack_minutes"] = 0
    case["external_observation"]["task_dates"][1]["critical"] = True

    result = evaluate_golden_gate(case)

    assert result["gate_status"] == "FAILED"
    assert result["external_observation_status"] == "FAILED"
    assert result["errors"] == [
        "EXTERNAL_OBSERVATION_DATE_MISMATCH:synthetic-task:short-work:total_slack_minutes",
        "EXTERNAL_OBSERVATION_DATE_MISMATCH:synthetic-task:short-work:critical",
    ]


def test_negative_lag_slice_matches_independent_microsoft_project_observation() -> None:
    case = json.loads(NEGATIVE_LAG_CASE_PATH.read_text(encoding="utf-8"))

    result = evaluate_golden_gate(case)

    assert case["engine_profile_id"] == NEGATIVE_LAG_ENGINE_PROFILE_ID
    assert {dependency["type"] for dependency in case["dependencies"]} == {"FS", "SS", "FF", "SF"}
    assert {dependency["lag_minutes"] for dependency in case["dependencies"]} == {0, -120}
    assert result["gate_status"] == "PASSED"
    assert result["external_observation_status"] == "PASSED"
    assert result["errors"] == []


def test_negative_lag_observation_rejects_lag_sign_drift() -> None:
    case = json.loads(NEGATIVE_LAG_CASE_PATH.read_text(encoding="utf-8"))
    case["external_observation"]["task_relations"][1]["microsoft_project_predecessors"] = "2FS+120 分钟工时"

    result = evaluate_external_observation(case)

    assert result["gate_status"] == "FAILED"
    assert result["errors"] == ["EXTERNAL_OBSERVATION_RELATION_LAG_MISMATCH:negative-task:fs"]


def test_hard_constraint_observation_records_current_microsoft_project_divergence() -> None:
    case = json.loads(HARD_CONSTRAINTS_CASE_PATH.read_text(encoding="utf-8"))

    observation_result = evaluate_external_observation(case)
    engine_result = evaluate_golden_gate(case)

    assert case["engine_profile_id"] == CONSTRAINTS_ENGINE_PROFILE_ID
    assert observation_result["external_observation_status"] == "PASSED"
    assert observation_result["gate_status"] == "PASSED"
    assert case["compatibility_decision"]["status"] == "KNOWN_DIVERGENCE_ACCEPTED"
    assert case["compatibility_decision"]["policy"] == "YUXI_NETWORK_PRECEDENCE_WITH_FNLT_WARNING"
    assert engine_result["gate_status"] == "FAILED"
    assert engine_result["errors"] == [
        "EXTERNAL_OBSERVATION_DATE_MISMATCH:constraint-v12:a:free_slack_minutes",
        "EXTERNAL_OBSERVATION_DATE_MISMATCH:constraint-v12:b:free_slack_minutes",
        "EXTERNAL_OBSERVATION_DATE_MISMATCH:constraint-v12:c:total_slack_minutes",
        "EXTERNAL_OBSERVATION_DATE_MISMATCH:constraint-v12:d:early_start",
        "EXTERNAL_OBSERVATION_DATE_MISMATCH:constraint-v12:d:early_finish",
    ]


def test_status_date_observation_confirms_remaining_work_reschedule_for_v14() -> None:
    case = json.loads(STATUS_DATE_CASE_PATH.read_text(encoding="utf-8"))

    result = evaluate_external_observation(case)

    assert result["gate_status"] == "PASSED"
    assert result["external_observation_status"] == "PASSED"
    assert result["errors"] == []
    assert case["external_observation"]["reschedule_action_code"] == 2
    progress = {
        item["task_id"]: item for item in case["external_observation"]["task_progress"]
    }
    assert progress["progress-v14:b"] == {
        "task_id": "progress-v14:b",
        "microsoft_project_percent_complete": 40,
        "microsoft_project_actual_start": "2026-09-11T08:00:00+08:00",
        "microsoft_project_actual_finish": None,
        "microsoft_project_remaining_duration_minutes": 1440,
        "microsoft_project_stop": "2026-09-14T17:00:00+08:00",
        "microsoft_project_resume": "2026-09-17T08:00:00+08:00",
    }
    dates = {item["task_id"]: item for item in case["expected"]["task_dates"]}
    assert dates["progress-v14:b"]["early_finish"] == "2026-09-21T17:00:00+08:00"
    assert dates["progress-v14:c"]["early_finish"] == "2026-09-24T17:00:00+08:00"


def test_status_date_observation_rejects_remaining_duration_drift() -> None:
    case = json.loads(STATUS_DATE_CASE_PATH.read_text(encoding="utf-8"))
    case["external_observation"]["task_progress"][1][
        "microsoft_project_remaining_duration_minutes"
    ] = 480

    result = evaluate_external_observation(case)

    assert result["gate_status"] == "FAILED"
    assert result["errors"] == [
        "EXTERNAL_OBSERVATION_PROGRESS_MISMATCH:progress-v14:b:"
        "microsoft_project_remaining_duration_minutes"
    ]
