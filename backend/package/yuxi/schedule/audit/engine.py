"""Orchestrate fixed Schedule audit rules and stable output ordering."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import replace

from yuxi.schedule.audit.context import AuditContext
from yuxi.schedule.audit.rules import RULES
from yuxi.schedule.contracts.audit import AuditResult, IssueSummary
from yuxi.schedule.domain.models import AuditExecution, AuditFinding, ScheduleSnapshot

RULE_SET_VERSION = "schedule-audit-mvp-v1"
SEVERITY_ORDER = {"blocker": 0, "warning": 1, "info": 2}


def audit_schedule(
    schedule: ScheduleSnapshot,
    *,
    schedule_snapshot_id: str,
    audit_run_id: str,
) -> AuditExecution:
    context = AuditContext.build(schedule)
    findings = [finding for rule in RULES for finding in rule(context)]
    normalized = tuple(sorted((_with_issue_key(item) for item in findings), key=_sort_key))
    counts = Counter(item.severity for item in normalized)
    result = AuditResult(
        audit_run_id=audit_run_id,
        schedule_snapshot_id=schedule_snapshot_id,
        rule_set_version=RULE_SET_VERSION,
        statistics=context.statistics,
        capabilities=context.capabilities,
        dependency_date_checks=context.dependency_date_checks,
        issue_summary=IssueSummary(
            total=len(normalized),
            blocker=counts["blocker"],
            warning=counts["warning"],
            info=counts["info"],
        ),
    )
    return AuditExecution(result=result, findings=normalized)


def _with_issue_key(finding: AuditFinding) -> AuditFinding:
    payload = {
        "rule_id": finding.rule_id,
        "rule_version": finding.rule_version,
        "object_refs": sorted(finding.object_refs),
        "evidence": finding.evidence,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    issue_key = f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
    return replace(finding, object_refs=tuple(sorted(finding.object_refs)), issue_key=issue_key)


def _sort_key(finding: AuditFinding) -> tuple[int, str, str, str, str]:
    primary_ref = finding.object_refs[0] if finding.object_refs else ""
    return (
        SEVERITY_ORDER[finding.severity],
        finding.category,
        finding.rule_id,
        primary_ref,
        finding.issue_key,
    )
