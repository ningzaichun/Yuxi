# 天气查询工具实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一个按城市查询 Open-Meteo 当前天气的独立内置工具，并接入现有工具注册、配置和通用展示链路。

**Architecture:** 工具实现集中在新的 `buildin/weather.py`，使用 Open-Meteo 地理编码和天气接口完成两段式查询。现有 `@tool` 注册器、`buildin/__init__.py` 导入入口和前端通用工具渲染保持不变，只增加显式注册和针对真实行为的单元测试。

**Tech Stack:** Python 3.12+、LangChain Tool、Pydantic、httpx、pytest、pytest-httpx、Ruff。

## Global Constraints

- 首版只查询当前天气，不实现天气预报、空气质量、历史天气和同名城市交互选择。
- 数据源固定为 Open-Meteo，不新增 API Key、缓存、重试或供应商回退。
- 工具代码使用独立文件，不重构 `buildin/tools.py`，不新增前端专用组件。
- 城市不存在、上游请求失败或必要字段缺失时必须显式报错，不返回伪造数据。
- 保留工作区现有 `docker/sandbox_provisioner/sandbox.env` 修改，不纳入本功能提交。

---

### Task 1: 以测试驱动实现并注册天气工具

**Files:**
- Create: `backend/package/yuxi/agents/toolkits/buildin/weather.py`
- Modify: `backend/package/yuxi/agents/toolkits/buildin/__init__.py`
- Test: `backend/test/unit/toolkits/buildin/test_weather_tool.py`

**Interfaces:**
- Consumes: `yuxi.agents.toolkits.registry.tool`、Open-Meteo `/v1/search` 和 `/v1/forecast`。
- Produces: LangChain 工具对象 `get_weather`，工具 ID 为 `get_weather`，协程输入为 `city: str`，结果为天气结构化字典。

- [ ] **Step 1: 编写成功查询、城市不存在、响应不完整、请求失败和注册元数据测试**

```python
from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from yuxi.agents.toolkits.buildin.weather import get_weather
from yuxi.agents.toolkits.registry import get_extra_metadata

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_get_weather_returns_current_weather(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=(
            "https://geocoding-api.open-meteo.com/v1/search"
            "?name=%E5%8C%97%E4%BA%AC&count=1&language=zh&format=json"
        ),
        json={
            "results": [
                {
                    "name": "北京",
                    "country": "中国",
                    "latitude": 39.9075,
                    "longitude": 116.39723,
                    "timezone": "Asia/Shanghai",
                }
            ]
        },
    )
    httpx_mock.add_response(
        url=(
            "https://api.open-meteo.com/v1/forecast"
            "?latitude=39.9075&longitude=116.39723"
            "&current=temperature_2m%2Capparent_temperature%2Crelative_humidity_2m%2Cweather_code%2Cwind_speed_10m"
            "&timezone=Asia%2FShanghai"
        ),
        json={
            "current": {
                "time": "2026-07-21T17:15",
                "temperature_2m": 30.0,
                "apparent_temperature": 34.0,
                "relative_humidity_2m": 63,
                "weather_code": 3,
                "wind_speed_10m": 6.6,
            }
        },
    )

    result = await get_weather.coroutine(city="北京")

    assert result == {
        "city": "北京",
        "country": "中国",
        "latitude": 39.9075,
        "longitude": 116.39723,
        "timezone": "Asia/Shanghai",
        "weather": "阴",
        "temperature_c": 30.0,
        "apparent_temperature_c": 34.0,
        "relative_humidity_percent": 63,
        "wind_speed_kmh": 6.6,
        "observed_at": "2026-07-21T17:15",
        "source": "Open-Meteo",
    }


@pytest.mark.asyncio
async def test_get_weather_rejects_unknown_city(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json={"results": []})

    with pytest.raises(ValueError, match="未找到城市"):
        await get_weather.coroutine(city="不存在的城市")


@pytest.mark.asyncio
async def test_get_weather_rejects_incomplete_weather_response(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        json={
            "results": [
                {
                    "name": "北京",
                    "country": "中国",
                    "latitude": 39.9075,
                    "longitude": 116.39723,
                    "timezone": "Asia/Shanghai",
                }
            ]
        }
    )
    httpx_mock.add_response(json={"current": {"temperature_2m": 30.0}})

    with pytest.raises(ValueError, match="天气服务返回数据不完整"):
        await get_weather.coroutine(city="北京")


@pytest.mark.asyncio
async def test_get_weather_reports_request_failure(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("network unavailable"))

    with pytest.raises(ValueError, match="天气服务请求失败"):
        await get_weather.coroutine(city="北京")


def test_get_weather_registers_buildin_metadata() -> None:
    metadata = get_extra_metadata("get_weather")

    assert get_weather.name == "get_weather"
    assert metadata is not None
    assert metadata.category == "buildin"
    assert metadata.display_name == "查询天气"
```

