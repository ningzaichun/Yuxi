from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from test.support.schedule_suite import execute_suite_case, normalize_suite_document
from yuxi.schedule.audit.engine import audit_schedule
from yuxi.schedule.contracts.canonical_v2_3 import CanonicalScheduleV23
from yuxi.schedule.contracts.canonical_v2_4 import CanonicalScheduleV24
from yuxi.schedule.contracts.canonical_v2_5 import CanonicalScheduleV25
from yuxi.schedule.contracts.canonical_v2_6 import CanonicalScheduleV26
from yuxi.schedule.contracts.canonical_v2_7 import CanonicalScheduleV27
from yuxi.schedule.contracts.envelope import ScheduleSnapshotSubmission
from yuxi.schedule.contracts.optimization import GoalOptimizationRequest
from yuxi.schedule.forward_engine import (
    CALENDAR_EXCEPTIONS_ENGINE_PROFILE_ID,
    COMPLETED_PROGRESS_ENGINE_PROFILE_ID,
    CONSTRAINTS_ENGINE_PROFILE_ID,
    IN_PROGRESS_ENGINE_PROFILE_ID,
    MILESTONE_ENGINE_PROFILE_ID,
    MULTI_CALENDAR_ENGINE_PROFILE_ID,
    NEGATIVE_LAG_ENGINE_PROFILE_ID,
    RESOURCE_ANALYSIS_ENGINE_PROFILE_ID,
    calculate_minimal_forward_schedule,
    recalculation_profile_for,
)
from yuxi.schedule.importers import import_canonical_schedule
from yuxi.schedule.goal_optimizer import optimize_project_finish
from yuxi.schedule.preflight import preflight_schedule_input


SUITE_ROOT = Path(__file__).resolve().parents[4] / "Yuxi_复杂排期测试套件_v1"


def _load(case_id: str, name: str) -> dict:
    return json.loads((SUITE_ROOT / case_id / name).read_text(encoding="utf-8"))


def _expected_view(payload: dict) -> dict:
    fields = (
        "case_id",
        "status",
        "project_dates",
        "task_dates",
        "summary_dates",
        "issues",
        "resource_conflicts",
        "assignment_costs",
        "baseline_variances",
    )
    return {field: payload[field] for field in fields}


def _single_calendar_exception_subset() -> dict:
    document = copy.deepcopy(_load("C02_MULTI_CALENDAR_EXCEPTIONS", "input.json"))
    document["case_id"] = "C02_SINGLE_CALENDAR_EXCEPTIONS"
    document["title"] = "单日历停工与补班"
    document["calendars"] = [
        calendar for calendar in document["calendars"] if calendar["calendar_id"] == "calendar:standard"
    ]
    document["tasks"] = [task for task in document["tasks"] if task["task_id"] != "task:site"]
    document["dependencies"] = [
        dependency
        for dependency in document["dependencies"]
        if "task:site" not in {dependency["predecessor_task_id"], dependency["successor_task_id"]}
    ]
    return document


def _completed_progress_subset() -> dict:
    document = copy.deepcopy(_load("C05_BASELINE_PROGRESS", "input.json"))
    document["case_id"] = "C05_COMPLETED_NOT_STARTED_SUBSET"
    kept_task_ids = {"task:root", "task:start", "task:a", "task:c", "task:finish"}
    document["tasks"] = [task for task in document["tasks"] if task["task_id"] in kept_task_ids]
    start_to_a = next(
        dependency for dependency in document["dependencies"] if dependency["dependency_id"] == "dep:start:a:FS:0"
    )
    a_to_c = copy.deepcopy(
        next(dependency for dependency in document["dependencies"] if dependency["dependency_id"] == "dep:a:b:FS:0")
    )
    a_to_c.update(
        {
            "dependency_id": "dep:a:c:FS:0",
            "successor_task_id": "task:c",
        }
    )
    c_to_finish = next(
        dependency for dependency in document["dependencies"] if dependency["dependency_id"] == "dep:c:finish:FS:0"
    )
    document["dependencies"] = [start_to_a, a_to_c, c_to_finish]
    return document


