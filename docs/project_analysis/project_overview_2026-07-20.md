# 📊 项目分析报告

生成时间：2026-07-20 19:03:42

## 🧱 技术栈

| 层次 | 主要技术 | 仓库依据 |
| --- | --- | --- |
| 后端语言与运行时 | Python `>=3.12,<3.14`、uv | `backend/pyproject.toml`、`backend/uv.lock` |
| HTTP 与异步任务 | FastAPI、Uvicorn、ARQ | `backend/pyproject.toml`、`backend/server/main.py`、`backend/server/worker_main.py` |
| 智能体编排 | LangGraph 1.x、LangChain 1.x、DeepAgents、MCP adapters | `backend/package/pyproject.toml`、`backend/package/yuxi/agents` |
| 前端 | Vue 3、Vite、Pinia、Vue Router、Ant Design Vue、Less | `web/package.json`、`web/src/main.js` |
| 业务数据库 | PostgreSQL、SQLAlchemy asyncio、psycopg | `backend/package/pyproject.toml`、`backend/package/yuxi/storage/postgres` |
| 队列与运行事件 | Redis、ARQ | `backend/package/yuxi/storage/redis`、`backend/package/yuxi/services/run_queue_service.py`、`run_worker.py` |
| 对象与向量存储 | MinIO、Milvus、Etcd | `backend/package/yuxi/storage/minio`、`backend/package/yuxi/knowledge/implementations/milvus.py`、Compose 文件 |
| 图数据库 | Neo4j 5.x | `backend/package/yuxi/storage/neo4j`、Compose 文件 |
| 文档解析 | MinerU、PaddleOCR/PaddleX、RapidOCR、DeepSeek OCR | `backend/package/yuxi/knowledge/parser`、`docker/mineru.Dockerfile`、`docker/paddlex.Dockerfile` |
| 沙盒执行 | Sandbox Provisioner、agent-sandbox、Docker/Kubernetes Runtime | `docker/sandbox_provisioner/app.py`、`backend/package/yuxi/agents/backends/sandbox` |
| 构建与部署 | Docker Compose、Dockerfile、pnpm、VitePress | `docker-compose*.yml`、`deploy/split`、`docker`、`docs/package.json` |

项目描述仍提到 LightRAG，但当前知识库和图谱主实现落在 `knowledge/implementations/milvus.py` 与 `knowledge/graphs`。LightRAG 更接近历史来源而非当前运行时核心，依据为 `README.md` 的致谢说明和实际依赖/源码目录。

## 🗂️ 项目结构

| 目录或文件 | 职责 | 阅读入口 |
| --- | --- | --- |
| `backend/server` | FastAPI 应用、HTTP 路由、生命周期和 Worker 入口 | `backend/server/main.py`、`routers/__init__.py` |
| `backend/package/yuxi` | 可复用业务包，包含智能体、服务、仓储、知识库和存储 | `backend/package/pyproject.toml`、各一级子目录 |
| `backend/test` | 后端单元、集成、E2E 测试 | `backend/test/unit`、`integration`、`e2e` |
| `web/src` | Vue 工作台，包含页面、组件、API、状态和组合逻辑 | `web/src/main.js`、`router/index.js` |
| `docker` | API/Web/解析服务镜像及 Sandbox Provisioner | `docker/*.Dockerfile`、`docker/sandbox_provisioner` |
| `deploy/split` | 两台服务器拆分生产部署的 Compose 和通用环境模板 | `deploy/split/README.md` |
| `scripts/split-deploy` | 本地配置初始化、依赖安装、宿主机源码服务启动与检查 | `Start-HostService.ps1`、`Test-HostDev.ps1` |
| `packages/yuxi-cli` | 独立 CLI 客户端及测试 | `packages/yuxi-cli/pyproject.toml` |
| `docs` | VitePress 用户/开发文档，以及不参与站点构建的当前分析和变更记录 | `docs/.vitepress/config.mts` |
| `dev_docs` | 当前业务 PRD | `dev_docs/k_product_ai_three_core_business_scenarios_prd_2026-07-14.md` |
| `ARCHITECTURE.md` | 稳定代码地图、运行拓扑和架构不变量 | `ARCHITECTURE.md` |
| `docker-compose.yml` | 完整本地 Compose；混合开发时只启动 Sandbox Provisioner | `docs/develop-guides/local-development.md` |
| `docker-compose.prod.yml` | 单服务器一体化生产部署 | `docs/advanced/deployment.md` |

