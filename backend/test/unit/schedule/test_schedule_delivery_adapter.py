from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime

import pytest

from yuxi.schedule.delivery_adapter import (
    ScheduleDeliveryApplicationError,
    apply_delivery_to_source_copy,
)
from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22


def _content_sha256(source: dict) -> str:
    contract = CanonicalScheduleV22.model_validate(source)
    value = json.dumps(
        contract.model_dump(mode="json", exclude_none=False),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _delivery(source: dict) -> dict:
    removed = copy.deepcopy(next(item for item in source["dependencies"] if item["dependency_id"] == "dependency:81"))
    added = {
        "dependency_id": "candidate:test:1",
        "predecessor_task_id": "task:179",
        "successor_task_id": "task:236",
        "type": "FS",
        "source_type_code": 1,
        "lag_minutes": 480,
        "lag_calendar_policy": "UNSPECIFIED_REQUIRES_ENGINE_PROFILE",
    }
    operations = [
        {
            "operation_id": "remove:dependency:81",
            "operation": "remove_dependency",
            "dependency_id": "dependency:81",
            "expected_before": removed,
        },
        {
            "operation_id": "add:candidate:test:1",
            "operation": "add_dependency",
            "dependency": added,
            "expected_before": None,
        },
    ]
    return {
        "delivery_schema_version": "schedule_delivery_draft_v0",
        "candidate_snapshot_id": "candidate-test",
        "candidate_kind": "dependency_normalization",
        "candidate_status": "valid",
        "user_attitude": "accepted",
        "base_snapshot_content_sha256": _content_sha256(source),
        "application_allowed": True,
        "application_blocking_reasons": [],
        "effective_patch": {
            "operations": operations,
            "removed_dependencies": [removed],
            "added_dependencies": [added],
            "change_origin": "requested",
        },
    }


def test_adapter_applies_delivery_to_independent_consistent_source_copy(
    canonical_schedule_payload: dict,
) -> None:
    source_before = copy.deepcopy(canonical_schedule_payload)

    result = apply_delivery_to_source_copy(
        canonical_schedule_payload,
        _delivery(canonical_schedule_payload),
        new_source_snapshot_id="source-copy-1",
        generated_at=datetime(2026, 8, 12, 10, 40, tzinfo=UTC),
    )

    assert canonical_schedule_payload == source_before
    assert result["snapshot_id"] == "source-copy-1"
    assert not any(item["dependency_id"] == "dependency:81" for item in result["dependencies"])
    assert any(
        item["predecessor_task_id"] == "task:179"
        and item["successor_task_id"] == "task:236"
        and item["type"] == "FS"
        and item["lag_minutes"] == 480
        for item in result["dependencies"]
    )
    assert result["statistics"]["summary_task_dependencies"] == 3
    assert result["statistics"]["open_finish_tasks"] == 14
    assert "dependency:81" not in result["validation"]["network_quality"]["summary_task_dependency_ids"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidate_status", "invalid"),
        ("application_allowed", False),
        ("base_snapshot_content_sha256", "sha256:" + "0" * 64),
    ],
)
def test_adapter_rejects_delivery_that_is_not_safe_to_apply(
    canonical_schedule_payload: dict,
    field: str,
    value: object,
) -> None:
    delivery = _delivery(canonical_schedule_payload)
    delivery[field] = value

    with pytest.raises(ScheduleDeliveryApplicationError):
        apply_delivery_to_source_copy(
            canonical_schedule_payload,
            delivery,
            new_source_snapshot_id="source-copy-1",
            generated_at=datetime(2026, 8, 12, 10, 40, tzinfo=UTC),
        )


def test_adapter_rejects_changed_remove_precondition(canonical_schedule_payload: dict) -> None:
    delivery = _delivery(canonical_schedule_payload)
    delivery["effective_patch"]["operations"][0]["expected_before"]["lag_minutes"] = 0

    with pytest.raises(ScheduleDeliveryApplicationError):
        apply_delivery_to_source_copy(
            canonical_schedule_payload,
            delivery,
            new_source_snapshot_id="source-copy-1",
            generated_at=datetime(2026, 8, 12, 10, 40, tzinfo=UTC),
        )


def test_adapter_removes_stale_validation_when_last_summary_dependency_is_resolved(
    canonical_schedule_payload: dict,
) -> None:
    source = copy.deepcopy(canonical_schedule_payload)
    target = next(item for item in source["dependencies"] if item["dependency_id"] == "dependency:81")
    summary_dependency_ids = set(source["validation"]["network_quality"]["summary_task_dependency_ids"])
    source["dependencies"] = [
        item
        for item in source["dependencies"]
        if item["dependency_id"] == target["dependency_id"] or item["dependency_id"] not in summary_dependency_ids
    ]
    delivery = _delivery(source)

    result = apply_delivery_to_source_copy(
        source,
        delivery,
        new_source_snapshot_id="source-copy-last-summary",
        generated_at=datetime(2026, 8, 12, 11, 0, tzinfo=UTC),
    )

    assert result["statistics"]["summary_task_dependencies"] == 0
    assert result["validation"]["network_quality"]["summary_task_dependency_ids"] == []
    assert not any(
        item["code"] == "SUMMARY_TASK_DEPENDENCIES"
        for item in result["validation"]["source_vs_conversion_assessment"]["source_mpp_findings"]
    )
    assert not any(item["code"] == "SUMMARY_TASK_DEPENDENCIES" for item in result["validation"]["issues"])
    assert result["validation"]["summary"]["issue_count"] == len(result["validation"]["issues"])