def test_c07_preflight_collects_all_frozen_blockers_without_engine() -> None:
    document = _load("C07_INVALID_GUARDRAILS", "input.json")

    issues = [issue.as_suite_issue() for issue in preflight_schedule_input(document)]
    result = execute_suite_case(document)

    assert issues == _load("C07_INVALID_GUARDRAILS", "expected.json")["issues"]
    assert _expected_view(result.actual) == _expected_view(_load("C07_INVALID_GUARDRAILS", "expected.json"))
    assert result.engine_called is False
    assert result.engine_result is None
    assert result.canonical is None


def test_c06_resource_overallocation_and_costs_strictly_match_suite() -> None:
    document = _load("C06_RESOURCE_OVERALLOCATION", "input.json")

    result = execute_suite_case(document)

    assert result.engine_called is True
    assert result.engine_result is not None
    assert isinstance(result.canonical, CanonicalScheduleV27)
    assert recalculation_profile_for(result.canonical) == RESOURCE_ANALYSIS_ENGINE_PROFILE_ID
    assert _expected_view(result.actual) == _expected_view(_load("C06_RESOURCE_OVERALLOCATION", "expected.json"))


def test_v27_envelope_accepts_resource_assignments() -> None:
    canonical, _ = normalize_suite_document(_load("C06_RESOURCE_OVERALLOCATION", "input.json"))

    submission = ScheduleSnapshotSubmission.model_validate(
        {
            "request_id": "request:c06",
            "external_project_id": canonical.project.project_id,
            "external_snapshot_id": canonical.snapshot_id,
            "external_revision": "1",
            "snapshot": canonical.model_dump(mode="json"),
        }
    )

    assert isinstance(submission.snapshot, CanonicalScheduleV27)
    assert len(submission.snapshot.resources) == 2
    assert len(submission.snapshot.assignments) == 4


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload["assignments"][0].update(task_id="task:missing"), "unknown task"),
        (
            lambda payload: payload["assignments"][0].update(resource_id="resource:missing"),
            "unknown resource",
        ),
        (
            lambda payload: payload["assignments"][1].update(assignment_id=payload["assignments"][0]["assignment_id"]),
            "duplicate assignment_id",
        ),
        (lambda payload: payload["assignments"][0].update(units=0), "greater than 0"),
        (lambda payload: payload["assignments"][0].update(task_id="task:start"), "activity task"),
    ],
)
def test_v27_rejects_invalid_assignment_contract(mutation, message: str) -> None:
    canonical, _ = normalize_suite_document(_load("C06_RESOURCE_OVERALLOCATION", "input.json"))
    payload = canonical.model_dump(mode="json")
    mutation(payload)

    with pytest.raises(ValidationError, match=message):
        CanonicalScheduleV27.model_validate(payload)


def test_resource_analysis_does_not_change_cpm_dates() -> None:
    resource_document = _load("C06_RESOURCE_OVERALLOCATION", "input.json")
    resource_schedule, _ = normalize_suite_document(resource_document)
    without_resources = copy.deepcopy(resource_document)
    without_resources["resources"] = []
    without_resources["assignments"] = []
    plain_schedule, _ = normalize_suite_document(without_resources)

    resource_result = calculate_minimal_forward_schedule(
        resource_schedule,
        engine_profile_id=recalculation_profile_for(resource_schedule),
    )
    plain_result = calculate_minimal_forward_schedule(
        plain_schedule,
        engine_profile_id=recalculation_profile_for(plain_schedule),
    )

    assert {item["task_id"]: (item["early_start"], item["early_finish"]) for item in resource_result["task_dates"]} == {
        item["task_id"]: (item["early_start"], item["early_finish"]) for item in plain_result["task_dates"]
    }


def test_resource_analysis_detects_cumulative_overallocation_without_an_overloaded_pair() -> None:
    document = copy.deepcopy(_load("C06_RESOURCE_OVERALLOCATION", "input.json"))
    crew_assignments = [
        assignment
        for assignment in document["assignments"]
        if assignment["resource_id"] == "resource:crew"
    ]
    for assignment in crew_assignments:
        assignment["units"] = 0.4
    document["assignments"].append(
        {
            "assignment_id": "assign:c:crew",
            "task_id": "task:c",
            "resource_id": "resource:crew",
            "units": 0.4,
        }
    )
    schedule, _ = normalize_suite_document(document)

    result = calculate_minimal_forward_schedule(
        schedule,
        engine_profile_id=recalculation_profile_for(schedule),
    )

    assert [
        conflict
        for conflict in result["resource_conflicts"]
        if conflict["resource_id"] == "resource:crew"
    ] == [
        {
            "resource_id": "resource:crew",
            "task_ids": ["task:a", "task:b", "task:c"],
            "overlap_start": "2026-09-07T08:00:00+08:00",
            "overlap_finish": "2026-09-08T17:00:00+08:00",
            "combined_units": 1.2,
            "max_units": 1.0,
        }
    ]


