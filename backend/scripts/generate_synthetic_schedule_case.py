"""Generate a deterministic synthetic Schedule case for protocol regression."""

from __future__ import annotations

import hashlib
import json
import uuid
from argparse import ArgumentParser
from collections import Counter
from pathlib import Path

from yuxi.schedule.audit.engine import audit_schedule
from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22
from yuxi.schedule.importers.canonical_v2_2 import import_canonical_schedule_v2_2

SNAPSHOT_ID = "snapshot:synthetic-case-s-v1"
CALENDAR_ID = "calendar:synthetic-standard"


def build_synthetic_schedule_case() -> dict:
    """Build a small case from explicit synthetic facts, never from a business fixture."""

    tasks = [
        _task("root", None, "1", 1, "合成项目总览", "summary", "2026-09-01", "2026-09-30"),
        _task("phase-a", "root", "1.1", 2, "合成阶段甲", "summary", "2026-09-01", "2026-09-15"),
        _task("design", "phase-a", "1.1.1", 3, "合成方案确认", "activity", "2026-09-01", "2026-09-03"),
        _task("build", "phase-a", "1.1.2", 3, "合成实施分组", "summary", "2026-09-04", "2026-09-15"),
        _task("build-a", "build", "1.1.2.1", 4, "合成实施任务甲", "activity", "2026-09-04", "2026-09-08"),
        _task("build-b", "build", "1.1.2.2", 4, "合成实施任务乙", "activity", "2026-09-09", "2026-09-15"),
        _task("phase-b", "root", "1.2", 2, "合成阶段乙", "summary", "2026-09-16", "2026-09-30"),
        _task("verify", "phase-b", "1.2.1", 3, "合成验证任务", "activity", "2026-09-16", "2026-09-20"),
        _task("handover", "phase-b", "1.2.2", 3, "合成交付任务", "activity", "2026-09-21", "2026-09-30"),
    ]
    dependencies = [
        _dependency("design-build-a", "design", "build-a", "FS", 0),
        _dependency("build-a-build-b", "build-a", "build-b", "SS", 0),
        _dependency("phase-a-verify", "phase-a", "verify", "FF", 0),
        _dependency("verify-handover", "verify", "handover", "SF", 0),
        _dependency("build-b-handover", "build-b", "handover", "FS", 480),
    ]
    generated_at = "2026-08-13T00:00:00+08:00"
    source_sha256 = hashlib.sha256(b"yuxi-synthetic-schedule-case-s-v1").hexdigest()
    source = {
        "schema_version": "canonical_schedule_v2.2",
        "snapshot_id": SNAPSHOT_ID,
        "generated_at": generated_at,
        "source": {
            "format": "SYNTHETIC_TEST_DATA",
            "file_name": "synthetic-schedule-case-s.json",
            "sha256": source_sha256,
            "extraction_method": "DETERMINISTIC_CASE_FACTORY",
            "extraction_application_version": "1",
            "opened_read_only": True,
        },
        "semantics": {
            "time_zone": "Asia/Shanghai",
            "time_zone_source": "SYNTHETIC_CASE_DECLARATION",
            "duration_storage_unit": "working_minute",
            "lag_storage_unit": "working_minute",
            "lag_calendar_policy": "UNSPECIFIED_REQUIRES_ENGINE_PROFILE",
            "task_calendar_resolution": "PROJECT_DEFAULT_ONLY",
            "source_dates_preserved": True,
        },
        "project": {
            "project_id": "project:synthetic-case-s",
            "name": "合成协议回归案例 S",
            "source_file_name": "synthetic-schedule-case-s.json",
            "planned_start": "2026-09-01T08:00:00+08:00",
            "planned_finish": "2026-09-30T17:00:00+08:00",
            "planned_date_source": "SYNTHETIC_CASE_DECLARATION",
            "source_project_summary": {
                "source_unique_id": 0,
                "name": "合成协议回归案例 S",
                "start": "2026-09-01T08:00:00+08:00",
                "finish": "2026-09-30T17:00:00+08:00",
                "duration_minutes": 10560,
            },
            "current_date": generated_at,
            "status_date": None,
            "default_calendar_id": CALENDAR_ID,
            "default_daily_work_minutes": 480,
            "default_calendar_weekly_work_minutes": 2400,
        },
        "statistics": _empty_statistics(),
        "capabilities": _empty_capabilities(),
        "calendars": [_calendar()],
        "resources": [],
        "tasks": tasks,
        "dependencies": dependencies,
        "assignments": [],
        "validation": _validation(generated_at, source_sha256, tasks, dependencies),
    }
    contract = CanonicalScheduleV22.model_validate(source)
    execution = audit_schedule(
        import_canonical_schedule_v2_2(contract),
        schedule_snapshot_id=SNAPSHOT_ID,
        audit_run_id="audit:synthetic-case-s-v1",
    )
    source["statistics"] = execution.result.statistics
    source["capabilities"] = {
        name: capability.model_dump(mode="json")
        for name, capability in execution.result.capabilities.items()
    }
    source["validation"]["capabilities"] = source["capabilities"]
    source["validation"]["summary"]["recalculation_allowed"] = source["capabilities"][
        "cpm_recalculation"
    ]["allowed"]
    return CanonicalScheduleV22.model_validate(source).model_dump(mode="json", exclude_none=False)


