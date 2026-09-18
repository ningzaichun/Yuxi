from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from yuxi.schedule.audit.engine import audit_schedule
from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22
from yuxi.schedule.contracts.canonical_v2_3 import CanonicalScheduleV23
from yuxi.schedule.contracts.canonical_v2_4 import CanonicalScheduleV24
from yuxi.schedule.contracts.canonical_v2_5 import CanonicalScheduleV25
from yuxi.schedule.contracts.canonical_v2_8 import CanonicalScheduleV28
from yuxi.schedule.contracts.import_v1 import (
    MicrosoftProjectInterchangeFormalV11,
    MicrosoftProjectInterchangeV11,
    ScheduleImportEnvelope,
)
from yuxi.schedule.contracts.optimization import GoalOptimizationRequest
from yuxi.schedule.goal_optimizer import optimize_project_finish
from yuxi.schedule.forward_engine import (
    CALENDAR_EXCEPTIONS_ENGINE_PROFILE_ID,
    CONSTRAINTS_ENGINE_PROFILE_ID,
    MULTI_CALENDAR_ENGINE_PROFILE_ID,
    calculate_minimal_forward_schedule,
    recalculation_profile_for,
)
from yuxi.schedule.importers import (
    UnsupportedScheduleImportVersionError,
    build_default_schedule_import_registry,
    import_canonical_schedule,
)

CASE_PATH = (
    Path(__file__).resolve().parents[4]
    / "Microsoft_Project_水泵站排期_MOCK_v1.1"
    / "Microsoft_Project_水泵站排期_MOCK_v1.1.json"
)
VARIANT_PATH = (
    Path(__file__).resolve().parents[4]
    / "Microsoft_Project_水泵站排期_MOCK_v1.1"
    / "Microsoft_Project_水泵站排期_MOCK_v1.1_no_milestones.json"
)


@pytest.fixture
def water_pump_import_document() -> dict:
    return json.loads(CASE_PATH.read_text(encoding="utf-8"))


def test_import_envelope_requires_core_identity_and_keeps_document_uninterpreted() -> None:
    envelope = ScheduleImportEnvelope.model_validate(
        {
            "request_id": "request-1",
            "external_project_id": "project-1",
            "external_snapshot_id": "snapshot-1",
            "external_revision": "v1.1",
            "document": {"schema_version": "vendor-v1", "vendor_extension": {"enabled": True}},
        }
    )

    assert envelope.document["vendor_extension"] == {"enabled": True}

    with pytest.raises(ValidationError):
        ScheduleImportEnvelope.model_validate(
            {
                "external_project_id": "project-1",
                "external_snapshot_id": "snapshot-1",
                "external_revision": "v1.1",
                "document": {},
            }
        )


def test_source_contract_preserves_unknown_fields_but_rejects_missing_or_wrong_declared_fields(
    water_pump_import_document: dict,
) -> None:
    extended = copy.deepcopy(water_pump_import_document)
    extended["vendor_extension"] = {"display_color": "blue"}
    extended["tasks"][0]["vendor_task_code"] = "ROOT"

    contract = MicrosoftProjectInterchangeV11.model_validate(extended)

    assert contract.model_extra["vendor_extension"] == {"display_color": "blue"}
    assert contract.tasks[0].model_extra["vendor_task_code"] == "ROOT"

    missing = copy.deepcopy(water_pump_import_document)
    del missing["project"]["default_calendar_id"]
    with pytest.raises(ValidationError, match="Field required"):
        MicrosoftProjectInterchangeV11.model_validate(missing)

    wrong_type = copy.deepcopy(water_pump_import_document)
    wrong_type["tasks"][0]["source_id"] = "1"
    with pytest.raises(ValidationError, match="valid integer"):
        MicrosoftProjectInterchangeV11.model_validate(wrong_type)


def test_source_contract_rejects_unknown_references_and_parent_cycles(water_pump_import_document: dict) -> None:
    unknown_reference = copy.deepcopy(water_pump_import_document)
    unknown_reference["dependencies"][0]["predecessor_task_id"] = "task:missing"
    with pytest.raises(ValidationError, match="references an unknown task"):
        MicrosoftProjectInterchangeV11.model_validate(unknown_reference)

    parent_cycle = copy.deepcopy(water_pump_import_document)
    parent_cycle["tasks"][0]["parent_task_id"] = parent_cycle["tasks"][1]["task_id"]
    with pytest.raises(ValidationError, match="parent hierarchy contains a cycle"):
        MicrosoftProjectInterchangeV11.model_validate(parent_cycle)


