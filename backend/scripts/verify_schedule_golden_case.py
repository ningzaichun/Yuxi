"""Verify Microsoft Project observations and supported schedule-engine golden cases."""

from __future__ import annotations

import copy
import json
import sys
from argparse import ArgumentParser
from datetime import datetime
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT / "package") not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT / "package"))

from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22  # noqa: E402
from yuxi.schedule.forward_engine import (  # noqa: E402
    ENGINE_PROFILE_ID,
    REVERSE_FLOAT_ENGINE_PROFILE_ID,
    SUMMARY_ROLLUP_ENGINE_PROFILE_ID,
    calculate_minimal_forward_schedule,
)

DEFAULT_CASE_PATH = BACKEND_ROOT / "test" / "data" / "schedule" / "microsoft_project_s3_golden_case.json"
BASE_SOURCE_PATH = BACKEND_ROOT / "test" / "data" / "schedule" / "schedule_v2_2_synthetic_case_s.json"
PENDING_STATUS = "PENDING_MS_PROJECT_CONFIRMATION"
CONFIRMED_STATUS = "CONFIRMED_BY_MS_PROJECT"
MICROSOFT_PROJECT_CONSTRAINT_TYPES = {
    "AS_SOON_AS_POSSIBLE": 0,
    "START_NO_EARLIER_THAN": 4,
    "FINISH_NO_EARLIER_THAN": 6,
}


