from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

import scripts.export_schedule_suite_canonical as suite_export
from scripts.export_schedule_suite_canonical import main, suite_oracle_matches
from scripts.run_schedule_complex_suite import main as run_suite
from test.support.schedule_suite import execute_suite_case
from yuxi.schedule.audit.context import AuditContext
from yuxi.schedule.contracts.canonical import parse_canonical_schedule
from yuxi.schedule.importers import import_canonical_schedule

SUITE_ROOT = Path(__file__).resolve().parents[4] / "Yuxi_复杂排期测试套件_v1"


def test_small_suite_runner_keeps_all_oracles_passed_and_seven_ui_eligible(tmp_path: Path) -> None:
    actual_root = tmp_path / "actual"

    assert run_suite(["--suite-root", str(SUITE_ROOT), "--actual-root", str(actual_root)]) == 0

    summary = json.loads((actual_root / "suite_run_summary.json").read_text(encoding="utf-8"))
    assert summary["passed"] == 8
    assert summary["unsupported"] == 0
    assert summary["failed"] == 0
    assert summary["ui_export_eligible"] == 7


def test_export_suite_canonical_writes_importable_cases_and_skips_invalid_case(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_root = tmp_path / "schedule-ui-import"

    exit_code = main(
        [
            "--suite-root",
            str(SUITE_ROOT),
            "--output-root",
            str(output_root),
        ]
    )

    assert exit_code == 0
    exported = sorted(path.name for path in output_root.glob("*.canonical.json"))
    assert exported == [
        "C01_RELATION_MATRIX.canonical.json",
        "C02_MULTI_CALENDAR_EXCEPTIONS.canonical.json",
        "C03_NESTED_SUMMARY_BRANCHES.canonical.json",
        "C04_CONSTRAINTS_DEADLINES.canonical.json",
        "C05_BASELINE_PROGRESS.canonical.json",
        "C06_RESOURCE_OVERALLOCATION.canonical.json",
        "C08_MICROSOFT_PROJECT_OBSERVED.canonical.json",
    ]
    for path in output_root.glob("*.canonical.json"):
        parse_canonical_schedule(json.loads(path.read_text(encoding="utf-8")))

    output = capsys.readouterr().out
    assert "C07_INVALID_GUARDRAILS: SKIPPED (VALIDATION_FAILED" in output
    assert "Export complete: 7 exported, 1 skipped" in output


def test_export_suite_reference_uses_verified_engine_dates_and_rebuilds_conclusions(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "schedule-ui-import"
    assert main(["--suite-root", str(SUITE_ROOT), "--output-root", str(output_root)]) == 0

    for case_id in (
        "C01_RELATION_MATRIX",
        "C02_MULTI_CALENDAR_EXCEPTIONS",
        "C03_NESTED_SUMMARY_BRANCHES",
        "C04_CONSTRAINTS_DEADLINES",
        "C05_BASELINE_PROGRESS",
        "C06_RESOURCE_OVERALLOCATION",
    ):
        document = json.loads((SUITE_ROOT / case_id / "input.json").read_text(encoding="utf-8"))
        execution = execute_suite_case(document)
        engine_dates = {item["task_id"]: item for item in execution.engine_result["task_dates"]}
        payload = json.loads((output_root / f"{case_id}.canonical.json").read_text(encoding="utf-8"))

        assert payload["source"]["extraction_method"] == "YUXI_TEST_SUITE_ENGINE_REFERENCE"
        assert payload["semantics"]["source_dates_preserved"] is False
        assert payload["project"]["planned_date_source"] == "YUXI_SUITE_ENGINE_REFERENCE"
        assert payload["project"]["planned_start"] == execution.engine_result["project_start"]
        assert payload["project"]["planned_finish"] == execution.engine_result["finish_after"]
        for task in payload["tasks"]:
            expected = engine_dates[task["task_id"]]
            assert task["planned_start"] == expected["early_start"]
            assert task["planned_finish"] == expected["early_finish"]
            assert task["source_calculation"] == {
                "early_start": expected["early_start"],
                "early_finish": expected["early_finish"],
                "late_start": expected["late_start"],
                "late_finish": expected["late_finish"],
                "total_slack_minutes": expected["total_slack_minutes"],
                "free_slack_minutes": expected["free_slack_minutes"],
                "critical": expected["critical"],
            }

        canonical = parse_canonical_schedule(payload)
        context = AuditContext.build(import_canonical_schedule(canonical))
        capabilities = {name: capability.model_dump(mode="json") for name, capability in context.capabilities.items()}
        assert payload["statistics"] == context.statistics
        assert payload["capabilities"] == capabilities
        assert payload["validation"]["capabilities"] == capabilities
        assert (
            len(payload["validation"]["network_quality"]["source_schedule_violations"])
            == context.dependency_date_checks.violation_count
        )

    c01 = json.loads((output_root / "C01_RELATION_MATRIX.canonical.json").read_text(encoding="utf-8"))
    assert len({task["planned_finish"] for task in c01["tasks"]}) > 1


def test_export_preserves_microsoft_project_observation_dates(tmp_path: Path) -> None:
    output_root = tmp_path / "schedule-ui-import"
    assert (
        main(
            [
                "--suite-root",
                str(SUITE_ROOT),
                "--output-root",
                str(output_root),
                "--case-id",
                "C08_MICROSOFT_PROJECT_OBSERVED",
            ]
        )
        == 0
    )

    payload = json.loads((output_root / "C08_MICROSOFT_PROJECT_OBSERVED.canonical.json").read_text(encoding="utf-8"))
    expected = json.loads((SUITE_ROOT / "C08_MICROSOFT_PROJECT_OBSERVED" / "expected.json").read_text(encoding="utf-8"))
    expected_dates = {
        item["task_id"]: (item["start"], item["finish"])
        for item in [*expected["task_dates"], *expected["summary_dates"]]
    }

    assert payload["source"]["extraction_method"] == "YUXI_TEST_SUITE_ADAPTER"
    assert payload["semantics"]["source_dates_preserved"] is True
    assert {
        task["task_id"]: (task["planned_start"], task["planned_finish"]) for task in payload["tasks"]
    } == expected_dates


def test_suite_oracle_gate_rejects_any_frozen_result_difference() -> None:
    expected = json.loads((SUITE_ROOT / "C01_RELATION_MATRIX" / "expected.json").read_text(encoding="utf-8"))
    actual = json.loads(json.dumps(expected))
    assert suite_oracle_matches(actual, expected) is True

    actual["project_dates"]["finish"] = "2026-09-16T17:00:00+08:00"
    assert suite_oracle_matches(actual, expected) is False


def test_export_skips_unsupported_even_when_a_canonical_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    execute = suite_export.execute_suite_case

    def unsupported(document: dict):
        result = execute(document)
        actual = {**result.actual, "status": "UNSUPPORTED"}
        actual["issues"] = [{"code": "ENGINE_PROFILE_BLOCKED"}]
        return replace(result, actual=actual)

    monkeypatch.setattr(suite_export, "execute_suite_case", unsupported)
    output_root = tmp_path / "unsupported"
    assert (
        main(
            [
                "--suite-root",
                str(SUITE_ROOT),
                "--output-root",
                str(output_root),
                "--case-id",
                "C01_RELATION_MATRIX",
            ]
        )
        == 0
    )

    assert list(output_root.glob("*.canonical.json")) == []
    assert "SKIPPED (UNSUPPORTED: ENGINE_PROFILE_BLOCKED)" in capsys.readouterr().out


def test_export_skips_oracle_mismatch_before_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    execute = suite_export.execute_suite_case

    def mismatch(document: dict):
        result = execute(document)
        actual = json.loads(json.dumps(result.actual))
        actual["project_dates"]["finish"] = "2026-09-16T17:00:00+08:00"
        return replace(result, actual=actual)

    monkeypatch.setattr(suite_export, "execute_suite_case", mismatch)
    output_root = tmp_path / "mismatch"
    assert (
        main(
            [
                "--suite-root",
                str(SUITE_ROOT),
                "--output-root",
                str(output_root),
                "--case-id",
                "C01_RELATION_MATRIX",
            ]
        )
        == 0
    )

    assert list(output_root.glob("*.canonical.json")) == []
    assert "SKIPPED (ORACLE_MISMATCH)" in capsys.readouterr().out


def test_export_is_byte_deterministic(tmp_path: Path) -> None:
    roots = [tmp_path / "run-a", tmp_path / "run-b"]
    for root in roots:
        assert main(["--suite-root", str(SUITE_ROOT), "--output-root", str(root)]) == 0

    def hashes(root: Path) -> dict[str, str]:
        return {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(root.glob("*.canonical.json"))
        }

    assert hashes(roots[0]) == hashes(roots[1])


def test_export_suite_canonical_rejects_output_inside_frozen_suite() -> None:
    with pytest.raises(SystemExit, match="2"):
        main(
            [
                "--suite-root",
                str(SUITE_ROOT),
                "--output-root",
                str(SUITE_ROOT / "generated"),
            ]
        )
