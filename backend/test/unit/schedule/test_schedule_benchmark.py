from __future__ import annotations

from yuxi.schedule.audit.engine import audit_schedule
from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22
from yuxi.schedule.contracts.canonical_v2_4 import CanonicalScheduleV24
from yuxi.schedule.forward_engine import (
    MULTI_CALENDAR_ENGINE_PROFILE_ID,
    calculate_minimal_forward_schedule,
)
from yuxi.schedule.importers.canonical_v2_2 import import_canonical_schedule_v2_2

from schedule_benchmark import (
    build_multi_calendar_schedule_benchmark_payload,
    build_schedule_benchmark_payload,
)


def test_m4_5_benchmark_payload_is_valid_and_matches_calculated_facts() -> None:
    contract = CanonicalScheduleV22.model_validate(build_schedule_benchmark_payload())
    schedule = import_canonical_schedule_v2_2(contract)
    execution = audit_schedule(
        schedule,
        schedule_snapshot_id="benchmark-snapshot",
        audit_run_id="benchmark-audit",
    )

    assert len(contract.tasks) == 1_000
    assert len(contract.dependencies) == 5_000
    assert execution.result.statistics["tasks"] == 1_000
    assert execution.result.statistics["dependencies"] == 5_000
    assert execution.result.dependency_date_checks.model_dump() == {
        "checked": 5_000,
        "skipped": 0,
        "violation_count": 0,
        "skipped_reasons": {},
    }
    assert not {item.rule_id for item in execution.findings} & {
        "STATISTICS_MISMATCH",
        "SOURCE_CAPABILITY_MISMATCH",
        "SELF_DEPENDENCY",
        "DUPLICATE_RELATION",
        "DEPENDENCY_CYCLE",
        "ZERO_LAG_DATE_VIOLATION",
    }


def test_multi_calendar_benchmark_payload_runs_profile_v11() -> None:
    contract = CanonicalScheduleV24.model_validate(
        build_multi_calendar_schedule_benchmark_payload()
    )

    result = calculate_minimal_forward_schedule(
        contract,
        engine_profile_id=MULTI_CALENDAR_ENGINE_PROFILE_ID,
    )

    assert result["status"] == "calculated"
    assert result["engine_version"] == "11.0.0"
    assert len(result["task_dates"]) == 1_000
