from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22
from yuxi.schedule.contracts.envelope import ScheduleSnapshotSubmission
from yuxi.schedule.contracts.errors import validation_error_to_schedule_detail


def test_sanitized_schedule_satisfies_canonical_v2_2(canonical_schedule_payload: dict) -> None:
    schedule = CanonicalScheduleV22.model_validate(canonical_schedule_payload)

    assert len(schedule.tasks) == 90
    assert len(schedule.dependencies) == 90
    assert schedule.statistics.summary_tasks == 10


@pytest.mark.parametrize(
    ("mutate", "expected_message"),
    [
        (lambda data: data["tasks"].append(copy.deepcopy(data["tasks"][0])), "duplicate task_id"),
        (
            lambda data: data["dependencies"].append(copy.deepcopy(data["dependencies"][0])),
            "duplicate dependency_id",
        ),
        (lambda data: data["calendars"].append(copy.deepcopy(data["calendars"][0])), "duplicate calendar_id"),
        (lambda data: data["resources"].append(copy.deepcopy(data["resources"][0])), "duplicate resource_id"),
        (
            lambda data: data["dependencies"][0].update({"predecessor_task_id": "task:missing"}),
            "references an unknown task",
        ),
        (
            lambda data: data["project"].update({"default_calendar_id": "calendar:missing"}),
            "unknown calendar",
        ),
        (
            lambda data: data["tasks"][0].update({"effective_calendar_id": "calendar:missing"}),
            "unknown effective calendar",
        ),
        (
            lambda data: data["calendars"][0].update({"parent_calendar_id": "calendar:missing"}),
            "unknown parent calendar",
        ),
        (lambda data: data["tasks"][0].update({"parent_task_id": data["tasks"][0]["task_id"]}), "contains a cycle"),
    ],
)
def test_semantic_boundary_rejects_invalid_identity_or_reference(
    canonical_schedule_payload: dict,
    mutate,
    expected_message: str,
) -> None:
    payload = copy.deepcopy(canonical_schedule_payload)
    mutate(payload)

    with pytest.raises(ValidationError, match=expected_message):
        CanonicalScheduleV22.model_validate(payload)


def test_contract_rejects_datetime_without_timezone(canonical_schedule_payload: dict) -> None:
    payload = copy.deepcopy(canonical_schedule_payload)
    payload["tasks"][0]["planned_start"] = "2028-01-01T08:00:00"

    with pytest.raises(ValidationError):
        CanonicalScheduleV22.model_validate(payload)


def test_contract_preserves_uninterpreted_assignments(canonical_schedule_payload: dict) -> None:
    payload = copy.deepcopy(canonical_schedule_payload)
    payload["assignments"] = [{"source_assignment_fact": "preserved"}]

    schedule = CanonicalScheduleV22.model_validate(payload)

    assert schedule.assignments == [{"source_assignment_fact": "preserved"}]


def test_contract_rejects_unfrozen_lag_policy(canonical_schedule_payload: dict) -> None:
    payload = copy.deepcopy(canonical_schedule_payload)
    payload["semantics"]["lag_calendar_policy"] = "invented-policy"

    with pytest.raises(ValidationError):
        CanonicalScheduleV22.model_validate(payload)


def test_contract_enforces_task_collection_limit(canonical_schedule_payload: dict) -> None:
    payload = copy.deepcopy(canonical_schedule_payload)
    payload["tasks"] = [copy.deepcopy(payload["tasks"][0]) for _ in range(5_001)]

    with pytest.raises(ValidationError, match="at most 5000 items"):
        CanonicalScheduleV22.model_validate(payload)


def test_envelope_validation_error_uses_json_pointer_without_input_value(canonical_schedule_payload: dict) -> None:
    payload = {
        "request_id": "request-1",
        "external_project_id": "project-1",
        "external_snapshot_id": "snapshot-1",
        "external_revision": "v1",
        "snapshot": copy.deepcopy(canonical_schedule_payload),
    }
    payload["snapshot"]["tasks"][0]["planned_start"] = "2028-01-01T08:00:00"

    with pytest.raises(ValidationError) as captured:
        ScheduleSnapshotSubmission.model_validate(payload)

    detail = validation_error_to_schedule_detail(captured.value)
    assert detail.errors[0].path == "/snapshot/tasks/0/planned_start"
    assert detail.errors[0].code == "INVALID_DATETIME"
    assert "2028-01-01" not in detail.model_dump_json()
