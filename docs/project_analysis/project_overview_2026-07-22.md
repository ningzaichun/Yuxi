# 📊 项目分析报告

生成时间：2026-07-22 10:23:56

## 🧱 技术栈

| 层次 | 主要技术 | 仓库依据 |
| --- | --- | --- |
| 后端 | Python 3.12/3.13、FastAPI、Uvicorn、ARQ、LangGraph | `backend/pyproject.toml`、`backend/package/pyproject.toml`、`backend/server` |
| 前端 | Vue 3、Vite、Pinia、Vue Router、Ant Design Vue、Less | `web/package.json`、`web/src` |
| 业务与任务存储 | PostgreSQL、SQLAlchemy asyncio、Redis | `backend/package/yuxi/storage/postgres`、`backend/package/yuxi/storage/redis` |
| 知识库与图谱 | Milvus、Etcd、Neo4j 5.26、python-igraph | `knowledge/implementations/milvus.py`、`knowledge/graphs`、Compose 文件 |
| 文件与解析 | MinIO、MinerU、PaddleX/PaddleOCR、RapidOCR、DeepSeek OCR | `knowledge/parser`、`storage/minio` |
| 构建部署 | uv、pnpm、Docker Compose | `backend/pyproject.toml`、`web/package.json`、`docker-compose*.yml` |

README 中的 LightRAG 是历史设计来源；当前实际知识图谱主链路是项目自研的 Milvus + Neo4j 实现，依据为 `README.md` 第 96 行和 `backend/package/yuxi/knowledge/graphs`。

## 🗂️ 项目结构

| 目录 | 职责 |
| --- | --- |
| `backend/server` | FastAPI 应用入口、路由和 Worker 入口 |
| `backend/package/yuxi/agents` | LangGraph 智能体、中间件、工具和运行后端 |
| `backend/package/yuxi/services` | 聊天、任务、评估等跨模块用例编排 |
| `backend/package/yuxi/repositories` | PostgreSQL 业务对象和知识库元数据访问 |
| `backend/package/yuxi/knowledge` | 知识库、解析、分块、检索、图谱和评估领域逻辑 |
| `backend/package/yuxi/storage` | PostgreSQL、Redis、MinIO、Neo4j 连接与模型 |
| `backend/test` | unit、integration、e2e 三层后端测试 |
| `web/src/apis` | 前端 HTTP 接口封装 |
| `web/src/views`、`components` | 页面入口与可复用交互组件 |
| `deploy`、`docker`、`scripts` | 生产部署、本地容器和宿主机开发脚本 |

## 🧩 功能模块

- 智能体模块：组合模型、知识库、Skills、MCP、附件、SubAgents 和 Sandbox。
- 知识库模块：支持 Milvus、本地/远程只读连接器、Dify、Notion 等类型；只有 Milvus 类型支持当前独立图谱构建。
- 文档模块：上传文件后进行解析、分块和向量入库，并在 PostgreSQL 保存文件与 Chunk 元数据。
- 知识图谱模块：LLM 从 Chunk 抽取实体与关系；Neo4j 保存图结构，PostgreSQL 保存实体、三元组及其 Chunk 引用，Milvus 保存实体和三元组向量。
- 图增强检索：先召回实体/三元组作为种子，在 Neo4j 中取子图，再以 Personalized PageRank 找回相关 Chunk，与基础检索结果融合。
- 前端图谱模块：知识库详情页提供图谱配置、异步构建状态、可视化搜索、重置和检索开关。

## 🔄 核心流程

### 知识图谱构建

1. 管理员创建 Milvus 知识库并选择 embedding 模型。
2. 上传文件，依次完成解析与向量入库，生成 `knowledge_chunks` 记录。
3. 在知识库详情的“知识图谱”页选择 LLM 模型，填写可选 Schema、并发数和模型参数，保存后锁定抽取器类型。
4. 前端请求 `POST /api/knowledge/databases/{kb_id}/graph-build/index`；路由向 API 进程内的 `Tasker` 队列提交 `knowledge_graph_index` 后台任务，任务状态持久化到 PostgreSQL。
5. `MilvusGraphService.build_pending_chunks` 读取 `graph_indexed != true` 的 Chunk，并发调用 LLM 抽取器。
6. 规范化实体和关系后，服务写入 Neo4j 图结构、PostgreSQL 图谱元数据、Milvus 实体/三元组向量集合，最后把 Chunk 标记为已建图。
7. 前端轮询状态并通过 `GET /api/graph/subgraph` 读取 Neo4j 子图进行可视化。