- [ ] **Step 2: 运行测试并确认因模块尚不存在而失败**

Run:

```powershell
Set-Location backend
uv run --group test pytest test/unit/toolkits/buildin/test_weather_tool.py
```

Expected: FAIL，错误包含 `ModuleNotFoundError: No module named 'yuxi.agents.toolkits.buildin.weather'`。

- [ ] **Step 3: 新增最小天气工具实现**

`backend/package/yuxi/agents/toolkits/buildin/weather.py`：

```python
from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field

from yuxi.agents.toolkits.registry import tool

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
_CURRENT_FIELDS = "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m"
_WEATHER_DESCRIPTIONS = {
    0: "晴",
    1: "大部晴朗",
    2: "局部多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "中等毛毛雨",
    55: "强毛毛雨",
    56: "轻微冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "轻微冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "米雪",
    80: "小阵雨",
    81: "中等阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "强阵雪",
    95: "雷暴",
    96: "雷暴伴轻微冰雹",
    99: "雷暴伴强冰雹",
}


class WeatherInput(BaseModel):
    city: str = Field(min_length=1, description="需要查询当前天气的城市名称，例如：北京、上海、深圳")


@tool(
    category="buildin",
    tags=["天气", "查询"],
    display_name="查询天气",
    description="查询指定城市的当前真实天气。用户询问某个城市当前天气、温度、湿度或风速时使用。",
    args_schema=WeatherInput,
)
async def get_weather(city: str) -> dict[str, Any]:
    city = city.strip()
    if not city:
        raise ValueError("城市名称不能为空")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            location_response = await client.get(
                _GEOCODING_URL,
                params={"name": city, "count": 1, "language": "zh", "format": "json"},
            )
            location_response.raise_for_status()
            location_data = location_response.json()
            locations = location_data.get("results") if isinstance(location_data, dict) else None
            if not locations:
                raise ValueError(f"未找到城市：{city}")

            try:
                location = locations[0]
                latitude = location["latitude"]
                longitude = location["longitude"]
                timezone = location["timezone"]
            except (KeyError, TypeError) as exc:
                raise ValueError("天气服务返回数据不完整") from exc
            weather_response = await client.get(
                _WEATHER_URL,
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "current": _CURRENT_FIELDS,
                    "timezone": timezone,
                },
            )
            weather_response.raise_for_status()
            weather_data = weather_response.json()
    except httpx.HTTPError as exc:
        raise ValueError(f"天气服务请求失败：{exc}") from exc

    try:
        current = weather_data["current"]
        weather_code = current["weather_code"]
        return {
            "city": location["name"],
            "country": location["country"],
            "latitude": latitude,
            "longitude": longitude,
            "timezone": timezone,
            "weather": _WEATHER_DESCRIPTIONS.get(weather_code, f"未知天气代码 {weather_code}"),
            "temperature_c": current["temperature_2m"],
            "apparent_temperature_c": current["apparent_temperature"],
            "relative_humidity_percent": current["relative_humidity_2m"],
            "wind_speed_kmh": current["wind_speed_10m"],
            "observed_at": current["time"],
            "source": "Open-Meteo",
        }
    except (KeyError, TypeError) as exc:
        raise ValueError("天气服务返回数据不完整") from exc
```

