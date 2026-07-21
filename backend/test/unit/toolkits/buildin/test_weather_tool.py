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
        url=("https://geocoding-api.open-meteo.com/v1/search?name=%E5%8C%97%E4%BA%AC&count=1&language=zh&format=json"),
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
