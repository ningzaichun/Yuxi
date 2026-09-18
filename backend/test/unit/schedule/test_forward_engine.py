from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts.generate_synthetic_schedule_case import build_synthetic_schedule_case
from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22
from yuxi.schedule.contracts.canonical_v2_4 import CanonicalScheduleV24
from yuxi.schedule.contracts.canonical_v2_8 import CanonicalScheduleV28
from scripts.verify_schedule_golden_case import _build_canonical_source, evaluate_golden_gate
from yuxi.schedule.forward_engine import (
    CALENDAR_EXCEPTIONS_ENGINE_PROFILE_ID,
    ENGINE_PROFILE_ID,
    INACTIVE_ENGINE_PROFILE_ID,
    MULTI_CALENDAR_ENGINE_PROFILE_ID,
    NEGATIVE_LAG_ENGINE_PROFILE_ID,
    REVERSE_FLOAT_ENGINE_PROFILE_ID,
    SUMMARY_ROLLUP_ENGINE_PROFILE_ID,
    UnifiedWorkCalendar,
    calculate_minimal_forward_schedule,
    recalculation_profile_for,
)
from yuxi.schedule.work_calendar import build_effective_work_calendars

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


def _signed_lag_source(relation_type: str = "FS") -> CanonicalScheduleV22:
    payload = _supported_source()
    payload["semantics"]["lag_calendar_policy"] = "UNIFIED_PROJECT_CALENDAR_WORKING_MINUTES"
    payload["tasks"] = [
        task
        for task in payload["tasks"]
        if task["task_id"] in {"synthetic-task:design", "synthetic-task:handover"}
    ]
    for task in payload["tasks"]:
        task["parent_task_id"] = None
        task["outline_level"] = 1
        task["constraint"] = {"type": "AS_SOON_AS_POSSIBLE", "date": None}
        task["actual_start"] = None
        task["actual_finish"] = None
        task["percent_complete"] = 0
    predecessor = next(task for task in payload["tasks"] if task["task_id"] == "synthetic-task:design")
    predecessor["scheduling_mode"] = "manual"
    predecessor["planned_start"] = "2026-09-03T08:00:00+08:00"
    predecessor["planned_finish"] = "2026-09-03T17:00:00+08:00"
    payload["dependencies"] = [
        {
            "dependency_id": f"dependency:signed-{relation_type.lower()}",
            "predecessor_task_id": "synthetic-task:design",
            "successor_task_id": "synthetic-task:handover",
            "type": relation_type,
            "source_type_code": {"FF": 0, "FS": 1, "SF": 2, "SS": 3}[relation_type],
            "lag_minutes": -240,
            "lag_calendar_policy": "UNIFIED_PROJECT_CALENDAR_WORKING_MINUTES",
        }
    ]
    return CanonicalScheduleV22.model_validate(payload)


def _calendar_exception_source() -> CanonicalScheduleV24:
    payload = _supported_source()
    payload["schema_version"] = "canonical_schedule_v2.4"
    payload["semantics"]["lag_calendar_policy"] = "UNIFIED_PROJECT_CALENDAR_WORKING_MINUTES"
    for dependency in payload["dependencies"]:
        dependency["lag_calendar_policy"] = "UNIFIED_PROJECT_CALENDAR_WORKING_MINUTES"
    payload["calendars"][0]["exceptions"] = [
        {
            "exception_id": "holiday",
            "name": "停工日",
            "start_date": "2026-09-09T00:00:00+08:00",
            "finish_date": "2026-09-09T23:59:59+08:00",
            "working": False,
            "intervals": [],
        },
        {
            "exception_id": "makeup",
            "name": "周日补班",
            "start_date": "2026-09-13T00:00:00+08:00",
            "finish_date": "2026-09-13T23:59:59+08:00",
            "working": True,
            "intervals": [
                {"start": "08:00:00", "finish": "12:00:00"},
                {"start": "13:00:00", "finish": "17:00:00"},
            ],
        },
    ]
    return CanonicalScheduleV24.model_validate(payload)