def test_v27_audit_reports_source_date_resource_overallocation() -> None:
    document = copy.deepcopy(_load("C06_RESOURCE_OVERALLOCATION", "input.json"))
    expected = _load("C06_RESOURCE_OVERALLOCATION", "expected.json")
    dates = {item["task_id"]: item for item in [*expected["task_dates"], *expected["summary_dates"]]}
    for task in document["tasks"]:
        task["observed_start"] = dates[task["task_id"]]["start"]
        task["observed_finish"] = dates[task["task_id"]]["finish"]
    canonical, options = normalize_suite_document(document)

    execution = audit_schedule(
        import_canonical_schedule(canonical),
        schedule_snapshot_id=canonical.snapshot_id,
        audit_run_id="audit:c06-resource",
        options=options,
    )

    resource_findings = [finding for finding in execution.findings if finding.rule_id == "RESOURCE_OVERALLOCATION"]
    assert [finding.object_refs for finding in resource_findings] == [
        ("resource:crane",),
        ("resource:crew",),
    ]
    assert [finding.evidence["task_ids"] for finding in resource_findings] == [
        ["task:a", "task:c"],
        ["task:a", "task:b"],
    ]
    assert execution.result.capabilities["resource_leveling"].model_dump(mode="json") == {
        "allowed": False,
        "reasons": ["RESOURCE_LEVELING_NOT_IMPLEMENTED"],
    }


def test_c03_nested_summary_and_milestone_strictly_match_suite() -> None:
    document = _load("C03_NESTED_SUMMARY_BRANCHES", "input.json")

    result = execute_suite_case(document)

    assert _expected_view(result.actual) == _expected_view(_load("C03_NESTED_SUMMARY_BRANCHES", "expected.json"))
    assert result.engine_called is True
    assert result.canonical.schema_version == "canonical_schedule_v2.3"
    assert recalculation_profile_for(result.canonical) == MILESTONE_ENGINE_PROFILE_ID
    assert "boundary_whitelist" not in result.canonical.model_dump(mode="json")
    assert {item["code"] for item in result.yuxi_audit["issues"]} >= {
        "BASELINE_MISSING",
        "STATUS_DATE_MISSING",
        "NO_SOURCE_ASSIGNMENTS",
    }


def test_c08_microsoft_project_observation_strictly_matches_suite() -> None:
    result = execute_suite_case(_load("C08_MICROSOFT_PROJECT_OBSERVED", "input.json"))

    assert _expected_view(result.actual) == _expected_view(_load("C08_MICROSOFT_PROJECT_OBSERVED", "expected.json"))
    assert result.engine_called is True
    assert result.actual["issues"] == []


def test_c01_signed_lag_strictly_matches_suite() -> None:
    result = execute_suite_case(_load("C01_RELATION_MATRIX", "input.json"))

    assert _expected_view(result.actual) == _expected_view(_load("C01_RELATION_MATRIX", "expected.json"))
    assert result.engine_called is True
    assert result.actual["issues"] == []
    assert recalculation_profile_for(result.canonical) == NEGATIVE_LAG_ENGINE_PROFILE_ID


