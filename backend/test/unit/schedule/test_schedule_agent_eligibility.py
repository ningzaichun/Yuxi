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
            return {"tools": ["get_schedule_audit", "get_schedule_issue_context"]}
        return context

    @asynccontextmanager
    async def session():
        yield object()

    monkeypatch.setattr("yuxi.repositories.agent_repository.AgentRepository", FakeAgentRepository)
    monkeypatch.setattr("yuxi.agents.context.normalize_agent_context_config", normalize)
    monkeypatch.setattr("yuxi.storage.postgres.manager.pg_manager.get_async_session_context", session)

    result = await list_schedule_capable_agents(SimpleNamespace(uid="owner-1"))

    assert result == [{"agent_id": "eligible", "name": "Eligible", "description": "ok"}]
