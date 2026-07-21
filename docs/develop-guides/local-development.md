# 本地开发指南

本地开发采用“远程基础设施 + 本机源码进程 + 本机 Docker Sandbox”的固定拓扑：

| 服务 | 位置 | 启动方式 |
| --- | --- | --- |
| PostgreSQL、Redis、MinIO、Milvus、Etcd、Neo4j | 基础设施服务器 | Docker Compose |
| API、Worker、Web | 开发机宿主机 | uv / pnpm，支持热重载 |
| Sandbox Provisioner | 开发机 Docker | 只启动 `docker-compose.yml` 中的目标服务 |
| Sandbox Runtime | 开发机 Docker | Provisioner 按线程动态创建 |

`sandbox-provisioner` 是控制面，不是实际执行命令的 Sandbox。API 和 Worker 通过 `127.0.0.1:8002` 请求它创建或复用 Runtime 容器。

## 1. 前置条件

- Python 3.12 或 3.13、uv。
- Node.js 24+、pnpm 10.x。
- Docker Desktop 或 Docker Engine。
- 能通过私网、VPN 或受控隧道访问基础设施服务器。

## 2. 初始化本机配置

从仓库根目录执行：

```powershell
.\scripts\split-deploy\Initialize-SplitConfig.ps1 -Scope Host
```

该命令基于以下模板创建私有配置，已有文件默认不会覆盖：

- `deploy/split/.env.host.dev.template` → 根目录 `.env`
- `web/.env.local.example` → `web/.env.local`

在 `.env` 中填写远程 PostgreSQL、Redis、MinIO、Milvus、Neo4j 地址和必要密钥。远程服务不能填写 Compose 内部服务名；密码位于 URL 中时需要百分号编码。

Sandbox 相关配置保持为：

```dotenv
SANDBOX_PROVIDER=provisioner
SANDBOX_PROVISIONER_URL=http://127.0.0.1:8002
SANDBOX_PROVISIONER_BACKEND=docker
SANDBOX_DOCKER_SANDBOX_HOST=127.0.0.1
```

不要提交 `.env`、`web/.env.local` 或任何真实凭据。

## 3. 安装依赖

```powershell
.\scripts\split-deploy\Install-HostDevDependencies.ps1
```

脚本使用 uv 同步后端开发/测试依赖，并用锁文件安装前端依赖。

## 4. 启动本机 Sandbox

先把宿主机线程目录的绝对路径传给 Dockerized Provisioner，再只启动 Compose 中的目标服务：

```powershell
$threadsPath = New-Item -ItemType Directory -Force -Path (Join-Path $PWD 'saves/threads')
$env:SANDBOX_DOCKER_THREADS_HOST_PATH = $threadsPath.FullName
docker compose -f docker-compose.yml up -d sandbox-provisioner
```

指定绝对路径很重要：宿主机 API、Worker、文件查看器和 Sandbox Runtime 必须看到同一个 `saves/threads`。省略该变量会落到完整 Compose 使用的另一套文件目录。

检查 Provisioner：

```powershell
Invoke-RestMethod http://127.0.0.1:8002/health
docker compose -f docker-compose.yml ps sandbox-provisioner
```

健康检查只证明控制面可用。首次调用 Shell、Python、文件或 Skill 工具时，Provisioner 才会拉取并创建真正的 Sandbox Runtime 容器。

本地开发不要同时启动 `docker-compose.prod.yml` 中同名的 Provisioner，否则会产生容器名或端口冲突。

## 5. 启动源码程序

分别在三个终端运行：

```powershell
.\scripts\split-deploy\Start-HostService.ps1 -Service Api
```

```powershell
.\scripts\split-deploy\Start-HostService.ps1 -Service Worker
```

```powershell
.\scripts\split-deploy\Start-HostService.ps1 -Service Web
```

本地地址：

| 服务 | 地址 |
| --- | --- |
| Web | `http://127.0.0.1:5173` |
| API | `http://127.0.0.1:5050` |
| API 文档 | `http://127.0.0.1:5050/docs` |
| Sandbox Provisioner | `http://127.0.0.1:8002` |

API 和 Worker 监控 `backend/server`、`backend/package`，Web 使用 Vite HMR。修改 `.env`、`web/.env.local` 或依赖文件后仍需重启对应进程。

## 6. 验证

```powershell
.\scripts\split-deploy\Test-HostDev.ps1
```

该脚本检查配置、API、Web 和 Provisioner。Worker 没有独立健康接口，还需要确认 ARQ 启动日志并执行一次真实 Agent 任务。

首次环境验收至少覆盖：

- 普通聊天不创建不必要的 Sandbox。
- Worker 能从远程 Redis 消费任务。
- 文件或命令工具能创建 Runtime，并读写当前线程目录。
- 上传文件返回的地址使用可被浏览器访问的 `MINIO_PUBLIC_URI`。

## 7. 停止

API、Worker、Web 在各自终端按 `Ctrl+C`。停止 Provisioner但保留其容器和数据：

```powershell
docker compose -f docker-compose.yml stop sandbox-provisioner
```

不要使用 `down --volumes`。运行中的 Runtime 容器由 Provisioner 的空闲回收逻辑管理，停止前应确认没有进行中的工具调用。

## 8. 故障定位顺序

1. 检查开发机到远程基础设施端口的连通性。
2. 检查 `http://127.0.0.1:8002/health`。
3. 检查 `docker logs sandbox-provisioner` 和按需创建的 Runtime 容器。
4. 检查 API、Worker 是否加载了同一份根 `.env`。
5. 检查宿主机和 Runtime 是否都指向根目录 `saves/threads`。
6. 最后检查 Agent、Tool 或提示词逻辑，避免把网络和挂载问题误判为模型问题。

Sandbox 的组件职责和安全边界见 [Sandbox 架构与设计](/agents/sandbox-architecture)。
