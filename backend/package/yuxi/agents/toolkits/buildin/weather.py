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
