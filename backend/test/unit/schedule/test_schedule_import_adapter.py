from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from yuxi.schedule.audit.engine import audit_schedule
from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22
from yuxi.schedule.contracts.import_v1 import MicrosoftProjectInterchangeV11, ScheduleImportEnvelope
from yuxi.schedule.importers import (
    UnsupportedScheduleImportVersionError,
    build_default_schedule_import_registry,
    import_canonical_schedule_v2_2,
)

CASE_PATH = (
    Path(__file__).resolve().parents[4]
    / "Microsoft_Project_水泵站排期_MOCK_v1.1"
    / "Microsoft_Project_水泵站排期_MOCK_v1.1.json"
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


def test_water_pump_adapter_recomputes_strict_canonical_and_expected_audit(
    water_pump_import_document: dict,
) -> None:
    result = build_default_schedule_import_registry().normalize(water_pump_import_document)
    canonical = result.canonical
    execution = audit_schedule(
        import_canonical_schedule_v2_2(canonical),
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
    assert canonical.capabilities.cpm_recalculation.model_dump() == {
        "allowed": False,
        "reasons": ["MILESTONE_UNSUPPORTED"],
    }
    assert execution.result.dependency_date_checks.model_dump() == {
        "checked": 16,
        "skipped": 3,
        "violation_count": 0,
        "skipped_reasons": {"LAG_CALENDAR_POLICY_UNSPECIFIED": 3},
    }
    assert not any(finding.rule_id == "STATISTICS_MISMATCH" for finding in execution.findings)
    assert not any(finding.rule_id == "SOURCE_CAPABILITY_MISMATCH" for finding in execution.findings)
    CanonicalScheduleV22.model_validate(canonical.model_dump(mode="json"))


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
    assert result.canonical.capabilities.cpm_recalculation.allowed is False
    assert "/vendor_extension" in report.preserved_fields
    assert {
        "/statistics",
        "/capabilities",
        "/validation",
        "/vendor_extension",
    } <= {item.path for item in report.ignored_for_audit}
    assert "SOURCE_CALCULATION_UNAVAILABLE" in {item.code for item in report.unsupported_semantics}


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


def _canonical_sha256(canonical: CanonicalScheduleV22) -> str:
    serialized = json.dumps(
        canonical.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()