def test_c02_single_calendar_exception_subset_uses_v10() -> None:
    result = execute_suite_case(_single_calendar_exception_subset())

    assert _expected_view(result.actual) == {
        "case_id": "C02_SINGLE_CALENDAR_EXCEPTIONS",
        "status": "SUCCEEDED",
        "project_dates": {
            "start": "2026-09-07T08:00:00+08:00",
            "finish": "2026-09-13T17:00:00+08:00",
        },
        "task_dates": [
            {
                "task_id": "task:finish",
                "start": "2026-09-13T17:00:00+08:00",
                "finish": "2026-09-13T17:00:00+08:00",
                "calendar_id": "calendar:standard",
            },
            {
                "task_id": "task:handover",
                "start": "2026-09-11T08:00:00+08:00",
                "finish": "2026-09-13T17:00:00+08:00",
                "calendar_id": "calendar:standard",
            },
            {
                "task_id": "task:office",
                "start": "2026-09-07T08:00:00+08:00",
                "finish": "2026-09-10T17:00:00+08:00",
                "calendar_id": "calendar:standard",
            },
            {
                "task_id": "task:start",
                "start": "2026-09-07T08:00:00+08:00",
                "finish": "2026-09-07T08:00:00+08:00",
                "calendar_id": "calendar:standard",
            },
        ],
        "summary_dates": [
            {
                "task_id": "task:root",
                "start": "2026-09-07T08:00:00+08:00",
                "finish": "2026-09-13T17:00:00+08:00",
            }
        ],
        "issues": [],
        "resource_conflicts": [],
        "assignment_costs": [],
        "baseline_variances": [],
    }
    assert isinstance(result.canonical, CanonicalScheduleV24)
    assert recalculation_profile_for(result.canonical) == CALENDAR_EXCEPTIONS_ENGINE_PROFILE_ID
    audit = audit_schedule(
        import_canonical_schedule(result.canonical),
        schedule_snapshot_id="snapshot:c02-single-calendar",
        audit_run_id="audit:c02-single-calendar",
    )
    assert audit.result.capabilities["cpm_recalculation"].allowed is True


def test_c02_full_multi_calendar_case_strictly_matches_suite() -> None:
    result = execute_suite_case(_load("C02_MULTI_CALENDAR_EXCEPTIONS", "input.json"))

    assert _expected_view(result.actual) == _expected_view(_load("C02_MULTI_CALENDAR_EXCEPTIONS", "expected.json"))
    assert result.engine_called is True
    assert isinstance(result.canonical, CanonicalScheduleV24)
    assert recalculation_profile_for(result.canonical) == MULTI_CALENDAR_ENGINE_PROFILE_ID
    assert result.canonical.semantics.lag_calendar_policy == "SUCCESSOR_TASK_CALENDAR"
    audit = audit_schedule(
        import_canonical_schedule(result.canonical),
        schedule_snapshot_id="snapshot:c02-multi-calendar",
        audit_run_id="audit:c02-multi-calendar",
    )
    assert audit.result.capabilities["cpm_recalculation"].allowed is True
    assert audit.result.dependency_date_checks.checked == 5
    assert audit.result.dependency_date_checks.skipped == 0


def test_c04_constraints_and_management_targets_strictly_match_suite() -> None:
    result = execute_suite_case(_load("C04_CONSTRAINTS_DEADLINES", "input.json"))

    assert _expected_view(result.actual) == _expected_view(_load("C04_CONSTRAINTS_DEADLINES", "expected.json"))
    assert result.engine_called is True
    assert isinstance(result.canonical, CanonicalScheduleV25)
    assert recalculation_profile_for(result.canonical) == CONSTRAINTS_ENGINE_PROFILE_ID
    assert result.actual["status"] == "SUCCEEDED_WITH_ISSUES"
    assert [issue["code"] for issue in result.actual["issues"]] == [
        "DEADLINE_MISSED",
        "FINISH_CONSTRAINT_VIOLATED",
        "HARD_CONSTRAINT_NETWORK_CONFLICT",
        "PROJECT_REQUIRED_FINISH_MISSED",
    ]
    fixed = next(item for item in result.actual["task_dates"] if item["task_id"] == "task:c")
    assert fixed["start"] == "2026-09-15T08:00:00+08:00"


def test_v25_audit_reports_source_deadline_and_required_finish_variance() -> None:
    canonical, _ = normalize_suite_document(_load("C04_CONSTRAINTS_DEADLINES", "input.json"))
    payload = canonical.model_dump(mode="json", exclude_none=False)
    payload["project"]["planned_finish"] = "2026-09-21T17:00:00+08:00"
    task = next(item for item in payload["tasks"] if item["task_id"] == "task:d")
    task["planned_finish"] = "2026-09-21T17:00:00+08:00"
    source = CanonicalScheduleV25.model_validate(payload)

    execution = audit_schedule(
        import_canonical_schedule(source),
        schedule_snapshot_id="snapshot:v25-constraint-audit",
        audit_run_id="audit:v25-constraint-audit",
    )

    findings = {finding.rule_id: finding for finding in execution.findings}
    assert findings["DEADLINE_MISSED"].evidence["variance_minutes"] == 480
    assert findings["FINISH_CONSTRAINT_VIOLATED"].evidence["variance_minutes"] == 480
    assert findings["PROJECT_REQUIRED_FINISH_MISSED"].evidence["negative_float_minutes"] == -480


