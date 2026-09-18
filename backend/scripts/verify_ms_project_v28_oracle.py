"""Validate the frozen Microsoft Project v2.8 semantics Oracle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ORACLE_PATH = (
    BACKEND_ROOT
    / "test"
    / "data"
    / "schedule"
    / "microsoft_project_v28_inactive_summary_dependency_oracle.json"
)

EXPECTED_DECISIONS = {
    "inactive_progress_policy": "REJECT_INACTIVE_WITH_ACTUAL_OR_PROGRESS_FACTS",
    "inactive_rollup_policy": "EXCLUDE_INACTIVE_DESCENDANTS",
    "inactive_dependency_policy": (
        "PRESERVE_SOURCE_RELATION_AND_REQUIRE_EXPLICIT_LEAF_REPLACEMENT"
    ),
    "summary_dependency_policy": (
        "PRESERVE_SOURCE_RELATION_AND_REQUIRE_EXPLICIT_LEAF_REPLACEMENT"
    ),
    "microsoft_project_difference": (
        "Microsoft Project accepts and schedules through inactive and summary relations, "
        "but Yuxi does not silently infer replacement leaf relations."
    ),
}

EXPECTED_MUTATIONS = {
    ("SET_INACTIVE", "task:3"): True,
    ("SET_INACTIVE", "task:6"): False,
    ("SET_INACTIVE", "task:9"): True,
    ("SET_DEPENDENCY:SUMMARY_TO_ACTIVITY", "dependency:5"): True,
    ("SET_DEPENDENCY:ACTIVITY_TO_SUMMARY", "dependency:7"): True,
    ("SET_DEPENDENCY:SUMMARY_TO_SUMMARY", "dependency:10"): True,
}


def load_oracle(path: Path = DEFAULT_ORACLE_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_oracle(oracle: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if oracle.get("schema_version") != "microsoft_project_schedule_semantics_oracle_v1":
        errors.append("SCHEMA_VERSION_UNSUPPORTED")

    observation = oracle.get("external_observation")
    if not isinstance(observation, dict):
        return [*errors, "EXTERNAL_OBSERVATION_REQUIRED"]
    if observation.get("observation_status") != "CAPTURED_BY_MS_PROJECT_COM":
        errors.append("OBSERVATION_STATUS_INVALID")
    if observation.get("independent_recapture_count", 0) < 2:
        errors.append("INDEPENDENT_RECAPTURE_REQUIRED")
    if observation.get("save_reopen_mismatches") != []:
        errors.append("SAVE_REOPEN_MISMATCH")

    mutations = {
        (item.get("operation"), item.get("object_ref")): item.get("accepted")
        for item in observation.get("mutation_outcomes", [])
    }
    if mutations != EXPECTED_MUTATIONS:
        errors.append("MUTATION_OUTCOMES_MISMATCH")

    facts = {item.get("task_id"): item for item in observation.get("task_facts", [])}
    required_fact_ids = {
        "task:2",
        "task:3",
        "task:4",
        "task:6",
        "task:7",
        "task:9",
        "task:11",
        "task:14",
        "task:15",
        "task:16",
        "task:19",
        "task:22",
    }
    if not required_fact_ids <= facts.keys():
        errors.append("REQUIRED_TASK_FACTS_MISSING")
    else:
        if facts["task:3"].get("active") is not False or facts["task:3"].get("critical") is not False:
            errors.append("INACTIVE_CHAIN_FACT_MISMATCH")
        inactive_relations = (
            facts["task:2"].get("successors"),
            facts["task:3"].get("predecessors"),
            facts["task:3"].get("successors"),
            facts["task:4"].get("predecessors"),
        )
        if inactive_relations != ("3", "2", "4", "3"):
            errors.append("INACTIVE_RELATION_FACT_MISMATCH")
        if (
            facts["task:7"].get("finish") != "2026-09-08T17:00:00+08:00"
            or facts["task:9"].get("finish") != "2026-09-25T17:00:00+08:00"
        ):
            errors.append("INACTIVE_ROLLUP_FACT_MISMATCH")
        if (facts["task:11"].get("successors"), facts["task:14"].get("predecessors")) != ("14", "11"):
            errors.append("SUMMARY_TO_ACTIVITY_FACT_MISMATCH")
        if (facts["task:15"].get("successors"), facts["task:16"].get("predecessors")) != ("16", "15"):
            errors.append("ACTIVITY_TO_SUMMARY_FACT_MISMATCH")
        if (facts["task:19"].get("successors"), facts["task:22"].get("predecessors")) != ("22", "19"):
            errors.append("SUMMARY_TO_SUMMARY_FACT_MISMATCH")

    if oracle.get("business_decisions") != EXPECTED_DECISIONS:
        errors.append("BUSINESS_DECISIONS_MISMATCH")
    expected = oracle.get("expected", {})
    if expected.get("confirmation_status") != "CONFIRMED_BY_MS_PROJECT":
        errors.append("CONFIRMATION_STATUS_INVALID")
    if expected.get("microsoft_project_version") != observation.get("microsoft_project_version"):
        errors.append("PROJECT_VERSION_MISMATCH")
    return errors


def main() -> int:
    errors = validate_oracle(load_oracle())
    print(json.dumps({"status": "PASSED" if not errors else "FAILED", "errors": errors}, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
