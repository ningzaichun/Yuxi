#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


FIELDS = (
    "status",
    "project_dates",
    "task_dates",
    "summary_dates",
    "issues",
    "resource_conflicts",
    "assignment_costs",
    "baseline_variances",
)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"顶层必须是对象: {path}")
    return value


def first_difference(expected: Any, actual: Any, path: str = "$") -> str | None:
    if type(expected) is not type(actual):
        return f"{path}: 类型不同 expected={type(expected).__name__}, actual={type(actual).__name__}"
    if isinstance(expected, dict):
        expected_keys, actual_keys = set(expected), set(actual)
        if expected_keys != actual_keys:
            return f"{path}: 字段不同 missing={sorted(expected_keys-actual_keys)}, extra={sorted(actual_keys-expected_keys)}"
        for key in expected:
            difference = first_difference(expected[key], actual[key], f"{path}.{key}")
            if difference:
                return difference
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            return f"{path}: 数组长度不同 expected={len(expected)}, actual={len(actual)}"
        for index, (expected_item, actual_item) in enumerate(zip(expected, actual)):
            difference = first_difference(expected_item, actual_item, f"{path}[{index}]")
            if difference:
                return difference
    elif expected != actual:
        return f"{path}: expected={expected!r}, actual={actual!r}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="验证复杂排期测试套件的实际输出")
    parser.add_argument("--suite-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--actual-root", type=Path, help="包含 CASE_ID/actual.json 的目录；省略时检查模板会被拒绝")
    args = parser.parse_args()
    suite = args.suite_root.resolve()
    manifest = load(suite / "manifest.json")
    failures = []
    for case in manifest["cases"]:
        case_id = case["case_id"]
        expected = load(suite / case_id / "expected.json")
        actual_path = (args.actual_root / case_id / "actual.json") if args.actual_root else (suite / case_id / "actual_template.json")
        try:
            actual = load(actual_path)
            if actual.get("case_id") != case_id:
                failures.append((case_id, f"case_id 不匹配: {actual.get('case_id')!r}"))
                continue
            expected_view = {field: expected.get(field) for field in FIELDS}
            actual_view = {field: actual.get(field) for field in FIELDS}
            difference = first_difference(expected_view, actual_view)
            if difference:
                failures.append((case_id, difference))
            else:
                print(f"PASS {case_id}")
        except ValueError as exc:
            failures.append((case_id, str(exc)))
    if failures:
        print(f"FAIL: {len(failures)} case(s)")
        for case_id, message in failures:
            print(f"- {case_id}: {message}")
        return 1
    print(f"ALL PASS: {manifest['case_count']} cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
