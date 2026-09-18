"""Resource allocation findings based on preserved source task dates."""

from yuxi.schedule.audit.context import AuditContext
from yuxi.schedule.audit.rules.contract import _finding
from yuxi.schedule.domain.models import AuditFinding
from yuxi.schedule.resource_analyzer import analyze_snapshot_resources


def audit_resources(context: AuditContext) -> list[AuditFinding]:
    if not context.schedule.assignments:
        return []

    analysis = analyze_snapshot_resources(context.schedule)
    return [
        _finding(
            "RESOURCE_OVERALLOCATION",
            "resource",
            "warning",
            (conflict["resource_id"],),
            {
                "task_ids": conflict["task_ids"],
                "overlap_start": conflict["overlap_start"],
                "overlap_finish": conflict["overlap_finish"],
                "combined_units": conflict["combined_units"],
                "max_units": conflict["max_units"],
            },
            "资源在来源计划的重叠任务区间内超配。",
            "审阅资源冲突；当前阶段只分析，不会自动移动任务或执行资源平衡。",
        )
        for conflict in analysis["resource_conflicts"]
    ]
