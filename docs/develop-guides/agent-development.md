# 新业务智能体开发指南

本指南回答一个具体问题：当出现新的业务需求时，如何在 Yuxi 中创建、扩展、测试并交付一个智能体。

先记住 Yuxi 中两个容易混淆的概念：

- **Agent**：数据库中的业务智能体，包含名称、`slug`、后端类型、系统提示词、模型、工具、知识库、Skills、MCP、子智能体和共享范围。大多数新业务需求只需要创建一个 Agent。
- **Agent Backend**：Python 中实现的 LangGraph 运行引擎，例如 `ChatbotAgent` 和 `SubAgentBackend`。只有执行图、状态结构或中间件链路不同，才需要开发新的 Backend。

推荐顺序是：**先用配置验证业务，再补 Skill / Tool / MCP，最后才考虑新 Backend**。不要为一段新提示词复制整套 LangGraph 实现。

## 1. 先定义业务验收标准

开发前先写一张最小需求卡，至少回答：

| 项目 | 需要明确的内容 |
| --- | --- |
| 用户 | 谁使用这个智能体，拥有哪些数据和操作权限 |
| 输入 | 用户会提供什么文本、附件或结构化参数 |
| 输出 | 最终回答、文件或业务动作的格式 |
| 数据 | 需要查询哪些知识库或外部系统 |
| 动作 | 只读查询，还是会创建、修改外部数据 |
| 人工确认 | 哪些高风险动作必须先让用户确认 |
| 成功标准 | 哪些可观察结果代表任务完成 |
| 非目标 | 本次明确不处理什么 |

示例：

```text
名称：售后工单助手
用户：客服人员
输入：客户描述、订单号、可选截图
输出：问题分类、处理建议、回复草稿
数据：售后政策知识库、订单查询接口
动作：第一期只查询，不自动创建或修改工单
成功标准：典型问题分类正确，建议引用有效政策，订单不存在时明确说明
非目标：不处理退款审批，不替客服向客户发送消息
```

这个边界决定后续选择提示词、知识库、Skill、Tool、MCP 还是自定义 Backend。

## 2. 选择最小实现路径

| 需求特征 | 推荐方式 | 是否需要写 Python |
| --- | --- | --- |
| 新角色、新提示词、不同模型或资源组合 | 创建 Agent，复用 `ChatbotAgent` | 否 |
| 稳定的方法论、步骤或输出规范 | Skill | 通常否 |
| 调用 Yuxi 内部 Python 能力 | 内置 Tool | 是 |
| 调用已有外部标准服务 | MCP | 通常否 |
| 检索企业文档 | 知识库 + `knowledge-base` Skill | 否 |
| 把独立任务交给专用角色 | 子智能体 | 通常否 |
| 需要新的 LangGraph 节点、状态或中间件顺序 | 自定义 Agent Backend | 是 |

如果一个需求同时需要多种能力，仍然从最小组合开始。例如“售后工单助手”可以先使用：

```text
ChatbotAgent
  + 售后处理 Skill
  + 售后政策知识库
  + 订单查询 Tool 或 MCP
```

它不需要新的 Agent Backend。

## 3. 路径一：直接创建业务 Agent

这是默认路径，适用于绝大多数业务智能体。

### 3.1 准备环境

先按[本地开发指南](./local-development.md)启动 API、Worker、Web 和 Sandbox Provisioner，并确认：

- Web：`http://127.0.0.1:5173`
- API：`http://127.0.0.1:5050`
- Sandbox Provisioner：`http://127.0.0.1:8002`
- Worker 已成功连接 Redis 并等待任务

如果只是使用现有工具且不涉及 Sandbox，普通聊天不应为了启动检查而创建 Runtime 容器。

### 3.2 在管理页创建

1. 打开“智能体管理”，对应前端路由 `/model-manage?tab=agents`。
2. 点击“新增智能体”。
3. 填写名称、稳定的 `slug`、描述、图标和共享范围。
4. 后端选择“智能助手”，即 `ChatbotAgent`。
5. 首次保存后再次打开该智能体。
6. 在“模型配置”“工具配置”“其他配置”中设置系统提示词、模型和资源。
7. 保存后点击“去对话”进行验证。

当前界面在创建阶段只保存基本信息；运行配置页签会在 Agent 创建后进入编辑状态时出现。因此“创建后再编辑一次”是正常流程。

`slug` 是 API、会话绑定和运行记录使用的稳定标识。建议使用小写英文和连字符，例如 `after-sales-assistant`，创建后不要把名称当成稳定标识使用。

### 3.3 编写系统提示词

系统提示词优先描述稳定边界，不要把一次性任务写进长期配置。可以使用下面的最小结构：

```text
你是【角色名称】，服务于【目标用户】。

你的目标：
- 【目标一】
- 【目标二】

工作步骤：
1. 先确认完成任务所需的关键信息。
2. 按需读取已配置的 Skill、知识库或调用工具。
3. 基于真实结果回答，不补造未返回的数据。
4. 按【输出格式】给出结果。

约束：
- 不执行【明确禁止的动作】。
- 涉及【高风险动作】时，必须先获得用户确认。
- 数据不足或工具失败时，明确说明缺少什么以及下一步。
```

不要仅靠提示词实现本应由权限、参数校验或业务服务保证的安全边界。外部写操作仍要在 Tool、MCP 服务或后端接口中执行真实鉴权。