def _inherited_task_calendar_source() -> CanonicalScheduleV24:
    payload = _calendar_exception_source().model_dump(mode="json")
    payload["semantics"]["lag_calendar_policy"] = "SUCCESSOR_TASK_CALENDAR"
    for dependency in payload["dependencies"]:
        dependency["lag_calendar_policy"] = "SUCCESSOR_TASK_CALENDAR"
    child_calendar = copy.deepcopy(payload["calendars"][0])
    child_calendar.update(
        {
            "calendar_id": "calendar:child",
            "source_index": 2,
            "name": "继承任务日历",
            "parent_calendar_id": payload["project"]["default_calendar_id"],
            "exceptions": [
                {
                    "exception_id": "child-holiday-override",
                    "name": "子日历恢复工作",
                    "start_date": "2026-09-09T00:00:00+08:00",
                    "finish_date": "2026-09-09T23:59:59+08:00",
                    "working": True,
                    "intervals": [
                        {"start": "08:00:00", "finish": "12:00:00"},
                        {"start": "13:00:00", "finish": "17:00:00"},
                    ],
                }
            ],
        }
    )
    for day in child_calendar["weekly_pattern"].values():
        day["day_type"] = "INHERITED"
        day["intervals"] = []
    payload["calendars"].append(child_calendar)
    task = next(item for item in payload["tasks"] if item["task_id"] == "synthetic-task:handover")
    task["calendar_id"] = "calendar:child"
    task["effective_calendar_id"] = "calendar:child"
    return CanonicalScheduleV24.model_validate(payload)


def _inactive_source(*, keep_relations: bool) -> CanonicalScheduleV28:
    payload = _supported_source()
    payload["schema_version"] = "canonical_schedule_v2.8"
    payload["semantics"]["lag_calendar_policy"] = "SUCCESSOR_TASK_CALENDAR"
    payload["resources"] = []
    payload["assignments"] = []
    for dependency in payload["dependencies"]:
        dependency["lag_calendar_policy"] = "SUCCESSOR_TASK_CALENDAR"
    inactive_task = next(
        task for task in payload["tasks"] if task["task_id"] == "synthetic-task:verify"
    )
    inactive_task["active"] = False
    if not keep_relations:
        payload["dependencies"] = [
            dependency
            for dependency in payload["dependencies"]
            if "synthetic-task:verify"
            not in {
                dependency["predecessor_task_id"],
                dependency["successor_task_id"],
            }
        ]
    return CanonicalScheduleV28.model_validate(payload)


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
    assert (
        calendar.working_minutes_between(
            datetime.fromisoformat("2026-09-02T12:00:00+08:00"),
            datetime.fromisoformat("2026-09-03T12:00:00+08:00"),
        )
        == 480
    )


def test_work_calendar_shifts_signed_minutes_across_lunch_night_and_weekend() -> None:
    source = CanonicalScheduleV22.model_validate(_supported_source())
    calendar = UnifiedWorkCalendar(source.calendars[0], ZoneInfo("Asia/Shanghai"))

    assert calendar.shift_working_minutes(
        datetime.fromisoformat("2026-09-01T10:00:00+08:00"), 240
    ) == datetime.fromisoformat("2026-09-01T15:00:00+08:00")
    assert calendar.shift_working_minutes(
        datetime.fromisoformat("2026-09-01T15:00:00+08:00"), -240
    ) == datetime.fromisoformat("2026-09-01T10:00:00+08:00")
    assert calendar.shift_working_minutes(
        datetime.fromisoformat("2026-09-07T10:00:00+08:00"), -240
    ) == datetime.fromisoformat("2026-09-04T15:00:00+08:00")
    start = datetime.fromisoformat("2026-09-04T15:00:00+08:00")
    assert calendar.shift_working_minutes(calendar.shift_working_minutes(start, 720), -720) == start