def load_golden_case(path: Path = DEFAULT_CASE_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_golden_gate(case: dict[str, Any]) -> dict[str, Any]:
    """Return a gate result without treating a pending external baseline as success."""
    validation_errors = _validate_case(case)
    if validation_errors:
        return _gate_result(case, "FAILED", validation_errors)

    engine_result = calculate_minimal_forward_schedule(
        _build_canonical_source(case),
        engine_profile_id=case["engine_profile_id"],
    )
    if engine_result["status"] != "calculated":
        blocker_codes = [item["code"] for item in engine_result["support"]["blockers"]]
        return _gate_result(case, "FAILED", [f"ENGINE_BLOCKED:{code}" for code in blocker_codes])

    actual_dates = {item["task_id"]: item for item in engine_result["task_dates"]}
    task_ids = {item["task_id"] for item in case["tasks"]}
    if set(actual_dates) != task_ids:
        return _gate_result(case, "FAILED", ["ENGINE_TASK_SET_MISMATCH"])
    metadata_errors = _compare_task_metadata(case["tasks"], actual_dates)
    metadata_errors.extend(_validate_rollup_assertions(case, actual_dates))
    if metadata_errors:
        return _gate_result(case, "FAILED", metadata_errors)
    observation_errors = _compare_dates(
        task_ids,
        actual_dates,
        case["external_observation"]["task_dates"],
        "EXTERNAL_OBSERVATION",
    )
    if observation_errors:
        return _gate_result(case, "FAILED", observation_errors, "FAILED")

    expected = case["expected"]
    if expected["confirmation_status"] == PENDING_STATUS:
        pending_errors = []
        if expected["task_dates"]:
            pending_errors.append("PENDING_EXPECTED_DATES_MUST_BE_EMPTY")
        for field in ("confirmed_by", "confirmed_at", "microsoft_project_version"):
            if expected[field] is not None:
                pending_errors.append(f"PENDING_{field.upper()}_MUST_BE_NULL")
        if pending_errors:
            return _gate_result(case, "FAILED", pending_errors, "PASSED")
        return _gate_result(case, "PENDING", [], "PASSED")

    confirmation_errors = [
        f"CONFIRMED_{field.upper()}_REQUIRED"
        for field in ("confirmed_by", "confirmed_at", "microsoft_project_version")
        if not expected[field]
    ]
    confirmation_errors.extend(_compare_dates(task_ids, actual_dates, expected["task_dates"], "CONFIRMED"))
    return _gate_result(
        case,
        "FAILED" if confirmation_errors else "PASSED",
        confirmation_errors,
        "PASSED",
    )


def evaluate_external_observation(case: dict[str, Any]) -> dict[str, Any]:
    """Validate an external observation without invoking the Yuxi engine."""
    errors = _validate_external_case(case)
    expected = case.get("expected")
    confirmation_status = expected.get("confirmation_status") if isinstance(expected, dict) else None
    if errors:
        status = "FAILED"
    elif confirmation_status == CONFIRMED_STATUS:
        status = "PASSED"
    else:
        status = "PENDING"
    return {
        "case_id": case.get("case_id"),
        "verification_scope": "EXTERNAL_OBSERVATION_ONLY_NO_YUXI_ENGINE",
        "gate_status": status,
        "external_observation_status": "FAILED" if errors else "PASSED",
        "confirmation_status": confirmation_status,
        "engine_profile_id": case.get("engine_profile_id"),
        "errors": errors,
    }


def _validate_case(case: dict[str, Any]) -> list[str]:
    errors = []
    if case.get("schema_version") != "microsoft_project_schedule_golden_case_v1":
        errors.append("SCHEMA_VERSION_UNSUPPORTED")
    if case.get("engine_profile_id") not in {
        ENGINE_PROFILE_ID,
        SUMMARY_ROLLUP_ENGINE_PROFILE_ID,
        REVERSE_FLOAT_ENGINE_PROFILE_ID,
    }:
        errors.append("ENGINE_PROFILE_MISMATCH")
    if case.get("expected_value_source") != "MICROSOFT_PROJECT_MANUAL_CONFIRMATION":
        errors.append("EXPECTED_VALUE_SOURCE_INVALID")
    observation = case.get("external_observation")
    if not isinstance(observation, dict):
        errors.append("EXTERNAL_OBSERVATION_REQUIRED")
    elif observation.get("observation_status") != "CAPTURED_BY_MS_PROJECT_COM":
        errors.append("EXTERNAL_OBSERVATION_STATUS_INVALID")
    expected = case.get("expected")
    if not isinstance(expected, dict):
        return [*errors, "EXPECTED_REQUIRED"]
    if expected.get("confirmation_status") not in {PENDING_STATUS, CONFIRMED_STATUS}:
        errors.append("CONFIRMATION_STATUS_INVALID")
    task_ids = [item.get("task_id") for item in case.get("tasks", [])]
    if not task_ids or len(task_ids) != len(set(task_ids)):
        errors.append("TASK_IDS_INVALID")
    known_task_ids = set(task_ids)
    for task in case.get("tasks", []):
        if task.get("task_type", "activity") not in {"activity", "summary"}:
            errors.append(f"TASK_TYPE_INVALID:{task.get('task_id')}")
        if task.get("duration_minutes", 0) <= 0:
            errors.append(f"TASK_DURATION_INVALID:{task.get('task_id')}")
        if not set(task.get("predecessor_task_ids", [])) <= known_task_ids:
            errors.append(f"PREDECESSOR_UNKNOWN:{task.get('task_id')}")
        if task.get("parent_task_id") is not None and task["parent_task_id"] not in known_task_ids:
            errors.append(f"PARENT_UNKNOWN:{task.get('task_id')}")
    return errors


def _validate_external_case(case: dict[str, Any]) -> list[str]:
    errors = []
    if case.get("schema_version") != "microsoft_project_schedule_golden_case_v1":
        errors.append("SCHEMA_VERSION_UNSUPPORTED")
    if case.get("expected_value_source") != "MICROSOFT_PROJECT_MANUAL_CONFIRMATION":
        errors.append("EXPECTED_VALUE_SOURCE_INVALID")

    task_ids = [item.get("task_id") for item in case.get("tasks", [])]
    if not task_ids or len(task_ids) != len(set(task_ids)):
        errors.append("TASK_IDS_INVALID")
    known_task_ids = set(task_ids)
    for dependency in case.get("dependencies", []):
        if dependency.get("predecessor_task_id") not in known_task_ids:
            errors.append("DEPENDENCY_PREDECESSOR_UNKNOWN")
        if dependency.get("successor_task_id") not in known_task_ids:
            errors.append("DEPENDENCY_SUCCESSOR_UNKNOWN")
        if dependency.get("type") not in {"FS", "SS", "FF", "SF"}:
            errors.append("DEPENDENCY_TYPE_INVALID")
        if dependency.get("lag_minutes", -1) < 0:
            errors.append("NEGATIVE_DEPENDENCY_LAG_UNSUPPORTED")
    for task in case.get("tasks", []):
        scheduling_mode = task.get("scheduling_mode", "automatic")
        if scheduling_mode not in {"automatic", "manual"}:
            errors.append(f"TASK_SCHEDULING_MODE_INVALID:{task.get('task_id')}")
        if scheduling_mode == "manual":
            try:
                planned_start = datetime.fromisoformat(task.get("planned_start", ""))
            except (TypeError, ValueError):
                errors.append(f"MANUAL_TASK_START_INVALID:{task.get('task_id')}")
            else:
                if planned_start.tzinfo is None:
                    errors.append(f"MANUAL_TASK_START_TIME_ZONE_REQUIRED:{task.get('task_id')}")
        constraint = task.get("constraint") or {"type": "AS_SOON_AS_POSSIBLE", "date": None}
        if constraint.get("type") not in MICROSOFT_PROJECT_CONSTRAINT_TYPES:
            errors.append("TASK_CONSTRAINT_TYPE_INVALID")
        elif constraint["type"] == "AS_SOON_AS_POSSIBLE":
            if constraint.get("date") is not None:
                errors.append(f"ASAP_CONSTRAINT_DATE_MUST_BE_NULL:{task.get('task_id')}")
        elif not constraint.get("date"):
            errors.append(f"TASK_CONSTRAINT_DATE_REQUIRED:{task.get('task_id')}")

    observation = case.get("external_observation")
    if not isinstance(observation, dict):
        errors.append("EXTERNAL_OBSERVATION_REQUIRED")
    else:
        if observation.get("observation_status") != "CAPTURED_BY_MS_PROJECT_COM":
            errors.append("EXTERNAL_OBSERVATION_STATUS_INVALID")
        if (
            any(task.get("scheduling_mode") == "manual" for task in case.get("tasks", []))
            and observation.get("manual_input_order") != "MANUAL_START_THEN_RELATIONSHIPS"
        ):
            errors.append("EXTERNAL_OBSERVATION_MANUAL_INPUT_ORDER_INVALID")
        observed_dates = observation.get("task_dates", [])
        if {item.get("task_id") for item in observed_dates} != known_task_ids:
            errors.append("EXTERNAL_OBSERVATION_TASK_SET_MISMATCH")
        extended_fields_present = any("late_start" in item for item in observed_dates)
        for item in observed_dates:
            date_fields = ("early_start", "early_finish", "late_start", "late_finish") if extended_fields_present else (
                "early_start",
                "early_finish",
            )
            for field in date_fields:
                try:
                    value = datetime.fromisoformat(item.get(field, ""))
                except (TypeError, ValueError):
                    errors.append(f"EXTERNAL_OBSERVATION_DATE_INVALID:{item.get('task_id')}:{field}")
                    continue
                if value.tzinfo is None:
                    errors.append(f"EXTERNAL_OBSERVATION_TIME_ZONE_REQUIRED:{item.get('task_id')}:{field}")
            if extended_fields_present:
                for field in ("total_slack_minutes", "free_slack_minutes"):
                    if not isinstance(item.get(field), int):
                        errors.append(f"EXTERNAL_OBSERVATION_SLACK_INVALID:{item.get('task_id')}:{field}")
                if not isinstance(item.get("critical"), bool):
                    errors.append(f"EXTERNAL_OBSERVATION_CRITICAL_INVALID:{item.get('task_id')}")
        successor_ids = {dependency.get("successor_task_id") for dependency in case.get("dependencies", [])}
        relations_by_task = {
            item.get("task_id"): item.get("microsoft_project_predecessors", "")
            for item in observation.get("task_relations", [])
        }
        relation_task_ids = set(relations_by_task)
        if relation_task_ids != successor_ids:
            errors.append("EXTERNAL_OBSERVATION_RELATION_SET_MISMATCH")
        else:
            for dependency in case.get("dependencies", []):
                relation = relations_by_task[dependency["successor_task_id"]]
                dependency_type = dependency["type"]
                lag_minutes = dependency["lag_minutes"]
                if dependency_type != "FS" and dependency_type not in relation:
                    errors.append(f"EXTERNAL_OBSERVATION_RELATION_TYPE_MISMATCH:{dependency['successor_task_id']}")
                if lag_minutes > 0 and str(lag_minutes) not in relation:
                    errors.append(f"EXTERNAL_OBSERVATION_RELATION_LAG_MISMATCH:{dependency['successor_task_id']}")
        if any("constraint" in task for task in case.get("tasks", [])):
            constraints_by_task = {item.get("task_id"): item for item in observation.get("task_constraints", [])}
            if set(constraints_by_task) != known_task_ids:
                errors.append("EXTERNAL_OBSERVATION_CONSTRAINT_SET_MISMATCH")
            else:
                for task in case.get("tasks", []):
                    constraint = task.get("constraint") or {"type": "AS_SOON_AS_POSSIBLE", "date": None}
                    observed = constraints_by_task[task["task_id"]]
                    expected_type = MICROSOFT_PROJECT_CONSTRAINT_TYPES.get(constraint.get("type"))
                    if observed.get("microsoft_project_constraint_type") != expected_type:
                        errors.append(f"EXTERNAL_OBSERVATION_CONSTRAINT_TYPE_MISMATCH:{task['task_id']}")
                    expected_date = constraint.get("date")
                    observed_date = observed.get("microsoft_project_constraint_date")
                    if expected_date != observed_date:
                        errors.append(f"EXTERNAL_OBSERVATION_CONSTRAINT_DATE_MISMATCH:{task['task_id']}")
        if any("scheduling_mode" in task for task in case.get("tasks", [])):
            modes_by_task = {item.get("task_id"): item for item in observation.get("task_scheduling_modes", [])}
            if set(modes_by_task) != known_task_ids:
                errors.append("EXTERNAL_OBSERVATION_SCHEDULING_MODE_SET_MISMATCH")
            else:
                for task in case.get("tasks", []):
                    expected_manual = task.get("scheduling_mode", "automatic") == "manual"
                    if modes_by_task[task["task_id"]].get("microsoft_project_manual") is not expected_manual:
                        errors.append(f"EXTERNAL_OBSERVATION_SCHEDULING_MODE_MISMATCH:{task['task_id']}")

    expected = case.get("expected")
    if not isinstance(expected, dict):
        errors.append("EXPECTED_REQUIRED")
    elif expected.get("confirmation_status") == PENDING_STATUS:
        if expected.get("task_dates"):
            errors.append("PENDING_EXPECTED_DATES_MUST_BE_EMPTY")
        for field in ("confirmed_by", "confirmed_at", "microsoft_project_version"):
            if expected.get(field) is not None:
                errors.append(f"PENDING_{field.upper()}_MUST_BE_NULL")
    elif expected.get("confirmation_status") == CONFIRMED_STATUS:
        for field in ("confirmed_by", "confirmed_at", "microsoft_project_version"):
            if not expected.get(field):
                errors.append(f"CONFIRMED_{field.upper()}_REQUIRED")
        if {item.get("task_id") for item in expected.get("task_dates", [])} != known_task_ids:
            errors.append("CONFIRMED_TASK_SET_MISMATCH")
        elif isinstance(observation, dict):
            errors.extend(
                _compare_dates(
                    known_task_ids,
                    {item["task_id"]: item for item in observation.get("task_dates", [])},
                    expected["task_dates"],
                    "CONFIRMED_OBSERVATION",
                )
            )
    else:
        errors.append("CONFIRMATION_STATUS_INVALID")
    return errors


def _compare_dates(
    task_ids: set[str],
    actual_dates: dict[str, dict[str, Any]],
    comparison_dates: list[dict[str, Any]],
    prefix: str,
) -> list[str]:
    dates_by_task = {item["task_id"]: item for item in comparison_dates}
    errors = []
    if set(dates_by_task) != task_ids:
        errors.append(f"{prefix}_TASK_SET_MISMATCH")
    for task_id in sorted(task_ids & set(dates_by_task)):
        comparison = dates_by_task[task_id]
        fields = (
            "early_start",
            "early_finish",
            "late_start",
            "late_finish",
            "total_slack_minutes",
            "free_slack_minutes",
            "critical",
        )
        for field in fields:
            if field not in comparison:
                continue
            if comparison[field] != actual_dates[task_id].get(field):
                errors.append(f"{prefix}_DATE_MISMATCH:{task_id}:{field}")
    return errors


def _compare_task_metadata(
    task_specs: list[dict[str, Any]],
    actual_dates: dict[str, dict[str, Any]],
) -> list[str]:
    errors = []
    for task in task_specs:
        if task.get("task_type", "activity") == "activity" and "task_type" not in actual_dates[task["task_id"]]:
            continue
        actual = actual_dates[task["task_id"]]
        expected = {
            "parent_task_id": task.get("parent_task_id"),
            "outline_level": task.get("outline_level", 1),
            "task_type": task.get("task_type", "activity"),
            "summary": task.get("task_type", "activity") == "summary",
        }
        for field, expected_value in expected.items():
            if actual.get(field) != expected_value:
                errors.append(f"ENGINE_TASK_METADATA_MISMATCH:{task['task_id']}:{field}")
    return errors


def _validate_rollup_assertions(
    case: dict[str, Any],
    actual_dates: dict[str, dict[str, Any]],
) -> list[str]:
    errors = []
    for assertion in case.get("rollup_assertions", []):
        summary_id = assertion["summary_task_id"]
        children = [actual_dates[child_id] for child_id in assertion["direct_child_task_ids"]]
        expected_start = min(child["early_start"] for child in children)
        expected_finish = max(child["early_finish"] for child in children)
        if actual_dates[summary_id]["early_start"] != expected_start:
            errors.append(f"SUMMARY_ROLLUP_START_MISMATCH:{summary_id}")
        if actual_dates[summary_id]["early_finish"] != expected_finish:
            errors.append(f"SUMMARY_ROLLUP_FINISH_MISMATCH:{summary_id}")
    return errors


def _build_canonical_source(case: dict[str, Any]) -> CanonicalScheduleV22:
    source = json.loads(BASE_SOURCE_PATH.read_text(encoding="utf-8"))
    source["snapshot_id"] = f"golden:{case['case_id']}"
    source["source"]["file_name"] = f"{case['case_id']}.json"
    source["project"]["project_id"] = f"project:{case['case_id']}"
    source["project"]["name"] = case.get("project_name", case["case_id"])
    source["semantics"]["time_zone"] = case["time_zone"]
    source["project"]["planned_start"] = case["project_start"]
    if "project_finish" in case:
        source["project"]["planned_finish"] = case["project_finish"]
    source["project"]["source_project_summary"]["start"] = case["project_start"]
    if "project_finish" in case:
        source["project"]["source_project_summary"]["finish"] = case["project_finish"]
    if "project_duration_minutes" in case:
        source["project"]["source_project_summary"]["duration_minutes"] = case["project_duration_minutes"]

    working_weekdays = set(case["calendar"]["working_weekdays"])
    intervals = case["calendar"]["working_intervals"]
    for weekday, day in source["calendars"][0]["weekly_pattern"].items():
        day["day_type"] = "WORKING" if weekday in working_weekdays else "NON_WORKING"
        day["intervals"] = copy.deepcopy(intervals) if weekday in working_weekdays else []
    source["calendars"][0]["exceptions"] = copy.deepcopy(case["calendar"]["exceptions"])

    templates = {
        task_type: next(task for task in source["tasks"] if task["task_type"] == task_type)
        for task_type in ("activity", "summary")
    }
    tasks = []
    for index, task_spec in enumerate(case["tasks"]):
        task_type = task_spec.get("task_type", "activity")
        task = copy.deepcopy(templates[task_type])
        task["task_id"] = task_spec["task_id"]
        task["source_id"] = task_spec.get("source_id", index + 1)
        task["source_unique_id"] = task_spec.get("source_unique_id", index + 1)
        task["source_guid"] = f"golden:{task_spec['task_id']}"
        task["parent_task_id"] = task_spec.get("parent_task_id")
        task["wbs"] = task_spec.get("wbs", str(index + 1))
        task["outline_level"] = task_spec.get("outline_level", 1)
        task["name"] = task_spec["name"]
        task["task_type"] = task_type
        task["duration_minutes"] = task_spec["duration_minutes"]
        task["source_work_minutes"] = 0 if task_type == "summary" else task_spec["duration_minutes"]
        task["scheduling_mode"] = task_spec.get("scheduling_mode", "automatic")
        task["calendar_id"] = None
        task["effective_calendar_id"] = source["project"]["default_calendar_id"]
        if "planned_start" in task_spec:
            task["planned_start"] = task_spec["planned_start"]
            task["planned_finish"] = task_spec["planned_finish"]
            for field in ("early_start", "late_start"):
                task["source_calculation"][field] = task_spec["planned_start"]
            for field in ("early_finish", "late_finish"):
                task["source_calculation"][field] = task_spec["planned_finish"]
        if "constraint" in task_spec:
            task["constraint"] = copy.deepcopy(task_spec["constraint"])
        else:
            task["constraint"] = {"type": "AS_SOON_AS_POSSIBLE", "date": None}
        tasks.append(task)
    source["tasks"] = tasks

    dependencies = []
    dependency_specs = case.get("dependencies")
    if dependency_specs is None:
        dependency_specs = [
            {
                "predecessor_task_id": predecessor_id,
                "successor_task_id": successor["task_id"],
                "type": "FS",
                "lag_minutes": 0,
            }
            for successor in case["tasks"]
            for predecessor_id in successor["predecessor_task_ids"]
        ]
    for dependency in dependency_specs:
        dependencies.append(
            {
                "dependency_id": (f"golden:{dependency['predecessor_task_id']}->{dependency['successor_task_id']}"),
                "predecessor_task_id": dependency["predecessor_task_id"],
                "successor_task_id": dependency["successor_task_id"],
                "type": dependency["type"],
                "source_type_code": 1,
                "lag_minutes": dependency["lag_minutes"],
                "lag_calendar_policy": "UNSPECIFIED_REQUIRES_ENGINE_PROFILE",
            }
        )
    source["dependencies"] = dependencies
    summary_ids = {task["task_id"] for task in tasks if task["task_type"] == "summary"}
    activity_ids = {task["task_id"] for task in tasks if task["task_type"] == "activity"}
    incoming = {task_id: 0 for task_id in activity_ids}
    outgoing = {task_id: 0 for task_id in activity_ids}
    dependency_types = {kind: 0 for kind in ("FS", "SS", "FF", "SF")}
    for dependency in dependencies:
        dependency_types[dependency["type"]] += 1
        if dependency["successor_task_id"] in incoming:
            incoming[dependency["successor_task_id"]] += 1
        if dependency["predecessor_task_id"] in outgoing:
            outgoing[dependency["predecessor_task_id"]] += 1
    source["statistics"].update(
        {
            "tasks": len(tasks),
            "summary_tasks": len(summary_ids),
            "leaf_tasks": len(activity_ids),
            "milestones": sum(task["duration_minutes"] == 0 for task in tasks if task["task_type"] == "activity"),
            "dependencies": len(dependencies),
            "dependency_types": dependency_types,
            "positive_lag_dependencies": sum(item["lag_minutes"] > 0 for item in dependencies),
            "negative_lag_dependencies": sum(item["lag_minutes"] < 0 for item in dependencies),
            "open_start_tasks": sum(count == 0 for count in incoming.values()),
            "open_finish_tasks": sum(count == 0 for count in outgoing.values()),
            "summary_task_dependencies": sum(
                item["predecessor_task_id"] in summary_ids or item["successor_task_id"] in summary_ids
                for item in dependencies
            ),
        }
    )
    return CanonicalScheduleV22.model_validate(source)


def _gate_result(
    case: dict[str, Any],
    status: str,
    errors: list[str],
    external_observation_status: str = "NOT_CHECKED",
) -> dict[str, Any]:
    return {
        "case_id": case.get("case_id"),
        "gate_status": status,
        "engine_profile_id": case.get("engine_profile_id"),
        "expected_value_source": case.get("expected_value_source"),
        "external_observation_status": external_observation_status,
        "errors": errors,
    }


if __name__ == "__main__":
    parser = ArgumentParser(description="校验 Microsoft Project 人工排期黄金样例门禁")
    parser.add_argument("--case", type=Path, default=DEFAULT_CASE_PATH)
    parser.add_argument(
        "--observation-only",
        action="store_true",
        help="只校验 Microsoft Project observation 和人工确认状态，不调用 Yuxi 引擎",
    )
    args = parser.parse_args()
    case = load_golden_case(args.case)
    result = evaluate_external_observation(case) if args.observation_only else evaluate_golden_gate(case)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit({"PASSED": 0, "FAILED": 1, "PENDING": 3}[result["gate_status"]])
