"""Rules comparing source declarations with independently calculated facts."""

from __future__ import annotations

from typing import Any

from yuxi.schedule.audit.context import AuditContext
from yuxi.schedule.domain.models import AuditFinding


def audit_contract(context: AuditContext) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    for path, actual in _flatten(context.statistics):
        source = _read_path(context.schedule.source_statistics, path)
        if source != actual:
            findings.append(
                _finding(
                    "STATISTICS_MISMATCH",
                    "contract",
                    "warning",
                    (context.schedule.project_id,),
                    {"field": path, "source": source, "calculated": actual},
                    f"来源统计 {path} 与 Yuxi 计算结果不一致。",
                    "检查转换结果与来源统计生成逻辑。",
                )
            )

    for name, capability in sorted(context.capabilities.items()):
        source = context.schedule.source_capabilities.get(name)
        calculated = capability.model_dump(mode="json")
        if source != calculated:
            findings.append(
                _finding(
                    "SOURCE_CAPABILITY_MISMATCH",
                    "contract",
                    "warning",
                    (f"capability:{name}",),
                    {"capability": name, "source": source, "calculated": calculated},
                    f"来源能力声明 {name} 与 Yuxi 投影结果不一致。",
                    "以 Yuxi 投影为准，并检查来源能力声明。",
                )
            )
    return findings


def _flatten(value: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    fields: list[tuple[str, Any]] = []
    for key in sorted(value):
        path = f"{prefix}.{key}" if prefix else key
        child = value[key]
        if isinstance(child, dict):
            fields.extend(_flatten(child, path))
        else:
            fields.append((path, child))
    return fields


def _read_path(value: dict[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        current = current.get(part) if isinstance(current, dict) else None
    return current


def _finding(
    rule_id: str,
    category: str,
    severity: str,
    object_refs: tuple[str, ...],
    evidence: dict[str, Any],
    message: str,
    recommendation: str,
) -> AuditFinding:
    return AuditFinding(
        rule_id=rule_id,
        rule_version="1",
        category=category,
        severity=severity,
        object_refs=object_refs,
        evidence=evidence,
        message=message,
        recommendation=recommendation,
    )
