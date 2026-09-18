from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.routers import mcp_router


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_count", [0, 2])
async def test_connection_requires_discovered_tools(monkeypatch, tool_count):
    async def get_server(*args):
        return SimpleNamespace(slug="blender-local")

    async def get_tools(*args):
        return [SimpleNamespace(name=f"tool_{index}") for index in range(tool_count)]

    monkeypatch.setattr(mcp_router, "get_server_or_404", get_server)
    monkeypatch.setattr(mcp_router, "get_all_mcp_tools", get_tools)

    if tool_count == 0:
        with pytest.raises(HTTPException) as exc:
            await mcp_router.test_mcp_server("blender-local", current_user=None, db=None)
        assert exc.value.status_code == 500
        assert "未发现可用工具" in exc.value.detail
    else:
        result = await mcp_router.test_mcp_server("blender-local", current_user=None, db=None)
        assert result["success"] is True
        assert result["tool_count"] == tool_count
