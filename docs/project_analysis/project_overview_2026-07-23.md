# 📊 项目分析报告

生成时间：2026-07-23 17:16:29

## 🧱 技术栈

- 后端：Python 3.12+、FastAPI、Uvicorn、SQLAlchemy Async、ARQ。
- 智能体：LangGraph v1、LangChain、MCP、可选 Langfuse tracing。
- 前端：Vue 3、Vite、Pinia、Ant Design Vue、Less，使用 pnpm。
- 数据与中间件：PostgreSQL、Redis、MinIO、Milvus/Etcd、Neo4j。
- 日志：业务代码主要使用 Loguru，同时保留 Python `logging`；容器运行日志由 Docker 收集。
- 构建与部署：uv、pnpm、Docker Compose。

依据：`backend/pyproject.toml`、`backend/package/pyproject.toml`、`web/package.json`、`docker-compose.yml`、`docker-compose.prod.yml`。

## 🗂️ 项目结构

- `backend/server`：FastAPI 入口、路由、中间件和生命周期管理。
- `backend/package/yuxi`：智能体、服务、仓储、存储、知识库、模型和通用工具。
- `web/src`：Vue 页面、组件、Pinia 状态和后端 API 封装。
- `docker`：API/Web 镜像、Nginx、Sandbox Provisioner 和基础设施辅助配置。
- `deploy/split`：基础设施与应用服务拆分部署配置。
- `saves` / `docker/volumes/yuxi`：宿主机开发或 Compose 部署时的应用文件、Agent SQLite checkpoint 和应用日志持久化目录。
- `docs`：用户文档、开发文档、需求方案、项目分析和逐次变更记录。

依据：`ARCHITECTURE.md`、仓库目录、Compose 文件。

## 🧩 功能模块

### 日志与观测模块

1. 应用运行日志
   - `backend/package/yuxi/utils/logging_config.py` 配置 Loguru。
   - 默认文件为相对工作目录下的 `saves/logs/yuxi-<进程启动日期>.log`。
   - 单文件达到 10 MB 时轮转，归档保留 30 天并压缩为 zip。
   - 同一日志同时写入文件和标准错误输出。
   - `httpx`、`openai`、`neo4j`、`urllib3` 的 WARNING 以上日志桥接到 Loguru。

2. HTTP 访问日志
   - `backend/server/utils/access_log_middleware.py` 记录客户端 IP/端口、HTTP 方法、路径、查询串、状态码和耗时。
   - 使用独立 Python `logging.StreamHandler`，且 `propagate=False`，因此只进入进程控制台/Docker 日志，不进入 `yuxi-*.log` 文件。
   - Uvicorn 自带 access handler 在 `backend/server/utils/common_utils.py` 中被清理，避免重复访问日志。

3. 管理员日志查看
   - `GET /api/system/logs` 读取 API 进程当前 `LOG_FILE` 的最后 1000 行，支持按级别过滤，仅管理员可访问。
   - 前端 `web/src/components/DebugComponent.vue` 通过 `web/src/apis/system_api.js` 调用该接口。
   - 前端“清空日志”只清空当前页面内存，不删除服务端文件。

4. 操作审计记录
   - PostgreSQL 业务表 `operation_logs` 保存 `user_id`、`operation`、`details`、`ip_address`、`timestamp`。
   - `backend/package/yuxi/services/operation_log_service.py` 提供手工写入函数。
   - 当前 13 个调用点仅覆盖登录/OIDC 登录、系统初始化、个人资料、用户增删改、头像、模拟用户、部门增删改等。
   - 仓库中未发现操作日志的查询 API 或前端管理页面。

5. Agent 运行与会话记录
   - PostgreSQL 的 `conversations`、`messages`、`tool_calls`、`agent_runs` 等表保存对话、消息、工具调用、运行状态、输入、错误和 request ID。
   - Redis Stream `run:events:<run_id>` 保存前端消费的运行事件，默认在最后一次追加事件后 7200 秒过期。
   - LangGraph checkpoint 默认使用 `saves/agents/<agent>/aio_history.db`；配置 `LANGGRAPH_CHECKPOINTER_BACKEND=postgres` 时可改用 PostgreSQL。
   - 这些是业务状态和短期事件，不等于不可篡改的操作审计日志。

6. LLM 链路追踪
   - 配置 Langfuse 凭证后，可按用户、线程和单轮执行查看模型调用、工具调用、耗时和错误。
   - Langfuse 是可选观测层，不替代本地运行日志、业务表或操作审计表。

7. 基础设施日志
   - API、Worker、Web、Sandbox Provisioner 的 stdout/stderr 由 Docker 日志驱动保存和查看。
   - Nginx 配置写 `/var/log/nginx/access.log` 与 `/var/log/nginx/error.log`，容器前台运行时也可结合 `docker logs` 查看。
   - Neo4j 和 Milvus 的内部日志目录在一体化或拆分 Compose 中有独立持久卷。

## 🔄 核心流程

### 普通 API 请求

浏览器请求 → Nginx（生产环境）→ FastAPI → `AccessLogMiddleware` 输出访问日志 → 路由/服务使用 Loguru 输出运行日志 → 运行日志同时进入 `saves/logs` 文件和容器控制台。

### 敏感管理操作

认证路由/部门路由完成业务写入 → 显式调用 `log_operation()` → 新增 `operation_logs` 记录并提交事务。没有全局中间件或领域事件自动覆盖其他业务操作。

### Agent 执行

聊天请求 → PostgreSQL 创建/更新 `agent_runs` 和消息 → ARQ Worker 执行 LangGraph → Redis Stream 暂存流式事件 → 最终消息、工具调用和运行结果进入 PostgreSQL → 可选向 Langfuse 上报 trace → 过程中的诊断信息写 Loguru 文件和控制台。

