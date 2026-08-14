from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt.tool_node import ToolRuntime
from langgraph.runtime import Runtime

from yuxi.agents.toolkits.registry import get_extra_metadata
from yuxi.agents.toolkits.schedule import tools as schedule_tools
from yuxi.services.schedule_audit_service import ScheduleNotFoundError


class FakeService:
    async def get_audit(self, uid, snapshot_id):
        return {
            "owner": uid,
            "schedule_snapshot_id": snapshot_id,
            "dependency_date_checks": {"checked": 68, "skipped": 22},
        }

    async def get_issue_context(self, uid, issue_id):
        if issue_id == "hidden":
            raise ScheduleNotFoundError
        return {"owner": uid, "issue": {"issue_id": issue_id, "origin": "YUXI_AUDIT"}}


def _runtime(uid: str | None) -> ToolRuntime:
    return ToolRuntime(
        state={},
        context=SimpleNamespace(uid=uid),
        tool_call_id="tool-1",
        store=None,
        stream_writer=lambda _: None,
        config={},
    )


@pytest.mark.asyncio
async def test_schedule_tools_use_runtime_uid_and_preserve_skip_scope(monkeypatch) -> None:
    monkeypatch.setattr(schedule_tools, "_service", FakeService)

    result = await schedule_tools.get_schedule_audit.ainvoke(
        {"schedule_snapshot_id": "snapshot-1", "runtime": _runtime("owner-1")}
    )

    assert result["owner"] == "owner-1"
    assert result["dependency_date_checks"] == {"checked": 68, "skipped": 22}


@pytest.mark.asyncio
async def test_schedule_issue_tool_hides_unauthorized_resource(monkeypatch) -> None:
    monkeypatch.setattr(schedule_tools, "_service", FakeService)

    result = await schedule_tools.get_schedule_issue_context.ainvoke(
        {"issue_id": "hidden", "runtime": _runtime("other-owner")}
    )

    assert result == {"error": "SCHEDULE_NOT_FOUND"}


@pytest.mark.asyncio
async def test_schedule_tools_reject_missing_runtime_uid(monkeypatch) -> None:
    monkeypatch.setattr(schedule_tools, "_service", FakeService)

    result = await schedule_tools.get_schedule_audit.ainvoke(
        {"schedule_snapshot_id": "snapshot-1", "runtime": _runtime(None)}
    )

    assert result == {"error": "SCHEDULE_USER_CONTEXT_MISSING"}


def test_schedule_tools_are_registered_as_buildin() -> None:
    audit_metadata = get_extra_metadata("get_schedule_audit")
    issue_metadata = get_extra_metadata("get_schedule_issue_context")

    assert audit_metadata is not None and audit_metadata.category == "buildin"
    assert issue_metadata is not None and issue_metadata.category == "buildin"


def test_schedule_tools_inject_runtime_without_exposing_it_to_model() -> None:
    assert schedule_tools.get_schedule_audit._injected_args_keys == frozenset({"runtime"})
    assert schedule_tools.get_schedule_issue_context._injected_args_keys == frozenset({"runtime"})
    assert set(schedule_tools.get_schedule_audit.args) == {"schedule_snapshot_id"}
    assert set(schedule_tools.get_schedule_issue_context.args) == {"issue_id"}


def test_schedule_tool_descriptions_preserve_agent_behavior_boundaries() -> None:
    audit_description = schedule_tools.get_schedule_audit.description
    issue_description = schedule_tools.get_schedule_issue_context.description

    assert "不会重新计算日期、关键路径或补丁" in audit_description
    assert "未检查" in audit_description
    assert "ignored/unsupported" in audit_description
    assert "不得描述成已参与 Yuxi 审查或计算" in audit_description
    assert "不得自行生成日期、关键路径或 Patch" in issue_description
    assert "YUXI_AUDIT" in issue_description
    assert "ignored/unsupported" in issue_description


@pytest.mark.asyncio
async def test_schedule_issue_tool_receives_runtime_from_tool_node(monkeypatch) -> None:
    monkeypatch.setattr(schedule_tools, "_service", FakeService)
    tool_node = ToolNode([schedule_tools.get_schedule_issue_context], handle_tool_errors=False)
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_schedule_issue_context",
                        "args": {"issue_id": "issue-1"},
                        "id": "tool-1",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    }

    result = await tool_node.ainvoke(
        state,
        runtime=Runtime(context=SimpleNamespace(uid="owner-1")),
    )

    payload = json.loads(result["messages"][0].content)
    assert payload == {
        "owner": "owner-1",
        "issue": {"issue_id": "issue-1", "origin": "YUXI_AUDIT"},
    }
