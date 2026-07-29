# API Key 外部项目接入

Yuxi 支持外部服务通过 API Key 调用平台中的智能体。本页以一次性的建筑施工过程规则校验为示例，介绍密钥创建、Agent 选择、激活规则和施工场景数据传递、同步调用、异步轮询、幂等、错误处理和生产安全要求。

如果只想先验证连通性，请直接阅读[五分钟完成首次调用](#五分钟完成首次调用)。

## 接入范围与推荐接口

外部项目通常只需要以下三个接口：

| 接口 | 用途 |
| --- | --- |
| `GET /api/agent` | 查询当前 API Key 身份可见的 Agent，并获取 `slug` |
| `POST /api/agent-invocation/agent-call/runs` | 创建 Agent 调用；支持同步等待或异步返回 |
| `POST /api/agent-invocation/agent-call/runs/result` | 查询异步调用结果 |

推荐优先使用 `agent-invocation` 接口。它会为每次调用自动创建内部临时线程，接受接近 OpenAI messages 的输入格式，并返回固定的 `output`、`choices` 和 `usage` 结构。业务方只读取并解析 `output` 中的最终规则校验 JSON。只有需要消费工具事件、运行状态等细粒度过程时，才需要使用通用 AgentRun + SSE 接口。

## 接入前准备

开始前需要准备：

- 一个可登录 Yuxi 的有效用户，该用户必须属于某个部门；
- 该用户可以访问的目标 Agent；
- 可从外部项目访问的 Yuxi 实例地址；
- 为此外部项目单独创建的 API Key。

API Key 继承绑定用户的身份和权限。调用方只能访问该用户本身可见的 Agent、会话和运行结果。

### 确定实例地址

文档中的 `base_url` 指 Yuxi 实例根地址，不包含 `/api`，末尾也不需要 `/`。

| 环境 | 示例 |
| --- | --- |
| 本地开发 | `http://localhost:5050` |
| 生产环境 | `https://yuxi.example.com` |

生产环境必须使用 HTTPS。API Key 通过请求头传输，使用公网 HTTP 会暴露密钥。

可访问以下地址确认服务是否正常：

```text
https://yuxi.example.com/docs
```

该页面是 FastAPI 自动生成的完整接口调试页。本项目维护的外部接入 OpenAPI 文件位于 [API 文档索引](../api/README.md)。

## 创建与保存 API Key

1. 登录 Yuxi。
2. 点击页面右上角的“系统设置”。
3. 进入“API Keys”。
4. 点击“创建 API Key”，填写能标识调用方的名称，例如 `construction-quality-production`。
5. 按需设置过期时间。
6. 创建后立即复制并保存完整密钥。

完整密钥形如：

```text
yxkey_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

完整密钥只在创建或重新生成时返回一次。Yuxi 数据库只保存 SHA-256 哈希和可识别前缀，之后无法找回原值。密钥遗失时必须重新生成，原密钥会立即失效。

建议在外部项目中通过环境变量或密钥管理服务保存：

```bash
export YUXI_BASE_URL="https://yuxi.example.com"
export YUXI_API_KEY="yxkey_替换为完整密钥"
export YUXI_AGENT_SLUG="construction-process-rule-validator"
```

PowerShell：

```powershell
$env:YUXI_BASE_URL = "https://yuxi.example.com"
$env:YUXI_API_KEY = "yxkey_替换为完整密钥"
$env:YUXI_AGENT_SLUG = "construction-process-rule-validator"
```

不要把真实密钥写入源码、Git 仓库、镜像、前端代码、URL 参数或日志。

## 五分钟完成首次调用

所有受保护接口都使用同一种认证请求头：

```http
Authorization: Bearer yxkey_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 1. 查询可用 Agent

`agent_slug` 是 Agent 的稳定字符串标识，不是数据库自增 ID，也不是 `backend_id`。

```bash
curl --fail-with-body \
  --request GET \
  --header "Authorization: Bearer ${YUXI_API_KEY}" \
  --header "Accept: application/json" \
  "${YUXI_BASE_URL}/api/agent"
```

响应中的 `agents[].slug` 可直接用于后续调用：

```json
{
  "agents": [
    {
      "slug": "construction-process-rule-validator",
      "name": "建筑施工过程规则校验",
      "description": "根据当前激活规则校验施工工序、工程部位和现场数据"
    }
  ]
}
```

列表只包含当前 API Key 绑定用户可见的 Agent。目标 Agent 不在列表中时，需要先调整 Agent 的共享权限或更换绑定用户。

### 2. 发起同步调用

同步模式适合命令行验证和耗时较短的请求。省略 `async_mode` 时默认为 `false`。

```bash
curl --fail-with-body \
  --request POST \
  --header "Authorization: Bearer ${YUXI_API_KEY}" \
  --header "Content-Type: application/json" \
  --data '{
    "agent_slug": "construction-process-rule-validator",
    "messages": [
      {
        "role": "user",
        "content": "【当前激活规则 active_rules】\n[{\"rule_id\":\"CONCRETE-POUR-001\",\"condition\":\"process_data.rebar_inspection_status == 'passed'\"}]\n\n【当前施工场景数据 scene_data】\n{\"construction_context\":{\"work_type\":\"混凝土浇筑\",\"building_no\":\"1号楼\",\"floor\":\"8层\"},\"process_data\":{\"rebar_inspection_status\":\"pending\"}}"
      }
    ],
    "request_id": "inspection-PC-2026-001-validation-1"
  }' \
  "${YUXI_BASE_URL}/api/agent-invocation/agent-call/runs"
```

成功响应：

```json
{
  "run_id": "2f249f09-4a21-4a2d-a248-9ad30ca87687",
  "agent_slug": "construction-process-rule-validator",
  "thread_id": "d4ac87d0-16dd-4ef4-9d0a-04c22dd39429",
  "status": "completed",
  "request_id": "inspection-PC-2026-001-validation-1",
  "output": "{\"validation_status\":\"non_compliant\",\"results\":[{\"rule_id\":\"CONCRETE-POUR-001\",\"status\":\"non_compliant\",\"reason\":\"钢筋隐蔽工程验收状态为 pending，未满足规则要求的 passed\"}]}",
  "choices": [
    {
      "index": 0,
      "messages": [
        {
          "role": "assistant",
          "content": "{\"validation_status\":\"non_compliant\",\"results\":[{\"rule_id\":\"CONCRETE-POUR-001\",\"status\":\"non_compliant\",\"reason\":\"钢筋隐蔽工程验收状态为 pending，未满足规则要求的 passed\"}]}"
        }
      ],
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

外部项目只读取 `output`，并将其解析为约定的 JSON 对象后返回给业务端。`choices` 用于兼容 OpenAI 风格的消费逻辑；某些模型或运行链路没有提供 token usage 时，`usage` 中的值会是 `0`。

## 运行完整 Python Demo

仓库提供了一个仅使用 Python 标准库的可运行 Demo：

- Demo 源码：`docs/public/examples/api-key-integration-demo/yuxi_agent_demo.py`

在仓库根目录执行：

```bash
python docs/public/examples/api-key-integration-demo/yuxi_agent_demo.py \
  "【当前激活规则 active_rules】[{\"rule_id\":\"CONCRETE-POUR-001\",\"condition\":\"钢筋隐蔽工程验收状态必须为passed\"}]【当前施工场景数据 scene_data】{\"work_type\":\"混凝土浇筑\",\"building_no\":\"1号楼\",\"floor\":\"8层\",\"rebar_inspection_status\":\"pending\"}"
```

列出当前身份可见的 Agent：

```bash
python docs/public/examples/api-key-integration-demo/yuxi_agent_demo.py --list-agents
```

验证异步模式：

```bash
python docs/public/examples/api-key-integration-demo/yuxi_agent_demo.py \
  --async-mode \
  "【当前激活规则 active_rules】[{\"rule_id\":\"SAFETY-EDGE-001\",\"condition\":\"scene_data.edge_protection_status == 'complete'\"}]【当前施工场景数据 scene_data】{\"building_no\":\"1号楼\",\"floor\":\"8层\",\"edge_protection_status\":\"missing\"}"
```

Demo 支持 `--request-id` 固定幂等 ID，以及 `--wait-timeout` 调整异步等待上限。每次运行都是独立校验，标准输出只打印解析后的业务 JSON。运行 `--help` 可查看完整参数。

## Agent 调用接口

### 创建调用

```http
POST /api/agent-invocation/agent-call/runs
Content-Type: application/json
Authorization: Bearer <API_KEY>
```

请求字段：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `agent_slug` | `string` | 是 | - | 目标 Agent 的 `slug` |
| `messages` | `array<object>` | 是 | - | 消息列表；系统从后向前取最后一条 `role=user` 消息作为本次输入 |
| `stream` | `boolean` | 否 | `false` | 当前必须为 `false`；传 `true` 返回 `422` |
| `agent_call_meta` | `object` | 否 | `{}` | 调用方追踪元数据；不能包含 `context` |
| `thread_id` | `string` | 否 | 自动生成 | 施工规则校验不传；其他需要线程资源的业务按自身流程使用 |
| `request_id` | `string` | 否 | 自动生成 UUID | 幂等 ID，去除首尾空白后最多 64 个字符 |
| `model_spec` | `string` | 否 | Agent 配置模型 | 覆盖本次调用的模型 |
| `async_mode` | `boolean` | 否 | `false` | `true` 时创建成功后立即返回 |

最小请求：

```json
{
  "agent_slug": "construction-process-rule-validator",
  "messages": [
    {
      "role": "user",
      "content": "【当前激活规则 active_rules】\n[]\n\n【当前施工场景数据 scene_data】\n{}"
    }
  ]
}
```

`messages[].content` 支持纯文本：

```json
{
  "role": "user",
  "content": "请根据当前激活规则校验1号楼8层梁板混凝土浇筑前置条件"
}
```

接口本身也支持 OpenAI 风格的 `text` 和 `image_url` 多模态数组，但这属于独立的图片业务，不应发送给 `construction-process-rule-validator`。图片业务应创建另一个 Agent，例如：

```json
{
  "agent_slug": "image-analysis-assistant",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "请分析图片内容并按照图片业务约定返回结果。"
        },
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/png;base64,iVBORw0KGgoAAA..."
          }
        }
      ]
    }
  ]
}
```

当前接口不会把整个 `messages` 数组重放为历史上下文，只提取最后一条 user 消息。因此每次规则校验都应在这一条消息中提供完整的 `active_rules` 和 `scene_data`，不要依赖历史消息。

### 响应字段

| 字段 | 说明 |
| --- | --- |
| `run_id` | 本次 AgentRun ID，用于查询结果和排查日志 |
| `agent_slug` | 实际运行的 Agent |
| `thread_id` | Yuxi 内部线程 ID；施工规则校验不传入也不复用 |
| `status` | `pending`、`running`、`completed`、`failed`、`cancelled` 或 `interrupted` |
| `request_id` | 本次请求的幂等/追踪 ID |
| `output` | 最终规则校验 JSON 的字符串形式；异步未完成时为空字符串 |
| `choices` | OpenAI 风格结果包装 |
| `usage` | token 使用量；无法取得时为 `0` |
| `error` | 运行失败时的错误对象，成功时通常不存在 |

`finish_reason` 的取值规则：

- `completed` 对应 `stop`；
- `failed`、`cancelled`、`interrupted` 对应同名值；
- 非终态对应 `null`。

## 生产环境推荐：异步调用

耗时不确定、会调用工具或可能超过网关超时的请求应使用异步模式。

### 1. 创建异步调用

```json
{
  "agent_slug": "construction-process-rule-validator",
  "messages": [
    {
      "role": "user",
      "content": "【当前激活规则 active_rules】\n[{\"rule_id\":\"CONCRETE-POUR-001\",\"condition\":\"process_data.rebar_inspection_status == 'passed'\"}]\n\n【当前施工场景数据 scene_data】\n{\"construction_context\":{\"work_type\":\"混凝土浇筑\",\"inspection_batch_id\":\"PC-2026-001\"},\"process_data\":{\"rebar_inspection_status\":\"pending\"}}"
    }
  ],
  "request_id": "inspection-PC-2026-001-validation-1",
  "async_mode": true
}
```

接口立即返回，典型状态为 `pending`：

```json
{
  "run_id": "ae3377d2-50c9-4520-8e86-f6f451f5928d",
  "agent_slug": "construction-process-rule-validator",
  "thread_id": "0e4dc23d-7c82-4bb0-becf-31da990bb7b7",
  "status": "pending",
  "request_id": "inspection-PC-2026-001-validation-1",
  "output": "",
  "choices": [
    {
      "index": 0,
      "messages": [
        {
          "role": "assistant",
          "content": ""
        }
      ],
      "finish_reason": null
    }
  ],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

### 2. 轮询结果

```http
POST /api/agent-invocation/agent-call/runs/result
Content-Type: application/json
Authorization: Bearer <API_KEY>
```

```json
{
  "run_id": "ae3377d2-50c9-4520-8e86-f6f451f5928d",
  "agent_slug": "construction-process-rule-validator"
}
```

`agent_slug` 可省略；传入时，服务端会校验 run 是否属于该 Agent，不匹配返回 `409`。

建议每 1 至 3 秒查询一次，并在调用方设置总等待上限。遇到 `completed` 即读取 `output`；遇到 `failed`、`cancelled` 或 `interrupted` 应停止轮询并记录 `run_id`、`request_id` 和 `error`。

不要在每次轮询时重新创建 run。

## 一次性校验与幂等

每次施工规则校验都是独立任务：

- 不传 `thread_id`、附件或图片；
- 下一次校验不复用上一次运行的上下文；
- 修改规则或场景数据后，携带完整新输入并生成新的 `request_id`；
- 外部业务系统不需要维护多轮消息历史。

### 使用 `request_id` 安全重试

`request_id` 用于幂等和跨系统追踪，最多 64 个字符。建议由调用方生成稳定且可定位的值，例如：

```text
inspection-PC-2026-001-validation-1
```

网络超时后，可以用相同的 `request_id` 和 `agent_slug` 重试，服务端会返回已创建的 run。相同 `request_id` 被用于其他用户、Agent 或线程时会返回 `409 request_id 冲突`。

不要用时间过短或会重复的序号作为全局 `request_id`。

### 校验最终 `output`

只有同时满足以下条件才算调用成功：

1. `status=completed`；
2. `output` 非空；
3. `output` 可以解析为 JSON 对象；
4. JSON 满足业务约定的 Schema。

Agent 的系统提示词必须要求只输出 JSON，不使用 Markdown 代码围栏，不在 JSON 前后添加解释。调用方仍需执行 JSON 解析和 Schema 校验，不能只依赖提示词。

## 错误处理

FastAPI 参数校验错误和业务错误都以 JSON 返回，常见格式为：

```json
{
  "detail": "错误说明"
}
```

部分冲突和超时会返回结构化 `detail`：

```json
{
  "detail": {
    "code": "run_busy",
    "message": "该智能体线程正在运行，请等待、查询或取消当前运行后再继续",
    "active_run_id": "run-id"
  }
}
```

| HTTP 状态码 | 常见原因 | 处理建议 |
| --- | --- | --- |
| `400` | API Key 用户没有部门等前置条件不满足 | 修复用户或部门配置 |
| `401` | 缺少认证头、格式错误、Key 不存在、已禁用或已过期 | 检查 `Bearer` 格式并在管理页确认 Key 状态 |
| `404` | Agent 不可见或不存在 | 调用 `GET /api/agent` 核对 `slug` 和权限 |
| `409` | `request_id` 冲突、线程绑定其他 Agent、线程正在运行 | 按 `detail` 修正幂等范围或等待当前 run |
| `422` | 请求字段无效、没有 user 消息、`stream=true` 等 | 修正请求体，不要原样重试 |
| `504` | 同步等待超过服务端上限，但 run 可能仍在执行 | 从 `detail.run` 取得 run 信息，改用异步模式查询 |
| `5xx` | 服务端或依赖暂时异常 | 使用退避重试；保留相同 `request_id` 防止重复创建 |

日志中建议记录：

- 调用方业务 ID；
- `request_id`；
- `run_id`；
- `thread_id`；
- HTTP 状态码和脱敏后的错误。

严禁记录 `Authorization` 请求头或完整 API Key。

## API Key 生命周期管理接口

通常应由用户在 Yuxi 设置页面管理密钥。需要自动化管理时，以下接口与其他 API 一样使用 Bearer 认证：

| 方法与路径 | 说明 |
| --- | --- |
| `GET /api/user/apikey/?skip=0&limit=100` | 列出当前用户的 Key；superadmin 可查看全部 |
| `POST /api/user/apikey/` | 创建 Key，响应中的 `secret` 只返回一次 |
| `GET /api/user/apikey/{api_key_id}` | 读取 Key 元数据，不返回完整 secret |
| `PUT /api/user/apikey/{api_key_id}` | 修改名称、过期时间或启用状态 |
| `POST /api/user/apikey/{api_key_id}/regenerate` | 重新生成并立即废止旧 Key |
| `DELETE /api/user/apikey/{api_key_id}` | 删除 Key |

普通用户只能管理自己的 Key，superadmin 可以管理其他用户的 Key。生产集成建议把“运行调用凭据”和“密钥管理凭据”分离，避免业务进程拥有不必要的密钥管理能力。

## 生产安全检查表

- 为每个外部系统和环境创建独立 API Key，例如生产与测试不要共用；
- API Key 绑定最小权限用户，不要默认使用 superadmin；
- 生产流量只允许 HTTPS，并在反向代理层关闭访问日志中的认证头；
- 密钥存入环境变量、Vault 或云密钥管理服务，不进入前端和 Git；
- 设置合理的过期时间和轮换周期；
- 泄露时先禁用或重新生成，再排查 `last_used_at` 与网关日志；
- 在 API 网关按调用方增加速率限制、请求体大小限制和超时；
- 使用稳定的 `request_id` 实现安全重试和链路追踪；
- 长任务使用异步模式，避免反向代理或客户端提前断开；
- 定期检查不再使用的 Key 并删除。

Yuxi 当前没有为 API Key 单独设置调用频率限制。公网部署应在 Nginx、API Gateway 或服务网格层补充限流。

## 浏览器直接调用与 CORS

API Key 更适合保存在服务端。把长期密钥放入浏览器 JavaScript 会暴露给终端用户，不建议这样接入。

如果确实需要由受控 Web 页面跨域调用，后端必须通过 `YUXI_CORS_ORIGINS` 显式允许页面来源，例如：

```text
YUXI_CORS_ORIGINS=https://portal.example.com
```

不要仅为解决 CORS 而把生产来源设置成 `*`。更推荐由业务后端保管 API Key，浏览器只调用业务后端。

## 接口事实来源

本文档对应以下实现：

- API Key 认证：`backend/server/utils/auth_middleware.py`
- API Key 管理：`backend/server/routers/user_router.py`
- 外部 Agent 路由：`backend/server/routers/agent_invocation_router.py`
- 外部调用语义与响应：`backend/package/yuxi/services/agent_invocation_service.py`
- Agent 列表：`backend/server/routers/agent_router.py`

机器可读契约与字段结构见 [外部 Agent 调用 API 参考](../api/modules/external-agent-invocation.md)。
