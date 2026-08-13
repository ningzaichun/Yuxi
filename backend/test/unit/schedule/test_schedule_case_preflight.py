from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

from scripts.generate_synthetic_schedule_case import build_synthetic_schedule_case
from yuxi.schedule.case_preflight import preflight_schedule_case

BACKEND_ROOT = Path(__file__).resolve().parents[3]
PREFLIGHT_SCRIPT = BACKEND_ROOT / "scripts" / "preflight_schedule_case.py"
FIXTURE_PATH = BACKEND_ROOT / "test" / "data" / "schedule" / "schedule_v2_2_sanitized.json"
SYNTHETIC_FIXTURE_PATH = (
    BACKEND_ROOT / "test" / "data" / "schedule" / "schedule_v2_2_synthetic_case_s.json"
)


def test_sanitized_case_is_profiled_without_business_identifiers(
    canonical_schedule_payload: dict,
) -> None:
    report = preflight_schedule_case(
        canonical_schedule_payload,
        case_type="sanitized",
        baseline=canonical_schedule_payload,
    )

    serialized = str(report)
    assert report["contract_valid"] is True
    assert report["automated_migration_precheck_passed"] is False
    assert report["engineering_protocol_precheck_passed"] is False
    assert report["blocking_reasons"] == [
        "REAL_BUSINESS_SOURCE_REQUIRED",
        "STRUCTURAL_DIFFERENCE_REQUIRED",
    ]
    assert report["profile"]["tasks"]["total"] == 90
    assert report["profile"]["summary_dependencies"]["total"] == 4
    assert "脱敏示例项目" not in serialized
    assert "脱敏任务" not in serialized
    assert "task:" not in serialized
    assert "dependency:" not in serialized


def test_real_case_requires_a_structural_difference_from_baseline(
    canonical_schedule_payload: dict,
) -> None:
    current = copy.deepcopy(canonical_schedule_payload)
    current["dependencies"][0]["type"] = "SS"
    current["dependencies"][0]["source_type_code"] = 3

    report = preflight_schedule_case(
        current,
        case_type="real",
        baseline=canonical_schedule_payload,
    )

    assert report["automated_migration_precheck_passed"] is True
    assert report["blocking_reasons"] == []
    assert report["baseline_comparison"]["structurally_different"] is True
    assert {
        item["field"] for item in report["baseline_comparison"]["differences"]
    } >= {"dependencies.types.FS", "dependencies.types.SS"}
    assert {
        item["field"]
        for item in report["baseline_comparison"]["migration_relevant_differences"]
    } == {"dependencies.types.FS", "dependencies.types.SS"}
    assert "TASK_NAMES_ARE_READABLE_IN_WORKBENCH" in report["human_review_required"]


def test_synthetic_case_can_pass_protocol_precheck_but_never_business_migration(
    canonical_schedule_payload: dict,
) -> None:
    report = preflight_schedule_case(
        build_synthetic_schedule_case(),
        case_type="synthetic",
        baseline=canonical_schedule_payload,
    )

    assert report["engineering_protocol_precheck_passed"] is True
    assert report["engineering_protocol_blocking_reasons"] == []
    assert report["automated_migration_precheck_passed"] is False
    assert report["business_migration_eligible"] is False
    assert report["blocking_reasons"] == ["DECLARED_SYNTHETIC_SOURCE"]
    assert report["case_limitations"] == ["CANNOT_REPLACE_INDEPENDENT_REAL_CASE_B"]
    assert report["profile"]["tasks"]["max_outline_level"] == 4
    assert report["profile"]["summary_dependencies"]["predecessor_only"] == 1
    assert report["profile"]["dependencies"]["types"] == {"FS": 2, "SS": 1, "FF": 1, "SF": 1}


def test_committed_synthetic_fixture_matches_deterministic_factory() -> None:
    committed = json.loads(SYNTHETIC_FIXTURE_PATH.read_text(encoding="utf-8"))

    assert committed == build_synthetic_schedule_case()
    assert committed["source"]["format"] == "SYNTHETIC_TEST_DATA"
    assert all("合成" in task["name"] for task in committed["tasks"])


