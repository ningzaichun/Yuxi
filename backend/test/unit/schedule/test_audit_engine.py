from __future__ import annotations

import copy
import random
from collections import Counter

from yuxi.schedule.audit.engine import audit_schedule
from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22
from yuxi.schedule.importers.canonical_v2_2 import import_canonical_schedule_v2_2
from yuxi.schedule.domain.models import ScheduleDependency
from yuxi.schedule.network import DependencyNetwork


def _audit(payload: dict):
    contract = CanonicalScheduleV22.model_validate(payload)
    schedule = import_canonical_schedule_v2_2(contract)
    return audit_schedule(schedule, schedule_snapshot_id="snapshot-test", audit_run_id="audit-test")


def test_golden_schedule_audit_matches_frozen_counts(canonical_schedule_payload: dict) -> None:
    execution = _audit(canonical_schedule_payload)
    rules = Counter(finding.rule_id for finding in execution.findings)

    assert execution.result.statistics["tasks"] == 90
    assert execution.result.statistics["dependencies"] == 90
    assert execution.result.dependency_date_checks.model_dump() == {
        "checked": 68,
        "skipped": 22,
        "violation_count": 0,
        "skipped_reasons": {"LAG_CALENDAR_POLICY_UNSPECIFIED": 22},
    }
    assert rules["OPEN_START"] == 2
    assert rules["OPEN_FINISH"] == 15
    assert rules["SUMMARY_TASK_DEPENDENCY"] == 4
    assert rules["ZERO_LAG_DATE_VIOLATION"] == 0
    assert rules["LAG_CALENDAR_POLICY_UNSPECIFIED"] == 1
    assert rules["STATISTICS_MISMATCH"] == 0
    assert rules["SOURCE_CAPABILITY_MISMATCH"] == 0


def test_audit_semantics_do_not_depend_on_source_array_order(canonical_schedule_payload: dict) -> None:
    original = _audit(canonical_schedule_payload)
    shuffled_payload = copy.deepcopy(canonical_schedule_payload)
    random.Random(20260811).shuffle(shuffled_payload["tasks"])
    random.Random(20260812).shuffle(shuffled_payload["dependencies"])
    random.Random(20260813).shuffle(shuffled_payload["resources"])
    shuffled = _audit(shuffled_payload)

    assert shuffled.result.model_dump(exclude={"created_at"}) == original.result.model_dump(exclude={"created_at"})
    assert shuffled.findings == original.findings


def test_zero_lag_violation_is_independently_detected(canonical_schedule_payload: dict) -> None:
    payload = copy.deepcopy(canonical_schedule_payload)
    zero_lag = next(dependency for dependency in payload["dependencies"] if dependency["lag_minutes"] == 0)
    successor = next(task for task in payload["tasks"] if task["task_id"] == zero_lag["successor_task_id"])
    predecessor = next(task for task in payload["tasks"] if task["task_id"] == zero_lag["predecessor_task_id"])
    if zero_lag["type"] in {"FS", "SS"}:
        successor["planned_start"] = "2000-01-01T08:00:00+08:00"
    else:
        successor["planned_finish"] = "2000-01-01T17:00:00+08:00"

    execution = _audit(payload)
    violation = next(item for item in execution.findings if item.rule_id == "ZERO_LAG_DATE_VIOLATION")

    assert execution.result.dependency_date_checks.violation_count == 1
    assert violation.object_refs == (zero_lag["dependency_id"],)
    assert predecessor["name"] not in violation.message


def test_network_defects_remain_auditable(canonical_schedule_payload: dict) -> None:
    payload = copy.deepcopy(canonical_schedule_payload)
    dependency = payload["dependencies"][0]
    dependency["successor_task_id"] = dependency["predecessor_task_id"]

    execution = _audit(payload)
    rules = {finding.rule_id for finding in execution.findings}

    assert "SELF_DEPENDENCY" in rules
    assert "DEPENDENCY_CYCLE" in rules
    assert "SELF_DEPENDENCY" in execution.result.capabilities["cpm_recalculation"].reasons


def test_dependency_cycle_detection_supports_the_5000_task_boundary() -> None:
    task_ids = {f"task-{index}" for index in range(5000)}
    dependencies = tuple(
        ScheduleDependency(
            dependency_id=f"dependency-{index}",
            predecessor_task_id=f"task-{index}",
            successor_task_id=f"task-{(index + 1) % 5000}",
            relation_type="FS",
            lag_minutes=0,
            lag_calendar_policy="NOT_APPLICABLE_ZERO_LAG",
        )
        for index in range(5000)
    )

    components = DependencyNetwork(task_ids, dependencies).cyclic_components()

    assert len(components) == 1
    assert len(components[0]) == 5000