def test_registry_rejects_unsupported_source_version(water_pump_import_document: dict) -> None:
    document = copy.deepcopy(water_pump_import_document)
    document["schema_version"] = "unsupported_schedule_v1"

    with pytest.raises(UnsupportedScheduleImportVersionError, match="unsupported Schedule import version"):
        build_default_schedule_import_registry().normalize(document)


def test_formal_interchange_contract_only_publishes_maintained_version(
    water_pump_import_document: dict,
) -> None:
    formal = copy.deepcopy(water_pump_import_document)
    formal["schema_version"] = "microsoft_project_interchange_v1.1"

    MicrosoftProjectInterchangeFormalV11.model_validate(formal)

    with pytest.raises(ValidationError, match="microsoft_project_interchange_v1.1"):
        MicrosoftProjectInterchangeFormalV11.model_validate(water_pump_import_document)


def test_formal_interchange_adapter_preserves_explicit_constraint_fields(
    water_pump_import_document: dict,
) -> None:
    document = copy.deepcopy(water_pump_import_document)
    document["schema_version"] = "microsoft_project_interchange_v1.1"
    document["project"]["required_finish"] = document["project"]["planned_finish"]
    activity = next(task for task in document["tasks"] if task["task_type"] == "TASK")
    activity["constraint_type_code"] = 2
    activity["constraint_date"] = activity["start"]
    activity["deadline"] = activity["finish"]

    result = build_default_schedule_import_registry().normalize(document)

    assert result.normalization_report.source_schema_version == (
        "microsoft_project_interchange_v1.1"
    )
    assert result.normalization_report.adapter_id == "microsoft_project_interchange_v1_1"
    assert result.normalization_report.adapter_version == "1.6.0"
    assert isinstance(result.canonical, CanonicalScheduleV25)
    canonical_activity = next(
        task for task in result.canonical.tasks if task.task_id == activity["task_id"]
    )
    assert canonical_activity.constraint.type == "MUST_START_ON"
    assert canonical_activity.constraint.date == canonical_activity.planned_start
    assert canonical_activity.deadline == canonical_activity.planned_finish


def test_legacy_mock_interchange_remains_registered(water_pump_import_document: dict) -> None:
    result = build_default_schedule_import_registry().normalize(water_pump_import_document)

    assert result.normalization_report.source_schema_version == (
        "microsoft_project_interchange_mock_v1.1"
    )
    assert result.normalization_report.adapter_version == "1.6.0"


def test_water_pump_adapter_recomputes_strict_canonical_and_expected_audit(
    water_pump_import_document: dict,
) -> None:
    result = build_default_schedule_import_registry().normalize(water_pump_import_document)
    canonical = result.canonical
    execution = audit_schedule(
        import_canonical_schedule(canonical),
        schedule_snapshot_id=canonical.snapshot_id,
        audit_run_id="audit:water-pump-import",
    )

    assert canonical.statistics.model_dump() == {
        "tasks": 21,
        "summary_tasks": 5,
        "leaf_tasks": 16,
        "milestones": 4,
        "dependencies": 19,
        "dependency_types": {"FS": 16, "SS": 3, "FF": 0, "SF": 0},
        "positive_lag_dependencies": 3,
        "negative_lag_dependencies": 0,
        "calendars": 1,
        "resources_raw": 0,
        "assignments": 0,
        "open_start_tasks": 1,
        "open_finish_tasks": 1,
        "summary_task_dependencies": 0,
        "source_schedule_dependency_violations": 0,
    }
    assert canonical.schema_version == "canonical_schedule_v2.3"
    assert canonical.capabilities.cpm_recalculation.model_dump() == {"allowed": True, "reasons": []}
    assert canonical.semantics.lag_calendar_policy == "UNIFIED_PROJECT_CALENDAR_WORKING_MINUTES"
    assert execution.result.dependency_date_checks.model_dump() == {
        "checked": 19,
        "skipped": 0,
        "violation_count": 0,
        "skipped_reasons": {},
    }
    assert not any(finding.rule_id == "LAG_CALENDAR_POLICY_UNSPECIFIED" for finding in execution.findings)
    assert not any(finding.rule_id == "LAG_DATE_VIOLATION" for finding in execution.findings)
    assert not any(finding.rule_id == "STATISTICS_MISMATCH" for finding in execution.findings)
    assert not any(finding.rule_id == "SOURCE_CAPABILITY_MISMATCH" for finding in execution.findings)
    CanonicalScheduleV23.model_validate(canonical.model_dump(mode="json"))


