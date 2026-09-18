from __future__ import annotations

import copy
import random
from collections import Counter

from yuxi.schedule.audit.engine import audit_schedule
from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22
from yuxi.schedule.contracts.canonical_v2_8 import CanonicalScheduleV28
from yuxi.schedule.importers.canonical_v2_2 import import_canonical_schedule_v2_2
from yuxi.schedule.importers.canonical_v2_8 import import_canonical_schedule_v2_8
from yuxi.schedule.domain.models import ScheduleDependency
from yuxi.schedule.network import DependencyNetwork

FROZEN_LAG_POLICY = "UNIFIED_PROJECT_CALENDAR_WORKING_MINUTES"


def _audit(payload: dict):
    contract = CanonicalScheduleV22.model_validate(payload)
    schedule = import_canonical_schedule_v2_2(contract)
    return audit_schedule(schedule, schedule_snapshot_id="snapshot-test", audit_run_id="audit-test")


def _audit_v28(payload: dict):
    document = copy.deepcopy(payload)
    document["schema_version"] = "canonical_schedule_v2.8"
    document["semantics"]["lag_calendar_policy"] = "SUCCESSOR_TASK_CALENDAR"
    document["resources"] = []
    document["assignments"] = []
    task_types = {task["task_id"]: task["task_type"] for task in document["tasks"]}
    document["dependencies"] = [
        dependency
        for dependency in document["dependencies"]
        if task_types[dependency["predecessor_task_id"]] != "summary"
        and task_types[dependency["successor_task_id"]] != "summary"
    ]
    for dependency in document["dependencies"]:
        dependency["lag_calendar_policy"] = "SUCCESSOR_TASK_CALENDAR"
    contract = CanonicalScheduleV28.model_validate(document)
    schedule = import_canonical_schedule_v2_8(contract)
    return audit_schedule(
        schedule,
        schedule_snapshot_id="snapshot-test",
        audit_run_id="audit-test",
    )


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


def _frozen_payload(canonical_schedule_payload: dict) -> dict:
    payload = copy.deepcopy(canonical_schedule_payload)
    payload["semantics"]["lag_calendar_policy"] = FROZEN_LAG_POLICY
    for dependency in payload["dependencies"]:
        dependency["lag_calendar_policy"] = FROZEN_LAG_POLICY
    return payload


def test_frozen_lag_policy_checks_positive_lag_relations(canonical_schedule_payload: dict) -> None:
    payload = _frozen_payload(canonical_schedule_payload)
    positive_lag_count = sum(dependency["lag_minutes"] != 0 for dependency in payload["dependencies"])

    execution = _audit(payload)
    rules = {finding.rule_id for finding in execution.findings}

    assert execution.result.dependency_date_checks.model_dump()["checked"] == 68 + positive_lag_count
    assert execution.result.dependency_date_checks.skipped == 0
    assert "LAG_CALENDAR_POLICY_UNSPECIFIED" not in rules


def test_frozen_lag_policy_detects_positive_lag_violation(canonical_schedule_payload: dict) -> None:
    payload = _frozen_payload(canonical_schedule_payload)
    lag_dependency = next(dependency for dependency in payload["dependencies"] if dependency["lag_minutes"] != 0)
    successor = next(task for task in payload["tasks"] if task["task_id"] == lag_dependency["successor_task_id"])
    if lag_dependency["type"] in {"FS", "SS"}:
        successor["planned_start"] = "2000-01-01T08:00:00+08:00"
    else:
        successor["planned_finish"] = "2000-01-01T17:00:00+08:00"

    execution = _audit(payload)
    violation = next(item for item in execution.findings if item.rule_id == "LAG_DATE_VIOLATION")

    assert lag_dependency["dependency_id"] in violation.object_refs
    assert execution.result.dependency_date_checks.violation_count >= 1


def test_frozen_lag_policy_checks_negative_lag(canonical_schedule_payload: dict) -> None:
    payload = _frozen_payload(canonical_schedule_payload)
    lag_dependency = next(dependency for dependency in payload["dependencies"] if dependency["lag_minutes"] != 0)
    lag_dependency["lag_minutes"] = -120

    execution = _audit(payload)

    assert execution.result.dependency_date_checks.skipped == 0
    assert execution.result.dependency_date_checks.skipped_reasons == {}
    assert "NEGATIVE_DEPENDENCY_LAG_UNSUPPORTED" not in execution.result.capabilities["cpm_recalculation"].reasons
    assert "LAG_CALENDAR_POLICY_UNSPECIFIED" not in {finding.rule_id for finding in execution.findings}


