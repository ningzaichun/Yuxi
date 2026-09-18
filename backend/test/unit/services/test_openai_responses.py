"""Responses 配置、无状态工具循环和真实 SDK 流式解析回归（不访问外网）。"""

import json
from types import SimpleNamespace

import httpx
import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from yuxi.agents.models import load_chat_model
from yuxi.models.providers.builtin import BUILTIN_PROVIDERS
from yuxi.models.providers.cache import ModelCache, ModelInfo
from yuxi.models.providers.service import _normalize_payload


@pytest.fixture
def responses_info(monkeypatch):
    template = next(p for p in BUILTIN_PROVIDERS if p["provider_id"] == "closeai")
    provider = SimpleNamespace(**_normalize_payload({**template, "api_key": "test-key"}))
    saved = {}
    cache = ModelCache()
    monkeypatch.setattr(cache, "_save_cache", saved.update)
    cache.rebuild([provider])
    # 模拟 API 写入 Redis 后，Worker 重新反序列化。
    info = ModelInfo.from_dict(json.loads(json.dumps(saved["closeai:gpt-6-astra"].to_dict())))
    monkeypatch.setattr("yuxi.agents.models.model_cache.get_model_info", lambda _: info)
    return info


def test_responses_preserves_stateless_reasoning_and_tool_history(responses_info):
    model = load_chat_model(responses_info.spec)
    payload = model._get_request_payload(
        [
            HumanMessage("查询项目状态"),
            AIMessage(
                content=[{"type": "reasoning", "id": "rs_test", "summary": [], "encrypted_content": "opaque"}],
                tool_calls=[{"name": "status", "args": {}, "id": "call_test"}],
                response_metadata={"id": "resp_previous", "output_version": "responses/v1"},
            ),
            ToolMessage("已完成", tool_call_id="call_test"),
        ]
    )

    assert model.use_responses_api is True
    assert payload["store"] is False
    assert payload["include"] == ["reasoning.encrypted_content"]
    assert "previous_response_id" not in payload
    assert "messages" not in payload
    assert "temperature" not in payload
    assert any(item.get("encrypted_content") == "opaque" for item in payload["input"])
    assert any(item.get("type") == "function_call" for item in payload["input"])
    assert any(item.get("call_id") == "call_test" and item.get("output") == "已完成" for item in payload["input"])


@pytest.mark.parametrize("protocol,model_type", [("typo", "chat"), ("openai_responses", "embedding")])
def test_invalid_model_protocol_is_rejected(protocol, model_type):
    with pytest.raises(ValueError, match="协议覆盖"):
        _normalize_payload(
            {"enabled_models": [{"id": "test", "type": model_type, "protocol_override": protocol}]},
            partial=True,
        )


@pytest.mark.asyncio
async def test_responses_agent_executes_tool_and_parses_stream(responses_info):
    requests = []
    executed = []

    def lookup_status() -> str:
        """Return the current project status."""
        executed.append(True)
        return "PROJECT_READY"

    def respond(request):
        assert request.url.path == "/v1/responses"
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["store"] is False
        assert "previous_response_id" not in payload
        assert "stream_options" not in payload
        if len(requests) == 1:
            output = [
                {
                    "type": "function_call",
                    "id": "fc_test",
                    "call_id": "call_test",
                    "name": "lookup_status",
                    "arguments": "{}",
                    "status": "completed",
                }
            ]
        else:
            output = [
                {
                    "type": "message",
                    "id": "msg_test",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "PROJECT_READY", "annotations": []}],
                }
            ]
        response = {
            "id": f"resp_{len(requests)}",
            "object": "response",
            "created_at": 1,
            "model": "gpt-6-astra",
            "status": "completed",
            "output": output,
            "parallel_tool_calls": True,
        }
        if not payload.get("stream"):
            return httpx.Response(200, json=response)
        events = [
            {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {**output[0], "status": "in_progress", "content": []},
            },
            {
                "type": "response.content_part.added",
                "output_index": 0,
                "content_index": 0,
                "item_id": "msg_test",
                "part": {"type": "output_text", "text": "", "annotations": []},
            },
            {
                "type": "response.output_text.delta",
                "output_index": 0,
                "content_index": 0,
                "item_id": "msg_test",
                "delta": "PROJECT_READY",
            },
            {"type": "response.output_item.done", "output_index": 0, "item": output[0]},
            {"type": "response.completed", "response": response},
        ]
        stream = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
        return httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = load_chat_model(responses_info.spec, http_async_client=client, max_retries=0)
        agent = create_agent(model=model, tools=[lookup_status])
        result = await agent.ainvoke({"messages": [HumanMessage("Use lookup_status and report the result.")]})
        assert executed == [True]
        assert result["messages"][-1].text == "PROJECT_READY"
        assert any(item.get("output") == "PROJECT_READY" for item in requests[1]["input"])

        chunks = [chunk async for chunk in model.astream(result["messages"] + [HumanMessage("Repeat it.")])]
        assert "".join(chunk.text for chunk in chunks) == "PROJECT_READY"
