# Agent 工具开发指南

本文档说明如何在 Yuxi 中新增、注册、测试和验证 Agent 工具。默认场景是开发一个随 Yuxi 代码发布的可信 Python 工具；如果目标是运行时接入外部服务，应先判断 MCP 是否更合适。

## 1. 先判断扩展方式

不要把所有能力都实现成内置工具。开始编码前，先按下面的边界选择：

| 方式 | 适用场景 | 是否需要发布 Yuxi | 管理方式 |
| --- | --- | --- | --- |
| 内置工具 | 需要访问 Yuxi 内部服务、运行时上下文、沙盒或可信本地代码 | 是 | 代码注册，管理员为智能体选择 |
| MCP | 独立外部系统、希望管理员动态接入或启停的工具集 | 否，MCP 服务独立发布 | 扩展页面管理 MCP 服务和工具 |
| Skill | 给 Agent 提供工作流、方法、脚本和工具依赖说明 | 通常不需要修改 Agent 核心代码 | Skills 页面安装和启用 |

简单判断：

- 能力必须调用 Yuxi 内部对象或可信本地代码：使用内置工具。
- 能力可以独立部署，并希望不修改 Yuxi 就能接入：优先使用 MCP。
- 目标是教 Agent 如何组合已有工具完成任务：使用 Skill，不要再造执行工具。

内置工具本质上是后端可执行代码。不要提供让普通用户在页面中直接编写或上传 Python 工具的入口，这会引入远程代码执行、依赖管理和权限隔离问题。

## 2. 理解现有工具链路

内置工具的主要链路如下：

```text
工具模块被导入
  → @tool 创建 LangChain 工具并写入注册表
  → toolkits/service.py 提取元数据
  → /api/system/tools 返回工具目录
  → 智能体配置保存工具 ID
  → resolve_configured_runtime_tools() 装配运行时工具
  → 前端渲染工具调用和结果
```

对应代码位置：

- `backend/package/yuxi/agents/toolkits/registry.py`：`@tool` 装饰器和进程内注册表。
- `backend/package/yuxi/agents/toolkits/service.py`：工具元数据、分类查询和运行时工具解析。
- `backend/package/yuxi/agents/toolkits/buildin/__init__.py`：内置工具显式导入入口。
- `backend/server/routers/tool_router.py`：`/api/system/tools` 查询接口。
- `web/src/components/ToolCallingResult/ToolCallRenderer.vue`：工具调用结果选择专用或通用渲染器。

注册发生在模块导入时，工具元数据又有进程内缓存。因此新增工具后需要让 API 和 Worker 重新加载代码；只刷新浏览器工具列表不能发现尚未被后端进程导入的模块。

## 3. 目录和文件组织

### 3.1 默认使用独立文件

一个职责单一、实现规模较小的工具，放在：

```text
backend/package/yuxi/agents/toolkits/buildin/<capability>.py
```

例如天气工具：

```text
backend/package/yuxi/agents/toolkits/buildin/weather.py
```

不要继续把不相关能力堆入 `buildin/tools.py`，也不要创建 `common_tools.py`、`general_tools.py` 这类容易成为杂物箱的文件。

### 3.2 何时升级为领域包

满足以下任一条件时，可以按领域建立文件夹：

- 同一领域已有多个相关工具。
- 多个工具共享客户端、Schema 或稳定的业务逻辑。
- 单个文件已经难以从上到下理解。

推荐结构：

```text
toolkits/
└── weather/
    ├── __init__.py
    ├── client.py
    ├── schemas.py
    └── tools.py
```

不要仅为了“未来可能复用”提前创建 `BaseTool`、Provider 抽象或多层 helper。当前 `@tool` 已经是所有本地工具共享的注册协议。

## 4. 开发前定义验收标准

先把需求压缩成可测试的输入、输出和失败行为。以天气工具首版为例：

- 输入：非空城市名称。
- 输出：城市、天气描述、温度、体感温度、湿度、风速和观测时间。
- 数据源：Open-Meteo。
- 失败：城市不存在、上游请求失败或响应缺少必要字段时明确报错。
- 不包含：天气预报、缓存、重试、多供应商回退和专用前端卡片。

如果无法用几句话说清验收标准，先继续拆需求，不要开始编码。

## 5. 先写失败测试

新增功能遵循测试优先：先定义希望使用的工具接口，运行测试确认它因功能尚不存在而失败，再写最小实现。

测试文件放在：

```text
backend/test/unit/toolkits/buildin/test_<capability>_tool.py
```

调用异步 LangChain 工具时，可以直接调用工具对象的 `coroutine`：

```python
result = await get_weather.coroutine(city="北京")
```

外部 HTTP 服务必须在单元测试中 Mock，不要让单元测试依赖网络。项目已经提供 `pytest-httpx`：