### 3.4 配置资源

资源字段未显式配置时，当前语义通常是启用该用户可访问的全部资源。面向具体业务时，推荐保存明确的允许列表，减少无关工具和数据进入模型上下文。

| 配置 | 建议 |
| --- | --- |
| 模型 | 先用系统默认模型验证；只有质量、上下文或成本不满足时再调整 |
| Tools | 只选择完成业务动作所需的工具 |
| 知识库 | 只选择与业务范围匹配的知识库 |
| Skills | 选择稳定业务方法论和交付规范 |
| MCP | 选择需要访问的外部服务 |
| 子智能体 | 只有存在明确独立任务边界时启用 |

Context 字段、配置生成和运行期加载的详细机制见[智能体配置](../agents/agents-config.md)。

## 4. 路径二：为 Agent 增加业务能力

当配置本身不够时，不要立刻新增 Backend。先按能力类型扩展。

### 4.1 Skill：业务方法和工作流

适合：

- 固定分析步骤
- 行业术语与判断规则
- 输出模板和交付检查表
- 需要组合多个现有工具的操作说明
- 需要在 Sandbox 中运行配套脚本

Skill 可以声明依赖的 Tool、MCP 和其他 Skill。模型读取 `SKILL.md` 并激活 Skill 后，运行时才会挂载对应依赖。完整开发流程见[Skills 开发指南](./skills-development.md)。

### 4.2 Tool：Yuxi 内部的确定性动作

适合：

- 调用现有 Python 服务或仓储
- 需要 Yuxi 运行时上下文、权限或线程信息
- 需要稳定、结构化的参数和返回值

工具必须有清晰的输入 Schema、稳定的工具 ID 和明确失败语义。开发与注册方式见[Agent 工具开发指南](./agent-tool-development.md)。

### 4.3 MCP：复用外部服务

如果 CRM、工单、搜索或数据库能力已经以 MCP Server 提供，优先直接配置 MCP，不要在 Yuxi 内重复实现同一个客户端。接入方式见[MCP 集成](../agents/mcp-integration.md)。

### 4.4 知识库：检索业务资料

知识库适合存放政策、手册、合同、案例等可检索资料。Agent 通过内置 `knowledge-base` Skill 激活知识库工具，不应在单个 Graph 中硬编码知识库查询逻辑。

验证时不要只看“返回了答案”，还要确认：

- 使用了正确的知识库；
- 引用或来源能够定位到真实内容；
- 找不到依据时没有编造；
- 当前用户无权访问的知识库不可见。

### 4.5 子智能体：独立任务角色

只有满足下面条件时才拆子智能体：

- 子任务有独立目标和完成定义；
- 可以通过清晰输入和输出与主 Agent 协作；
- 子任务值得拥有独立上下文、checkpoint 和运行记录。

创建时选择 `SubAgentBackend`。主 Agent 通过 `subagents` 配置允许列表，并使用 `subagent_start`、`subagent_status`、`subagent_await`、`subagent_cancel` 等工具管理运行。Yuxi 当前不支持子智能体继续创建孙子智能体。详见[子智能体](../agents/subagents-management.md)。

不要把一段线性流程机械拆成多个子智能体。简单步骤放在一个 Skill 或一个主 Agent 中通常更容易理解和测试。

## 5. 路径三：开发自定义 Agent Backend

只有以下情况才新增 Backend：

- 需要 `create_agent` 之外的自定义 LangGraph 拓扑；
- 需要新的 state schema 或节点间状态；
- 必须改变模型调用、工具调用或中间件顺序；
- 需要与普通 `ChatbotAgent` 不同的流式或恢复语义。

如果只是默认提示词或资源不同，创建一个 `ChatbotAgent` 类型的 Agent 即可。

### 5.1 先理解 Backend 的运行边界

一个 Python Backend 可以被多个数据库 Agent 复用。例如“合同审查助手”和“招标文件审查助手”可以拥有不同 `slug`、提示词、知识库和共享范围，但同时使用 `ContractReviewAgent` Backend。

运行时关系是：

```text
Agent.slug
  -> Agent.backend_id
  -> AgentManager 获取 Backend 单例
  -> Backend.context_schema 还原 config_json.context
  -> 注入 uid / thread_id / run_id
  -> prepare_agent_runtime_context 过滤用户可访问资源
  -> Backend.get_graph(context)
  -> BaseAgent 统一执行 stream / invoke / resume
```

这里有三个重要约束：

1. **Backend 类名是持久化协议。** `ContractReviewAgent` 会作为 `backend_id` 写入数据库。随意改类名会让已有 Agent 找不到 Backend。
2. **Backend 实例是进程内共享单例。** 不要把当前用户、线程或资源列表保存到 `self` 上。
3. **Graph 通常依赖本次 Context。** 如果工具、MCP、Skills 或知识库因用户而异，不要把带用户资源的编译结果永久缓存到 `self.graph`，否则可能产生跨用户配置污染。

`BaseAgent` 已经统一处理：

- 把字典配置更新到 `context_schema`；
- 设置 LangGraph `thread_id`、`uid` 和 `recursion_limit`；
- 透传 Langfuse callbacks、metadata 和 tags；
- 将 messages、values、tools、tasks、lifecycle 和 custom 事件转换为上层可消费的流；
- 使用 checkpoint 读取历史、恢复中断和继续运行。

