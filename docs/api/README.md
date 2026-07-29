# Yuxi API 文档

本目录维护从后端路由和数据模型核对得到的外部 API 契约。完整运行时接口仍可在 Yuxi 实例的 `/docs` 查看。

## 模块索引

| 模块 | 人类可读文档 | OpenAPI 模块 |
| --- | --- | --- |
| 外部 Agent 调用 | [external-agent-invocation.md](./modules/external-agent-invocation.md) | [external-agent-invocation.yaml](./modules/external-agent-invocation.yaml) |

聚合的 OpenAPI 3.1 文档见 [openapi.yaml](./openapi.yaml)。

## 扫描范围

当前外部接入模块核对了以下实现：

- `backend/server/routers/agent_router.py`
- `backend/server/routers/agent_invocation_router.py`
- `backend/server/routers/chat_router.py`
- `backend/package/yuxi/services/agent_invocation_service.py`
- `backend/package/yuxi/services/conversation_service.py`
- `backend/package/yuxi/agents/middlewares/attachment.py`
- `backend/package/yuxi/utils/image_processor.py`
- `backend/server/utils/auth_middleware.py`

## 更新策略

后端代码是接口事实来源。接口路径、字段、校验或响应发生变化时，只增量更新受影响的模块；人工编写的示例和说明在不与代码冲突时保留。