根目录 `saves`、`user-data`、`.env`、依赖目录和 IDE 配置均属于本地运行状态，并由 `.gitignore` 排除，不应作为源码模块理解。

## 🧩 功能模块

### Web 与 HTTP 边界

- `web/src/views` 提供 Agent 对话、工作区、仪表盘、模型和扩展页面；`web/src/router/index.js` 负责认证、管理员和超级管理员页面边界。
- `web/src/apis` 是前端 HTTP 调用的集中入口；`stores` 保存用户、智能体等状态；`composables` 承担流式运行和交互逻辑。
- `backend/server/routers/__init__.py` 集中注册系统、认证、智能体、聊天、知识库、图谱、Skills、MCP、工具和工作区路由；`LITE_MODE` 会跳过知识库、评估和图谱接口。
- `backend/server/main.py` 负责 FastAPI 应用、中间件、CORS 和登录限流，不承载主要领域流程。

### 用例与持久化边界

- `backend/package/yuxi/services` 是用例层。聊天、AgentRun、运行队列、用户身份、工作区、文件查看、Langfuse 和后台任务均从这里编排。
- `backend/package/yuxi/repositories` 封装 Agent、会话、用户、知识库、任务和反馈等数据库查询。
- `backend/package/yuxi/storage` 提供 PostgreSQL、Redis、MinIO 和 Neo4j 的连接与底层模型；业务路由不应绕过服务/仓储直接耦合这些实现。

### 智能体与扩展能力

- `backend/package/yuxi/agents` 组合内置 Agent、上下文、中间件、工具包、MCP、Skills 和执行后端。
- `agents/backends/composite.py` 将工作区、附件、Skills、知识库和 Sandbox 文件能力组合为 Agent 可用后端。
- `agents/backends/sandbox` 负责向 Provisioner 请求沙盒、保活并把命令/文件能力适配给 Agent。
- `services/agent_run_service.py`、`run_queue_service.py` 和 `run_worker.py` 连接 HTTP 请求、Redis 队列与 LangGraph 执行。

### 知识库、文档与图谱

- `knowledge/manager.py`、`factory.py` 和 `implementations` 根据知识库类型选择 Milvus、Dify、Notion 或只读连接器。
- `knowledge/parser` 统一文档解析入口；`knowledge/chunking` 提供多种文本分块策略。
- `knowledge/graphs` 负责实体关系抽取、Milvus 图向量和 Neo4j 图谱服务。
- `knowledge/eval` 提供评测集生成、指标计算和评估服务。

### CLI

`packages/yuxi-cli` 是独立发布单元，通过 `yuxi_cli.main:app` 提供命令行入口。由于对应发布 workflow 已移除，是否继续对外发布属于后续产品决策；当前源码和 `docs/intro/cli.md` 仍表明 CLI 功能尚未从项目中废弃。

## 🔄 核心流程

### Agent 对话与异步运行

1. Vue 页面通过 `web/src/apis` 请求 `/api`。
2. `backend/server/routers/chat_router.py` 或 `agent_router.py` 完成认证上下文和请求装配。
3. `chat_service.py`、`agent_run_service.py` 读取会话、Agent、工具、知识库和扩展配置，并创建运行任务。
4. ARQ Worker 从远程 Redis 消费任务，`run_worker.py` 执行 LangGraph Agent。
5. Agent 中间件挂载知识库、MCP、Skills、附件和 Sandbox 能力。
6. 运行事件进入 Redis，业务记录和最终状态进入 PostgreSQL，文件进入 MinIO 或线程文件目录。
7. 前端通过 SSE/轮询消费事件并渲染消息、工具调用、引用和产物。