自定义 Backend 的主要职责是**为本次 Context 构建正确的 Compiled Graph**，不应重新实现聊天、run 队列或 SSE。

### 5.2 选择自定义程度

自定义 Backend 可以分成三层：

| 层级 | 改动 | 推荐场景 |
| --- | --- | --- |
| A. 复用 `ChatbotAgent` | 只换 Context、名称和默认值 | 需要独立 Backend 标识，但执行能力完全相同 |
| B. 自定义 `create_agent` 装配 | 自己选择 Prompt、Tools、State 和 Middleware | 大多数真正的自定义 Backend |
| C. 原生 `StateGraph` | 自己定义节点、边和条件路由 | 确实存在确定性多阶段状态机 |

优先从 A 或 B 开始。只有流程必须由代码强制经过多个节点，而不是由模型按提示词决定时，才升级到 C。

### 5.3 教程目标：合同审查 Backend

下面以 `ContractReviewAgent` 为例，验收标准是：

- 支持上传并读取合同附件；
- 支持选择模型、工具、知识库、MCP 和 Skills；
- 支持“标准审查”和“严格审查”两种模式；
- 输出中文或英文 Markdown；
- 保留 Yuxi 的文件系统、附件、Skill、摘要、Todo、工具调用修正、模型重试和 Token 状态能力；
- 不允许调用子智能体；
- 复用 Yuxi 的 checkpoint、流式事件和运行服务。

“不启用子智能体”是这个示例的明确业务取舍，不是因为对应中间件不重要。真实需求需要时应显式补回。

### 5.4 创建目录

当前 `AgentManager` 只扫描 `backend/package/yuxi/agents/buildin/` 的直接子目录。一个可发现的 Backend 应放在：

```text
backend/package/yuxi/agents/buildin/
└── contract_review/
    ├── __init__.py
    ├── context.py
    ├── graph.py
    ├── prompt.py
    ├── state.py        # 仅在需要自定义状态时创建
    └── tools.py        # 仅在需要 Backend 私有工具时创建
```

最小实现只需要 `__init__.py`、`context.py`、`graph.py` 和 `prompt.py`。不要提前创建空的 `state.py`、`tools.py` 或通用 `utils.py`。

`__init__.py` 必须导出定义在该包下的 `BaseAgent` 子类。自动发现还会检查该类的 `__module__` 是否位于当前包下，因此从其他包转手导入一个现有 Agent 类不会被误注册。

### 5.5 路径 A：复用标准 Chatbot Graph

如果只想验证新 Backend 的发现和配置链路，可以先复用 `ChatbotAgent` 的完整运行能力：

```python
# context.py
from dataclasses import dataclass, field

from yuxi.agents.buildin.chatbot import ChatBotContext


@dataclass(kw_only=True)
class ContractReviewContext(ChatBotContext):
    system_prompt: str = field(
        default="你是合同审查助手。请识别合同风险并引用真实条款，不要编造不存在的内容。",
        metadata={
            "name": "系统提示词",
            "description": "定义合同审查角色、边界和补充要求",
            "kind": "prompt",
        },
    )
```

```python
# graph.py
from yuxi.agents.buildin.chatbot import ChatbotAgent

from .context import ContractReviewContext


class ContractReviewAgent(ChatbotAgent):
    name = "合同审查"
    description = "复用标准 ChatbotAgent 运行链路的合同审查后端"
    context_schema = ContractReviewContext
```

```python
# __init__.py
from .context import ContractReviewContext
from .graph import ContractReviewAgent

__all__ = ["ContractReviewAgent", "ContractReviewContext"]
```

这段代码只创建了一个可独立选择的 Backend，并没有引入新的执行行为。若业务行为仍然相同，应停在“创建 Agent 实例”路径，不必保留这个重复 Backend。

### 5.6 路径 B：定义完整 Context

确实需要独立运行行为时，先定义 Context。建议仍然继承 `BaseContext`，保留 Yuxi 的模型和资源配置：

```python
# context.py
from dataclasses import dataclass, field

from yuxi.agents import BaseContext


@dataclass(kw_only=True)
class ContractReviewContext(BaseContext):
    system_prompt: str = field(
        default="只基于合同原文和已配置知识库给出结论；无法确认时标记为待核实。",
        metadata={
            "name": "补充审查要求",
            "description": "追加到 Backend 固定提示词之后的业务要求",
            "kind": "prompt",
        },
    )

    review_mode: str = field(
        default="standard",
        metadata={
            "name": "审查模式",
            "description": "标准模式关注主要风险，严格模式逐项检查并提高证据要求",
            "type": "select",
            "options": ["standard", "strict"],
        },
    )

    output_language: str = field(
        default="zh-CN",
        metadata={
            "name": "输出语言",
            "description": "最终审查报告使用的语言",
            "type": "select",
            "options": ["zh-CN", "en-US"],
        },
    )

    require_citations: bool = field(
        default=True,
        metadata={
            "name": "要求条款引用",
            "description": "结论是否必须附带合同条款或知识库依据",
        },
    )
```

常用 `metadata`：

