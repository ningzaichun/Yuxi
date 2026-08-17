"""Read-only Agent tools for persisted Yuxi Schedule audit evidence."""

from typing import Annotated, Any

from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import Field

from yuxi.agents.toolkits.registry import tool
from yuxi.services.schedule_audit_service import (
    ScheduleAuditService,
    ScheduleDependencyError,
    ScheduleNotFoundError,
)


def _service() -> ScheduleAuditService:
    return ScheduleAuditService()


# 不要启用 postponed annotations 或显式传入 args_schema：当前 LangChain 需要从这里的
# 具体类型注解识别并注入 ToolRuntime，否则 ToolNode 传入的用户上下文会被参数校验丢弃。
@tool(
    category="buildin",
    tags=["排期", "工期优化", "授权"],
    display_name="读取工期目标优化边界",
)
async def get_schedule_goal_optimization_context(
    schedule_snapshot_id: Annotated[str, Field(description="排期快照的不透明 ID")],
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """读取工期目标、可授权任务和固定硬约束的安全投影。

    本工具只用于收集和解释优化意图，不创建 Candidate。必须让用户明确选择目标、目标日期、
    可压缩任务、授权后工期和 locked tasks；不得替用户推断可压缩工期或伪造授权。最终提交
    必须回到排期页面确认。成本、资源、依赖、Lag、日历、里程碑和任务模式不在本阶段授权范围。
    """
    uid = str(getattr(runtime.context, "uid", "") or "").strip()
    if not uid:
        return {"error": "SCHEDULE_USER_CONTEXT_MISSING"}
    try:
        return await _service().get_goal_optimization_context(uid, schedule_snapshot_id)
    except ScheduleNotFoundError:
        return {"error": "SCHEDULE_NOT_FOUND"}
    except ScheduleDependencyError:
        return {"error": "SCHEDULE_DEPENDENCY_FAILURE"}


@tool(
    category="buildin",
    tags=["排期", "审查", "整份计划"],
    display_name="读取整份排期审查上下文",
)
async def get_schedule_review_context(
    schedule_snapshot_id: Annotated[str, Field(description="排期快照的不透明 ID")],
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """读取整份排期快照的受控审查上下文。

    回答整份计划问题时必须先调用本工具。只能依据返回的 YUXI_AUDIT、Issue 和 Capability
    汇总确定性发现；涉及具体问题时调用 get_schedule_issue_context，并使用 evidence_locator
    提供证据链接。Capability 阻断优先于 Issue 严重等级；同级处理顺序只能明确
    标注为“建议”。没有版本化规则和证据时不得判断工程业务合理性，不得自行计算日期、
    关键路径、成本或 Patch，也不得把完整来源或 ignored/unsupported 字段当成已审查事实。
    """
    uid = str(getattr(runtime.context, "uid", "") or "").strip()
    if not uid:
        return {"error": "SCHEDULE_USER_CONTEXT_MISSING"}
    try:
        return await _service().get_review_context(uid, schedule_snapshot_id)
    except ScheduleNotFoundError:
        return {"error": "SCHEDULE_NOT_FOUND"}
    except ScheduleDependencyError:
        return {"error": "SCHEDULE_DEPENDENCY_FAILURE"}


@tool(
    category="buildin",
    tags=["排期", "审查"],
    display_name="读取排期审查",
)
async def get_schedule_audit(
    schedule_snapshot_id: Annotated[str, Field(description="排期快照的不透明 ID")],
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """读取 Yuxi 已持久化的确定性排期审查摘要。

    该工具不会重新计算日期、关键路径或补丁。非零 Lag 关系只能按返回统计说明为“未检查”，
    不得描述成已验证无冲突；来源 Validation 以及规范化报告中 ignored/unsupported 的字段
    不得描述成已参与 Yuxi 审查或计算。
    """
    uid = str(getattr(runtime.context, "uid", "") or "").strip()
    if not uid:
        return {"error": "SCHEDULE_USER_CONTEXT_MISSING"}
    try:
        return await _service().get_audit(uid, schedule_snapshot_id)
    except ScheduleNotFoundError:
        return {"error": "SCHEDULE_NOT_FOUND"}
    except ScheduleDependencyError:
        return {"error": "SCHEDULE_DEPENDENCY_FAILURE"}


@tool(
    category="buildin",
    tags=["排期", "审查", "证据"],
    display_name="读取排期问题证据",
)
async def get_schedule_issue_context(
    issue_id: Annotated[str, Field(description="Yuxi 审查 Issue 的不透明 ID")],
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """读取一个 Yuxi Schedule Issue 的证据和直接网络上下文。

    只解释返回的 YUXI_AUDIT 事实。不得自行生成日期、关键路径或 Patch，也不得把来源 Validation
    当作 Yuxi 结论；规范化报告中 ignored/unsupported 的字段也不得描述成已参与审查或计算。
    """
    uid = str(getattr(runtime.context, "uid", "") or "").strip()
    if not uid:
        return {"error": "SCHEDULE_USER_CONTEXT_MISSING"}
    try:
        return await _service().get_issue_context(uid, issue_id)
    except ScheduleNotFoundError:
        return {"error": "SCHEDULE_NOT_FOUND"}
    except ScheduleDependencyError:
        return {"error": "SCHEDULE_DEPENDENCY_FAILURE"}
