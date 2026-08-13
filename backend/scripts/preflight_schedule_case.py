"""Validate and profile a Schedule case without exposing business content."""

from __future__ import annotations

import json
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from yuxi.schedule.case_preflight import preflight_schedule_case


def main() -> None:
    parser = ArgumentParser(description="预检 canonical_schedule_v2.2 案例并输出隐私安全的结构报告")
    parser.add_argument("source", type=Path, help="待预检的 canonical_schedule_v2.2 JSON")
    parser.add_argument("--case-type", required=True, choices=("real", "sanitized", "synthetic"))
    parser.add_argument("--baseline", type=Path, help="用于结构差异比较的基线案例")
    parser.add_argument(
        "--purpose",
        choices=("business-migration", "protocol-regression"),
        default="business-migration",
        help="业务迁移要求真实案例；协议回归允许结构合格的 synthetic 案例",
    )
    args = parser.parse_args()

    try:
        source = _read_json(args.source)
        baseline = _read_json(args.baseline) if args.baseline else None
        report = preflight_schedule_case(source, case_type=args.case_type, baseline=baseline)
    except json.JSONDecodeError as exc:
        report = {
            "contract_valid": False,
            "error_code": "INVALID_JSON",
            "location": {"line": exc.lineno, "column": exc.colno},
        }
    except ValidationError as exc:
        report = {
            "contract_valid": False,
            "error_code": "INVALID_CANONICAL_SCHEDULE",
            "error_count": exc.error_count(),
            "locations": [_safe_location(error["loc"]) for error in exc.errors()],
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["contract_valid"]:
        raise SystemExit(2)
    passed_field = (
        "automated_migration_precheck_passed"
        if args.purpose == "business-migration"
        else "engineering_protocol_precheck_passed"
    )
    raise SystemExit(0 if report[passed_field] else 3)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_location(location: tuple[str | int, ...]) -> str:
    return ".".join(str(part) for part in location)


if __name__ == "__main__":
    main()
