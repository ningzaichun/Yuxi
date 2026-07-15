# 📊 项目分析报告

生成时间：2026-07-15 11:25:49

## 🧱 技术栈

- 语言：Python 3.12～3.13、JavaScript/Vue 单文件组件、PowerShell、Shell。
- 后端：FastAPI、Uvicorn、ARQ、LangGraph/Yuxi 业务包，依赖由 uv 管理；依据为 `backend/pyproject.toml`。
- 前端：Vue 3、Vite、Pinia、Vue Router、Ant Design Vue、Less，依赖由 pnpm 管理；依据为 `web/package.json`。
- 数据与中间件：PostgreSQL 16、Redis 7、MinIO、Milvus 2.5.6、Etcd 3.5.5、Neo4j 5.26；依据为根目录 Compose 文件。
- 构建与运行：Docker Compose、API/Web Dockerfile；开发态 API、Worker、Web 通过源码卷实现热重载。

## 🗂️ 项目结构

- `backend/server`：FastAPI 入口、HTTP 路由、生命周期与 Worker 入口。
- `backend/package/yuxi`：Agent、服务、仓储、知识库、对象存储和外部集成等核心业务。
- `backend/test`：unit、integration、e2e 分层测试。
- `web/src`：Vue 页面、组件、Pinia 状态、API 封装和样式。
- `docker`：应用镜像、Nginx 与 Sandbox Provisioner。
- `deploy/split`：本次新增的基础设施/应用拆分 Compose 和环境模板。
- `scripts/split-deploy`：本次新增的拆分部署 PowerShell 操作脚本。
- `docs`：用户/开发文档、项目分析与变更记录。

## 🧩 功能模块

- 智能体运行：API 创建运行，ARQ Worker 执行 LangGraph，Redis 承载任务与运行事件。
- 对话与配置：路由委托服务层，业务记录和配置主要持久化到 PostgreSQL。
- 知识库：文件进入 MinIO，经解析/分块后写入 Milvus，并通过 Agent 工具检索。
- 知识图谱：Neo4j 保存图关系，Milvus 保存图谱向量索引。
- 扩展能力：Tools、MCP、Skills、SubAgents 由 Agent context 和 middleware 组合。
- 沙盒：Sandbox Provisioner 通过 Docker Socket 创建执行容器，本地保存线程工作区和产物。
- 前端：Vue 对话窗口、管理页和事件流消费统一调用 `/api`。

## 🔄 核心流程

浏览器请求由 Web 的 API 层进入 FastAPI 路由，路由调用 `yuxi.services`，服务层通过 repositories/storage 访问 PostgreSQL、Redis、MinIO、Milvus 和 Neo4j。长任务由 API 写入 Redis 队列，Worker 消费后运行 Agent，运行事件再经 Redis 回到 API/前端。文件上传先写 MinIO，知识库处理把元数据写 PostgreSQL、向量写 Milvus；图谱路径额外访问 Neo4j。

拆分部署不改变上述调用链，只把基础设施的 Compose 服务名地址改为显式远程连接串。API、Worker、Web、Sandbox 仍在同一本地 Compose 网络，服务器基础设施在另一 Compose 网络中，通过宿主机端口和受控网络边界访问。

## 🧠 架构设计

项目是模块化单体加独立异步 Worker 的架构，不是按业务拆分的微服务。API 与 Worker 共享 `backend/package/yuxi` 业务包和同一组存储依赖。前端是独立 SPA。Docker Compose 是当前开发与部署编排的事实来源。

本次部署拆分采用“应用进程与有状态依赖分离”，未把业务模块拆成远程服务，因此不引入新的内部 HTTP 协议，也不改变路由、服务、仓储边界。

## 🌐 外部依赖

- 模型与搜索服务：SiliconFlow、OpenAI-compatible provider、Tavily 等，取决于环境与持久化配置。
- Tools/MCP：由用户配置的外部工具与 MCP Server，运行时按 Agent 配置调用。
- 文档解析：可选 MinerU、PaddleX、PaddleOCR 云服务。
- 基础设施：PostgreSQL、Redis、MinIO、Milvus/Etcd、Neo4j。
- 执行环境：Docker daemon 与 Sandbox 镜像。

## ⚠️ 风险与技术债

- MinIO 客户端在 `storage/minio/client.py` 固定 `secure=False`，公开 URL 固定拼接 HTTP 9000。建议新增内部 URI、公开 URI 和 TLS 三个独立配置。
- Milvus 数据库变量不统一：图谱读取 `MILVUS_DB`，普通知识库固定 `yuxi`，现有 Compose 的 `MILVUS_DB_NAME`不产生预期作用。建议统一成一个经过测试的配置字段。
- 原一体化 Compose 的 PostgreSQL、MinIO、Neo4j 使用开发默认密码，Redis 无密码。拆分模板已强制密钥，但原方式仍只适用于可信开发环境。
- API 和 Worker 与多个有状态依赖强耦合，远程网络抖动会同时影响请求和异步任务。建议增加基础设施监控、连接池指标和明确的故障恢复手册，而不是用静默回退掩盖连接故障。
- Sandbox Provisioner 挂载 Docker Socket，拥有较高宿主机权限。开发态可接受，生产态应评估独立节点、受限运行时或 Kubernetes 后端。
- 单机 Milvus/Etcd/MinIO 的一致性备份复杂，不能只备份 Milvus 单个卷。建议制定协调停写/快照或使用版本匹配的官方备份工具。
- 拆分后跨主机传输默认无 TLS。推荐先使用私网/VPN，生产前为数据库和对象存储链路补齐加密。

## 📌 总结

Yuxi 的业务代码已经通过环境变量访问主要存储，具备应用与基础设施拆分的基础。最小可行方案是保留原 Compose，新增服务器基础设施 Compose与本地应用 Compose，并把连接串、凭据和网络白名单分别放入不入库的环境文件。真正的生产化仍需完成 MinIO TLS/公开地址改造、密钥管理、监控以及跨存储一致性备份验证。