def test_frozen_lag_policy_detects_negative_lag_violation(canonical_schedule_payload: dict) -> None:
    payload = _frozen_payload(canonical_schedule_payload)
    lag_dependency = next(dependency for dependency in payload["dependencies"] if dependency["lag_minutes"] != 0)
    lag_dependency["lag_minutes"] = -120
    successor = next(task for task in payload["tasks"] if task["task_id"] == lag_dependency["successor_task_id"])
    if lag_dependency["type"] in {"FS", "SS"}:
        successor["planned_start"] = "2000-01-01T08:00:00+08:00"
    else:
        successor["planned_finish"] = "2000-01-01T17:00:00+08:00"

    execution = _audit(payload)
    violation = next(item for item in execution.findings if item.rule_id == "LAG_DATE_VIOLATION")

    assert lag_dependency["dependency_id"] in violation.object_refs
    assert execution.result.dependency_date_checks.violation_count >= 1


def test_network_defects_remain_auditable(canonical_schedule_payload: dict) -> None:
    payload = copy.deepcopy(canonical_schedule_payload)
    dependency = payload["dependencies"][0]
    dependency["successor_task_id"] = dependency["predecessor_task_id"]

    execution = _audit(payload)
    rules = {finding.rule_id for finding in execution.findings}

    assert "SELF_DEPENDENCY" in rules
    assert "DEPENDENCY_CYCLE" in rules
    assert "SELF_DEPENDENCY" in execution.result.capabilities["cpm_recalculation"].reasons


def test_cpm_capability_blocks_summary_without_direct_children(canonical_schedule_payload: dict) -> None:
    payload = copy.deepcopy(canonical_schedule_payload)
    summary_ids = {task["task_id"] for task in payload["tasks"] if task["task_type"] == "summary"}
    summary_id = next(
        task_id for task_id in summary_ids if any(task["parent_task_id"] == task_id for task in payload["tasks"])
    )
    for task in payload["tasks"]:
        if task["parent_task_id"] == summary_id:
            task["parent_task_id"] = None

    execution = _audit(payload)

    assert "SUMMARY_WITHOUT_CHILDREN" in execution.result.capabilities["cpm_recalculation"].reasons


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


def test_v28_isolated_inactive_task_is_excluded_without_blocking_cpm(
    canonical_schedule_payload: dict,
) -> None:
    payload = copy.deepcopy(canonical_schedule_payload)
    inactive = next(task for task in payload["tasks"] if task["task_type"] == "activity")
    inactive.update(
        {
            "active": False,
            "percent_complete": 0,
            "actual_start": None,
            "actual_finish": None,
        }
    )
    payload["dependencies"] = [
        dependency
        for dependency in payload["dependencies"]
        if inactive["task_id"]
        not in {
            dependency["predecessor_task_id"],
            dependency["successor_task_id"],
        }
    ]

    execution = _audit_v28(payload)

    assert execution.result.capabilities["cpm_recalculation"].allowed is True
    inactive_finding = next(
        item for item in execution.findings if item.rule_id == "INACTIVE_TASK_EXCLUDED"
    )
    assert inactive_finding.object_refs == (inactive["task_id"],)


def test_v28_inactive_dependency_is_audited_and_blocks_cpm(
    canonical_schedule_payload: dict,
) -> None:
    payload = copy.deepcopy(canonical_schedule_payload)
    dependency = next(
        item
        for item in payload["dependencies"]
        if all(
            next(
                task
                for task in payload["tasks"]
                if task["task_id"] == task_id
            )["task_type"]
            == "activity"
            for task_id in (item["predecessor_task_id"], item["successor_task_id"])
        )
    )
    inactive = next(
        task
        for task in payload["tasks"]
        if task["task_id"] == dependency["successor_task_id"]
    )
    inactive.update(
        {
            "active": False,
            "percent_complete": 0,
            "actual_start": None,
            "actual_finish": None,
        }
    )

    execution = _audit_v28(payload)

    capability = execution.result.capabilities["cpm_recalculation"]
    assert capability.allowed is False
    assert "INACTIVE_TASK_DEPENDENCIES" in capability.reasons
    finding = next(
        item
        for item in execution.findings
        if item.rule_id == "INACTIVE_TASK_DEPENDENCY"
        and dependency["dependency_id"] in item.object_refs
    )
    assert finding.evidence["inactive_task_ids"] == [inactive["task_id"]]
    assert execution.result.dependency_date_checks.skipped_reasons[
        "INACTIVE_TASK_DEPENDENCY_REQUIRES_DECISION"
    ] >= 1