def _task(
    task_id: str,
    parent_task_id: str | None,
    wbs: str,
    outline_level: int,
    name: str,
    task_type: str,
    start: str,
    finish: str,
) -> dict:
    planned_start = f"{start}T08:00:00+08:00"
    planned_finish = f"{finish}T17:00:00+08:00"
    duration_minutes = 0 if start == finish else 480
    return {
        "task_id": f"synthetic-task:{task_id}",
        "source_id": len(wbs.replace(".", "")),
        "source_unique_id": int(hashlib.sha256(task_id.encode()).hexdigest()[:7], 16),
        "source_guid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"yuxi-synthetic-task:{task_id}")),
        "parent_task_id": f"synthetic-task:{parent_task_id}" if parent_task_id else None,
        "wbs": wbs,
        "outline_level": outline_level,
        "name": name,
        "task_type": task_type,
        "active": True,
        "scheduling_mode": "automatic",
        "project_task_type": "FIXED_DURATION" if task_type == "summary" else "FIXED_UNITS",
        "calendar_id": None,
        "effective_calendar_id": CALENDAR_ID,
        "planned_start": planned_start,
        "planned_finish": planned_finish,
        "duration_minutes": duration_minutes,
        "source_work_minutes": 0,
        "percent_complete": 0,
        "actual_start": None,
        "actual_finish": None,
        "deadline": None,
        "constraint": {"type": "AS_SOON_AS_POSSIBLE", "date": None},
        "baseline_0": {"exists": False, "start": None, "finish": None},
        "source_calculation": {
            "early_start": planned_start,
            "early_finish": planned_finish,
            "late_start": planned_start,
            "late_finish": planned_finish,
            "total_slack_minutes": 0,
            "free_slack_minutes": 0,
            "critical": False,
        },
        "source_resource_names_text": None,
        "notes": "仅用于 synthetic 协议回归，不代表真实业务计划。",
    }


def _dependency(
    dependency_id: str,
    predecessor_task_id: str,
    successor_task_id: str,
    relation_type: str,
    lag_minutes: int,
) -> dict:
    return {
        "dependency_id": f"synthetic-dependency:{dependency_id}",
        "predecessor_task_id": f"synthetic-task:{predecessor_task_id}",
        "successor_task_id": f"synthetic-task:{successor_task_id}",
        "type": relation_type,
        "source_type_code": {"FF": 0, "FS": 1, "SF": 2, "SS": 3}[relation_type],
        "lag_minutes": lag_minutes,
        "lag_calendar_policy": "UNSPECIFIED_REQUIRES_ENGINE_PROFILE",
    }


