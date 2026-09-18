"""Export frozen Schedule suite cases as UI-importable Canonical JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = BACKEND_ROOT / "package"
for import_root in (BACKEND_ROOT, PACKAGE_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from test.support.schedule_suite import (  # noqa: E402
    execute_suite_case,
    suite_oracle_matches,
    verify_suite_package,
)
from yuxi.schedule.audit.context import AuditContext  # noqa: E402
from yuxi.schedule.audit.rules.dependency import audit_dependencies  # noqa: E402
from yuxi.schedule.contracts.canonical import parse_canonical_schedule  # noqa: E402
from yuxi.schedule.importers import import_canonical_schedule  # noqa: E402

def project_suite_reference(canonical: Any, engine_result: dict[str, Any]) -> Any:
    """Project a verified Suite Engine result into an importable reference schedule."""
    if engine_result.get("status") != "calculated":
        raise ValueError("Suite Reference projection requires a calculated Engine Result")

    payload = canonical.model_dump(mode="json", exclude_none=False)
    dates_by_task = {item["task_id"]: item for item in engine_result["task_dates"]}
    if set(dates_by_task) != {task["task_id"] for task in payload["tasks"]}:
        raise ValueError("Engine Result task set does not match the Canonical schedule")

    payload["source"]["extraction_method"] = "YUXI_TEST_SUITE_ENGINE_REFERENCE"
    payload["semantics"]["source_dates_preserved"] = False
    payload["project"]["planned_start"] = engine_result["project_start"]
    payload["project"]["planned_finish"] = engine_result["finish_after"]
    payload["project"]["planned_date_source"] = "YUXI_SUITE_ENGINE_REFERENCE"
    payload["project"]["source_project_summary"]["start"] = engine_result["project_start"]
    payload["project"]["source_project_summary"]["finish"] = engine_result["finish_after"]

    for task in payload["tasks"]:
        engine_dates = dates_by_task[task["task_id"]]
        task["planned_start"] = engine_dates["early_start"]
        task["planned_finish"] = engine_dates["early_finish"]
        task["source_calculation"] = {
            "early_start": engine_dates["early_start"],
            "early_finish": engine_dates["early_finish"],
            "late_start": engine_dates["late_start"],
            "late_finish": engine_dates["late_finish"],
            "total_slack_minutes": engine_dates["total_slack_minutes"],
            "free_slack_minutes": engine_dates["free_slack_minutes"],
            "critical": engine_dates["critical"],
        }

    provisional = parse_canonical_schedule(payload)
    context = AuditContext.build(import_canonical_schedule(provisional))
    source_schedule_violations = [
        {
            "dependency_id": finding.object_refs[0],
            "code": "SOURCE_DATE_VIOLATION",
        }
        for finding in audit_dependencies(context)
        if finding.rule_id in {"ZERO_LAG_DATE_VIOLATION", "LAG_DATE_VIOLATION"}
    ]
    if len(source_schedule_violations) != context.dependency_date_checks.violation_count:
        raise ValueError("Suite Reference dependency validation conclusions are inconsistent")

    default_calendar = context.lag_calendars and context.lag_calendars.get(provisional.project.default_calendar_id)
    if default_calendar is not None:
        payload["project"]["source_project_summary"]["duration_minutes"] = default_calendar.working_minutes_between(
            provisional.project.planned_start,
            provisional.project.planned_finish,
        )

    capabilities = {name: capability.model_dump(mode="json") for name, capability in context.capabilities.items()}
    payload["statistics"] = context.statistics
    payload["capabilities"] = capabilities
    payload["validation"]["capabilities"] = capabilities
    payload["validation"]["summary"]["recalculation_allowed"] = capabilities["cpm_recalculation"]["allowed"]
    payload["validation"]["network_quality"]["source_schedule_violations"] = source_schedule_violations
    return parse_canonical_schedule(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export Yuxi complex Schedule suite cases for UI import")
    parser.add_argument("--suite-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--case-id")
    args = parser.parse_args(argv)

    suite_root = args.suite_root.resolve()
    output_root = args.output_root.resolve()
    if output_root == suite_root or suite_root in output_root.parents:
        parser.error("--output-root must be outside --suite-root")

    verify_suite_package(suite_root)
    manifest = json.loads((suite_root / "manifest.json").read_text(encoding="utf-8"))
    case_ids = [case["case_id"] for case in manifest["cases"]]
    if args.case_id:
        if args.case_id not in case_ids:
            parser.error(f"unknown case id: {args.case_id}")
        case_ids = [args.case_id]

    output_root.mkdir(parents=True, exist_ok=True)
    exported = 0
    skipped = 0
    for case_id in case_ids:
        document = json.loads((suite_root / case_id / "input.json").read_text(encoding="utf-8"))
        expected = json.loads((suite_root / case_id / "expected.json").read_text(encoding="utf-8"))
        result = execute_suite_case(document)
        if result.actual["status"] not in {"SUCCEEDED", "SUCCEEDED_WITH_ISSUES"}:
            issue_codes = ", ".join(issue["code"] for issue in result.actual["issues"])
            detail = f": {issue_codes}" if issue_codes else ""
            print(f"{case_id}: SKIPPED ({result.actual['status']}{detail})")
            skipped += 1
            continue
        if result.canonical is None or result.engine_result is None:
            print(f"{case_id}: SKIPPED (MISSING_CALCULATED_RESULT)")
            skipped += 1
            continue
        if not suite_oracle_matches(result.actual, expected):
            print(f"{case_id}: SKIPPED (ORACLE_MISMATCH)")
            skipped += 1
            continue

        canonical = result.canonical
        if not document["oracle"]["is_microsoft_project_observation"]:
            canonical = project_suite_reference(canonical, result.engine_result)
        payload = canonical.model_dump(mode="json", exclude_none=False)
        parse_canonical_schedule(payload)
        target = output_root / f"{case_id}.canonical.json"
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"{case_id}: EXPORTED {target}")
        exported += 1

    print(f"Export complete: {exported} exported, {skipped} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