def test_effective_calendar_applies_holiday_makeup_day_and_reverse_arithmetic() -> None:
    source = _calendar_exception_source()
    calendar = UnifiedWorkCalendar(source.calendars[0], ZoneInfo("Asia/Shanghai"))

    assert calendar.add_working_minutes(
        datetime.fromisoformat("2026-09-08T13:00:00+08:00"), 480
    ) == datetime.fromisoformat("2026-09-10T12:00:00+08:00")
    assert calendar.add_working_minutes(
        datetime.fromisoformat("2026-09-11T13:00:00+08:00"), 480
    ) == datetime.fromisoformat("2026-09-13T12:00:00+08:00")
    assert calendar.subtract_working_minutes(
        datetime.fromisoformat("2026-09-13T12:00:00+08:00"), 480
    ) == datetime.fromisoformat("2026-09-11T13:00:00+08:00")
    assert calendar.working_minutes_between(
        datetime.fromisoformat("2026-09-09T00:00:00+08:00"),
        datetime.fromisoformat("2026-09-10T00:00:00+08:00"),
    ) == 0
    assert calendar.working_minutes_between(
        datetime.fromisoformat("2026-09-13T00:00:00+08:00"),
        datetime.fromisoformat("2026-09-14T00:00:00+08:00"),
    ) == 480


def test_effective_calendar_resolves_parent_then_child_overrides() -> None:
    source = _inherited_task_calendar_source()
    calendars = build_effective_work_calendars(source.calendars, ZoneInfo("Asia/Shanghai"))
    holiday = datetime.fromisoformat("2026-09-09T09:00:00+08:00")

    assert calendars[source.project.default_calendar_id].next_working_instant(holiday) == datetime.fromisoformat(
        "2026-09-10T08:00:00+08:00"
    )
    assert calendars["calendar:child"].next_working_instant(holiday) == holiday

    result = calculate_minimal_forward_schedule(
        source,
        engine_profile_id=recalculation_profile_for(source),
    )
    assert recalculation_profile_for(source) == MULTI_CALENDAR_ENGINE_PROFILE_ID
    assert result["status"] == "calculated"
    assert result["engine_version"] == "11.0.0"


def test_multi_calendar_profile_blocks_calendar_inheritance_cycle() -> None:
    payload = _inherited_task_calendar_source().model_dump(mode="json")
    payload["calendars"][0]["parent_calendar_id"] = "calendar:child"
    source = CanonicalScheduleV24.model_validate(payload)

    result = calculate_minimal_forward_schedule(
        source,
        engine_profile_id=MULTI_CALENDAR_ENGINE_PROFILE_ID,
    )

    assert result["status"] == "blocked"
    assert "CALENDAR_INHERITANCE_CYCLE" in {
        blocker["code"] for blocker in result["support"]["blockers"]
    }


def test_effective_calendar_applies_continuous_non_working_exception_range() -> None:
    payload = _calendar_exception_source().model_dump(mode="json")
    payload["calendars"][0]["exceptions"][0]["finish_date"] = "2026-09-10T23:59:59+08:00"
    source = CanonicalScheduleV24.model_validate(payload)
    calendar = UnifiedWorkCalendar(source.calendars[0], ZoneInfo("Asia/Shanghai"))

    assert calendar.add_working_minutes(
        datetime.fromisoformat("2026-09-08T13:00:00+08:00"), 480
    ) == datetime.fromisoformat("2026-09-11T12:00:00+08:00")
    assert calendar.subtract_working_minutes(
        datetime.fromisoformat("2026-09-11T12:00:00+08:00"), 480
    ) == datetime.fromisoformat("2026-09-08T13:00:00+08:00")


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


@pytest.mark.parametrize(
    ("relation_type", "expected_start", "expected_finish"),
    [
        ("FS", "2026-09-03T13:00:00+08:00", "2026-09-04T12:00:00+08:00"),
        ("SS", "2026-09-02T13:00:00+08:00", "2026-09-03T12:00:00+08:00"),
        ("FF", "2026-09-02T13:00:00+08:00", "2026-09-03T13:00:00+08:00"),
        ("SF", "2026-09-01T13:00:00+08:00", "2026-09-02T13:00:00+08:00"),
    ],
)
def test_negative_lag_profile_supports_all_relation_types(
    relation_type: str,
    expected_start: str,
    expected_finish: str,
) -> None:
    source = _signed_lag_source(relation_type)

    result = calculate_minimal_forward_schedule(
        source,
        engine_profile_id=NEGATIVE_LAG_ENGINE_PROFILE_ID,
    )

    dates = {item["task_id"]: item for item in result["task_dates"]}
    successor = dates["synthetic-task:handover"]
    assert result["status"] == "calculated"
    assert result["engine_version"] == "9.0.0"
    assert successor["early_start"] == expected_start
    assert successor["early_finish"] == expected_finish
    assert {"late_start", "late_finish", "total_slack_minutes", "free_slack_minutes", "critical"} <= successor.keys()


