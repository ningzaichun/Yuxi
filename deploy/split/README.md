# Yuxi 拆分生产部署

拆分部署包含两个相互独立的 Docker Compose 项目：

| 服务器角色 | Compose 与配置 | 运行服务 |
| --- | --- | --- |
| 基础设施服务器 | `docker-compose.infra.yml` + `.env.infra` | PostgreSQL、Redis、MinIO、Etcd、Milvus、Neo4j |
| 应用服务器 | `docker-compose.app.yml` + `.env.app` | Web、API、Worker、Sandbox Provisioner |

以下命令都在仓库根目录执行。两台服务器只需保留各自使用的配置文件，且必须通过私网或 VPN 互通。

## 准备配置

```bash
cp deploy/split/.env.infra.template deploy/split/.env.infra
cp deploy/split/.env.app.template deploy/split/.env.app
```

填写配置后，先进行解析校验。校验不会启动容器：

```bash
docker compose --project-name yuxi-infra --project-directory . --env-file deploy/split/.env.infra -f deploy/split/docker-compose.infra.yml config --quiet
docker compose --project-name yuxi-app --project-directory . --env-file deploy/split/.env.app -f deploy/split/docker-compose.app.yml config --quiet
```

## 基础设施服务器

```bash
docker compose --project-name yuxi-infra --project-directory . --env-file deploy/split/.env.infra -f deploy/split/docker-compose.infra.yml pull
docker compose --project-name yuxi-infra --project-directory . --env-file deploy/split/.env.infra -f deploy/split/docker-compose.infra.yml up -d
docker compose --project-name yuxi-infra --project-directory . --env-file deploy/split/.env.infra -f deploy/split/docker-compose.infra.yml ps
```

停止服务但保留数据卷：

```bash
docker compose --project-name yuxi-infra --project-directory . --env-file deploy/split/.env.infra -f deploy/split/docker-compose.infra.yml down --remove-orphans
```

## 应用服务器

```bash
docker compose --project-name yuxi-app --project-directory . --env-file deploy/split/.env.app -f deploy/split/docker-compose.app.yml up -d --build
docker compose --project-name yuxi-app --project-directory . --env-file deploy/split/.env.app -f deploy/split/docker-compose.app.yml ps
```

健康检查默认通过 Web 容器暴露的端口访问：

```bash
curl --fail http://127.0.0.1:8080/api/system/health
```

停止服务但保留应用文件和模型卷：

```bash
docker compose --project-name yuxi-app --project-directory . --env-file deploy/split/.env.app -f deploy/split/docker-compose.app.yml down --remove-orphans
```

## 数据与安全边界

- 不要提交 `.env.infra`、`.env.app`、根 `.env` 或 `web/.env.local`。
- 日常停止命令不要增加 `--volumes`，否则会删除 Compose 管理的数据卷。
- PostgreSQL、Redis、MinIO、Milvus 和 Neo4j 端口只向受控私网开放。
- 应用服务器中的 API、Worker 和 Sandbox Provisioner 必须使用同一组基础设施地址与凭据。
- 一体化生产部署使用根目录 `docker-compose.prod.yml`；本目录不负责本地源码开发。