def test_declared_synthetic_source_cannot_be_relabelled_as_real(
    canonical_schedule_payload: dict,
) -> None:
    report = preflight_schedule_case(
        build_synthetic_schedule_case(),
        case_type="real",
        baseline=canonical_schedule_payload,
    )

    assert report["engineering_protocol_precheck_passed"] is True
    assert report["automated_migration_precheck_passed"] is False
    assert report["business_migration_eligible"] is False
    assert report["blocking_reasons"] == ["DECLARED_SYNTHETIC_SOURCE"]
    assert report["case_limitations"] == ["CANNOT_REPLACE_INDEPENDENT_REAL_CASE_B"]


def test_real_case_without_baseline_cannot_pass_migration_precheck(
    canonical_schedule_payload: dict,
) -> None:
    report = preflight_schedule_case(canonical_schedule_payload, case_type="real")

    assert report["automated_migration_precheck_passed"] is False
    assert report["blocking_reasons"] == ["BASELINE_CASE_REQUIRED"]


def test_supporting_object_difference_alone_does_not_qualify_as_case_b(
    canonical_schedule_payload: dict,
) -> None:
    current = copy.deepcopy(canonical_schedule_payload)
    current["assignments"].append({"source": "synthetic-test-object"})

    report = preflight_schedule_case(
        current,
        case_type="real",
        baseline=canonical_schedule_payload,
    )

    assert report["baseline_comparison"]["structurally_different"] is True
    assert report["baseline_comparison"]["migration_relevant_differences"] == []
    assert report["automated_migration_precheck_passed"] is False
    assert report["blocking_reasons"] == ["STRUCTURAL_DIFFERENCE_REQUIRED"]


def test_cli_returns_zero_and_json_for_qualified_real_case(
    canonical_schedule_payload: dict,
    tmp_path: Path,
) -> None:
    current = copy.deepcopy(canonical_schedule_payload)
    current["dependencies"][0]["type"] = "SS"
    current["dependencies"][0]["source_type_code"] = 3
    source_path = tmp_path / "case-b.json"
    source_path.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")

    result = _run_cli(source_path, "real", FIXTURE_PATH)

    report = json.loads(result.stdout)
    assert result.returncode == 0
    assert report["automated_migration_precheck_passed"] is True


def test_cli_returns_three_for_valid_nonqualifying_case() -> None:
    result = _run_cli(FIXTURE_PATH, "sanitized", FIXTURE_PATH)

    report = json.loads(result.stdout)
    assert result.returncode == 3
    assert report["contract_valid"] is True
    assert report["automated_migration_precheck_passed"] is False


def test_cli_returns_zero_for_synthetic_protocol_regression(
    canonical_schedule_payload: dict,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "synthetic-case.json"
    source_path.write_text(
        json.dumps(build_synthetic_schedule_case(), ensure_ascii=False),
        encoding="utf-8",
    )

    result = _run_cli(
        source_path,
        "synthetic",
        FIXTURE_PATH,
        purpose="protocol-regression",
    )

    report = json.loads(result.stdout)
    assert result.returncode == 0
    assert report["engineering_protocol_precheck_passed"] is True
    assert report["business_migration_eligible"] is False


def test_cli_keeps_synthetic_case_blocked_for_business_migration() -> None:
    result = _run_cli(SYNTHETIC_FIXTURE_PATH, "synthetic", FIXTURE_PATH)

    report = json.loads(result.stdout)
    assert result.returncode == 3
    assert report["engineering_protocol_precheck_passed"] is True
    assert report["blocking_reasons"] == ["DECLARED_SYNTHETIC_SOURCE"]


def test_cli_returns_two_and_only_error_locations_for_invalid_contract(
    canonical_schedule_payload: dict,
    tmp_path: Path,
) -> None:
    current = copy.deepcopy(canonical_schedule_payload)
    invalid_value = "private-invalid-date-value"
    current["project"]["source_project_summary"]["start"] = invalid_value
    source_path = tmp_path / "invalid-case.json"
    source_path.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")

    result = _run_cli(source_path, "real", FIXTURE_PATH)

    report = json.loads(result.stdout)
    assert result.returncode == 2
    assert report == {
        "contract_valid": False,
        "error_code": "INVALID_CANONICAL_SCHEDULE",
        "error_count": 1,
        "locations": ["project.source_project_summary.start"],
    }
    assert invalid_value not in result.stdout


def _run_cli(
    source: Path,
    case_type: str,
    baseline: Path,
    *,
    purpose: str = "business-migration",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(PREFLIGHT_SCRIPT),
            str(source),
            "--case-type",
            case_type,
            "--baseline",
            str(baseline),
            "--purpose",
            purpose,
        ],
        cwd=BACKEND_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