def test_multi_calendar_profile_uses_successor_calendar_for_all_relation_types() -> None:
    expected_dates = {
        "FS": ("2026-09-16T15:00:00+08:00", "2026-09-23T15:00:00+08:00"),
        "SS": ("2026-09-12T07:00:00+08:00", "2026-09-18T17:00:00+08:00"),
        "FF": ("2026-09-09T15:00:00+08:00", "2026-09-16T15:00:00+08:00"),
        "SF": ("2026-09-07T08:00:00+08:00", "2026-09-12T18:00:00+08:00"),
    }
    for relation_type, expected in expected_dates.items():
        document = copy.deepcopy(_load("C02_MULTI_CALENDAR_EXCEPTIONS", "input.json"))
        kept_task_ids = {"task:root", "task:start", "task:office", "task:site"}
        document["tasks"] = [task for task in document["tasks"] if task["task_id"] in kept_task_ids]
        start_to_office = next(
            item for item in document["dependencies"] if item["dependency_id"] == "dep:start:office:FS:0"
        )
        office_to_site = next(
            item for item in document["dependencies"] if item["dependency_id"] == "dep:start:site:FS:0"
        )
        office_to_site.update(
            {
                "dependency_id": f"dep:office:site:{relation_type}:2400",
                "predecessor_task_id": "task:office",
                "type": relation_type,
                "lag_minutes": 2400,
            }
        )
        document["dependencies"] = [start_to_office, office_to_site]
        canonical, _ = normalize_suite_document(document)

        result = calculate_minimal_forward_schedule(
            canonical,
            engine_profile_id=recalculation_profile_for(canonical),
        )

        site = next(item for item in result["task_dates"] if item["task_id"] == "task:site")
        assert (site["early_start"], site["early_finish"]) == expected


def test_multi_calendar_profile_uses_successor_calendar_for_negative_lag() -> None:
    document = copy.deepcopy(_load("C02_MULTI_CALENDAR_EXCEPTIONS", "input.json"))
    kept_task_ids = {"task:root", "task:start", "task:site", "task:handover", "task:finish"}
    document["tasks"] = [task for task in document["tasks"] if task["task_id"] in kept_task_ids]
    kept_dependency_ids = {
        "dep:start:site:FS:0",
        "dep:site:handover:FS:0",
        "dep:handover:finish:FS:0",
    }
    document["dependencies"] = [
        item for item in document["dependencies"] if item["dependency_id"] in kept_dependency_ids
    ]
    next(item for item in document["dependencies"] if item["dependency_id"] == "dep:site:handover:FS:0")[
        "lag_minutes"
    ] = -600
    canonical, _ = normalize_suite_document(document)

    result = calculate_minimal_forward_schedule(
        canonical,
        engine_profile_id=recalculation_profile_for(canonical),
    )

    handover = next(item for item in result["task_dates"] if item["task_id"] == "task:handover")
    assert result["status"] == "calculated"
    assert handover["early_start"] == "2026-09-10T15:00:00+08:00"
    assert handover["early_finish"] == "2026-09-13T15:00:00+08:00"


def test_v24_audit_checks_non_zero_lag_with_calendar_exceptions() -> None:
    document = _single_calendar_exception_subset()
    dependency = next(item for item in document["dependencies"] if item["dependency_id"] == "dep:office:handover:FS:0")
    dependency["lag_minutes"] = 480
    canonical, _ = normalize_suite_document(document)

    execution = audit_schedule(
        import_canonical_schedule(canonical),
        schedule_snapshot_id="snapshot:v24-lag-audit",
        audit_run_id="audit:v24-lag-audit",
    )

    assert execution.result.dependency_date_checks.checked == 3
    assert execution.result.dependency_date_checks.skipped == 0
    assert dependency["dependency_id"] in next(
        finding.object_refs for finding in execution.findings if finding.rule_id == "LAG_DATE_VIOLATION"
    )


