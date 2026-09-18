from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import test.support.schedule_suite as suite_support
from scripts.export_schedule_suite_canonical import main as export_suite
from scripts.rebuild_schedule_large_suite import main as rebuild_suite
from scripts.run_schedule_complex_suite import main as run_suite
from test.support.schedule_suite import (
    LARGE_SUITE_CONTRACT_VERSION,
    LargeScheduleEngineTestInputV2,
    execute_suite_case,
    normalize_suite_document,
    parse_suite_document,
    suite_oracle_matches,
)

SUITE_ROOT = Path(__file__).resolve().parents[4] / "Yuxi_大型复杂排期测试套件_v1"


def _load(case_id: str, name: str = "input.json") -> dict:
    return json.loads((SUITE_ROOT / case_id / name).read_text(encoding="utf-8"))


def _hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    }


def test_large_v2_contract_rejects_unknown_top_level_and_task_fields() -> None:
    document = _load("L01_EPC_FULL_LIFECYCLE")
    document["unknown_protocol_field"] = True

    with pytest.raises(ValidationError, match="unknown_protocol_field"):
        parse_suite_document(document)

    document = _load("L01_EPC_FULL_LIFECYCLE")
    document["tasks"][0]["unknown_task_field"] = True

    with pytest.raises(ValidationError, match="unknown_task_field"):
        parse_suite_document(document)


def test_large_v2_contract_preserves_explicit_wbs_and_outline_level() -> None:
    document = copy.deepcopy(_load("L06_SCALE_500_DAG"))
    document["resources"] = []
    document["assignments"] = []

    source = parse_suite_document(document)
    canonical, _ = normalize_suite_document(document)
    expected = {task.task_id: (task.wbs, task.outline_level) for task in source.tasks}

    assert isinstance(source, LargeScheduleEngineTestInputV2)
    assert {task.task_id: (task.wbs, task.outline_level) for task in canonical.tasks} == expected
    assert canonical.source.extraction_method == "YUXI_TEST_SUITE_V2_ADAPTER"


@pytest.mark.parametrize(
    ("case_id", "expected_reasons"),
    [
        (
            "L01_EPC_FULL_LIFECYCLE",
            {
                "LARGE_RESOURCE_CONFLICT_ORACLE_UNSUPPORTED",
                "RESOURCE_BASELINE_COMBINATION_UNSUPPORTED",
            },
        ),
        (
            "L02_MULTI_SITE_PARALLEL",
            {
                "CUMULATIVE_RESOURCE_OVERALLOCATION_ORACLE_MISMATCH",
                "WORKING_TIME_BOUNDARY_POLICY_ORACLE_MISMATCH",
            },
        ),
        ("L03_CALENDAR_SHIFT_STRESS", {"CROSS_MIDNIGHT_CALENDAR_UNSUPPORTED"}),
        (
            "L04_BASELINE_PROGRESS_FORECAST",
            {
                "LARGE_RESOURCE_CONFLICT_ORACLE_UNSUPPORTED",
                "RESOURCE_BASELINE_COMBINATION_UNSUPPORTED",
                "RESOURCE_PROGRESS_COMBINATION_UNSUPPORTED",
            },
        ),
    ],
)
def test_large_suite_unsupported_cases_fail_closed(case_id: str, expected_reasons: set[str]) -> None:
    result = execute_suite_case(_load(case_id))

    assert result.actual["status"] == "UNSUPPORTED"
    assert {issue["code"] for issue in result.actual["issues"]} == expected_reasons
    assert result.yuxi_audit["suite_contract_version"] == LARGE_SUITE_CONTRACT_VERSION
    assert result.engine_called is False
    assert result.canonical is None


def test_large_invalid_case_matches_frozen_oracle_without_calling_engine() -> None:
    result = execute_suite_case(_load("L05_BULK_INVALID_GUARDRAILS"))
    expected = _load("L05_BULK_INVALID_GUARDRAILS", "expected.json")

    assert result.actual["status"] == "VALIDATION_FAILED"
    assert suite_oracle_matches(result.actual, expected) is True
    assert result.yuxi_audit["suite_contract_version"] == LARGE_SUITE_CONTRACT_VERSION
    assert result.engine_called is False