### 图增强检索

1. 管理员在“检索测试”的检索配置中启用 `use_graph_retrieval`。
2. Milvus 先执行向量、关键词或混合检索。
3. 查询同时召回图实体和图三元组，结合基础 Chunk 形成种子权重。
4. Neo4j 返回两跳子图，`python-igraph` 执行 Personalized PageRank。
5. 图谱召回的 Chunk 与基础结果按 `graph_weight` 融合，再按配置决定是否 rerank。

## 🧠 架构设计

Yuxi 是“模块化单体 + 独立异步 Worker + 外部基础设施”的架构。API 与 Worker 共享 `yuxi` 业务包；前端独立构建；PostgreSQL、Redis、MinIO、Milvus/Etcd、Neo4j 作为外部基础设施。

知识图谱不是单一 Neo4j 存储：

- Neo4j：Chunk、Entity 和 RELATION 的拓扑与子图遍历。
- PostgreSQL：知识库配置、Chunk 建图状态、抽取结果、实体/三元组及来源引用。
- Milvus：文档 Chunk 向量，以及图实体/三元组向量，用于将自然语言查询映射到图谱种子。
- API 进程内 `Tasker` + PostgreSQL：执行建图协程，并持久化任务状态和进度。Redis/独立 ARQ Worker 用于其他运行链路，不是当前图谱构建任务的执行者。

## 🌐 外部依赖

- PostgreSQL、Redis、MinIO、Milvus/Etcd、Neo4j。
- 至少一个可由 `select_model` 访问的对话模型，以及 Milvus 知识库所需的 embedding 模型。
- 可选文档解析服务：MinerU、PaddleX/PaddleOCR、RapidOCR、DeepSeek OCR。
- 本地开发依赖远程基础设施，API/Worker/Web 在宿主机运行；完整部署可由 Compose 提供全部基础设施。

## ⚠️ 风险与技术债

1. **图谱构建跨三类持久化。** 单个 Chunk 依次写 Neo4j、PostgreSQL 和 Milvus，当前没有跨存储事务；中途失败时 Chunk 保持待处理，可重试，但已完成的部分写入依赖幂等合并。建议持续保留跨存储失败与重试的集成测试，并提供一致性检查工具。
2. **抽取质量取决于 Schema 与模型。** 空 Schema 会退化为通用 `Entity/RELATED_TO`，同名实体的类型或命名不一致会造成图谱碎片。建议在正式全量构建前用少量代表性文档试建并评审实体类型、关系类型和别名规则。
3. **默认并发较高。** 前端默认 LLM 并发为 50，虽然允许范围是 1–1000，但可能触发模型限流或费用突增。建议根据供应商限额从 3–10 起步，确认稳定后再提高。
4. **修改 Schema 不会重算旧数据。** 保存新模型或 Schema 只影响尚未建图的 Chunk；如需全库一致，必须重置并重新抽取。UI 已提示此约束，但操作前仍需评估成本。
5. **图增强检索默认关闭。** 图谱构建成功不代表问答已经使用图谱；还需在检索配置中显式启用 `use_graph_retrieval`。建议在产品引导和验收清单中明确区分“建图成功”和“图检索启用”。
6. **异常被检索层降级。** 图检索失败时 `MilvusKB` 记录错误并返回基础结果，用户可能只看到普通 RAG 结果。建议通过日志/指标区分“未命中图谱”与“图谱检索异常”，避免静默误判效果。

## 📌 总结

当前项目已经具备完整的独立建图和图增强 RAG 链路。最短可行路径是：准备基础设施与模型 → 创建 Milvus 知识库 → 上传、解析并入库文档 → 设计并保存图谱 Schema → 小批量建图并检查 → 全量构建 → 启用图检索 → 用跨文档关系问题验收。关键实现入口为 `knowledge_router.py`、`milvus_graph_service.py`、`KnowledgeGraphSection.vue` 和 `milvus.py`。