def test_v23_is_accepted_by_versioned_snapshot_envelope() -> None:
    canonical, _ = normalize_suite_document(_load("C03_NESTED_SUMMARY_BRANCHES", "input.json"))

    submission = ScheduleSnapshotSubmission.model_validate(
        {
            "request_id": "request-v23",
            "external_project_id": canonical.project.project_id,
            "external_snapshot_id": canonical.snapshot_id,
            "external_revision": "1",
            "snapshot": canonical.model_dump(mode="json"),
        }
    )

    assert isinstance(submission.snapshot, CanonicalScheduleV23)
    assert any(task.task_type == "milestone" for task in submission.snapshot.tasks)


def test_v24_is_accepted_by_versioned_snapshot_envelope() -> None:
    canonical, _ = normalize_suite_document(_single_calendar_exception_subset())

    submission = ScheduleSnapshotSubmission.model_validate(
        {
            "request_id": "request-v24",
            "external_project_id": canonical.project.project_id,
            "external_snapshot_id": canonical.snapshot_id,
            "external_revision": "1",
            "snapshot": canonical.model_dump(mode="json"),
        }
    )

    assert isinstance(submission.snapshot, CanonicalScheduleV24)
    assert submission.snapshot.calendars[0].exceptions[0].exception_id == "holiday"


def test_v25_is_accepted_by_versioned_snapshot_envelope() -> None:
    canonical, _ = normalize_suite_document(_load("C04_CONSTRAINTS_DEADLINES", "input.json"))

    submission = ScheduleSnapshotSubmission.model_validate(
        {
            "request_id": "request-v25",
            "external_project_id": canonical.project.project_id,
            "external_snapshot_id": canonical.snapshot_id,
            "external_revision": "1",
            "snapshot": canonical.model_dump(mode="json"),
        }
    )

    assert isinstance(submission.snapshot, CanonicalScheduleV25)
    assert submission.snapshot.project.required_finish.isoformat() == "2026-09-18T17:00:00+08:00"
    assert next(task for task in submission.snapshot.tasks if task.task_id == "task:c").constraint.type == (
        "MUST_START_ON"
    )


def test_v26_completed_and_not_started_subset_keeps_actuals_and_calculates_baseline_variance() -> None:
    result = execute_suite_case(_completed_progress_subset())

    assert isinstance(result.canonical, CanonicalScheduleV26)
    assert recalculation_profile_for(result.canonical) == COMPLETED_PROGRESS_ENGINE_PROFILE_ID
    assert result.actual["status"] == "SUCCEEDED"
    assert result.actual["project_dates"] == {
        "start": "2026-09-07T08:00:00+08:00",
        "finish": "2026-09-15T17:00:00+08:00",
    }
    dates = {item["task_id"]: item for item in result.actual["task_dates"]}
    assert dates["task:start"]["start"] == "2026-09-07T08:00:00+08:00"
    assert dates["task:a"]["start"] == "2026-09-08T08:00:00+08:00"
    assert dates["task:a"]["finish"] == "2026-09-10T17:00:00+08:00"
    assert dates["task:c"]["start"] == "2026-09-11T08:00:00+08:00"
    assert result.actual["baseline_variances"] == [
        {"task_id": "task:a", "start_variance_minutes": 480, "finish_variance_minutes": 480},
        {"task_id": "task:c", "start_variance_minutes": -1920, "finish_variance_minutes": -1920},
        {"task_id": "task:finish", "start_variance_minutes": -1920, "finish_variance_minutes": -1920},
        {"task_id": "task:start", "start_variance_minutes": 0, "finish_variance_minutes": 0},
    ]

    execution = audit_schedule(
        import_canonical_schedule(result.canonical),
        schedule_snapshot_id=result.canonical.snapshot_id,
        audit_run_id="audit:c05-phase5a",
    )
    assert execution.result.capabilities["cpm_recalculation"].allowed is True