def test_recalculation_profile_routes_negative_lag_to_v9() -> None:
    source = _signed_lag_source()

    assert recalculation_profile_for(source) == NEGATIVE_LAG_ENGINE_PROFILE_ID


def test_negative_lag_profile_still_blocks_multiple_calendars() -> None:
    payload = _signed_lag_source().model_dump(mode="json")
    second_calendar = copy.deepcopy(payload["calendars"][0])
    second_calendar["calendar_id"] = "calendar:secondary"
    second_calendar["source_index"] = 2
    second_calendar["name"] = "Secondary"
    payload["calendars"].append(second_calendar)

    result = calculate_minimal_forward_schedule(
        CanonicalScheduleV22.model_validate(payload),
        engine_profile_id=NEGATIVE_LAG_ENGINE_PROFILE_ID,
    )

    assert result["status"] == "blocked"
    assert "MULTIPLE_CALENDARS_UNSUPPORTED" in {
        blocker["code"] for blocker in result["support"]["blockers"]
    }


def test_calendar_exception_profile_routes_v24_and_inherits_signed_lag_features() -> None:
    source = _calendar_exception_source()

    result = calculate_minimal_forward_schedule(
        source,
        engine_profile_id=recalculation_profile_for(source),
    )

    assert recalculation_profile_for(source) == CALENDAR_EXCEPTIONS_ENGINE_PROFILE_ID
    assert result["status"] == "calculated"
    assert result["engine_version"] == "10.0.0"
    assert all(
        {"late_start", "late_finish", "total_slack_minutes", "free_slack_minutes", "critical"}
        <= item.keys()
        for item in result["task_dates"]
    )


def test_older_profile_keeps_calendar_exceptions_blocked() -> None:
    result = calculate_minimal_forward_schedule(
        _calendar_exception_source(),
        engine_profile_id=NEGATIVE_LAG_ENGINE_PROFILE_ID,
    )

    assert result["status"] == "blocked"
    assert "CALENDAR_EXCEPTIONS_UNSUPPORTED" in {
        blocker["code"] for blocker in result["support"]["blockers"]
    }


def test_calendar_exception_profile_blocks_overlapping_ranges() -> None:
    payload = _calendar_exception_source().model_dump(mode="json")
    payload["calendars"][0]["exceptions"].append(
        {
            "exception_id": "overlap",
            "name": "冲突例外",
            "start_date": "2026-09-09T08:00:00+08:00",
            "finish_date": "2026-09-10T17:00:00+08:00",
            "working": False,
            "intervals": [],
        }
    )

    result = calculate_minimal_forward_schedule(
        CanonicalScheduleV24.model_validate(payload),
        engine_profile_id=CALENDAR_EXCEPTIONS_ENGINE_PROFILE_ID,
    )

    assert result["status"] == "blocked"
    assert "CALENDAR_EXCEPTIONS_CONFLICT" in {
        blocker["code"] for blocker in result["support"]["blockers"]
    }


def test_calendar_exception_conflict_reports_ranges_nested_in_long_exception() -> None:
    payload = _calendar_exception_source().model_dump(mode="json")
    payload["calendars"][0]["exceptions"] = [
        {
            "exception_id": "shutdown-outer",
            "name": "连续停工",
            "start_date": "2026-09-09T00:00:00+08:00",
            "finish_date": "2026-09-12T23:59:59+08:00",
            "working": False,
            "intervals": [],
        },
        {
            "exception_id": "shutdown-inner-a",
            "name": "嵌套停工 A",
            "start_date": "2026-09-10T00:00:00+08:00",
            "finish_date": "2026-09-10T23:59:59+08:00",
            "working": False,
            "intervals": [],
        },
        {
            "exception_id": "shutdown-inner-b",
            "name": "嵌套停工 B",
            "start_date": "2026-09-11T00:00:00+08:00",
            "finish_date": "2026-09-11T23:59:59+08:00",
            "working": False,
            "intervals": [],
        },
    ]

    result = calculate_minimal_forward_schedule(
        CanonicalScheduleV24.model_validate(payload),
        engine_profile_id=CALENDAR_EXCEPTIONS_ENGINE_PROFILE_ID,
    )

    blocker = next(item for item in result["support"]["blockers"] if item["code"] == "CALENDAR_EXCEPTIONS_CONFLICT")
    assert blocker["object_refs"] == ["shutdown-inner-a", "shutdown-inner-b", "shutdown-outer"]