@pytest.mark.parametrize(
    ("case_id", "expected_conflicts"),
    [
        ("L06_SCALE_500_DAG", 100),
    ],
)
def test_large_resource_cases_strictly_match_frozen_oracle(
    case_id: str,
    expected_conflicts: int,
) -> None:
    document = _load(case_id)
    expected = _load(case_id, "expected.json")

    result = execute_suite_case(document)

    assert result.engine_called is True
    assert result.actual["status"] == "SUCCEEDED_WITH_ISSUES"
    assert suite_oracle_matches(result.actual, expected) is True
    assert len(result.actual["resource_conflicts"]) == expected_conflicts
    assert len(expected["resource_conflicts"]) == expected_conflicts


def test_l02_pair_conflicts_match_but_cumulative_conflicts_and_working_boundaries_do_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _load("L02_MULTI_SITE_PARALLEL")
    expected = _load("L02_MULTI_SITE_PARALLEL", "expected.json")
    monkeypatch.setattr(suite_support, "_unsupported_reasons", lambda source: ())

    result = execute_suite_case(document)
    actual_dates = {item["task_id"]: item for item in result.actual["task_dates"]}
    expected_dates = {item["task_id"]: item for item in expected["task_dates"]}

    actual_conflicts = result.actual["resource_conflicts"]
    extra_conflicts = [conflict for conflict in actual_conflicts if conflict not in expected["resource_conflicts"]]

    assert all(conflict in actual_conflicts for conflict in expected["resource_conflicts"])
    assert len(extra_conflicts) == 135
    assert all(len(conflict["task_ids"]) >= 3 for conflict in extra_conflicts)
    assert all(conflict["combined_units"] > conflict["max_units"] for conflict in extra_conflicts)
    assert result.actual["assignment_costs"] == expected["assignment_costs"]
    assert {
        task_id
        for task_id, actual in actual_dates.items()
        if actual != expected_dates[task_id]
    } == {"task:s02-gate", "task:s04-gate", "task:s06-gate", "task:s08-gate"}
    assert suite_oracle_matches(result.actual, expected) is False


def test_large_suite_runner_records_explicit_gate_summary(tmp_path: Path) -> None:
    actual_root = tmp_path / "actual"

    assert run_suite(["--suite-root", str(SUITE_ROOT), "--actual-root", str(actual_root)]) == 0

    summary = json.loads((actual_root / "suite_run_summary.json").read_text(encoding="utf-8"))
    assert summary["case_count"] == 6
    assert summary["passed"] == 2
    assert summary["unsupported"] == 4
    assert summary["failed"] == 0
    assert summary["ui_export_eligible"] == 1
    assert {item["case_id"] for item in summary["cases"] if item["gate_status"] == "PASSED"} == {
        "L05_BULK_INVALID_GUARDRAILS",
        "L06_SCALE_500_DAG",
    }


def test_large_suite_export_writes_only_oracle_matched_cases(tmp_path: Path) -> None:
    output_root = tmp_path / "ui-import"

    assert export_suite(["--suite-root", str(SUITE_ROOT), "--output-root", str(output_root)]) == 0

    assert sorted(path.name for path in output_root.glob("*.canonical.json")) == [
        "L06_SCALE_500_DAG.canonical.json",
    ]


def test_large_suite_rebuild_is_byte_deterministic(tmp_path: Path) -> None:
    outputs = [tmp_path / "rebuild-a", tmp_path / "rebuild-b"]

    for output in outputs:
        assert rebuild_suite(["--source-root", str(SUITE_ROOT), "--output-root", str(output)]) == 0

    assert _hashes(outputs[0]) == _hashes(outputs[1]) == _hashes(SUITE_ROOT)