| 字段 | 作用 |
| --- | --- |
| `name` | 表单标签 |
| `description` | 配置说明 |
| `type` | `string`、`number`、`select`、`list` 等控件提示 |
| `kind` | `llm`、`prompt`、`tools`、`knowledges`、`mcps`、`skills`、`subagents` 等特殊资源类型 |
| `options` | 单选或多选候选项；也可以是返回列表的 callable |
| `auth` | `admin` 或 `superadmin`，限制字段读取和保存 |
| `configurable=False` | 保留运行字段但不暴露到表单 |
| `hide=True` | 完全不生成配置项 |

注意：

- Context 是 dataclass，不会自动验证通过 API 写入的任意字符串。对于 `review_mode` 这类业务枚举，应在使用边界显式校验。
- `update_from_dict()` 只更新 Context 已存在的字段，未知键会被忽略。
- `tools`、`knowledges`、`mcps`、`skills` 和 `subagents` 有特殊的权限归一化语义，不要用自定义字段重复表达同一资源。
- 如果某个配置只能由管理员修改，使用 `auth`，不要只在前端隐藏。

### 5.7 独立构建 Prompt

把固定角色、业务枚举解释和用户可配置补充提示分开，避免在 `get_graph()` 中拼接大段字符串：

```python
# prompt.py
from yuxi.utils.datetime_utils import shanghai_now

from .context import ContractReviewContext


_MODE_INSTRUCTIONS = {
    "standard": "优先识别影响履约、付款、责任和终止的主要风险。",
    "strict": "逐项检查主体、标的、价款、履约、违约、终止、争议和附件一致性。",
}

_LANGUAGE_INSTRUCTIONS = {
    "zh-CN": "使用简体中文输出。",
    "en-US": "Write the final report in English.",
}

TODO_PROMPT = "复杂审查任务先使用 write_todos 规划，步骤名称保持简短。"


def build_review_prompt(context: ContractReviewContext) -> str:
    mode_instruction = _MODE_INSTRUCTIONS.get(context.review_mode)
    if mode_instruction is None:
        raise ValueError(f"不支持的审查模式: {context.review_mode}")

    language_instruction = _LANGUAGE_INSTRUCTIONS.get(context.output_language)
    if language_instruction is None:
        raise ValueError(f"不支持的输出语言: {context.output_language}")

    citation_instruction = (
        "每项风险必须给出对应合同条款、附件或知识库依据。"
        if context.require_citations
        else "有明确依据时附带条款或知识库来源。"
    )

    return f"""
当前日期：{shanghai_now().strftime("%Y-%m-%d")}

你是合同审查智能体。你的任务是识别风险并给出可执行修改建议。

审查要求：
- {mode_instruction}
- {citation_instruction}
- 区分原文事实、审查判断和修改建议。
- 找不到合同依据时标记“待核实”，不得补造条款。
- 不代替用户作出法律决定，不执行外部写操作。
- {language_instruction}

输出结构：
1. 审查摘要
2. 风险清单：风险等级、原文依据、问题说明、修改建议
3. 待核实事项

用户补充要求：
{context.system_prompt or "无"}
""".strip()
```

固定安全边界应写在 Backend Prompt 中；允许不同 Agent 实例调整的业务细节放在 `context.system_prompt`。不要让可配置提示词完全覆盖固定边界。

### 5.8 完整装配 Graph

下面的 `graph.py` 是可以直接落地的最小完整示例：

```python
# graph.py
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRetryMiddleware, TodoListMiddleware

from yuxi.agents import BaseAgent, BaseState, load_chat_model, resolve_chat_model_spec
from yuxi.agents.backends import create_agent_filesystem_middleware
from yuxi.agents.context import (
    DEFAULT_SUMMARY_KEEP_MESSAGES,
    DEFAULT_SUMMARY_L2_TRIGGER_RATIO,
    DEFAULT_SUMMARY_THRESHOLD_K,
    DEFAULT_SUMMARY_TOOL_RESULT_TOKEN_LIMIT,
    DEFAULT_TOOL_RESULT_EVICTION_K_TOKENS,
    DEFAULT_YUXI_SUMMARY_PROMPT,
    prepare_agent_runtime_context,
)
from yuxi.agents.middlewares import (
    TokenUsageMiddleware,
    create_summary_middleware,
    save_attachments_to_fs,
)
from yuxi.agents.middlewares.skills import SkillsMiddleware
from yuxi.agents.toolkits.service import resolve_configured_runtime_tools

from .context import ContractReviewContext
from .prompt import TODO_PROMPT, build_review_prompt


def _build_middlewares(context: ContractReviewContext) -> list:
    summary_trigger_tokens = (
        getattr(context, "summary_threshold", DEFAULT_SUMMARY_THRESHOLD_K) * 1024
    )
    summary_middleware = create_summary_middleware(
        model=load_chat_model(
            fully_specified_name=resolve_chat_model_spec(context.model)
        ),
        trigger=("tokens", summary_trigger_tokens),
        keep=(
            "messages",
            getattr(context, "summary_keep_messages", DEFAULT_SUMMARY_KEEP_MESSAGES),
        ),
        summary_prompt=(
            getattr(context, "summary_prompt", None) or DEFAULT_YUXI_SUMMARY_PROMPT
        ),
        trim_tokens_to_summarize=summary_trigger_tokens,
        tool_result_offload_token_limit=getattr(
            context,
            "summary_tool_result_token_limit",
            DEFAULT_SUMMARY_TOOL_RESULT_TOKEN_LIMIT,
        ),
        l1_l2_trigger_ratio=getattr(
            context,
            "summary_l2_trigger_ratio",
            DEFAULT_SUMMARY_L2_TRIGGER_RATIO,
        ),
    )

    return [
        create_agent_filesystem_middleware(
            getattr(context, "tool_token_limit", DEFAULT_TOOL_RESULT_EVICTION_K_TOKENS) * 1024,
            context=context,
        ),
        save_attachments_to_fs,
        SkillsMiddleware(),
        summary_middleware,
        TodoListMiddleware(system_prompt=TODO_PROMPT),
        PatchToolCallsMiddleware(),
        ModelRetryMiddleware(max_retries=context.model_retry_times),
        TokenUsageMiddleware(),
    ]


class ContractReviewAgent(BaseAgent):
    name = "合同审查"
    description = "读取合同附件和知识库，输出结构化风险清单与修改建议"
    capabilities = ["file_upload", "files"]
    context_schema = ContractReviewContext

    async def get_graph(self, context=None, **kwargs):
        context = await prepare_agent_runtime_context(
            context or self.context_schema(),
            context_schema=self.context_schema,
        )
        model_spec = resolve_chat_model_spec(context.model)

        return create_agent(
            model=load_chat_model(fully_specified_name=model_spec),
            tools=await resolve_configured_runtime_tools(context),
            system_prompt=build_review_prompt(context),
            middleware=_build_middlewares(context),
            state_schema=BaseState,
            checkpointer=await self._get_checkpointer(),
        )
```