- [ ] **Step 4: 显式注册工具**

在 `backend/package/yuxi/agents/toolkits/buildin/__init__.py` 导入：

```python
from .weather import get_weather
```

并将 `"get_weather"` 加入 `__all__`。

- [ ] **Step 5: 运行天气工具单元测试并确认通过**

Run:

```powershell
Set-Location backend
uv run --group test pytest test/unit/toolkits/buildin/test_weather_tool.py
```

Expected: 5 tests passed。

- [ ] **Step 6: 运行工具注册与运行时相关回归测试**

Run:

```powershell
Set-Location backend
uv run --group test pytest test/unit/toolkits/test_tool_registry.py test/unit/services/test_tool_service.py
```

Expected: all tests passed。

---

### Task 2: 更新记录并完成静态和运行验证

**Files:**
- Modify: `docs/develop-guides/changelog.md`
- Create: `docs/change_logs/change_2026-07-21_17-19-01.md`

**Interfaces:**
- Consumes: Task 1 已通过测试的 `get_weather` 工具。
- Produces: 面向维护者的正式 changelog 条目和本次真实变更审计记录。

- [ ] **Step 1: 在当前版本开发记录中增加天气工具说明**

在 `docs/develop-guides/changelog.md` 的 `v0.7.1 (current)` → `开发记录` 中新增：

```markdown
- 新增 Agent 内置天气查询工具：按城市名称通过 Open-Meteo 地理编码与当前天气接口返回城市、天气状况、温度、体感温度、湿度和风速；工具使用独立 `weather.py` 模块注册，无需 API Key，城市不存在或上游异常时明确报错，并复用现有通用工具调用展示。
```

- [ ] **Step 2: 使用 code-change-log-assistant 生成独立中文变更记录**

创建 `docs/change_logs/change_2026-07-21_17-19-01.md`，基于实际 diff 记录需求背景、修改文件、核心实现、测试结果和未包含范围，不记录敏感值。

- [ ] **Step 3: 格式化并检查本次 Python 文件**

Run:

```powershell
Set-Location backend
uv run ruff format package/yuxi/agents/toolkits/buildin/weather.py test/unit/toolkits/buildin/test_weather_tool.py
uv run ruff check package/yuxi/agents/toolkits/buildin/weather.py package/yuxi/agents/toolkits/buildin/__init__.py test/unit/toolkits/buildin/test_weather_tool.py
```

Expected: `All checks passed!`，格式化后无额外失败。

- [ ] **Step 4: 运行全部相关单元测试**

Run:

```powershell
Set-Location backend
uv run --group test pytest test/unit/toolkits/buildin/test_weather_tool.py test/unit/toolkits/test_tool_registry.py test/unit/services/test_tool_service.py
```

Expected: all tests passed。

- [ ] **Step 5: 验证后端工具发现结果**

Run:

```powershell
Set-Location backend
uv run python -c "from yuxi.agents.toolkits.service import get_tool_metadata; print(next(item for item in get_tool_metadata('buildin') if item['slug'] == 'get_weather'))"
```

Expected: 输出包含 `slug: get_weather`、`name: 查询天气`、`category: buildin`。

- [ ] **Step 6: 检查并提交功能变更**

Run:

```powershell
git diff --check
git status --short
git add backend/package/yuxi/agents/toolkits/buildin/weather.py backend/package/yuxi/agents/toolkits/buildin/__init__.py backend/test/unit/toolkits/buildin/test_weather_tool.py docs/develop-guides/changelog.md docs/change_logs/change_2026-07-21_17-19-01.md
git commit -m "feat(agent): 新增天气查询工具"
```

Expected: 提交只包含天气工具、测试和对应文档，不包含 `docker/sandbox_provisioner/sandbox.env`。
