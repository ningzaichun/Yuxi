# 外部 Agent 调用 API 参考

## 模块边界

本模块覆盖外部项目完成 Agent 调用所需的最小接口：

- 查询当前身份可见的 Agent；
- 创建会话并查询历史消息；
- 上传、查询和删除线程附件；
- 上传并处理多模态图片；
- 同步或异步创建 Agent 调用；
- 查询异步调用结果。

本文请求示例统一使用一次性的建筑施工过程规则校验场景；接口契约本身不限定业务领域。规则校验不传 `thread_id`、附件或图片。线程附件和多模态图片接口服务于另一个独立业务。

业务实施流程见[业务智能体外部系统接入指南](../../advanced/business-agent-external-integration.md)，密钥创建和完整调用字段见 [API Key 外部项目接入](../../advanced/api-key-integration.md)。

## 接口与源码映射

| 接口 | Handler | 源码 |
| --- | --- | --- |
| `GET /api/agent` | `list_agents` | `backend/server/routers/agent_router.py` |
| `POST /api/chat/thread` | `create_thread` | `backend/server/routers/chat_router.py` |
| `GET /api/chat/thread/{thread_id}/history` | `get_thread_history` | `backend/server/routers/chat_router.py` |
| `POST /api/chat/thread/{thread_id}/attachments` | `upload_thread_attachment` | `backend/server/routers/chat_router.py` |
| `GET /api/chat/thread/{thread_id}/attachments` | `list_thread_attachments` | `backend/server/routers/chat_router.py` |
| `DELETE /api/chat/thread/{thread_id}/attachments/{file_id}` | `delete_thread_attachment` | `backend/server/routers/chat_router.py` |
| `POST /api/chat/image/upload` | `upload_image` | `backend/server/routers/chat_router.py` |
| `POST /api/agent-invocation/agent-call/runs` | `create_agent_call_run` | `backend/server/routers/agent_invocation_router.py` |
| `POST /api/agent-invocation/agent-call/runs/result` | `get_agent_call_run_result` | `backend/server/routers/agent_invocation_router.py` |

以上接口都依赖 `get_required_user`，支持 `Authorization: Bearer <yxkey_...>` API Key 认证。

## 请求与响应摘要

### `GET /api/agent`

返回当前 API Key 绑定用户可见的主 Agent：

```json
{
  "agents": [
    {
      "slug": "construction-process-rule-validator",
      "name": "建筑施工过程规则校验"
    },
    {
      "slug": "document-analysis-assistant",
      "name": "独立文件分析"
    }
  ]
}
```

后续请求使用 `agents[].slug`。

### `POST /api/chat/thread`

创建显式线程。该接口不属于规则校验主链路；附件业务需要上传文件时使用，并取得响应中的 `id`：

```json
{
  "agent_id": "document-analysis-assistant",
  "title": "独立文件分析任务",
  "metadata": {
    "business_type": "document_analysis",
    "business_id": "DOC-TASK-001"
  }
}
```

### 线程附件

本节属于独立附件业务，不属于建筑施工规则校验。

`POST /api/chat/thread/{thread_id}/attachments` 使用 multipart 字段 `file` 上传一个最大 5 MB 的附件。服务端保存原文件并尝试生成 Markdown；响应 `status=parsed` 表示存在可读 Markdown，`status=uploaded` 表示只保存原文件。

`GET /api/chat/thread/{thread_id}/attachments` 返回附件列表和限制，`DELETE /api/chat/thread/{thread_id}/attachments/{file_id}` 删除附件。

附件加入线程后会同步到 Agent state，并由附件中间件向模型提供可读路径。当前 `agent-call` 不接收 `attachment_file_ids`，附件按线程生效。

### `POST /api/chat/image/upload`

本节属于独立图片业务，不属于建筑施工规则校验。

使用 multipart 字段 `file` 上传图片。入口拒绝超过 10 MB 的文件，支持 JPEG、PNG、WebP、GIF 和 BMP，响应提供处理后的 `image_content`、`thumbnail_content` 和 `mime_type`。调用方将其组装成 data URL 后放入 `messages[].content[].image_url.url`。

### `GET /api/chat/thread/{thread_id}/history`

返回线程内部消息历史，包含消息内容、`run_id`、`request_id`、图片 Base64、工具调用和扩展元数据。一次性规则校验的正常业务流程不调用该接口；它只用于管理员排障或审计。

### `POST /api/agent-invocation/agent-call/runs`

最小请求：

```json
{
  "agent_slug": "construction-process-rule-validator",
  "messages": [
    {
      "role": "user",
      "content": "【当前激活规则 active_rules】\n[{\"rule_id\":\"CONCRETE-POUR-001\",\"condition\":\"process_data.rebar_inspection_status == 'passed'\"}]\n\n【当前施工场景数据 scene_data】\n{\"construction_context\":{\"work_type\":\"混凝土浇筑\",\"inspection_batch_id\":\"PC-2026-001\"},\"process_data\":{\"rebar_inspection_status\":\"pending\"}}"
    }
  ]
}
```

服务从后向前选取最后一条 `role=user` 消息。`content` 可以是非空字符串，也可以是由 `text`、`image_url` 组成的 OpenAI 风格多模态数组。

`async_mode=false` 时等待终态并返回结果；`async_mode=true` 时立即返回非终态响应。`stream=true` 当前会返回 `422`。

规则校验场景每次请求都在最后一条 user 消息中传入完整激活规则和施工场景，不传附件或图片。调用成功后只解析 `AgentCallResponse.output`；其内容必须是业务智能体按约定生成的 JSON 字符串。API 层不会替调用方验证该字符串是否满足业务 JSON Schema。

### `POST /api/agent-invocation/agent-call/runs/result`

```json
{
  "run_id": "run-uuid",
  "agent_slug": "construction-process-rule-validator"
}
```

`agent_slug` 可省略，传入时用于校验 run 归属。返回结构与创建接口一致。

## 主要 Schema

`AgentCallRunCreate`：

- `agent_slug: string`：必填；
- `messages: object[]`：必填；
- `stream: boolean = false`；
- `agent_call_meta: object = {}`，不得包含 `context`；
- `thread_id: string | null`；
- `request_id: string | null`，归一化后最多 64 字符；
- `model_spec: string | null`；
- `async_mode: boolean = false`。

`AgentCallResponse`：

- `run_id`、`agent_slug`、`thread_id`、`request_id`；
- `status`；
- `output`；
- `choices[].messages[]` 和 `choices[].finish_reason`；
- `usage.prompt_tokens`、`usage.completion_tokens`、`usage.total_tokens`；
- 失败时可包含 `error`。

## 本次增量变更

- 新增会话创建、历史消息、线程附件和图片处理接口说明；
- 补充线程附件同步到 Agent state，以及当前 `agent-call` 不支持消息级 `attachment_file_ids` 的边界；
- 明确建筑施工规则校验按一次性任务使用，历史接口仅用于排障，业务方只消费 `output` 中的最终 JSON；
- 未修改或移除后端接口。