依据：`backend/server/routers/chat_router.py`、`backend/package/yuxi/services/agent_run_service.py`、`run_queue_service.py`、`run_worker.py`、`web/src/composables`。

### 知识库数据流

1. 上传接口接收文件并保存对象/元数据。
2. Parser 按配置选择 MinerU、PaddleX、RapidOCR 等实现。
3. Chunking 将解析结果切分，模型生成 embedding。
4. Milvus 保存向量与检索字段；PostgreSQL 保存知识库、文件和 chunk 元数据。
5. 启用图谱时，抽取器生成实体关系，Neo4j 与 Milvus 图服务共同提供图检索。
6. Agent 通过知识库工具访问检索结果，而不是在路由或页面中硬编码知识实现。

依据：`backend/server/routers/knowledge_router.py`、`backend/package/yuxi/knowledge`、`repositories/knowledge_*`。

### 本地 Sandbox 数据流

1. 宿主机 API/Worker 通过 `SANDBOX_PROVISIONER_URL=http://127.0.0.1:8002` 调用 Dockerized Provisioner。
2. `docker/sandbox_provisioner/app.py` 通过 Docker Socket 创建或复用 Runtime 容器。
3. Provisioner 把根目录 `saves/threads` 中的 workspace、uploads、outputs 和 Skills 目录挂载到 Runtime。
4. Runtime 执行 Shell、Python 和文件操作，结果通过 Sandbox backend 返回 Agent。
5. 空闲 Runtime 由 Provisioner 回收，持久文件仍保留在线程目录。

依据：`docker-compose.yml`、`docker/sandbox_provisioner/app.py`、`agents/backends/sandbox`、`docs/develop-guides/local-development.md`。

## 🧠 架构设计

Yuxi 的源码形态是“模块化单体 + 独立异步 Worker + 外部基础设施”，不是按业务领域拆成可独立发布的微服务：

- API 与 Worker 共享 `backend/package/yuxi` 业务包和数据库模型。
- Web 是独立构建的前端应用。
- Sandbox Provisioner 是独立控制面服务，Runtime 容器按需产生。
- PostgreSQL、Redis、MinIO、Milvus/Etcd、Neo4j 是外部基础设施，可与应用同机或分机部署。

部署与开发边界：

| 场景 | 基础设施 | API/Worker/Web | Sandbox Provisioner |
| --- | --- | --- | --- |
| 日常开发 | 远程服务器 Compose | 开发机宿主进程 | 开发机 Docker |
| 一体化生产 | 同一服务器 Compose | 同一服务器 Compose | 同一服务器 Compose |
| 拆分生产 | 基础设施服务器 Compose | 应用服务器 Compose | 应用服务器 Compose |

此设计让源码热重载与生产容器化共存，但要求 `.env`、Compose 服务名、真实服务器地址和文件挂载严格区分。

## 🌐 外部依赖

- OpenAI 兼容模型、Anthropic、DeepSeek 等模型 API：依据 `backend/package/pyproject.toml` 与 `yuxi/models`。
- Tavily 等联网工具：依据 `backend/package/pyproject.toml` 和环境模板。
- PostgreSQL、Redis、MinIO、Milvus/Etcd、Neo4j：依据三套 Compose 与 `yuxi/storage`。
- MinerU、PaddleX/PaddleOCR、RapidOCR、DeepSeek OCR：依据 `knowledge/parser` 与 Dockerfile。
- Langfuse：依据 `services/langfuse_service.py`、评估脚本与文档。
- MCP Server、Dify、Notion 等可选连接器：依据 `agents/mcp`、`knowledge/implementations`。
- Sandbox Runtime 镜像与 Docker/Kubernetes API：依据 `docker/sandbox_provisioner/app.py` 和 Compose 环境变量。

外部服务地址和密钥来自私有 `.env` 或部署环境文件；模板只定义字段，不应包含真实凭据。

## ⚠️ 风险与技术债

### 1. Compose 定义存在重复漂移风险

`docker-compose.yml`、`docker-compose.prod.yml` 和两个拆分 Compose 都声明了 API、Worker、存储或 Sandbox 的部分配置。镜像版本、环境变量和挂载规则可能只在其中一处更新。