def test_calendar_exception_profile_still_blocks_multiple_calendars() -> None:
    payload = _calendar_exception_source().model_dump(mode="json")
    second_calendar = copy.deepcopy(payload["calendars"][0])
    second_calendar["calendar_id"] = "calendar:secondary"
    second_calendar["source_index"] = 2
    second_calendar["exceptions"] = []
    payload["calendars"].append(second_calendar)

    result = calculate_minimal_forward_schedule(
        CanonicalScheduleV24.model_validate(payload),
        engine_profile_id=CALENDAR_EXCEPTIONS_ENGINE_PROFILE_ID,
    )

    assert result["status"] == "blocked"
    assert "MULTIPLE_CALENDARS_UNSUPPORTED" in {
        blocker["code"] for blocker in result["support"]["blockers"]
    }


def test_calendar_exception_profile_blocks_calendar_without_recurring_work() -> None:
    payload = _calendar_exception_source().model_dump(mode="json")
    for day in payload["calendars"][0]["weekly_pattern"].values():
        day["day_type"] = "NON_WORKING"
        day["intervals"] = []

    result = calculate_minimal_forward_schedule(
        CanonicalScheduleV24.model_validate(payload),
        engine_profile_id=CALENDAR_EXCEPTIONS_ENGINE_PROFILE_ID,
    )

    assert result["status"] == "blocked"
    assert "CALENDAR_INTERVALS_INVALID" in {
        blocker["code"] for blocker in result["support"]["blockers"]
    }


def test_negative_lag_locked_conflict_reports_signed_required_dates() -> None:
    payload = _signed_lag_source().model_dump(mode="json")
    successor = next(task for task in payload["tasks"] if task["task_id"] == "synthetic-task:handover")
    successor["planned_start"] = "2026-09-01T08:00:00+08:00"
    successor["planned_finish"] = "2026-09-01T17:00:00+08:00"

    result = calculate_minimal_forward_schedule(
        CanonicalScheduleV22.model_validate(payload),
        locked_task_ids={"synthetic-task:handover"},
        engine_profile_id=NEGATIVE_LAG_ENGINE_PROFILE_ID,
    )

    assert result["status"] == "invalid"
    assert result["conflicts"] == [
        {
            "code": "LOCKED_TASK_DEPENDENCY_CONFLICT",
            "task_id": "synthetic-task:handover",
            "fixed_start": "2026-09-01T08:00:00+08:00",
            "fixed_finish": "2026-09-01T17:00:00+08:00",
            "required_start": "2026-09-03T13:00:00+08:00",
            "required_finish": "2026-09-04T12:00:00+08:00",
        }
    ]


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


def test_forward_engine_matches_confirmed_reverse_float_and_critical_results() -> None:
    case = json.loads(REVERSE_FLOAT_CASE_PATH.read_text(encoding="utf-8"))

    result = evaluate_golden_gate(case)

    assert result["gate_status"] == "PASSED"
    assert result["external_observation_status"] == "PASSED"
    assert result["engine_profile_id"] == REVERSE_FLOAT_ENGINE_PROFILE_ID


