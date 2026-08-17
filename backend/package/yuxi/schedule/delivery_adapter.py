"""Controlled source-system adapter for technically applicable dependency Deliveries."""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from datetime import datetime
from typing import Any

from yuxi.schedule.audit.engine import audit_schedule
from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22
from yuxi.schedule.importers.canonical_v2_2 import import_canonical_schedule_v2_2

DELIVERY_SCHEMA_VERSION = "schedule_delivery_draft_v0"
DEPENDENCY_NORMALIZATION_KIND = "dependency_normalization"


class ScheduleDeliveryApplicationError(ValueError):
    pass


def apply_delivery_to_source_copy(
    source: dict[str, Any],
    delivery: dict[str, Any],
    *,
    new_source_snapshot_id: str,
    generated_at: datetime,
) -> dict[str, Any]:
    """Return a validated source copy after the caller's external approval."""

    source_contract = CanonicalScheduleV22.model_validate(source)
    if delivery.get("delivery_schema_version") != DELIVERY_SCHEMA_VERSION:
        raise ScheduleDeliveryApplicationError("unsupported delivery schema version")
    if delivery.get("candidate_kind") != DEPENDENCY_NORMALIZATION_KIND:
        raise ScheduleDeliveryApplicationError("unsupported candidate kind")
    if delivery.get("candidate_status") != "valid" or not delivery.get("application_allowed"):
        raise ScheduleDeliveryApplicationError("candidate is not eligible for application")
    if delivery.get("application_blocking_reasons"):
        raise ScheduleDeliveryApplicationError("delivery contains application blocking reasons")
    if delivery.get("base_snapshot_content_sha256") != _content_sha256(source_contract):
        raise ScheduleDeliveryApplicationError("base snapshot hash does not match delivery")

    result = copy.deepcopy(source_contract.model_dump(mode="json", exclude_none=False))
    dependencies = {item["dependency_id"]: item for item in result["dependencies"]}
    for operation in delivery["effective_patch"]["operations"]:
        if operation["operation"] == "remove_dependency":
            dependency_id = operation["dependency_id"]
            if dependencies.get(dependency_id) != operation["expected_before"]:
                raise ScheduleDeliveryApplicationError(f"remove precondition failed for dependency {dependency_id}")
            del dependencies[dependency_id]
        elif operation["operation"] == "add_dependency":
            dependency = operation["dependency"]
            dependency_id = dependency["dependency_id"]
            if operation["expected_before"] is not None or dependency_id in dependencies:
                raise ScheduleDeliveryApplicationError(f"add precondition failed for dependency {dependency_id}")
            dependencies[dependency_id] = dependency
        else:
            raise ScheduleDeliveryApplicationError(f"unsupported delivery operation {operation['operation']}")

    result["dependencies"] = list(dependencies.values())
    result["snapshot_id"] = new_source_snapshot_id
    result["generated_at"] = generated_at.isoformat()
    result["source"]["sha256"] = _source_copy_sha256(source_contract, delivery, new_source_snapshot_id)
    result["source"]["extraction_method"] = "YUXI_CONTROLLED_DELIVERY_ADAPTER"
    result["source"]["extraction_application_version"] = DELIVERY_SCHEMA_VERSION
    result["source"]["opened_read_only"] = False

    projected = CanonicalScheduleV22.model_validate(result)
    execution = audit_schedule(
        import_canonical_schedule_v2_2(projected),
        schedule_snapshot_id=new_source_snapshot_id,
        audit_run_id=f"adapter:{delivery['candidate_snapshot_id']}",
    )
    result["statistics"] = execution.result.statistics
    result["capabilities"] = {
        name: capability.model_dump(mode="json") for name, capability in execution.result.capabilities.items()
    }
    _update_source_validation(result, delivery, execution.result.capabilities)
    return CanonicalScheduleV22.model_validate(result).model_dump(mode="json", exclude_none=False)


def _content_sha256(source: CanonicalScheduleV22) -> str:
    canonical_bytes = json.dumps(
        source.model_dump(mode="json", exclude_none=False),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical_bytes).hexdigest()}"


def _source_copy_sha256(
    source: CanonicalScheduleV22,
    delivery: dict[str, Any],
    new_source_snapshot_id: str,
) -> str:
    identity = json.dumps(
        {
            "base_source_sha256": source.source.sha256,
            "candidate_snapshot_id": delivery["candidate_snapshot_id"],
            "effective_patch": delivery["effective_patch"],
            "new_source_snapshot_id": new_source_snapshot_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def _update_source_validation(
    source: dict[str, Any],
    delivery: dict[str, Any],
    capabilities: dict[str, Any],
) -> None:
    validation = source["validation"]
    validation["snapshot_id"] = source["snapshot_id"]
    validation["generated_at"] = source["generated_at"]
    validation["source_sha256"] = source["source"]["sha256"]
    validation["capabilities"] = {name: capability.model_dump(mode="json") for name, capability in capabilities.items()}

    tasks = {task["task_id"]: task for task in source["tasks"]}
    dependencies = source["dependencies"]
    incoming = Counter(item["successor_task_id"] for item in dependencies)
    outgoing = Counter(item["predecessor_task_id"] for item in dependencies)
    leaf_ids = {task_id for task_id, task in tasks.items() if task["task_type"] != "summary"}
    summary_ids = {task_id for task_id, task in tasks.items() if task["task_type"] == "summary"}
    network_refs = {
        "OPEN_STARTS": sorted(task_id for task_id in leaf_ids if not incoming[task_id]),
        "OPEN_FINISHES": sorted(task_id for task_id in leaf_ids if not outgoing[task_id]),
        "SUMMARY_TASK_DEPENDENCIES": sorted(
            item["dependency_id"]
            for item in dependencies
            if item["predecessor_task_id"] in summary_ids or item["successor_task_id"] in summary_ids
        ),
    }

    network_quality = validation["network_quality"]
    network_quality["open_start_task_ids"] = network_refs["OPEN_STARTS"]
    network_quality["open_finish_task_ids"] = network_refs["OPEN_FINISHES"]
    network_quality["summary_task_dependency_ids"] = network_refs["SUMMARY_TASK_DEPENDENCIES"]

    assessment = validation["source_vs_conversion_assessment"]
    assessment["source_mpp_findings"] = _project_network_findings(assessment["source_mpp_findings"], network_refs)
    validation["issues"] = _project_network_findings(validation["issues"], network_refs)
    summary = validation["summary"]
    summary["issue_count"] = len(validation["issues"])
    summary["blocker_count"] = sum(item["severity"] == "blocker" for item in validation["issues"])
    summary["warning_count"] = sum(item["severity"] == "warning" for item in validation["issues"])
    summary["recalculation_allowed"] = capabilities["cpm_recalculation"].allowed
    summary["status"] = "valid" if summary["recalculation_allowed"] else "blocked_for_recalculation"

    removed_ids = {item["dependency_id"] for item in delivery["effective_patch"]["removed_dependencies"]}
    if removed_ids.intersection(network_refs["SUMMARY_TASK_DEPENDENCIES"]):
        raise ScheduleDeliveryApplicationError("removed summary dependency remains in source copy")


def _project_network_findings(
    findings: list[dict[str, Any]],
    network_refs: dict[str, list[str]],
) -> list[dict[str, Any]]:
    result = []
    for finding in findings:
        refs = network_refs.get(finding["code"])
        if refs is None:
            result.append(finding)
        elif refs:
            finding["object_refs"] = refs
            result.append(finding)
    return result
