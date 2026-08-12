"""Dependency graph shape rules."""

from yuxi.schedule.audit.context import AuditContext
from yuxi.schedule.audit.rules.contract import _finding
from yuxi.schedule.domain.models import AuditFinding


def audit_network(context: AuditContext) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    for dependency in sorted(context.schedule.dependencies, key=lambda item: item.dependency_id):
        if dependency.predecessor_task_id == dependency.successor_task_id:
            findings.append(
                _finding(
                    "SELF_DEPENDENCY",
                    "network",
                    "blocker",
                    (dependency.dependency_id,),
                    {"task_id": dependency.predecessor_task_id},
                    "依赖关系的前置和后置指向同一任务。",
                    "删除自依赖或修正前后置任务。",
                )
            )

    for relation, dependencies in sorted(context.network.relation_groups.items()):
        if len(dependencies) > 1:
            dependency_ids = tuple(sorted(item.dependency_id for item in dependencies))
            findings.append(
                _finding(
                    "DUPLICATE_RELATION",
                    "network",
                    "warning",
                    dependency_ids,
                    {"relationship": list(relation), "dependency_ids": list(dependency_ids)},
                    "存在重复的逻辑依赖关系。",
                    "保留一条有效关系并删除重复项。",
                )
            )

    for component in context.network.cyclic_components():
        findings.append(
            _finding(
                "DEPENDENCY_CYCLE",
                "network",
                "blocker",
                component,
                {"task_ids": list(component)},
                "依赖网络中存在循环。",
                "调整依赖关系，使网络成为无环图。",
            )
        )

    leaf_tasks = sorted(
        (task for task in context.schedule.tasks if task.task_type != "summary"),
        key=lambda item: item.task_id,
    )
    for task in leaf_tasks:
        if not context.network.incoming[task.task_id]:
            findings.append(
                _finding(
                    "OPEN_START",
                    "network",
                    "warning",
                    (task.task_id,),
                    {"task_id": task.task_id},
                    "叶子任务没有前置关系。",
                    "确认它是否应作为网络起点，否则补充前置关系。",
                )
            )
        if not context.network.outgoing[task.task_id]:
            findings.append(
                _finding(
                    "OPEN_FINISH",
                    "network",
                    "warning",
                    (task.task_id,),
                    {"task_id": task.task_id},
                    "叶子任务没有后续关系。",
                    "确认它是否应作为网络终点，否则补充后续关系。",
                )
            )
    return findings