```python
import pytest
from pytest_httpx import HTTPXMock

from yuxi.agents.toolkits.buildin.weather import get_weather


@pytest.mark.asyncio
async def test_get_weather_returns_current_weather(httpx_mock: HTTPXMock) -> None:
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
    httpx_mock.add_response(
        json={
            "current": {
                "time": "2026-07-21T17:15",
                "temperature_2m": 30.0,
                "apparent_temperature": 34.0,
                "relative_humidity_2m": 63,
                "weather_code": 3,
                "wind_speed_10m": 6.6,
            }
        }
    )

    result = await get_weather.coroutine(city="北京")

    assert result["city"] == "北京"
    assert result["temperature_c"] == 30.0
```

至少覆盖：

- 一条主要成功路径。
- 用户可预期的无结果场景。
- 外部依赖失败或返回数据不完整。
- 工具 ID、分类和展示名称等注册元数据。

更完整的测试要求见 [测试规范与工作流](./testing-guidelines.md)。

## 6. 编写工具模块

下面是推荐的最小结构：

```python
from __future__ import annotations

from pydantic import BaseModel, Field

from yuxi.agents.toolkits.registry import tool


class NormalizeTextInput(BaseModel):
    text: str = Field(
        min_length=1,
        description="需要去除首尾空白的文本",
    )


@tool(
    category="buildin",
    tags=["文本"],
    display_name="整理文本",
    description="去除文本首尾空白。用户明确要求清理一段短文本时使用。",
    args_schema=NormalizeTextInput,
)
def normalize_text(text: str) -> dict[str, str]:
    normalized = text.strip()
    if not normalized:
        raise ValueError("文本不能为空")

    return {"text": normalized}
```

这个示例可以直接运行，用于说明完整的注册外壳。涉及异步 HTTP、外部响应转换和异常边界的正式示例见 `backend/package/yuxi/agents/toolkits/buildin/weather.py`。

### 6.1 工具 ID

默认情况下，函数名就是工具 ID。`get_weather` 会保存到智能体配置并出现在模型工具定义中，因此必须：

- 使用稳定、清晰的英文 `snake_case` 名称。
- 在整个工具注册表中保持唯一。
- 发布后不要随意重命名，否则历史智能体配置中的旧 ID 会失效。

### 6.2 输入 Schema

- 使用 Pydantic 模型描述模型可传入的参数。
- 每个参数提供对模型有意义的 `description`。
- 只暴露用户或模型需要决定的参数。
- API Key、内部路径、用户 ID 等系统信息不要作为普通模型参数；应从配置、环境或 `ToolRuntime` 获取。

### 6.3 工具描述和元数据

- `description` 面向模型，应说明能力和适用时机，不只是重复函数名。
- `display_name` 面向用户，使用简洁中文。
- `tags` 用于目录识别和检索，不要堆叠无关标签。
- 普通可由智能体配置选择的本地工具，当前必须使用 `category="buildin"`。

目录名称和注册分类不是同一个概念。即使代码将来放进独立领域包，只要它仍是普通可配置本地工具，注册分类仍应是 `buildin`。`knowledge` 等分类有独立的资源和 Skill 装配语义，不要仅为了页面分组随意使用。

### 6.4 运行时上下文

需要线程、用户或沙盒信息时，通过 LangGraph `ToolRuntime` 注入：

```python
from langgraph.prebuilt.tool_node import ToolRuntime


async def example_tool(value: str, runtime: ToolRuntime) -> dict:
    thread_id = runtime.context.thread_id
    ...
```

不要信任模型传入用户 ID、线程 ID 或宿主机真实路径。涉及沙盒文件时，复用项目的虚拟路径解析和权限边界。

## 7. 错误处理规范

工具失败应让问题暴露出来，而不是返回看似成功的伪造数据。

推荐做法：

- 参数无效：抛出 `ValueError`，说明哪个输入不合法。
- 业务无结果：给出明确、可理解的错误，例如“未找到城市”。
- 外部请求失败：捕获对应客户端异常，保留异常链并转换成稳定错误信息。
- 响应字段缺失：明确报告上游数据不完整。

禁止：

- 请求失败后返回硬编码“默认天气”。
- 未经需求确认自动切换到另一个供应商。
- 使用 `except Exception` 吞掉程序错误并返回空对象。
- 为简单线性流程拆出大量单次使用 helper。
- 在日志、返回值、测试或文档中输出 API Key 等敏感信息。

`@tool` 已统一设置 `handle_tool_error`，但工具本身仍应提供清晰的错误边界；不要依赖通用处理器猜测业务失败原因。

## 8. 显式注册工具

只新增 Python 文件不会自动执行其中的装饰器。必须在对应包入口显式导入：

```python
# backend/package/yuxi/agents/toolkits/buildin/__init__.py
from .weather import get_weather

__all__ = [
    "get_weather",
]
```

实际修改时保留入口文件中已有导出，只增加新工具。显式导入的价值是：注册时机可预测，也能从入口快速看到当前内置能力。

## 9. 前端是否需要修改

大多数新工具不需要修改前端。

工具注册成功后：

- 扩展页面通过 `/api/system/tools` 获取名称、描述、参数、分类和标签。
- 智能体配置通过后端元数据获得可选工具。
- 未注册专用结果组件的工具由 `BaseToolCall` 通用展示参数和 JSON/文本结果。

只有出现明确的交互或可视化需求时，才增加前端专用渲染：

