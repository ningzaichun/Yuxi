#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


TASK_FIELDS = (
    "parent_task_id",
    "outline_level",
    "task_type",
    "summary",
    "scheduling_mode",
    "duration_minutes",
    "start",
    "finish",
)
DATE_FIELDS = {"start", "finish"}


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot parse {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"top-level JSON must be an object: {path}")
    return value


def minute(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO-8601 string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include an explicit UTC offset")
    if parsed.second or parsed.microsecond:
        raise ValueError(f"{label} must have minute precision")
    return parsed


def by_task_id(tasks: Any, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(tasks, list):
        raise ValueError(f"{label}.tasks must be an array")
    result: dict[str, dict[str, Any]] = {}
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise ValueError(f"{label}.tasks[{index}] must be an object")
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError(f"{label}.tasks[{index}].task_id must be a non-empty string")
        if task_id in result:
            raise ValueError(f"duplicate task_id in {label}: {task_id}")
        result[task_id] = task
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Exact Yuxi v6 nested-summary oracle comparator")
    parser.add_argument("expected", type=Path)
    parser.add_argument("actual", type=Path)
    args = parser.parse_args()
    try:
        expected_doc, actual_doc = load(args.expected), load(args.actual)
        for field in ("case_id", "case_version"):
            if expected_doc.get(field) != actual_doc.get(field):
                raise ValueError(
                    f"{field} mismatch: expected={expected_doc.get(field)!r}, actual={actual_doc.get(field)!r}"
                )
        expected = expected_doc.get("expected")
        actual = actual_doc.get("result")
        if not isinstance(expected, dict) or not isinstance(actual, dict):
            raise ValueError("expected must contain 'expected'; actual must contain 'result'")

        differences: list[dict[str, Any]] = []
        for field in ("project_start", "project_finish"):
            e_value, a_value = expected.get(field), actual.get(field)
            try:
                e_time, a_time = minute(e_value, f"expected.{field}"), minute(a_value, f"result.{field}")
                if e_time != a_time:
                    differences.append({"task":"<project>", "field":field, "expected":e_value, "actual":a_value, "delta_minutes":int((a_time-e_time).total_seconds()/60)})
            except ValueError as exc:
                differences.append({"task":"<project>", "field":field, "expected":e_value, "actual":a_value, "error":str(exc)})

        expected_tasks = by_task_id(expected.get("tasks"), "expected")
        actual_tasks = by_task_id(actual.get("tasks"), "result")
        for task_id in sorted(set(expected_tasks) - set(actual_tasks)):
            differences.append({"task":task_id, "field":"task", "expected":"present", "actual":"missing"})
        for task_id in sorted(set(actual_tasks) - set(expected_tasks)):
            differences.append({"task":task_id, "field":"task", "expected":"absent", "actual":"unexpected"})
        for task_id in sorted(set(expected_tasks) & set(actual_tasks)):
            e_task, a_task = expected_tasks[task_id], actual_tasks[task_id]
            for field in TASK_FIELDS:
                e_value, a_value = e_task.get(field), a_task.get(field)
                if field in DATE_FIELDS:
                    try:
                        e_time, a_time = minute(e_value, f"{task_id}.{field}"), minute(a_value, f"{task_id}.{field}")
                        if e_time != a_time:
                            differences.append({"task":task_id, "field":field, "expected":e_value, "actual":a_value, "delta_minutes":int((a_time-e_time).total_seconds()/60)})
                    except ValueError as exc:
                        differences.append({"task":task_id, "field":field, "expected":e_value, "actual":a_value, "error":str(exc)})
                elif e_value != a_value:
                    differences.append({"task":task_id, "field":field, "expected":e_value, "actual":a_value})

        # Explicit hierarchical invariants; these protect against merely echoing project dates.
        for assertion in expected_doc.get("assertions", []):
            summary_id = assertion.get("summary_task_id")
            children = assertion.get("direct_child_task_ids", [])
            if summary_id not in actual_tasks or any(child not in actual_tasks for child in children):
                continue
            summary = actual_tasks[summary_id]
            earliest = min(minute(actual_tasks[child]["start"], f"{child}.start") for child in children)
            latest = max(minute(actual_tasks[child]["finish"], f"{child}.finish") for child in children)
            if minute(summary["start"], f"{summary_id}.start") != earliest:
                differences.append({"task":summary_id, "field":"rollup_start", "expected":earliest.isoformat(), "actual":summary["start"]})
            if minute(summary["finish"], f"{summary_id}.finish") != latest:
                differences.append({"task":summary_id, "field":"rollup_finish", "expected":latest.isoformat(), "actual":summary["finish"]})

        if differences:
            print(f"FAIL {expected_doc['case_id']} v{expected_doc['case_version']}: {len(differences)} difference(s)")
            for item in differences:
                print("-")
                for key in ("task", "field", "expected", "actual", "delta_minutes", "error"):
                    if key in item: print(f"{key}: {item[key]}")
            return 1
        summaries = sum(1 for task in expected_tasks.values() if task.get("summary"))
        print(f"PASS {expected_doc['case_id']} v2: {len(expected_tasks)} tasks, {summaries} nested summaries, exact to the minute")
        return 0
    except (ValueError, KeyError) as exc:
        print(f"STRUCTURE ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
