"""Project-management completeness and engine-boundary rules."""

from yuxi.schedule.audit.context import AuditContext
from yuxi.schedule.audit.rules.contract import _finding
from yuxi.schedule.domain.models import AuditFinding


def audit_management(context: AuditContext) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    project_ref = (context.schedule.project_id,)
    for calendar in sorted(context.schedule.calendars, key=lambda item: item.calendar_id):
        if len(calendar.working_days) == 7:
            findings.append(
                _finding(
                    "SEVEN_DAY_WORK_CALENDAR",
                    "management",
                    "warning",
                    (calendar.calendar_id,),
                    {"working_days": list(calendar.working_days)},
                    "日历一周七天均配置为工作日。",
                    "确认这是正式业务日历而非转换或样例设置。",
                )
            )
    if not any(task.baseline_exists for task in context.schedule.tasks):
        findings.append(
            _finding(
                "BASELINE_MISSING",
                "management",
                "warning",
                project_ref,
                {"baseline_0_task_count": 0},
                "项目未保存 Baseline 0。",
                "在业务系统中确认并保存基准计划。",
            )
        )
    if context.schedule.status_date is None:
        findings.append(
            _finding(
                "STATUS_DATE_MISSING",
                "management",
                "warning",
                project_ref,
                {"status_date": None},
                "项目没有状态日期。",
                "设置状态日期后重新提交快照。",
            )
        )
    if not any(task.duration_minutes == 0 and task.task_type != "summary" for task in context.schedule.tasks):
        findings.append(
            _finding(
                "MILESTONE_MISSING",
                "management",
                "warning",
                project_ref,
                {"milestone_count": 0},
                "项目没有里程碑任务。",
                "确认关键交付节点并补充里程碑。",
            )
        )
    if context.schedule.assignment_count == 0:
        findings.append(
            _finding(
                "NO_SOURCE_ASSIGNMENTS",
                "resource",
                "warning",
                project_ref,
                {"assignment_count": 0},
                "来源快照没有 Assignment。",
                "如需资源分析，请在来源系统中补全可靠的 Assignment。",
            )
        )
    unclassified = sorted(
        resource.resource_id
        for resource in context.schedule.resources
        if resource.semantic_type == "UNCLASSIFIED"
    )
    if unclassified:
        findings.append(
            _finding(
                "RESOURCE_SEMANTICS_UNCLASSIFIED",
                "resource",
                "warning",
                project_ref,
                {"resource_ids": unclassified},
                "来源资源的业务语义尚未分类。",
                "业务确认资源类型和成本语义后再启用资源优化。",
            )
        )
    non_zero_lag_ids = sorted(
        dependency.dependency_id
        for dependency in context.schedule.dependencies
        if dependency.lag_minutes != 0
    )
    if non_zero_lag_ids:
        findings.append(
            _finding(
                "LAG_CALENDAR_POLICY_UNSPECIFIED",
                "engine_contract",
                "blocker",
                tuple(non_zero_lag_ids),
                {"dependency_ids": non_zero_lag_ids, "skipped_count": len(non_zero_lag_ids)},
                "非零 Lag 的日历计算策略尚未冻结，本次未检查这些关系的日期合规性。",
                "冻结 Engine Profile 的 Lag 日历策略后再执行确定性重算。",
            )
        )
    return findings