def test_adapter_emits_v24_for_structured_calendar_exceptions(
    water_pump_import_document: dict,
) -> None:
    document = copy.deepcopy(water_pump_import_document)
    document["calendars"][0]["exceptions"] = [
        {
            "exception_id": "future-holiday",
            "name": "未来停工日",
            "start_date": "2030-01-01T00:00:00+08:00",
            "finish_date": "2030-01-01T23:59:59+08:00",
            "working": False,
            "intervals": [],
        }
    ]

    result = build_default_schedule_import_registry().normalize(document)

    assert isinstance(result.canonical, CanonicalScheduleV24)
    assert result.canonical.capabilities.cpm_recalculation.allowed is True
    assert recalculation_profile_for(result.canonical) == CALENDAR_EXCEPTIONS_ENGINE_PROFILE_ID
    assert result.canonical.calendars[0].exceptions[0].exception_id == "future-holiday"


def test_adapter_emits_v24_successor_calendar_policy_for_task_calendars(
    water_pump_import_document: dict,
) -> None:
    document = copy.deepcopy(water_pump_import_document)
    task_calendar = copy.deepcopy(document["calendars"][0])
    task_calendar["calendar_id"] = "calendar:six-day"
    task_calendar["name"] = "六天任务日历"
    saturday = next(day for day in task_calendar["week_days"] if day["day"] == "SATURDAY")
    saturday.update(
        {
            "working": True,
            "intervals": [
                {"start": "07:00:00", "finish": "12:00:00"},
                {"start": "13:00:00", "finish": "18:00:00"},
            ],
        }
    )
    document["calendars"].append(task_calendar)
    next(task for task in document["tasks"] if task["task_type"] == "TASK")["calendar_id"] = (
        task_calendar["calendar_id"]
    )

    result = build_default_schedule_import_registry().normalize(document)

    assert isinstance(result.canonical, CanonicalScheduleV24)
    assert result.canonical.semantics.lag_calendar_policy == "SUCCESSOR_TASK_CALENDAR"
    assert {
        dependency.lag_calendar_policy for dependency in result.canonical.dependencies
    } == {"SUCCESSOR_TASK_CALENDAR"}
    assert result.canonical.capabilities.cpm_recalculation.allowed is True
    assert recalculation_profile_for(result.canonical) == MULTI_CALENDAR_ENGINE_PROFILE_ID


def test_adapter_emits_v25_for_traceable_constraints_and_deadline(
    water_pump_import_document: dict,
) -> None:
    document = copy.deepcopy(water_pump_import_document)
    activities = [task for task in document["tasks"] if task["task_type"] == "TASK"]
    activities[0]["constraint_type_code"] = 2
    activities[0]["constraint_date"] = activities[0]["start"]
    activities[1]["constraint_type_code"] = 7
    activities[1]["constraint_date"] = activities[1]["finish"]
    activities[1]["deadline"] = activities[1]["finish"]
    document["project"]["required_finish"] = document["project"]["planned_finish"]

    result = build_default_schedule_import_registry().normalize(document)

    assert isinstance(result.canonical, CanonicalScheduleV25)
    assert result.normalization_report.adapter_version == "1.6.0"
    assert result.canonical.semantics.lag_calendar_policy == "SUCCESSOR_TASK_CALENDAR"
    assert result.canonical.project.required_finish == result.canonical.project.planned_finish
    constraints = {task.task_id: task.constraint.type for task in result.canonical.tasks}
    assert constraints[activities[0]["task_id"]] == "MUST_START_ON"
    assert constraints[activities[1]["task_id"]] == "FINISH_NO_LATER_THAN"
    assert recalculation_profile_for(result.canonical) == CONSTRAINTS_ENGINE_PROFILE_ID


