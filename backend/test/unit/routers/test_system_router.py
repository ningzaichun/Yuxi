from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routers.system_router import load_info_config, system

pytestmark = pytest.mark.unit


async def test_load_info_config_uses_source_relative_template(monkeypatch, tmp_path):
    monkeypatch.delenv("YUXI_BRAND_FILE_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("server.routers.system_router.get_version", lambda: "0.7.1.dev0")

    config = await load_info_config()

    assert config["organization"]["name"] == "量程科技"
    assert config["organization"]["logo"] == "/k-ai-logo.png"
    assert config["organization"]["avatar"] == "/k-ai-logo.png"
    assert config["footer"]["copyright"].endswith("K-AI v0.7.1.dev0")


def test_discovery_endpoint_is_public(monkeypatch):
    monkeypatch.setattr("server.routers.system_router.get_version", lambda: "0.7.1.dev0")

    app = FastAPI()
    app.include_router(system, prefix="/api")
    response = TestClient(app).get("/api/system/discovery")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Yuxi"
    assert payload["version"] == "0.7.1.dev0"
    assert payload["api_prefix"] == "/api"
    assert payload["capabilities"]["cli"]["browser_login"] is True
    assert payload["capabilities"]["cli"]["api_key_auth"] is True
    assert payload["capabilities"]["cli"]["kb_upload"] is True
    assert payload["endpoints"]["cli_auth_sessions"] == "/api/auth/cli/sessions"