- 独立图标：检查 `web/src/components/ToolCallingResult/toolRegistry.js`。
- 专用结果组件：在 `web/src/components/ToolCallingResult/tools/` 新建组件，并注册到 `ToolCallRenderer.vue`。
- 需要隐藏或参与特殊分组：集中维护在 `toolRegistry.js`，不要把判断散落到聊天组件。

专用组件是展示增强，不应成为工具可执行的前置条件。新增专用渲染时还需要补前端测试并执行 pnpm 的格式化和 Lint。

## 10. 验证流程

### 10.1 RED：确认测试先失败

```powershell
Set-Location backend
uv run --group test pytest test/unit/toolkits/buildin/test_weather_tool.py
```

失败原因必须是功能尚不存在或行为尚未实现，而不是测试拼写错误、fixture 缺失或环境故障。

### 10.2 GREEN：实现后运行最小测试

```powershell
uv run --group test pytest test/unit/toolkits/buildin/test_weather_tool.py
```

### 10.3 运行注册链路回归测试

```powershell
uv run --group test pytest `
  test/unit/toolkits/test_tool_registry.py `
  test/unit/services/test_tool_service.py
```

### 10.4 格式化和 Lint

```powershell
uv run ruff format `
  package/yuxi/agents/toolkits/buildin/weather.py `
  test/unit/toolkits/buildin/test_weather_tool.py

uv run ruff check `
  package/yuxi/agents/toolkits/buildin/weather.py `
  package/yuxi/agents/toolkits/buildin/__init__.py `
  test/unit/toolkits/buildin/test_weather_tool.py
```

### 10.5 验证工具发现

```powershell
uv run python -c "from yuxi.agents.toolkits.service import get_tool_metadata; print(next(item for item in get_tool_metadata('buildin') if item['slug'] == 'get_weather'))"
```

结果应至少包含：

```text
slug: get_weather
name: 查询天气
category: buildin
```

### 10.6 验证真实链路

单元测试通过后，再在允许访问真实依赖的开发环境中验证：

1. 重启或确认 API、Worker 已热重载新模块。
2. 请求 `/api/system/tools`，确认工具出现在目录中。
3. 在智能体配置中选择工具并保存。
4. 发起一条能够明确触发工具的问题。
5. 确认 Worker 成功执行，聊天页面至少能够用通用组件展示参数和结果。

真实依赖验证不能替代单元测试，也不要把真实网络请求写进单元测试。

## 11. 文档和变更记录

完成代码后：

- 在 `docs/develop-guides/changelog.md` 当前版本中记录新增能力和关键边界。
- 如果增加面向用户的配置、部署要求或正式功能说明，同步更新对应正式文档和 `docs/.vitepress/config.mts` 导航。

变更记录只写已经发生并验证过的事实，不写计划中的能力。

## 12. 常见问题

### 工具文件已经创建，为什么页面看不到？

依次检查：

1. 是否在包的 `__init__.py` 中显式导入。
2. `@tool` 是否成功执行，工具 ID 是否与现有工具冲突。
3. API 是否重新加载了模块，元数据缓存是否仍来自旧进程。
4. `/api/system/tools` 是否已经返回该工具。

### 工具在扩展页面可见，为什么智能体配置里没有？

普通本地工具应使用 `category="buildin"`。当前智能体普通工具选项和运行时解析只选择这个分类；其他分类可能属于知识库、Skill 或专用装配链路。

### 工具能够调用，但页面展示很普通，是否注册失败？

不是。通用结果展示是默认行为。只有需要图表、文件预览或复杂交互时才增加专用前端组件。

### 新增工具后，已有智能体会自动启用吗？

取决于智能体保存的资源配置。使用“默认全部”的配置会按运行时可用工具解析；已经保存为显式工具列表的智能体需要手动勾选新工具。

### 一个文件应该放几个工具？

按领域内聚判断，而不是机械地“一工具一文件”。一个独立能力优先单文件；多个共享客户端和业务语义的工具可以放在同一领域包中。不要把无关工具放进同一个 `tools.py`。

## 13. 提交前 Checklist

- [ ] 已确认内置工具比 MCP 或 Skill 更适合当前需求。
- [ ] 输入、输出、失败行为和非目标已经明确。
- [ ] 工具 ID 唯一、稳定并使用 `snake_case`。
- [ ] 参数 Schema 和工具描述能让模型理解何时以及如何调用。
- [ ] 普通可配置工具使用 `category="buildin"`。
- [ ] 新模块已在对应 `__init__.py` 中显式导入。
- [ ] 新行为遵循测试优先，并看到了预期的 RED 和 GREEN。
- [ ] 单元测试不访问真实网络，也不依赖系统预置数据。
- [ ] 错误路径不会返回伪造成功数据或静默回退。
- [ ] 已运行相关单元测试、格式化和 Lint。
- [ ] 已验证后端工具发现；必要时完成真实 Agent 调用。
- [ ] 已判断是否需要前端专用渲染，默认优先复用通用组件。
- [ ] 已更新 changelog 和独立变更记录。
- [ ] 未提交密钥、Token、本地环境文件或真实测试凭据。
