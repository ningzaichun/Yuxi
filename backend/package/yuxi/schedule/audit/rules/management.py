"""Project-management completeness and engine-boundary rules."""

from yuxi.schedule.audit.context import AuditContext
from yuxi.schedule.audit.rules.contract import _finding
from yuxi.schedule.domain.models import AuditFinding


def audit_management(context: AuditContext) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    project_ref = (context.schedule.project_id,)
    for task in sorted(context.schedule.tasks, key=lambda item: item.task_id):
        if task.task_type == "summary":
            continue
        calendar = (
            context.lag_calendars.get(task.effective_calendar_id)
            if context.lag_calendars is not None
            else None
        )
        if task.deadline is not None and task.planned_finish > task.deadline:
            findings.append(
                _finding(
                    "DEADLINE_MISSED",
                    "constraint",
                    "warning",
                    (task.task_id,),
                    {
                        "deadline": task.deadline.isoformat(),
                        "planned_finish": task.planned_finish.isoformat(),
                        "variance_minutes": (
                            calendar.working_minutes_between(task.deadline, task.planned_finish)
                            if calendar is not None
                            else None
                        ),
                    },
                    "任务来源完成时间晚于 Deadline。",
                    "检查任务剩余计划；Deadline 仅用于管理预警，不会自动移动任务。",
                )
            )
        if (
            task.constraint_type == "FINISH_NO_LATER_THAN"
            and task.planned_finish > task.constraint_date
        ):
            findings.append(
                _finding(
                    "FINISH_CONSTRAINT_VIOLATED",
                    "constraint",
                    "warning",
                    (task.task_id,),
                    {
                        "constraint_type": task.constraint_type,
                        "constraint_date": task.constraint_date.isoformat(),
                        "planned_finish": task.planned_finish.isoformat(),
                        "variance_minutes": (
                            calendar.working_minutes_between(
                                task.constraint_date,
                                task.planned_finish,
                            )
                            if calendar is not None
                            else None
                        ),
                    },
                    "任务来源完成时间晚于 FNLT 上界。",
                    "确认硬约束和网络关系；Yuxi 不会截短工期来隐藏冲突。",
                )
            )
    if (
        context.schedule.required_finish is not None
        and context.schedule.planned_finish > context.schedule.required_finish
    ):
        calendar = (
            context.lag_calendars.get(context.schedule.default_calendar_id)
            if context.lag_calendars is not None
            else None
        )
        findings.append(
            _finding(
                "PROJECT_REQUIRED_FINISH_MISSED",
                "constraint",
                "warning",
                project_ref,
                {
                    "required_finish": context.schedule.required_finish.isoformat(),
                    "planned_finish": context.schedule.planned_finish.isoformat(),
                    "negative_float_minutes": (
                        -calendar.working_minutes_between(
                            context.schedule.required_finish,
                            context.schedule.planned_finish,
                        )
                        if calendar is not None
                        else None
                    ),
                },
                "项目来源完成时间晚于要求完成日期。",
                "将要求完成日期作为管理目标审查，不直接改写任务日期。",
            )
        )
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
    if context.lag_unchecked_dependency_ids:
        findings.append(
            _finding(
                "LAG_CALENDAR_POLICY_UNSPECIFIED",
                "engine_contract",
                "blocker",
                context.lag_unchecked_dependency_ids,
                {
                    "dependency_ids": list(context.lag_unchecked_dependency_ids),
                    "skipped_count": len(context.lag_unchecked_dependency_ids),
                    "skipped_reasons": context.dependency_date_checks.skipped_reasons,
                },
                "存在未检查的非零 Lag 依赖关系（Lag 日历策略未冻结或日历口径不支持），"
                "本次未验证这些关系的日期合规性。",
                "冻结 Lag 日历策略并统一项目日历口径后重新提交快照。",
            )
        )
    return findings