def test_adapter_emits_v28_only_when_source_supplies_inactive_fact(
    water_pump_import_document: dict,
) -> None:
    document = copy.deepcopy(water_pump_import_document)
    inactive = next(task for task in document["tasks"] if task["task_type"] == "TASK")
    inactive["active"] = False
    inactive["percent_complete"] = 0

    result = build_default_schedule_import_registry().normalize(document)

    assert isinstance(result.canonical, CanonicalScheduleV28)
    assert result.normalization_report.adapter_version == "1.6.0"
    canonical_task = next(
        task for task in result.canonical.tasks if task.task_id == inactive["task_id"]
    )
    assert canonical_task.active is False
    assert "INACTIVE_TASK_DEPENDENCIES" in result.canonical.capabilities.cpm_recalculation.reasons


def test_adapter_does_not_synthesize_v28_when_source_tasks_are_active(
    water_pump_import_document: dict,
) -> None:
    document = copy.deepcopy(water_pump_import_document)
    for task in document["tasks"]:
        task["active"] = True

    result = build_default_schedule_import_registry().normalize(document)

    assert not isinstance(result.canonical, CanonicalScheduleV28)
    assert isinstance(result.canonical, CanonicalScheduleV23)


def test_adapter_rejects_invalid_structured_calendar_exception(
    water_pump_import_document: dict,
) -> None:
    document = copy.deepcopy(water_pump_import_document)
    document["calendars"][0]["exceptions"] = [
        {
            "exception_id": "invalid-holiday",
            "name": "非法停工日",
            "start_date": "2030-01-01T00:00:00+08:00",
            "finish_date": "2030-01-01T23:59:59+08:00",
            "working": False,
            "intervals": [{"start": "08:00:00", "finish": "12:00:00"}],
        }
    ]

    with pytest.raises(ValidationError, match="working flag must match intervals"):
        build_default_schedule_import_registry().normalize(document)


def test_adapter_preserves_overlapping_exceptions_as_explicit_v10_blocker(
    water_pump_import_document: dict,
) -> None:
    document = copy.deepcopy(water_pump_import_document)
    document["calendars"][0]["exceptions"] = [
        {
            "exception_id": "shutdown-a",
            "name": "停工 A",
            "start_date": "2030-01-01T00:00:00+08:00",
            "finish_date": "2030-01-02T23:59:59+08:00",
            "working": False,
            "intervals": [],
        },
        {
            "exception_id": "shutdown-b",
            "name": "停工 B",
            "start_date": "2030-01-02T00:00:00+08:00",
            "finish_date": "2030-01-03T23:59:59+08:00",
            "working": False,
            "intervals": [],
        },
    ]

    result = build_default_schedule_import_registry().normalize(document)
    engine_result = calculate_minimal_forward_schedule(
        result.canonical,
        engine_profile_id=recalculation_profile_for(result.canonical),
    )

    assert result.canonical.capabilities.cpm_recalculation.model_dump() == {
        "allowed": False,
        "reasons": ["CALENDAR_EXCEPTIONS_CONFLICT"],
    }
    assert engine_result["status"] == "blocked"
    assert engine_result["support"]["blockers"] == [
        {
            "code": "CALENDAR_EXCEPTIONS_CONFLICT",
            "object_refs": ["shutdown-a", "shutdown-b"],
            "message": "同一项目日历的例外日期范围不能重叠",
        }
    ]


def test_water_pump_adapter_preserves_extensions_and_ignores_source_claims(
    water_pump_import_document: dict,
) -> None:
    document = copy.deepcopy(water_pump_import_document)
    document["vendor_extension"] = {"display_only": True}
    document["statistics"] = {"tasks": 999}
    document["capabilities"] = {"cpm_schedule_test": True}
    document["validation"] = {"status": "VENDOR_PASS"}

    result = build_default_schedule_import_registry().normalize(document)
    report = result.normalization_report

    assert result.source_document == document
    assert result.canonical.statistics.tasks == 21
    assert result.canonical.capabilities.cpm_recalculation.allowed is True
    assert "/vendor_extension" in report.preserved_fields
    assert {
        "/statistics",
        "/capabilities",
        "/validation",
        "/vendor_extension",
    } <= {item.path for item in report.ignored_for_audit}
    assert "SOURCE_CALCULATION_UNAVAILABLE" in {item.code for item in report.unsupported_semantics}