## 🧠 架构设计

项目是前后端分离、API 与异步 Worker 分进程的模块化服务架构。日志相关能力当前也是分层的：

- 运行诊断层：Loguru 文件 + stdout/stderr。
- HTTP 访问层：独立控制台访问日志。
- 业务审计层：PostgreSQL `operation_logs` 的少量手工埋点。
- Agent 状态层：PostgreSQL 业务记录 + Redis 临时事件 + LangGraph checkpoint。
- LLM 观测层：可选 Langfuse。

这些层各自有明确用途，但尚未通过统一 request/run/user 关联键、集中检索或统一保留策略形成完整的可观测与审计体系。

## 🌐 外部依赖

- PostgreSQL：业务记录、操作日志、Agent runs；可选 LangGraph checkpoint。
- Redis：ARQ 队列、运行事件、取消信号。
- Langfuse：可选的 LLM/Agent trace 与用户反馈 score。
- Docker Engine：容器 stdout/stderr 日志及 Sandbox Runtime 管理。
- MinIO、Milvus、Neo4j、Etcd：分别承担对象、向量、图和协调数据；其中 Neo4j/Milvus 有独立内部日志持久目录。

## ⚠️ 风险与技术债

1. 操作日志覆盖不足
   - 当前只有 13 个手工调用点，未覆盖知识库、Agent 配置、模型供应商、Skills、MCP、系统配置、API Key 等大量管理操作。
   - 建议先定义必须审计的高风险动作清单，再在服务层统一落库，不宜把所有 HTTP 请求机械写成操作日志。

2. 操作日志可能静默丢失
   - `log_operation()` 捕获所有异常后直接忽略，既无运行日志告警，也未回滚 session。
   - 函数允许 `user_id=None`，但数据库列为非空；不存在用户的登录失败记录会插入失败并被静默吞掉。
   - 建议修正字段/调用契约，并至少记录审计写入失败；是否允许审计失败阻断高风险操作应按业务等级决定。

3. 操作日志缺少查询与治理
   - 当前无查询 API、管理页面、分页/筛选、导出、保留周期和归档策略。
   - `details` 是自由文本，缺少资源类型、资源 ID、动作结果、request ID、前后值等结构化字段。
   - 与 User 的 ORM 关系配置了 delete-orphan；当前用户主要为软删除，但若发生硬删除，审计记录可能级联删除，不符合强审计场景。

4. 应用日志文件名不是真正的按天轮转
   - 日期只在进程导入配置时计算，跨天运行仍继续写进程启动日文件；当前工作区中 `yuxi-2026-07-21.log` 在 2026-07-23 仍有更新，已经验证这一行为。
   - 当前实际轮转条件是 10 MB，而不是自然日。
   - 建议若目标是按天归档，直接使用 Loguru 时间轮转并让文件命名与归档策略一致。

5. API 与 Worker 共写同一路径
   - Compose 中 API 与 Worker 共享 `/app/saves`，两者使用相同的 `LOG_FILE`。
   - 多进程共同写入和轮转同一文件会增加归档竞争、日志来源难区分等风险。
   - 建议至少按 `service` 拆分文件名，或统一只输出结构化 stdout 后交给日志采集系统。

6. 查询入口只能看到局部日志
   - `/api/system/logs` 只读取 API 进程计算出的当前文件最后 1000 行，看不到访问日志、Docker-only 日志、Nginx、Sandbox Provisioner、基础设施日志和历史压缩文件。
   - 当 API/Worker 在不同日期重启时，也可能分别写入不同日期文件。
   - 建议把该页面明确标注为“API/Worker 应用日志片段”，避免被误认为全系统日志中心。

7. 缺少统一关联与结构化字段
   - 普通应用日志格式没有稳定的 request ID、run ID、thread ID、user ID、service 字段，跨 API、Worker、Redis 事件和 Langfuse 排查时仍需人工拼接。
   - 建议优先引入日志上下文绑定与 JSON 输出，再决定是否接入 Loki/ELK/OpenSearch 等集中平台。

8. 访问日志包含完整查询串
   - 中间件会输出 `request.url.query`。虽然 CLI token 兑换已改为请求体，但其他接口若未来把敏感值放入 query，仍会落入 Docker 日志。
   - 建议执行查询参数白名单或敏感键脱敏。

9. 保留策略不统一
   - Loguru 文件标称保留 30 天；Redis 运行事件默认 2 小时；Docker 日志未在 Compose 中声明轮转上限；PostgreSQL 操作日志和业务记录未发现清理策略。
   - 建议按诊断、审计、业务记录分别定义保留时间、容量上限和备份要求。

## 📌 总结

Yuxi 当前已经具备应用文件日志、Docker/访问日志、数据库操作日志、Agent 运行记录和可选 Langfuse trace，但它们是并行的多套机制。

回答“日志保存在哪里”：

- 应用运行日志：文件 `saves/logs/yuxi-*.log`，同时输出到控制台/Docker。
- HTTP/Nginx/Sandbox/容器日志：主要在 stdout/stderr 与 Docker 日志中，部分基础设施另有持久卷。
- 操作记录：PostgreSQL `operation_logs`，但只覆盖少量账号与部门操作。
- 对话、工具调用和 Agent run：PostgreSQL 业务表。
- 流式运行事件：Redis，默认约 2 小时后过期。
- Agent checkpoint：默认 SQLite 文件，可配置 PostgreSQL。
- LLM 调用链：配置后进入 Langfuse。

因此，当前项目“有操作记录，但尚不是完整审计系统”；“有文件日志，也有数据库记录”，两者职责不同，不能互相替代。
