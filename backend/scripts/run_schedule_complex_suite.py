"""Run the frozen complex Schedule suite without mutating its source package."""

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Yuxi complex Schedule suite cases")
    parser.add_argument("--suite-root", type=Path, required=True)
    parser.add_argument("--actual-root", type=Path, required=True)
    parser.add_argument("--case-id")
    args = parser.parse_args(argv)

    suite_root = args.suite_root.resolve()
    actual_root = args.actual_root.resolve()
    if actual_root == suite_root or suite_root in actual_root.parents:
        parser.error("--actual-root must be outside --suite-root")

    verify_suite_package(suite_root)
    manifest = json.loads((suite_root / "manifest.json").read_text(encoding="utf-8"))
    case_ids = [case["case_id"] for case in manifest["cases"]]
    if args.case_id:
        if args.case_id not in case_ids:
            parser.error(f"unknown case id: {args.case_id}")
        case_ids = [args.case_id]

    gates = []
    for case_id in case_ids:
        document = json.loads((suite_root / case_id / "input.json").read_text(encoding="utf-8"))
        expected = json.loads((suite_root / case_id / "expected.json").read_text(encoding="utf-8"))
        result = execute_suite_case(document)
        oracle_match = suite_oracle_matches(result.actual, expected)
        execution_status = result.actual["status"]
        if execution_status == "UNSUPPORTED":
            gate_status = "UNSUPPORTED"
        elif oracle_match:
            gate_status = "PASSED"
        else:
            gate_status = "FAILED"
        reasons = (
            sorted({issue["code"] for issue in result.actual["issues"]})
            if gate_status == "UNSUPPORTED"
            else ([] if oracle_match else ["ORACLE_MISMATCH"])
        )
        gate = {
            "case_id": case_id,
            "gate_status": gate_status,
            "execution_status": execution_status,
            "oracle_match": oracle_match,
            "ui_export_eligible": (
                oracle_match
                and execution_status in {"SUCCEEDED", "SUCCEEDED_WITH_ISSUES"}
                and result.canonical is not None
                and result.engine_result is not None
                and result.engine_result.get("status") == "calculated"
            ),
            "reasons": reasons,
        }
        case_root = actual_root / case_id
        case_root.mkdir(parents=True, exist_ok=True)
        _write_json(case_root / "actual.json", result.actual)
        _write_json(case_root / "yuxi_audit.json", result.yuxi_audit)
        _write_json(case_root / "gate.json", gate)
        gates.append(gate)
        print(f"{case_id}: {gate_status} ({execution_status})")

    summary = {
        "suite_id": manifest["suite_id"],
        "case_count": len(gates),
        "passed": sum(gate["gate_status"] == "PASSED" for gate in gates),
        "unsupported": sum(gate["gate_status"] == "UNSUPPORTED" for gate in gates),
        "failed": sum(gate["gate_status"] == "FAILED" for gate in gates),
        "ui_export_eligible": sum(gate["ui_export_eligible"] for gate in gates),
        "cases": gates,
    }
    actual_root.mkdir(parents=True, exist_ok=True)
    _write_json(actual_root / "suite_run_summary.json", summary)
    return 1 if summary["failed"] else 0


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