```python
# __init__.py
from .context import ContractReviewContext
from .graph import ContractReviewAgent

__all__ = ["ContractReviewAgent", "ContractReviewContext"]
```

这套实现保留了：

- `prepare_agent_runtime_context`：按当前用户重新过滤工具、知识库、MCP 和 Skills；
- `resolve_configured_runtime_tools`：装配显式工具、MCP 工具和 Skill 依赖工具；
- filesystem middleware：提供线程文件、Sandbox 和只读 Skill 文件；
- attachment middleware：把附件路径注入运行上下文；
- `SkillsMiddleware`：按读取 `SKILL.md` 的行为激活 Skill 及依赖；
- Yuxi summary：按 Context 阈值压缩长上下文并处理大工具结果；
- Todo、工具调用兼容修正、模型重试和 token usage；
- `BaseState.artifacts` 和 `present_artifacts` 交付物状态；
- Yuxi 的 checkpoint、流式、resume 和 AgentRun 链路。

它有意没有挂载：

- `create_subagent_task_middleware`：本例不允许子智能体；
- `ChatBotState.subagent_runs`：没有子智能体就不需要该状态。

不要直接导入 `chatbot.graph._build_middlewares`。下划线函数不是稳定公共接口，而且会把当前 Backend 未声明的子智能体和摘要语义一起带入。

### 5.9 决定中间件去留

以当前 `ChatbotAgent` 为基准逐项判断：

| 能力 | 组件 | 删除后的直接影响 |
| --- | --- | --- |
| Sandbox、文件读写和大结果落盘 | `create_agent_filesystem_middleware` | 文件工具、Skill 脚本和 outputs 交付链路不可用 |
| 附件提示 | `save_attachments_to_fs` | 上传成功但模型不知道附件路径 |
| Skill 动态激活 | `SkillsMiddleware` | Skill 提示、依赖 Tool/MCP 挂载失效 |
| 子智能体 | `create_subagent_task_middleware` | 不生成子智能体生命周期工具 |
| 长上下文压缩 | `create_summary_middleware` | 长对话可能触发模型上下文溢出 |
| 任务规划 | `TodoListMiddleware` | 不再生成 todos 状态 |
| 工具消息兼容 | `PatchToolCallsMiddleware` | 部分模型的工具调用消息可能无法正确衔接 |
| 模型失败重试 | `ModelRetryMiddleware` | 临时模型错误直接中断 |
| Token 快照 | `TokenUsageMiddleware` | 前端状态面板没有 token usage |

示例已经保留 Yuxi 摘要链路。如果其他 Backend 选择自行装配摘要，不要只创建一个默认 `SummarizationMiddleware`。应参考当前 `chatbot/graph.py`，把 Context 中的：

- `summary_threshold`
- `summary_keep_messages`
- `summary_prompt`
- `summary_tool_result_token_limit`
- `summary_l2_trigger_ratio`

完整传给 `create_summary_middleware`，保持 Yuxi 的工具结果 offload 和实时压缩事件语义。

如果要恢复子智能体：

1. Context 应继承 `ChatBotContext` 或自行声明 `subagents` 资源字段；
2. 调用 `create_subagent_task_middleware(context)`；
3. 使用能够保存 `subagent_runs` 的 `ChatBotState`；
4. 接受“不支持孙子智能体”的现有约束。

### 5.10 自定义 State 不是必选项

只有节点、工具或中间件需要跨步骤保存额外数据时才新增 State。普通输出格式放在 Prompt 中即可。

例如要在 checkpoint 中保存结构化风险项：