def test_v26_is_accepted_by_versioned_snapshot_envelope() -> None:
    canonical, _ = normalize_suite_document(_completed_progress_subset())

    submission = ScheduleSnapshotSubmission.model_validate(
        {
            "request_id": "request-v26",
            "external_project_id": canonical.project.project_id,
            "external_snapshot_id": canonical.snapshot_id,
            "external_revision": "1",
            "snapshot": canonical.model_dump(mode="json"),
        }
    )

    assert isinstance(submission.snapshot, CanonicalScheduleV26)
    assert submission.snapshot.project.status_date.isoformat() == "2026-09-16T17:00:00+08:00"


def test_v26_rejects_missing_status_date_or_inconsistent_completed_facts() -> None:
    canonical, _ = normalize_suite_document(_completed_progress_subset())
    payload = canonical.model_dump(mode="json")
    payload["project"]["status_date"] = None

    with pytest.raises(ValidationError):
        CanonicalScheduleV26.model_validate(payload)

    payload = canonical.model_dump(mode="json")
    completed = next(task for task in payload["tasks"] if task["task_id"] == "task:a")
    completed["actual_finish"] = None

    with pytest.raises(ValidationError, match="COMPLETED tasks require"):
        CanonicalScheduleV26.model_validate(payload)


def test_c05_full_case_matches_frozen_expected_with_in_progress_forecast() -> None:
    result = execute_suite_case(_load("C05_BASELINE_PROGRESS", "input.json"))

    assert result.engine_called is True
    assert isinstance(result.canonical, CanonicalScheduleV26)
    assert recalculation_profile_for(result.canonical) == IN_PROGRESS_ENGINE_PROFILE_ID
    assert _expected_view(result.actual) == _expected_view(_load("C05_BASELINE_PROGRESS", "expected.json"))

    execution = audit_schedule(
        import_canonical_schedule(result.canonical),
        schedule_snapshot_id=result.canonical.snapshot_id,
        audit_run_id="audit:c05-phase5b",
    )
    assert execution.result.capabilities["cpm_recalculation"].allowed is True


def test_v26_in_progress_keeps_out_of_sequence_actual_start_and_reports_conflict() -> None:
    document = copy.deepcopy(_load("C05_BASELINE_PROGRESS", "input.json"))
    task_b = next(task for task in document["tasks"] if task["task_id"] == "task:b")
    task_b["actual_start"] = "2026-09-10T08:00:00+08:00"
    canonical, _ = normalize_suite_document(document)

    result = calculate_minimal_forward_schedule(
        canonical,
        engine_profile_id=recalculation_profile_for(canonical),
    )

    dates = {item["task_id"]: item for item in result["task_dates"]}
    assert result["status"] == "calculated"
    assert dates["task:b"]["early_start"] == "2026-09-10T08:00:00+08:00"
    assert dates["task:b"]["early_finish"] == "2026-09-21T17:00:00+08:00"
    assert result["issues"] == [
        {
            "severity": "warning",
            "code": "ACTUAL_START_NETWORK_CONFLICT",
            "object_ref": "task:b",
            "object_refs": ["task:b"],
            "evidence": {
                "actual_start": "2026-09-10T08:00:00+08:00",
                "network_required_start": "2026-09-11T08:00:00+08:00",
                "status_date": "2026-09-16T17:00:00+08:00",
                "remaining_start": "2026-09-17T08:00:00+08:00",
                "predicted_finish": "2026-09-21T17:00:00+08:00",
            },
            "message": "任务实际开始早于依赖网络要求；保留实际事实，并从状态日期之后排剩余工作。",
        }
    ]


def test_v13_keeps_in_progress_semantics_blocked_for_profile_reproducibility() -> None:
    canonical, _ = normalize_suite_document(_load("C05_BASELINE_PROGRESS", "input.json"))

    result = calculate_minimal_forward_schedule(
        canonical,
        engine_profile_id=COMPLETED_PROGRESS_ENGINE_PROFILE_ID,
    )

    assert result["status"] == "blocked"
    assert result["support"]["blockers"] == [
        {
            "code": "IN_PROGRESS_UNSUPPORTED",
            "object_refs": ["task:b"],
            "message": "阶段 5A 尚未启用进行中任务的剩余工作排程",
        }
    ]