def _calendar() -> dict:
    intervals = [{"start": "08:00", "finish": "12:00"}, {"start": "13:00", "finish": "17:00"}]
    return {
        "calendar_id": CALENDAR_ID,
        "source_index": 1,
        "name": "合成标准五日历",
        "parent_calendar_id": None,
        "weekly_pattern": {
            day: {
                "day_type": "WORKING" if day not in {"SATURDAY", "SUNDAY"} else "NON_WORKING",
                "intervals": intervals if day not in {"SATURDAY", "SUNDAY"} else [],
            }
            for day in ("SUNDAY", "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY")
        },
        "exceptions": [],
    }


def _empty_statistics() -> dict:
    return {
        "tasks": 0,
        "summary_tasks": 0,
        "leaf_tasks": 0,
        "milestones": 0,
        "dependencies": 0,
        "dependency_types": {"FS": 0, "SS": 0, "FF": 0, "SF": 0},
        "positive_lag_dependencies": 0,
        "negative_lag_dependencies": 0,
        "calendars": 0,
        "resources_raw": 0,
        "assignments": 0,
        "open_start_tasks": 0,
        "open_finish_tasks": 0,
        "summary_task_dependencies": 0,
        "source_schedule_dependency_violations": 0,
    }


def _empty_capabilities() -> dict:
    return {
        name: {"allowed": False, "reasons": ["SYNTHETIC_PROJECTION_PENDING"]}
        for name in (
            "gantt_display",
            "source_schedule_review",
            "cpm_recalculation",
            "resource_leveling",
            "resource_cost_optimization",
        )
    }


def _validation(
    generated_at: str,
    source_sha256: str,
    tasks: list[dict],
    dependencies: list[dict],
) -> dict:
    leaf_ids = {task["task_id"] for task in tasks if task["task_type"] == "activity"}
    summary_ids = {task["task_id"] for task in tasks if task["task_type"] == "summary"}
    incoming = Counter(item["successor_task_id"] for item in dependencies)
    outgoing = Counter(item["predecessor_task_id"] for item in dependencies)
    summary_dependency_ids = [
        item["dependency_id"]
        for item in dependencies
        if item["predecessor_task_id"] in summary_ids or item["successor_task_id"] in summary_ids
    ]
    return {
        "schema_version": "schedule_validation_report_v2.2",
        "snapshot_id": SNAPSHOT_ID,
        "generated_at": generated_at,
        "source_sha256": source_sha256,
        "summary": {
            "status": "blocked_for_recalculation",
            "issue_count": 0,
            "blocker_count": 0,
            "warning_count": 0,
            "source_fidelity_valid": True,
            "recalculation_allowed": False,
            "display_allowed": True,
        },
        "source_fidelity_checks": {
            "tasks_match_source": True,
            "dependencies_match_source": True,
            "calendars_match_source": True,
            "resources_match_source": True,
            "assignments_match_source": True,
            "synthetic_project_summary_added": False,
            "synthetic_resources_added": False,
            "synthetic_assignments_added": False,
            "project_dates_resolved_from_source_summary": True,
        },
        "network_quality": {
            "open_start_task_ids": sorted(task_id for task_id in leaf_ids if not incoming[task_id]),
            "open_finish_task_ids": sorted(task_id for task_id in leaf_ids if not outgoing[task_id]),
            "summary_task_dependency_ids": summary_dependency_ids,
            "source_schedule_violations": [],
        },
        "capabilities": _empty_capabilities(),
        "source_vs_conversion_assessment": {
            "source_mpp_findings": [],
            "source_format_limitations": [],
            "conversion_defects_fixed_in_v2_2": [],
            "engine_contract_decisions_required": [],
        },
        "issues": [],
    }


if __name__ == "__main__":
    parser = ArgumentParser(description="生成确定性的 synthetic Schedule 协议回归案例")
    parser.add_argument("--output", type=Path, help="写入 JSON 文件；未指定时输出到标准输出")
    args = parser.parse_args()
    content = json.dumps(build_synthetic_schedule_case(), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content, end="")