```python
# state.py
from typing import Annotated, Literal, TypedDict

from yuxi.agents import BaseState


class ReviewFinding(TypedDict):
    finding_id: str
    severity: Literal["low", "medium", "high"]
    title: str
    evidence: str


def merge_review_findings(
    existing: list[ReviewFinding] | None,
    new: list[ReviewFinding] | None,
) -> list[ReviewFinding]:
    merged = {item["finding_id"]: item for item in existing or []}
    for item in new or []:
        merged[item["finding_id"]] = item
    return list(merged.values())


class ContractReviewState(BaseState):
    review_findings: Annotated[list[ReviewFinding], merge_review_findings]
```

声明字段本身不会产生数据。必须由节点、中间件或工具返回 `Command(update=...)`：

```python
# tools.py
from typing import Annotated, Literal

from langchain.tools import InjectedToolCallId
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolRuntime
from langgraph.types import Command


@tool
def record_review_finding(
    finding_id: str,
    severity: Literal["low", "medium", "high"],
    title: str,
    evidence: str,
    runtime: ToolRuntime,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """记录或更新一条合同审查风险；同一 finding_id 会覆盖旧值。"""
    finding = {
        "finding_id": finding_id,
        "severity": severity,
        "title": title,
        "evidence": evidence,
    }
    return Command(
        update={
            "review_findings": [finding],
            "messages": [
                ToolMessage(
                    content=f"已记录风险 {finding_id}（当前模式：{runtime.context.review_mode}）",
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )
```

然后在 Graph 中显式装配：

```python
from .state import ContractReviewState
from .tools import record_review_finding

configured_tools = await resolve_configured_runtime_tools(context)

return create_agent(
    # 其他参数不变
    tools=[*configured_tools, record_review_finding],
    state_schema=ContractReviewState,
)
```

这个 `record_review_finding` 是 **Backend 私有工具**：

- 它只服务当前执行图；
- 不注册到 Yuxi 全局工具目录；
- 不出现在智能体配置页；
- 使用该 Backend 时始终可用。

如果工具需要被多个 Backend 选择和复用，应按[Agent 工具开发指南](./agent-tool-development.md)注册为公共内置工具，不要在多个 `graph.py` 中复制。

还要注意：当前 `chat_service.extract_agent_state()` 只向前端投影 `todos`、`files`、`artifacts`、`subagent_runs` 和 `token_usage`。`review_findings` 会保存在 checkpoint 中，但不会自动显示在现有状态面板。若需要专门展示，必须同步修改：

- `backend/package/yuxi/agents/state.py` 的 UI payload；
- `backend/package/yuxi/services/chat_service.py` 的 `extract_agent_state()`；
- 前端状态面板类型、分组与渲染；
- 对应后端和前端测试。

如果最终只需要一份报告，优先让 Agent 生成 Markdown 文件并调用 `present_artifacts`，通常不需要扩展全局状态协议。

### 5.11 自动发现与双进程加载

Agent Backend 在进程导入 `yuxi.agents.buildin` 时自动发现：

1. 扫描 `buildin/` 的直接子目录；
2. 要求目录存在 `__init__.py`；
3. 导入 `yuxi.agents.buildin.<package>`；
4. 找出包内导出的 `BaseAgent` 子类；
5. 以类名注册并实例化。

导入失败会在日志中记录“无法从 `<package>` 加载智能体”，该 Backend 不会出现在接口中。

新增或重命名 Backend 后必须同时重启 API 和 Worker：

- API 未加载：管理页看不到 Backend，不能创建或读取对应 Agent；
- Worker 未加载：API 可以创建 Run，但执行阶段找不到 Backend；
- 两边版本不一致：可能出现 Context 字段、Graph 或工具行为不一致。

检查发现结果：

```powershell
Invoke-RestMethod http://127.0.0.1:5050/api/agent/backends `
  -Headers @{ Authorization = "Bearer <本地测试令牌>" }
```

响应中应包含：

```json
{
  "backend_id": "ContractReviewAgent",
  "name": "合同审查",
  "type": "agent_backend"
}
```

真实令牌只用于本地命令，不要写进文档、测试或日志。

### 5.12 创建使用该 Backend 的 Agent

在管理页创建时选择“合同审查”，保存后再次编辑模型和资源配置。

也可以通过 API 一次性创建：

```powershell
$body = @{
  name = "采购合同审查助手"
  slug = "procurement-contract-review"
  backend_id = "ContractReviewAgent"
  description = "审查采购合同中的履约、付款、违约和终止风险"
  config_json = @{
    context = @{
      model = "provider-id:model-id"
      system_prompt = "重点检查采购方验收、发票和质保责任。"
      review_mode = "strict"
      output_language = "zh-CN"
      require_citations = $true
      tools = @()
      knowledges = @("procurement-policy-kb")
      mcps = @()
      skills = @("contract-review-method")
    }
  }
  share_config = @{
    access_level = "user"
    department_ids = @()
    user_uids = @("<当前用户 uid>")
  }
} | ConvertTo-Json -Depth 8

Invoke-RestMethod http://127.0.0.1:5050/api/agent `
  -Method Post `
  -ContentType "application/json" `
  -Headers @{ Authorization = "Bearer <本地测试令牌>" } `
  -Body $body
```

把示例中的模型、知识库、Skill 和 uid 替换为当前环境真实可访问的标识。后端会再次按当前用户权限过滤资源，不能通过请求体获得越权资源。

### 5.13 为 Backend 编写单元测试

至少测试 Context、Prompt 和 Graph 装配。下面的示例不调用真实模型、数据库或外部服务：

