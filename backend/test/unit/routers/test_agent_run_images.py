"""Multi-image chat requests survive persistence and both CloseAI wire protocols."""

import importlib
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.agents.models import _ToolCallChunkFixChatOpenAI
from yuxi.services.input_message_service import restore_chat_input_message

router = importlib.import_module("server.routers.agent_router")
IMAGE_URLS = ["data:image/png;base64,aW1hZ2Ux", "data:image/jpeg;base64,aW1hZ2Uy"]


@pytest.mark.parametrize("query", ["比较两张图片", ""])
@pytest.mark.parametrize("responses", [False, True])
def test_run_preserves_all_images_through_worker_restore_and_model_payload(monkeypatch, query, responses):
    captured = {}

    async def create_run(**kwargs):
        captured.update(kwargs)
        return {"run_id": "multi-image-run"}

    monkeypatch.setattr(router, "create_agent_run_view", create_run)
    app = FastAPI()
    app.include_router(router.agent_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[get_required_user] = lambda: SimpleNamespace(uid="user-1")
    result = TestClient(app).post(
        "/api/agent/runs",
        json={"agent_slug": "chatbot", "thread_id": "thread-1", "query": query, "image_urls": IMAGE_URLS},
    )
    assert result.status_code == 200
    message = captured["input_message"]
    assert message is not None
    assert message.message_type == "multimodal_image"
    restored = restore_chat_input_message(
        content=message.content,
        image_content=message.image_content,
        metadata=json.loads(json.dumps({"raw_message": message.raw_message()})),
    )
    model = _ToolCallChunkFixChatOpenAI(model="gpt-6-astra", api_key="test", use_responses_api=responses)
    payload = model._get_request_payload([restored.require_langchain_message()])
    content = payload["input" if responses else "messages"][0]["content"]
    urls = [
        part["image_url"] if responses else part["image_url"]["url"]
        for part in content
        if part["type"] == ("input_image" if responses else "image_url")
    ]
    assert urls == IMAGE_URLS
