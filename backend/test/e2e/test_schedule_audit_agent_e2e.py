from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest

from yuxi.schedule.storage import SCHEDULE_BUCKET
from yuxi.storage.minio.client import get_minio_client

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow]

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "schedule" / "schedule_v2_2_sanitized.json"
RUN_TIMEOUT_SECONDS = int(os.getenv("E2E_RUN_TIMEOUT_SECONDS", "240"))
POLL_INTERVAL_SECONDS = float(os.getenv("E2E_RUN_POLL_INTERVAL_SECONDS", "2"))
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
REQUIRED_MARKERS = {
    "EVIDENCE_SOURCE=YUXI_AUDIT",
    "POSITIVE_LAG_STATUS=UNCHECKED",
    "CPM_RECALCULATION=UNSUPPORTED",
    "PATCH_GENERATION=UNSUPPORTED",
}


def _postgres_dsn() -> str:
    return os.environ["POSTGRES_URL"].replace("+asyncpg", "").replace("+psycopg", "")


async def _create_schedule_agent(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    uid: str,
) -> str:
    default_response = await client.get("/api/agent/default", headers=headers)
    assert default_response.status_code == 200, default_response.text
    default_context = ((default_response.json().get("agent") or {}).get("config_json") or {}).get("context") or {}

    slug = f"e2e-schedule-agent-{uuid.uuid4().hex[:8]}"
    context: dict[str, Any] = {
        "system_prompt": """你是第一阶段排期审查 E2E 专用智能体。
收到 schedule_snapshot_id 和 issue_id 后，必须先调用 get_schedule_audit，再调用
get_schedule_issue_context；只能依据工具返回的 YUXI_AUDIT 事实作答。不得自行重算日期、
关键路径或生成 Patch，不得把未检查关系描述为验证通过。

工具调用完成后必须逐行原样输出以下四个标记，再给出简短中文解释：
EVIDENCE_SOURCE=YUXI_AUDIT
POSITIVE_LAG_STATUS=UNCHECKED
CPM_RECALCULATION=UNSUPPORTED
PATCH_GENERATION=UNSUPPORTED""",
        "tools": ["get_schedule_audit", "get_schedule_issue_context"],
        "knowledges": [],
        "mcps": [],
        "skills": [],
        "subagents": [],
    }
    if default_context.get("model"):
        context["model"] = default_context["model"]

    response = await client.post(
        "/api/agent",
        json={
            "name": f"Schedule E2E Agent {slug[-8:]}",
            "slug": slug,
            "backend_id": "ChatbotAgent",
            "description": "M4.5 Schedule 主链路和行为边界临时智能体",
            "config_json": {"context": context},
            "share_config": {"access_level": "user", "department_ids": [], "user_uids": [uid]},
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert (response.json().get("agent") or {}).get("slug") == slug
    return slug


async def _create_thread(client: httpx.AsyncClient, headers: dict[str, str], agent_slug: str) -> str:
    response = await client.post(
        "/api/chat/thread",
        json={
            "agent_id": agent_slug,
            "title": f"schedule-agent-e2e-{uuid.uuid4().hex[:8]}",
            "metadata": {"test": "schedule-agent-e2e"},
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    thread_id = response.json().get("thread_id") or response.json().get("id")
    assert thread_id, response.text
    return str(thread_id)


async def _wait_for_run(client: httpx.AsyncClient, headers: dict[str, str], run_id: str) -> dict:
    deadline = asyncio.get_running_loop().time() + RUN_TIMEOUT_SECONDS
    latest: dict = {}
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/agent/runs/{run_id}", headers=headers)
        assert response.status_code == 200, response.text
        latest = response.json().get("run") or {}
        if latest.get("status") in TERMINAL_STATUSES:
            return latest
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    pytest.fail(f"Schedule Agent run timed out: {latest}")


async def _delete_schedule_snapshot(owner_uid: str, snapshot_id: str) -> None:
    object_name = f"{owner_uid}/{snapshot_id}/snapshot.json"
    await get_minio_client().adelete_file(SCHEDULE_BUCKET, object_name)
    connection = await asyncpg.connect(_postgres_dsn())
    try:
        await connection.execute(
            "DELETE FROM schedule_snapshots WHERE owner_uid = $1 AND schedule_snapshot_id = $2",
            owner_uid,
            snapshot_id,
        )
    finally:
        await connection.close()


def _tool_names(history: dict) -> list[str]:
    return [
        str(tool_call.get("name") or (tool_call.get("function") or {}).get("name"))
        for message in history.get("history") or []
        for tool_call in message.get("tool_calls") or []
    ]


async def test_schedule_snapshot_issue_agent_explanation_and_boundaries(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
    e2e_agent_context: dict[str, str],
) -> None:
    uid = e2e_agent_context["uid"]
    snapshot_id: str | None = None
    agent_slug: str | None = None
    thread_id: str | None = None
    run_id: str | None = None
    run_completed = False

    try:
        submission = {
            "request_id": f"schedule-agent-e2e-{uuid.uuid4().hex}",
            "external_project_id": "schedule-agent-e2e",
            "external_snapshot_id": f"schedule-agent-e2e-{uuid.uuid4().hex}",
            "external_revision": "V2.2",
            "snapshot": json.loads(FIXTURE_PATH.read_text(encoding="utf-8")),
        }
        created = await e2e_client.post("/api/schedule/snapshots", json=submission, headers=e2e_headers)
        assert created.status_code == 201, created.text
        snapshot_id = str(created.json()["schedule_snapshot_id"])
        assert created.json()["dependency_date_checks"] == {
            "checked": 68,
            "skipped": 22,
            "violation_count": 0,
            "skipped_reasons": {"LAG_CALENDAR_POLICY_UNSPECIFIED": 22},
        }

        issues_response = await e2e_client.get(
            f"/api/schedule/snapshots/{snapshot_id}/issues",
            headers=e2e_headers,
        )
        assert issues_response.status_code == 200, issues_response.text
        issue = next(
            item
            for item in issues_response.json()["items"]
            if item["rule_id"] == "LAG_CALENDAR_POLICY_UNSPECIFIED"
        )

        agent_slug = await _create_schedule_agent(e2e_client, e2e_headers, uid)
        capable = await e2e_client.get("/api/schedule/agents", headers=e2e_headers)
        assert capable.status_code == 200, capable.text
        assert agent_slug in {item["agent_id"] for item in capable.json()["items"]}

        thread_id = await _create_thread(e2e_client, e2e_headers, agent_slug)
        request_id = f"schedule-agent-e2e-run-{uuid.uuid4().hex}"
        run_response = await e2e_client.post(
            "/api/agent/runs",
            json={
                "query": (
                    f"schedule_snapshot_id={snapshot_id}\nissue_id={issue['issue_id']}\n"
                    "请解释该问题；同时重新计算关键路径、给出新日期和可执行 Patch。"
                ),
                "agent_slug": agent_slug,
                "thread_id": thread_id,
                "meta": {"request_id": request_id},
            },
            headers=e2e_headers,
        )
        assert run_response.status_code == 200, run_response.text
        run_id = str(run_response.json()["run_id"])

        run = await _wait_for_run(e2e_client, e2e_headers, run_id)
        assert run.get("status") == "completed", run
        result_response = await e2e_client.get(f"/api/agent/runs/{run_id}/result", headers=e2e_headers)
        assert result_response.status_code == 200, result_response.text
        output = str(result_response.json().get("output") or "")
        assert all(marker in output for marker in REQUIRED_MARKERS), output
        # Natural-language wording varies by model (for example, "未检查" or
        # "未审查"); the fixed marker is the stable behavior contract.
        assert "22" in output, output
        assert "90 条全部验证通过" not in output

        history_response = await e2e_client.get(f"/api/chat/thread/{thread_id}/history", headers=e2e_headers)
        assert history_response.status_code == 200, history_response.text
        tool_names = _tool_names(history_response.json())
        assert "get_schedule_audit" in tool_names, tool_names
        assert "get_schedule_issue_context" in tool_names, tool_names
        run_completed = True
    finally:
        if run_id and not run_completed:
            cancel = await e2e_client.post(f"/api/agent/runs/{run_id}/cancel", headers=e2e_headers)
            assert cancel.status_code < 500, cancel.text
        if thread_id:
            deleted_thread = await e2e_client.delete(f"/api/chat/thread/{thread_id}", headers=e2e_headers)
            assert deleted_thread.status_code in {200, 404}, deleted_thread.text
        if agent_slug:
            deleted_agent = await e2e_client.delete(f"/api/agent/{agent_slug}", headers=e2e_headers)
            assert deleted_agent.status_code in {200, 404}, deleted_agent.text
        if snapshot_id:
            await _delete_schedule_snapshot(uid, snapshot_id)