```python
# backend/test/unit/agents/test_contract_review_agent.py
from unittest.mock import AsyncMock

import pytest

from yuxi.agents import BaseState
from yuxi.agents.buildin.contract_review import graph as graph_module
from yuxi.agents.buildin.contract_review.context import ContractReviewContext
from yuxi.agents.buildin.contract_review.graph import ContractReviewAgent
from yuxi.agents.buildin.contract_review.prompt import build_review_prompt


def test_contract_review_context_exposes_business_fields():
    items = ContractReviewContext.get_configurable_items(user_role="user")

    assert items["review_mode"]["default"] == "standard"
    assert items["review_mode"]["options"] == ["standard", "strict"]
    assert items["output_language"]["type"] == "select"
    assert items["require_citations"]["default"] is True


def test_contract_review_prompt_rejects_unknown_mode():
    context = ContractReviewContext(review_mode="unsupported")

    with pytest.raises(ValueError, match="不支持的审查模式"):
        build_review_prompt(context)


@pytest.mark.asyncio
async def test_contract_review_graph_uses_prepared_context(monkeypatch):
    context = ContractReviewContext(
        uid="user-1",
        thread_id="thread-1",
        model="provider:model",
        review_mode="strict",
    )
    captured = {}

    async def prepare(current, **kwargs):
        captured["context_schema"] = kwargs["context_schema"]
        return current

    async def resolve_tools(current):
        assert current is context
        return ["configured-tool"]

    def create_agent(**kwargs):
        captured.update(kwargs)
        return "compiled-graph"

    monkeypatch.setattr(graph_module, "prepare_agent_runtime_context", prepare)
    monkeypatch.setattr(graph_module, "resolve_configured_runtime_tools", resolve_tools)
    monkeypatch.setattr(graph_module, "resolve_chat_model_spec", lambda value: value)
    monkeypatch.setattr(graph_module, "load_chat_model", lambda fully_specified_name: "chat-model")
    monkeypatch.setattr(graph_module, "_build_middlewares", lambda current: ["middleware"])
    monkeypatch.setattr(graph_module, "create_agent", create_agent)

    agent = ContractReviewAgent.__new__(ContractReviewAgent)
    agent._get_checkpointer = AsyncMock(return_value="checkpointer")

    graph = await agent.get_graph(context=context)

    assert graph == "compiled-graph"
    assert captured["context_schema"] is ContractReviewContext
    assert captured["model"] == "chat-model"
    assert captured["tools"] == ["configured-tool"]
    assert captured["middleware"] == ["middleware"]
    assert captured["state_schema"] is BaseState
    assert captured["checkpointer"] == "checkpointer"
    assert "逐项检查" in captured["system_prompt"]
```

这组测试验证的是你负责的装配边界。`BaseAgent` 流式转换、Yuxi 标准中间件和 LangGraph 自身行为已有各自测试，不要在此重复模拟整条框架。

运行：

```powershell
Set-Location backend
uv run --group test pytest test/unit/agents/test_contract_review_agent.py
```

### 5.14 什么时候升级为原生 StateGraph

以下需求适合 `StateGraph`：

- 无论模型如何选择，都必须先解析文件，再审核，再复核，再生成报告；
- 某个节点失败后必须进入指定补偿节点；
- 不同风险等级必须走不同人工审批分支；
- 输入和输出需要与普通 ReAct Agent 不同的强类型 Schema。

此时仍应继承 `BaseAgent`，并保证外层契约：

```text
输入 state 至少兼容 messages
  -> StateGraph(state_schema, context_schema=YourContext)
  -> 添加节点、边和条件路由
  -> compile(checkpointer=await self._get_checkpointer())
  -> get_graph() 返回 CompiledStateGraph
```

实施时重点检查：

- State 应继承 `BaseState` 或至少兼容 LangChain `AgentState` 的 messages reducer；
- 模型节点应产生标准消息事件，否则聊天页面收不到流式回答；
- 自定义节点需要通过 runtime context 获取 `uid`、`thread_id` 和资源范围；
- 人机中断使用 LangGraph `interrupt` / `Command(resume=...)`，不要自建第二套暂停协议；
- checkpoint 只在最终外层 Graph 编译时装配，避免无意创建两套线程状态；
- 自定义 custom event 可以由 `BaseAgent` 透传，但上层服务和前端不会自动理解新的事件名称；
- 改变 resume、tools、tasks 或 lifecycle 事件形态时，必须补 AgentRun E2E。

原生 StateGraph 的代码高度依赖真实节点和路由需求。没有明确状态机之前不要先搭一个通用工作流框架。

### 5.15 常见故障

#### 管理页没有新 Backend

依次检查：

1. 包是否位于 `agents/buildin/` 的直接子目录；
2. 是否存在 `__init__.py`；
3. `__init__.py` 是否导出 Backend 类；
4. Backend 是否继承 `BaseAgent`；
5. 类是否真正定义在该包下；
6. API 启动日志是否出现导入失败；
7. API 是否已重启。

#### API 能看到，但 Run 执行失败

优先检查 Worker 是否重启并加载同一版本。随后检查：

- `backend_id` 是否与类名完全一致；
- Worker 使用的环境和依赖是否与 API 一致；
- Context 中的模型是否存在；
- `prepare_agent_runtime_context` 是否因用户或资源不存在而清空配置；
- 私有工具是否已传入 `create_agent`。

