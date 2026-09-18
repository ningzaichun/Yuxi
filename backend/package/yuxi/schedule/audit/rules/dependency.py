"""Dependency semantics supported by first-stage source-date review."""

from yuxi.schedule.audit.context import (
    AuditContext,
    _lag_calendar_for_dependency,
    dependency_date_is_valid,
    lag_date_is_valid,
)
from yuxi.schedule.audit.rules.contract import _finding
from yuxi.schedule.domain.models import AuditFinding


def audit_dependencies(context: AuditContext) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    if context.schedule.schema_version == "canonical_schedule_v2.8":
        for task in sorted(context.schedule.tasks, key=lambda item: item.task_id):
            if task.active:
                continue
            incident = sorted(
                (
                    dependency
                    for dependency in context.schedule.dependencies
                    if task.task_id
                    in {
                        dependency.predecessor_task_id,
                        dependency.successor_task_id,
                    }
                ),
                key=lambda item: item.dependency_id,
            )
            if not incident:
                continue
            findings.append(
                _finding(
                    "INACTIVE_TASK_DEPENDENCY",
                    "dependency",
                    "warning",
                    tuple(item.dependency_id for item in incident),
                    {
                        "inactive_task_ids": [task.task_id],
                        "source_dependency_ids": [item.dependency_id for item in incident],
                    },
                    "依赖关系涉及 inactive 任务，Yuxi 不会自动跨接活动网络。",
                    "在依赖决策工作台显式选择活动的前置和后续叶子任务。",
                )
            )
    for dependency in sorted(context.schedule.dependencies, key=lambda item: item.dependency_id):
        predecessor = context.tasks_by_id[dependency.predecessor_task_id]
        successor = context.tasks_by_id[dependency.successor_task_id]
        lag_calendar = _lag_calendar_for_dependency(
            context.lag_calendars,
            dependency,
            context.tasks_by_id,
        )
        if context.schedule.schema_version == "canonical_schedule_v2.8" and (
            not predecessor.active or not successor.active
        ):
            continue
        if predecessor.task_type == "summary" or successor.task_type == "summary":
            findings.append(
                _finding(
                    "SUMMARY_TASK_DEPENDENCY",
                    "dependency",
                    "warning",
                    (dependency.dependency_id,),
                    {
                        "predecessor_task_id": predecessor.task_id,
                        "successor_task_id": successor.task_id,
                    },
                    "依赖关系涉及汇总任务。",
                    "业务确认后将关系下沉到合适的叶子任务。",
                )
            )
        if dependency.lag_minutes == 0:
            if not dependency_date_is_valid(dependency.relation_type, predecessor, successor):
                findings.append(
                    _finding(
                        "ZERO_LAG_DATE_VIOLATION",
                        "dependency",
                        "blocker",
                        (dependency.dependency_id,),
                        {
                            "type": dependency.relation_type,
                            "predecessor_task_id": predecessor.task_id,
                            "successor_task_id": successor.task_id,
                        },
                        "零 Lag 关系不满足来源计划日期。",
                        "检查来源日期或依赖关系，修复后重新提交快照。",
                    )
                )
        elif lag_calendar is not None and not lag_date_is_valid(
            lag_calendar, dependency, context.tasks_by_id
        ):
            findings.append(
                _finding(
                    "LAG_DATE_VIOLATION",
                    "dependency",
                    "blocker",
                    (dependency.dependency_id,),
                    {
                        "type": dependency.relation_type,
                        "lag_minutes": dependency.lag_minutes,
                        "predecessor_task_id": predecessor.task_id,
                        "successor_task_id": successor.task_id,
                    },
                    "非零 Lag 关系不满足来源计划日期（冻结的有效工作日历口径）。",
                    "检查来源日期或依赖关系，修复后重新提交快照。",
                )
            )
    return findings