@pytest.mark.parametrize("evidence_field", ["opened_after_save", "project_recalculated_after_reopen"])
def test_adapter_blocks_source_review_when_reopen_evidence_is_false(
    water_pump_import_document: dict,
    evidence_field: str,
) -> None:
    document = copy.deepcopy(water_pump_import_document)
    document["source"][evidence_field] = False

    result = build_default_schedule_import_registry().normalize(document)
    execution = audit_schedule(
        import_canonical_schedule(result.canonical),
        schedule_snapshot_id=result.canonical.snapshot_id,
        audit_run_id="audit:source-fidelity-invalid",
    )

    assert result.canonical.validation.summary.source_fidelity_valid is False
    assert result.canonical.capabilities.source_schedule_review.model_dump() == {
        "allowed": False,
        "reasons": ["SOURCE_FIDELITY_INVALID"],
    }
    assert execution.result.capabilities["source_schedule_review"].model_dump() == {
        "allowed": False,
        "reasons": ["SOURCE_FIDELITY_INVALID"],
    }
    assert "SOURCE_FIDELITY_INVALID" in {item.code for item in result.normalization_report.unsupported_semantics}


def test_adapter_omits_milestone_unsupported_when_source_has_no_milestones(
    water_pump_import_document: dict,
) -> None:
    document = copy.deepcopy(water_pump_import_document)
    for task in document["tasks"]:
        if task["task_type"] == "MILESTONE":
            task["task_type"] = "TASK"
            task["duration_minutes"] = 480

    result = build_default_schedule_import_registry().normalize(document)

    assert result.canonical.schema_version == "canonical_schedule_v2.2"
    CanonicalScheduleV22.model_validate(result.canonical.model_dump(mode="json"))
    assert result.canonical.capabilities.cpm_recalculation.allowed is True
    assert "MILESTONE_UNSUPPORTED" not in {item.code for item in result.normalization_report.unsupported_semantics}


def test_water_pump_adapter_is_deterministic_and_unknown_fields_do_not_change_canonical(
    water_pump_import_document: dict,
) -> None:
    registry = build_default_schedule_import_registry()
    first = registry.normalize(water_pump_import_document)
    repeated = registry.normalize(water_pump_import_document)
    extended_document = copy.deepcopy(water_pump_import_document)
    extended_document["vendor_extension"] = {"opaque": [1, 2, 3]}
    extended = registry.normalize(extended_document)

    assert _canonical_sha256(first.canonical) == _canonical_sha256(repeated.canonical)
    assert _canonical_sha256(first.canonical) == _canonical_sha256(extended.canonical)


def test_water_pump_adapter_rejects_unmodeled_resource_semantics(water_pump_import_document: dict) -> None:
    document = copy.deepcopy(water_pump_import_document)
    document["resources"] = [{"resource_id": "resource:1", "vendor_type": "crew"}]

    with pytest.raises(ValueError, match="without resources or assignments"):
        build_default_schedule_import_registry().normalize(document)


def test_water_pump_no_milestones_variant_unlocks_cpm_and_goal_optimization() -> None:
    document = json.loads(VARIANT_PATH.read_text(encoding="utf-8"))
    result = build_default_schedule_import_registry().normalize(document)
    canonical = result.canonical
    execution = audit_schedule(
        import_canonical_schedule(canonical),
        schedule_snapshot_id=canonical.snapshot_id,
        audit_run_id="audit:no-milestones-variant",
    )

    assert execution.result.statistics["milestones"] == 0
    assert execution.result.capabilities["cpm_recalculation"].model_dump() == {
        "allowed": True,
        "reasons": [],
    }
    assert execution.result.dependency_date_checks.model_dump() == {
        "checked": 19,
        "skipped": 0,
        "violation_count": 0,
        "skipped_reasons": {},
    }
    assert not any(finding.severity == "blocker" for finding in execution.findings)

    request = GoalOptimizationRequest(
        request_id="goal-variant-unit",
        base_snapshot_content_sha256="sha256:" + "0" * 64,
        objective="MINIMIZE_PROJECT_FINISH",
        authorized_duration_options=[{"task_id": "task:18", "duration_minutes": 720}],
        locked_task_ids=[],
        authorization_confirmed=True,
    )
    optimized = optimize_project_finish(canonical, request)

    assert optimized["status"] == "calculated"
    assert optimized["finish_after"] < optimized["finish_before"]


def _canonical_sha256(canonical: CanonicalScheduleV22) -> str:
    serialized = json.dumps(
        canonical.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()