#### 上传了文件，但模型说看不到

检查 `capabilities` 是否包含 `file_upload`，Graph 是否保留 filesystem 和 attachment middleware，以及 API/Worker/Sandbox 是否共享同一个线程目录。

#### Skill 已选择，但依赖工具不能调用

必须同时满足：

- 调用了 `prepare_agent_runtime_context`；
- 使用了 `resolve_configured_runtime_tools`；
- 挂载了 `SkillsMiddleware`；
- filesystem middleware 可以读取 `/home/gem/skills`。

#### 自定义 State 有值，但页面不显示

这是当前 UI 投影边界，不是 checkpoint 丢失。确认是否真的需要扩展全局 `agent_state` 协议；如果只是最终交付，优先输出文件并使用 `present_artifacts`。

## 6. 测试与调试

遵循“检查 → 测试 → Lint”的顺序，完整约定见[测试规范与工作流](./testing-guidelines.md)。

### 6.1 配置型 Agent

至少准备以下场景：

| 场景 | 预期 |
| --- | --- |
| 典型成功输入 | 输出满足业务格式和事实要求 |
| 信息不足 | 主动询问关键缺失信息 |
| 知识库无结果 | 明确无依据，不编造 |
| 工具失败 | 展示真实失败并给出可执行下一步 |
| 越权资源 | 不可读取或调用 |
| 高风险动作 | 未确认前不执行 |
| 多轮对话 | 保持业务上下文且不串用其他线程数据 |

提示词质量不应只依赖一个“看起来正确”的示例。至少准备 5～10 个来自真实主要场景的固定问题，记录预期要点，并在每次修改提示词或资源组合后回归。

### 6.2 自定义 Backend 单元测试

测试放在 `backend/test/unit/agents/`。第 5.13 节已经给出完整装配测试，其他测试仍应优先验证确定性行为：

- Backend 能被导入，`id` 和 `context_schema` 正确；
- Context 默认值和可配置字段正确；
- 自定义节点或状态转换正确；
- 中间件选择与顺序符合设计；
- 无效输入会产生明确错误。

不要在单元测试中调用真实 LLM、远程知识库或外部 HTTP 服务。使用 fake model、stub tool 或 `monkeypatch` 隔离这些边界。

运行相关测试：

```powershell
Set-Location backend
uv run --group test pytest test/unit/agents/test_contract_review_agent.py
```

格式化和检查：

```powershell
uv run ruff format `
  package/yuxi/agents/buildin/contract_review `
  test/unit/agents/test_contract_review_agent.py

uv run ruff check `
  package/yuxi/agents/buildin/contract_review `
  test/unit/agents/test_contract_review_agent.py
```

### 6.3 接口和主链路

涉及 Agent 管理接口时运行相关集成测试；改变运行、流式、状态或 resume 主链路时补跑 E2E：

```powershell
Set-Location backend
uv run --group test pytest test/integration/api/test_chat_agent_sync.py
uv run --group test pytest test/e2e/test_agent_call_entrypoints_e2e.py -m e2e
```

这些测试需要按本地开发指南连接开发用基础设施。不要在回复或测试文件中输出 `.env` 里的敏感值。

### 6.4 调试顺序

1. 检查 `5050`、`5173`、`8002` 健康状态。
2. 检查 API 和 Worker 是否都加载了最新源码与同一份 `.env`。
3. 请求 `/api/agent/backends`，确认 Backend 已发现。
4. 请求 Agent 详情，确认 `backend_id` 和 `config_json.context`。
5. 检查 Worker 是否收到 run，以及失败发生在模型、工具还是中间件。
6. 只有涉及文件、脚本或 Skill 时，再检查 Provisioner 和 Runtime 容器。
7. 最后调整提示词或 Graph，避免把环境问题误判成智能体逻辑问题。

## 7. 交付检查表

- [ ] 业务用户、输入、输出、权限、成功标准和非目标已明确。
- [ ] 已选择最小扩展方式，没有为配置型需求新增 Backend。
- [ ] Agent 使用稳定 `slug`，共享范围符合业务要求。
- [ ] 系统提示词描述了角色、步骤、边界和失败行为。
- [ ] Tools、知识库、Skills、MCP 和子智能体已按最小允许列表配置。
- [ ] 写操作在真实服务边界完成鉴权，高风险动作有人工确认。
- [ ] 主要成功、失败、无数据、越权和多轮场景已验证。
- [ ] 自定义代码有对应单元测试；接口或主链路改动有集成/E2E 验证。
- [ ] 已完成相关格式化和 Lint。
- [ ] 面向用户的行为变化已更新正式文档和 `docs/develop-guides/changelog.md`。
- [ ] 未提交 `.env`、令牌、客户数据或测试产生的敏感文件。

## 8. 相关文档

- [智能体配置](../agents/agents-config.md)
- [中间件系统](../agents/middleware.md)
- [Agent 工具开发指南](./agent-tool-development.md)
- [Skills 开发指南](./skills-development.md)
- [MCP 集成](../agents/mcp-integration.md)
- [子智能体](../agents/subagents-management.md)
- [智能体评估](../agents/agent-evaluation.md)
- [本地开发指南](./local-development.md)
- [测试规范与工作流](./testing-guidelines.md)
