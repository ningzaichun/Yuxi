from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from yuxi.services.schedule_audit_service import list_schedule_capable_agents


@pytest.mark.asyncio
async def test_agent_eligibility_uses_normalized_runtime_tools(monkeypatch) -> None:
    agents = [
        SimpleNamespace(slug="eligible", name="Eligible", description="ok", config_json={"context": {"tools": None}}),
        SimpleNamespace(
            slug="issue-only",
            name="Issue Only",
            description="existing issue tools",
            config_json={"context": {"tools": ["get_schedule_audit", "get_schedule_issue_context"]}},
        ),
        SimpleNamespace(
            slug="partial",
            name="Partial",
            description="missing one tool",
            config_json={"context": {"tools": ["get_schedule_audit"]}},
        ),
    ]

    class FakeAgentRepository:
        def __init__(self, db):
            pass

        async def list_visible(self, *, user):
            return agents

    async def normalize(context, *, db, user):
        if context["tools"] is None:
            return {
                "tools": [
                    "get_schedule_goal_optimization_context",
                    "get_schedule_review_context",
                    "get_schedule_audit",
                    "get_schedule_issue_context",
                ]
            }
        return context

    @asynccontextmanager
    async def session():
        yield object()

    monkeypatch.setattr("yuxi.repositories.agent_repository.AgentRepository", FakeAgentRepository)
    monkeypatch.setattr("yuxi.agents.context.normalize_agent_context_config", normalize)
    monkeypatch.setattr("yuxi.storage.postgres.manager.pg_manager.get_async_session_context", session)

    issue_result = await list_schedule_capable_agents(SimpleNamespace(uid="owner-1"), review_scope="issue")
    snapshot_result = await list_schedule_capable_agents(SimpleNamespace(uid="owner-1"), review_scope="snapshot")
    goal_result = await list_schedule_capable_agents(SimpleNamespace(uid="owner-1"), review_scope="goal")

    assert issue_result == [
        {"agent_id": "eligible", "name": "Eligible", "description": "ok"},
        {"agent_id": "issue-only", "name": "Issue Only", "description": "existing issue tools"},
    ]
    assert snapshot_result == [{"agent_id": "eligible", "name": "Eligible", "description": "ok"}]
    assert goal_result == [{"agent_id": "eligible", "name": "Eligible", "description": "ok"}]
