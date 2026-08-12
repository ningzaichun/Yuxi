"""Dependency semantics supported by first-stage source-date review."""

from yuxi.schedule.audit.context import AuditContext, dependency_date_is_valid
from yuxi.schedule.audit.rules.contract import _finding
from yuxi.schedule.domain.models import AuditFinding


def audit_dependencies(context: AuditContext) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    for dependency in sorted(context.schedule.dependencies, key=lambda item: item.dependency_id):
        predecessor = context.tasks_by_id[dependency.predecessor_task_id]
        successor = context.tasks_by_id[dependency.successor_task_id]
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
        if dependency.lag_minutes == 0 and not dependency_date_is_valid(
            dependency.relation_type, predecessor, successor
        ):
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
    return findings
