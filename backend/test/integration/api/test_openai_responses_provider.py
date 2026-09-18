"""真实管理 API → 数据库/Redis → Responses 请求；模型端由本机 HTTP 服务代替。

独立运行以避免通用 integration fixture 的知识库清理和全库 schema 初始化：
pytest --confcutdir=test/integration/api test/integration/api/test_openai_responses_provider.py
"""

import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values


def test_provider_crud_reaches_responses_endpoint_and_removes_model():
    credentials = {**dotenv_values(Path(__file__).resolve().parents[2] / ".env.test"), **os.environ}
    username, password = credentials.get("TEST_USERNAME"), credentials.get("TEST_PASSWORD")
    if not username or not password:
        pytest.skip("Configure TEST_USERNAME and TEST_PASSWORD for the local API.")
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            received.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            body = json.dumps(
                {
                    "id": "resp_test",
                    "object": "response",
                    "created_at": 1,
                    "model": "gpt-6-astra",
                    "status": "completed",
                    "parallel_tool_calls": True,
                    "output": [
                        {
                            "id": "msg_test",
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": "1", "annotations": []}],
                        }
                    ],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    provider_id = f"test-responses-{uuid.uuid4().hex[:12]}"
    endpoint = f"/api/system/model-providers/{provider_id}"
    spec = f"{provider_id}:gpt-6-astra"
    created = False
    try:
        with httpx.Client(
            base_url=credentials.get("TEST_BASE_URL", "http://127.0.0.1:5050"), trust_env=False, timeout=30
        ) as client:
            auth = client.post("/api/auth/token", data={"username": username, "password": password})
            assert auth.status_code == 200
            client.headers["Authorization"] = f"Bearer {auth.json()['access_token']}"
            try:
                result = client.post(
                    "/api/system/model-providers",
                    json={
                        "provider_id": provider_id,
                        "display_name": "Responses integration test",
                        "provider_type": "openai",
                        "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                        "api_key": "test-key",
                        "capabilities": ["chat"],
                        "is_enabled": True,
                        "enabled_models": [
                            {
                                "id": "gpt-6-astra",
                                "type": "chat",
                                "source": "manual",
                                "protocol_override": "openai_responses",
                            }
                        ],
                    },
                )
                assert result.status_code == 200
                created = True
                result = client.get(endpoint)
                assert result.json()["data"]["enabled_models"][0]["protocol_override"] == "openai_responses"
                models = client.get("/api/system/model-providers/models/v2").json()["data"]
                assert models[provider_id]["models"][0]["spec"] == spec

                status = client.get("/api/system/model-providers/models/status", params={"spec": spec})
                assert status.json()["data"]["status"] == "available"
                assert len(received) == 1
                path, payload = received[0]
                assert path == "/v1/responses"
                assert payload["store"] is False
                assert payload["input"][0]["role"] == "user"
                assert "previous_response_id" not in payload

                invalid = client.put(
                    endpoint,
                    json={"enabled_models": [{"id": "gpt-6-astra", "type": "chat", "protocol_override": "invalid"}]},
                )
                assert invalid.status_code == 400
                assert (
                    client.get(endpoint).json()["data"]["enabled_models"][0]["protocol_override"] == "openai_responses"
                )
            finally:
                if created:
                    assert client.delete(endpoint).status_code == 200
                    assert client.get(endpoint).status_code == 404
                    assert provider_id not in client.get("/api/system/model-providers/models/v2").json()["data"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