def test_v14_reverse_float_uses_remaining_work_and_excludes_completed_criticality() -> None:
    canonical, _ = normalize_suite_document(_load("C05_BASELINE_PROGRESS", "input.json"))

    result = calculate_minimal_forward_schedule(
        canonical,
        engine_profile_id=recalculation_profile_for(canonical),
    )

    dates = {item["task_id"]: item for item in result["task_dates"]}
    assert {
        field: dates["task:a"][field]
        for field in (
            "late_start",
            "late_finish",
            "total_slack_minutes",
            "free_slack_minutes",
            "critical",
        )
    } == {
        "late_start": "2026-09-08T08:00:00+08:00",
        "late_finish": "2026-09-10T17:00:00+08:00",
        "total_slack_minutes": 0,
        "free_slack_minutes": 0,
        "critical": False,
    }
    assert {
        field: dates["task:b"][field]
        for field in (
            "late_start",
            "late_finish",
            "total_slack_minutes",
            "free_slack_minutes",
            "critical",
        )
    } == {
        "late_start": "2026-09-11T08:00:00+08:00",
        "late_finish": "2026-09-21T17:00:00+08:00",
        "total_slack_minutes": 0,
        "free_slack_minutes": 0,
        "critical": True,
    }


def test_v26_completed_task_keeps_out_of_sequence_actual_facts_and_reports_conflict() -> None:
    document = copy.deepcopy(_load("C05_BASELINE_PROGRESS", "input.json"))
    task_a = next(task for task in document["tasks"] if task["task_id"] == "task:a")
    task_a["actual_start"] = "2026-09-04T08:00:00+08:00"
    task_a["actual_finish"] = "2026-09-08T17:00:00+08:00"
    canonical, _ = normalize_suite_document(document)

    result = calculate_minimal_forward_schedule(
        canonical,
        engine_profile_id=recalculation_profile_for(canonical),
    )

    dates = {item["task_id"]: item for item in result["task_dates"]}
    issue = next(item for item in result["issues"] if item["object_ref"] == "task:a")
    assert dates["task:a"]["early_start"] == "2026-09-04T08:00:00+08:00"
    assert dates["task:a"]["early_finish"] == "2026-09-08T17:00:00+08:00"
    assert issue["code"] == "ACTUAL_START_NETWORK_CONFLICT"
    assert issue["evidence"] == {
        "actual_start": "2026-09-04T08:00:00+08:00",
        "actual_finish": "2026-09-08T17:00:00+08:00",
        "network_required_start": "2026-09-07T08:00:00+08:00",
        "status_date": "2026-09-16T17:00:00+08:00",
    }


def test_goal_optimizer_rejects_completed_v26_task() -> None:
    canonical, _ = normalize_suite_document(_completed_progress_subset())
    request = GoalOptimizationRequest.model_validate(
        {
            "request_id": "goal-v26-completed",
            "base_snapshot_content_sha256": "sha256:" + "a" * 64,
            "objective": "MINIMIZE_PROJECT_FINISH",
            "target_finish": None,
            "authorized_duration_options": [{"task_id": "task:a", "duration_minutes": 960}],
            "locked_task_ids": [],
            "authorization_confirmed": True,
        }
    )

    result = optimize_project_finish(canonical, request)

    assert result["status"] == "blocked"
    assert result["support"]["blockers"] == [
        {
            "code": "AUTHORIZED_TASK_COMPLETED",
            "object_refs": ["task:a"],
            "message": "已完成任务的实际事实不能参与工期优化",
        }
    ]


def test_goal_optimizer_rejects_in_progress_v26_task() -> None:
    canonical, _ = normalize_suite_document(_load("C05_BASELINE_PROGRESS", "input.json"))
    request = GoalOptimizationRequest.model_validate(
        {
            "request_id": "goal-v26-in-progress",
            "base_snapshot_content_sha256": "sha256:" + "a" * 64,
            "objective": "MINIMIZE_PROJECT_FINISH",
            "target_finish": None,
            "authorized_duration_options": [{"task_id": "task:b", "duration_minutes": 1920}],
            "locked_task_ids": [],
            "authorization_confirmed": True,
        }
    )

    result = optimize_project_finish(canonical, request)

    assert result["status"] == "blocked"
    assert result["support"]["blockers"] == [
        {
            "code": "AUTHORIZED_TASK_IN_PROGRESS",
            "object_refs": ["task:b"],
            "message": "进行中任务的实际事实和剩余工作不能参与工期优化",
        }
    ]