def test_reverse_float_profile_inherits_all_confirmed_forward_and_rollup_dates() -> None:
    for case_path in (
        POSITIVE_LAG_CASE_PATH,
        RELATION_TYPES_CASE_PATH,
        CONSTRAINTS_CASE_PATH,
        MANUAL_LOCKED_CASE_PATH,
        SUMMARY_ROLLUP_CASE_PATH,
    ):
        case = json.loads(case_path.read_text(encoding="utf-8"))
        result = calculate_minimal_forward_schedule(
            _build_canonical_source(case),
            engine_profile_id=REVERSE_FLOAT_ENGINE_PROFILE_ID,
        )
        actual_dates = {item["task_id"]: item for item in result["task_dates"]}

        assert result["status"] == "calculated"
        for expected in case["expected"]["task_dates"]:
            assert actual_dates[expected["task_id"]]["early_start"] == expected["early_start"]
            assert actual_dates[expected["task_id"]]["early_finish"] == expected["early_finish"]


def test_reverse_float_profile_projects_nested_summary_results() -> None:
    case = json.loads(SUMMARY_ROLLUP_CASE_PATH.read_text(encoding="utf-8"))
    source = _build_canonical_source(case)

    result = calculate_minimal_forward_schedule(source, engine_profile_id=REVERSE_FLOAT_ENGINE_PROFILE_ID)

    dates = {item["task_id"]: item for item in result["task_dates"]}
    assert result["status"] == "calculated"
    assert dates["task:2"]["late_start"] == min(
        dates["task:3"]["late_start"],
        dates["task:4"]["late_start"],
    )
    assert dates["task:2"]["late_finish"] == max(
        dates["task:3"]["late_finish"],
        dates["task:4"]["late_finish"],
    )
    assert dates["task:2"]["total_slack_minutes"] == min(
        dates["task:3"]["total_slack_minutes"],
        dates["task:4"]["total_slack_minutes"],
    )
    assert dates["task:2"]["critical"] is (
        dates["task:3"]["critical"] or dates["task:4"]["critical"]
    )


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


def test_v28_excludes_isolated_inactive_task_and_rolls_up_active_children() -> None:
    source = _inactive_source(keep_relations=False)

    result = calculate_minimal_forward_schedule(
        source,
        engine_profile_id=recalculation_profile_for(source),
    )

    assert result["status"] == "calculated"
    assert result["engine_profile_id"] == INACTIVE_ENGINE_PROFILE_ID
    dates = {item["task_id"]: item for item in result["task_dates"]}
    assert dates["synthetic-task:verify"]["calculation_status"] == "excluded_inactive"
    assert dates["synthetic-task:verify"]["critical"] is False
    assert dates["synthetic-task:verify"]["late_start"] is None
    assert dates["synthetic-task:phase-b"]["early_start"] == dates["synthetic-task:handover"]["early_start"]
    assert dates["synthetic-task:phase-b"]["early_finish"] == dates["synthetic-task:handover"]["early_finish"]


def test_v28_blocks_dependency_touching_inactive_task() -> None:
    source = _inactive_source(keep_relations=True)

    result = calculate_minimal_forward_schedule(
        source,
        engine_profile_id=recalculation_profile_for(source),
    )

    assert result["status"] == "blocked"
    inactive_blockers = [
        item
        for item in result["support"]["blockers"]
        if item["code"] == "INACTIVE_TASK_DEPENDENCY_REQUIRES_DECISION"
    ]
    assert {
        object_ref
        for item in inactive_blockers
        for object_ref in item["object_refs"]
    } == {
        "dependency:design-verify",
        "dependency:verify-handover",
    }


def test_v28_still_blocks_summary_dependency() -> None:
    source = _inactive_source(keep_relations=False)
    payload = source.model_dump(mode="json")
    payload["dependencies"].append(
        {
            "dependency_id": "dependency:summary-explicit-decision",
            "predecessor_task_id": "synthetic-task:phase-a",
            "successor_task_id": "synthetic-task:handover",
            "type": "FS",
            "source_type_code": 1,
            "lag_minutes": 0,
            "lag_calendar_policy": "SUCCESSOR_TASK_CALENDAR",
        }
    )

    result = calculate_minimal_forward_schedule(
        CanonicalScheduleV28.model_validate(payload),
        engine_profile_id=INACTIVE_ENGINE_PROFILE_ID,
    )

    assert result["status"] == "blocked"
    assert "SUMMARY_DEPENDENCY_UNSUPPORTED" in {
        item["code"] for item in result["support"]["blockers"]
    }
