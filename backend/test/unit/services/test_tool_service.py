from __future__ import annotations

from types import SimpleNamespace

from yuxi.agents.toolkits import service as tool_service


def test_get_tool_metadata_includes_config_guide(monkeypatch):
    tool_service._metadata_cache.clear()

    fake_tool = SimpleNamespace(
        name="demo_tool",
        description="demo description",
        metadata={},
        args={
            "query": {
                "type": "string",
                "description": "查询内容",
            }
        },
    )
    fake_extra = SimpleNamespace(
        category="buildin",
        tags=["demo"],
        display_name="演示工具",
        config_guide="请先配置 DEMO_API_KEY",
    )

    monkeypatch.setattr(
        "yuxi.agents.toolkits.registry.get_all_tool_instances",
        lambda: [fake_tool],
    )
    monkeypatch.setattr(
        "yuxi.agents.toolkits.registry.get_all_extra_metadata",
        lambda: {"demo_tool": fake_extra},
    )

    result = tool_service.get_tool_metadata()

    assert result == [
        {
            "slug": "demo_tool",
            "name": "演示工具",
            "description": "demo description",
            "metadata": {},
            "args": [
                {
                    "name": "query",
                    "type": "string",
                    "description": "查询内容",
                }
            ],
            "category": "buildin",
            "tags": ["demo"],
            "config_guide": "请先配置 DEMO_API_KEY",
        }
    ]

    tool_service._metadata_cache.clear()


def test_schedule_tool_metadata_excludes_injected_runtime():
    tool_service._metadata_cache.clear()

    result = {item["slug"]: item for item in tool_service.get_tool_metadata(category="buildin")}

    assert [arg["name"] for arg in result["get_schedule_audit"]["args"]] == ["schedule_snapshot_id"]
    assert [arg["name"] for arg in result["get_schedule_issue_context"]["args"]] == ["issue_id"]

    tool_service._metadata_cache.clear()