建议：短期通过 Compose `config --quiet` 和针对关键变量的测试保持一致；后续单独设计共享片段或生成流程，不在日常功能修改中顺手重构。

### 2. 本地开发脚本目录命名不再准确

删除服务器启停包装脚本后，`scripts/split-deploy` 主要服务于本机配置与宿主机源码进程，“split-deploy” 容易让读者误认为仍负责生产部署。

建议：后续独立迁移为 `scripts/dev`，同时更新 `ARCHITECTURE.md`、README、环境模板和开发文档。不要与业务功能开发混在同一提交。

### 3. Sandbox 具有高权限和文件路径约束

Provisioner 挂载 Docker Socket，并可创建执行不可信命令的容器；本地混合模式还要求宿主机程序与容器共享同一 `saves/threads`。路径错误可能表现为“健康接口正常但文件不可见”。

建议：生产环境使用受控私网、资源限制、固定镜像版本和独立故障域；为线程目录挂载增加真实文件读写的集成测试。依据：`docker/sandbox_provisioner/app.py`、`docs/agents/sandbox-architecture.md`。

### 4. 远程基础设施扩大开发故障面

本地 API 和 Worker 同时依赖远程 PostgreSQL、Redis、MinIO、Milvus 和 Neo4j。网络抖动、VPN、代理或服务器维护会影响本地开发，且 API 与 Worker 配置不一致时问题不易定位。

建议：把 `Test-HostDev.ps1` 保持为最短连通性入口；关键 AgentRun 验收必须同时观察 API 与 Worker，不用回退逻辑掩盖远程连接错误。

### 5. 公共文档与内部生成记录仍在同一顶层

本次已用 `docs/.vitepress/config.mts` 的 `srcExclude` 排除 `change_logs` 和 `project_analysis`，避免内部记录破坏站点构建；物理目录仍与正式文档同处 `docs`。

建议：若后续仍频繁生成分析/变更文件，统一迁移到仓库级 `internal/analysis` 与 `internal/change-logs`，并同步修改相关 Codex skill/项目规范；在规范未同步前不要只移动目录。

### 6. 产品需求文档边界分散

正式文档在 `docs`，业务 PRD 在 `dev_docs`，仍可能生效的业务实施计划位于被 Git 忽略的 `docs/vibe`。

建议：开发阶段开始前确认实施计划是否仍是当前范围；有效内容进入受版本控制的 `docs/product` 或 `product/requirements`，完成的日期化计划依赖 Git 历史而非长期堆积。

### 7. CLI 的产品状态不清晰

CLI 源码和文档仍在，但发布 workflow 已移除。`推测`：项目可能仍需要内部 CLI，只是不再自动发布；仓库没有足够证据判定它可以删除。

建议：在后续目录重组前先确认 CLI 的用户、发布渠道和测试责任，再决定保留、内置或移除。

## 📌 总结

当前项目的核心边界总体清晰：Vue 负责交互，FastAPI 路由保持适配层，`yuxi.services` 编排用例，repositories/storage 管理持久化，LangGraph + Worker 执行 Agent，知识库与 Sandbox 作为可组合能力接入。

本次清理后，运行方式收敛为一个开发拓扑和两个生产拓扑；生产服务器直接操作 Docker Compose，不再依赖 PowerShell 包装脚本；本地 Sandbox 由 Dockerized Provisioner 管理。

后续目录规划建议按独立任务分阶段实施：

1. 将 `scripts/split-deploy` 收敛为 `scripts/dev`。
2. 评估将根 Compose 与 `deploy/split` 统一放入更清晰的 `deploy/all-in-one`、`deploy/split`，但保留根默认入口或 Makefile 兼容命令。
3. 将有效 PRD/设计统一到受版本控制的产品文档目录。
4. 将生成式分析与变更记录移出公共文档树，并同步修改仓库规范和技能输出路径。
5. 最后再评估 CLI、旧公开文档和上游兼容入口是否属于当前产品范围。

以上目录迁移会影响构建上下文、文档链接和开发命令，应与后续业务功能开发分开提交和验证。
