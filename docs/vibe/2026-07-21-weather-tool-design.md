# 天气查询工具设计

## 目标

新增一个职责单一的内置天气查询工具，验证独立工具文件从后端注册、智能体配置到通用前端展示的完整链路，同时不重构现有工具代码。

## 验收标准

- 用户输入城市名称后，工具返回城市、国家或地区、天气状况、温度、体感温度、相对湿度和风速。
- 工具通过 Open-Meteo 获取真实天气数据，不需要 API Key。
- 工具出现在 `/api/system/tools` 返回结果和智能体内置工具选项中。
- 城市不存在、上游请求失败或响应缺少必要字段时，返回明确错误，不伪造数据或切换其他数据源。
- 前端使用现有通用工具调用组件展示，不新增专用天气组件。

## 实现范围

### 后端结构

- 新增 `backend/package/yuxi/agents/toolkits/buildin/weather.py`。
- 文件内定义城市输入模型、天气代码中文描述映射和 `get_weather` 工具。
- 在 `backend/package/yuxi/agents/toolkits/buildin/__init__.py` 中显式导入并导出 `get_weather`，确保模块加载时完成注册。
- 工具注册分类使用 `buildin`，以复用现有智能体工具选择和运行时解析链路。

### 数据流

1. `get_weather` 接收非空城市名称。
2. 调用 Open-Meteo `/v1/search` 地理编码接口，使用 `count=1`、`language=zh`，选择首个匹配结果并取得规范城市名、国家或地区、经纬度和时区。
3. 使用经纬度调用 Open-Meteo `/v1/forecast`，请求 `temperature_2m`、`apparent_temperature`、`relative_humidity_2m`、`weather_code` 和 `wind_speed_10m` 当前值，时区使用城市本地时区。
4. 将天气代码转换为中文描述，返回稳定的结构化字典。

返回字段固定为：

- `city`：规范城市名。
- `country`：国家或地区名。
- `latitude`、`longitude`：匹配城市坐标。
- `timezone`：城市时区。
- `weather`：中文天气描述。
- `temperature_c`、`apparent_temperature_c`：摄氏度。
- `relative_humidity_percent`：相对湿度百分比。
- `wind_speed_kmh`：每小时公里数。
- `observed_at`：天气数据对应的城市本地时间。
- `source`：固定为 `Open-Meteo`。

首版只查询当前天气，不包含未来预报、空气质量、历史天气和同名城市交互选择。

### 异常处理

- 城市名称为空：参数校验失败。
- 没有城市匹配结果：抛出明确的城市未找到错误。
- HTTP、状态码或响应结构异常：抛出明确的天气服务错误。
- 不增加重试、缓存、多供应商回退或伪造默认值。

## 测试

在 `backend/test/unit/toolkits/buildin/test_weather_tool.py` 中使用 Mock HTTP 响应验证：

- 正常城市查询返回预期结构和中文天气描述。
- 城市不存在时报告明确错误。
- 上游请求失败时报告明确错误。
- 工具元数据包含稳定 ID、展示名称和 `buildin` 分类。

相关单元测试通过后，执行与改动范围匹配的 Ruff 检查；如本地开发服务可用，再验证工具列表接口和一次智能体实际调用。

## Checklist

- [ ] 先编写失败的天气工具单元测试
- [ ] 实现独立 `weather.py`
- [ ] 在内置工具入口注册
- [ ] 更新项目变更记录
- [ ] 运行单元测试和代码检查
- [ ] 验证工具发现与通用展示链路
