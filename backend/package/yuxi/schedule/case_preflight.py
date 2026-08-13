"""Privacy-safe structural preflight for reusable Schedule business cases."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, Literal

from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22

CaseType = Literal["real", "sanitized", "synthetic"]
MIGRATION_DIFFERENCE_FIELDS = {
    "tasks.activity",
    "tasks.max_outline_level",
    "summary_dependencies.predecessor_only",
    "summary_dependencies.successor_only",
    "summary_dependencies.both",
    "dependencies.types.FS",
    "dependencies.types.SS",
    "dependencies.types.FF",
    "dependencies.types.SF",
}


def preflight_schedule_case(
    source: dict[str, Any],
    *,
    case_type: CaseType,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a case and return a report without business names or object IDs."""

    contract = CanonicalScheduleV22.model_validate(source)
    profile = _structural_profile(contract)
    baseline_profile = (
        _structural_profile(CanonicalScheduleV22.model_validate(baseline)) if baseline is not None else None
    )
    differences = _structural_differences(baseline_profile, profile) if baseline_profile else []
    migration_differences = [
        difference for difference in differences if difference["field"] in MIGRATION_DIFFERENCE_FIELDS
    ]
    source_declares_synthetic = contract.source.format == "SYNTHETIC_TEST_DATA"

    protocol_blocking_reasons = []
    if baseline_profile is None:
        protocol_blocking_reasons.append("BASELINE_CASE_REQUIRED")
    elif not migration_differences:
        protocol_blocking_reasons.append("STRUCTURAL_DIFFERENCE_REQUIRED")
    if profile["summary_dependencies"]["total"] == 0:
        protocol_blocking_reasons.append("SUMMARY_DEPENDENCY_REQUIRED")
    business_blocking_reasons = list(protocol_blocking_reasons)
    if source_declares_synthetic:
        business_blocking_reasons.insert(0, "DECLARED_SYNTHETIC_SOURCE")
    elif case_type != "real":
        business_blocking_reasons.insert(0, "REAL_BUSINESS_SOURCE_REQUIRED")

    canonical = contract.model_dump(mode="json", exclude_none=False)
    canonical_bytes = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "preflight_schema_version": "schedule_case_preflight_v0",
        "case_type": case_type,
        "contract_valid": True,
        "content_sha256": f"sha256:{hashlib.sha256(canonical_bytes).hexdigest()}",
        "structural_signature": _structural_signature(profile),
        "profile": profile,
        "baseline_comparison": {
            "provided": baseline_profile is not None,
            "structurally_different": bool(differences),
            "differences": differences,
            "migration_relevant_differences": migration_differences,
        },
        "engineering_protocol_precheck_passed": not protocol_blocking_reasons,
        "engineering_protocol_blocking_reasons": protocol_blocking_reasons,
        "automated_migration_precheck_passed": not business_blocking_reasons,
        "blocking_reasons": business_blocking_reasons,
        "business_migration_eligible": case_type == "real" and not business_blocking_reasons,
        "case_limitations": (
            []
            if case_type == "real" and not source_declares_synthetic
            else ["CANNOT_REPLACE_INDEPENDENT_REAL_CASE_B"]
        ),
        "human_review_required": [
            "SOURCE_IS_INDEPENDENT_REAL_BUSINESS_CASE",
            "TASK_NAMES_ARE_READABLE_IN_WORKBENCH",
            "BUSINESS_USER_CONFIRMS_DEPENDENCY_REPLACEMENT",
        ],
    }


def _structural_profile(contract: CanonicalScheduleV22) -> dict[str, Any]:
    task_types = Counter(task.task_type for task in contract.tasks)
    relation_types = Counter(dependency.type for dependency in contract.dependencies)
    lag_signs = Counter(
        "positive" if dependency.lag_minutes > 0 else "negative" if dependency.lag_minutes < 0 else "zero"
        for dependency in contract.dependencies
    )
    summary_ids = {task.task_id for task in contract.tasks if task.task_type == "summary"}
    summary_positions = Counter()
    for dependency in contract.dependencies:
        predecessor_summary = dependency.predecessor_task_id in summary_ids
        successor_summary = dependency.successor_task_id in summary_ids
        if predecessor_summary and successor_summary:
            summary_positions["both"] += 1
        elif predecessor_summary:
            summary_positions["predecessor_only"] += 1
        elif successor_summary:
            summary_positions["successor_only"] += 1

    task_names = [task.name for task in contract.tasks]
    return {
        "tasks": {
            "total": len(contract.tasks),
            "summary": task_types["summary"],
            "activity": task_types["activity"],
            "max_outline_level": max((task.outline_level for task in contract.tasks), default=0),
        },
        "dependencies": {
            "total": len(contract.dependencies),
            "types": {name: relation_types[name] for name in ("FS", "SS", "FF", "SF")},
            "lag_signs": {name: lag_signs[name] for name in ("zero", "positive", "negative")},
        },
        "summary_dependencies": {
            "total": sum(summary_positions.values()),
            "predecessor_only": summary_positions["predecessor_only"],
            "successor_only": summary_positions["successor_only"],
            "both": summary_positions["both"],
        },
        "supporting_objects": {
            "calendars": len(contract.calendars),
            "resources": len(contract.resources),
            "assignments": len(contract.assignments),
        },
        "name_quality_signals": {
            "empty_task_names": sum(not name.strip() for name in task_names),
            "replacement_characters": sum(name.count("\ufffd") for name in task_names),
        },
    }


def _structural_signature(profile: dict[str, Any]) -> str:
    payload = json.dumps(profile, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _structural_differences(
    baseline: dict[str, Any],
    current: dict[str, Any],
) -> list[dict[str, Any]]:
    differences = []
    for section in ("tasks", "dependencies", "summary_dependencies", "supporting_objects"):
        for field, current_value in current[section].items():
            baseline_value = baseline[section][field]
            if isinstance(current_value, dict):
                for child_field, child_value in current_value.items():
                    baseline_child = baseline_value[child_field]
                    if child_value != baseline_child:
                        differences.append(
                            {
                                "field": f"{section}.{field}.{child_field}",
                                "baseline": baseline_child,
                                "current": child_value,
                            }
                        )
            elif current_value != baseline_value:
                differences.append(
                    {
                        "field": f"{section}.{field}",
                        "baseline": baseline_value,
                        "current": current_value,
                    }
                )
    return differences
