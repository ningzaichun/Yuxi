"""Inactive task evidence rules."""

from yuxi.schedule.audit.context import AuditContext
from yuxi.schedule.audit.rules.contract import _finding
from yuxi.schedule.domain.models import AuditFinding


def audit_inactive_tasks(context: AuditContext) -> list[AuditFinding]:
    if context.schedule.schema_version != "canonical_schedule_v2.8":
        return []
    return [
        _finding(
            "INACTIVE_TASK_EXCLUDED",
            "task",
            "info",
            (task.task_id,),
            {
                "task_id": task.task_id,
                "source_start": task.planned_start.isoformat(),
                "source_finish": task.planned_finish.isoformat(),
            },
            "inactive 任务保留来源事实，但不参与 CPM、资源和工期优化。",
            "如需恢复其计算作用，请在来源系统重新激活任务后提交新快照。",
        )
        for task in sorted(context.schedule.tasks, key=lambda item: item.task_id)
        if not task.active
    ]
