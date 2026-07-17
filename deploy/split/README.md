# Yuxi Split Deployment

This directory separates three independent runtime roles:

| Role | Definition | Runtime |
| --- | --- | --- |
| Infrastructure server | `docker-compose.infra.yml` + `.env.infra` | PostgreSQL, Redis, MinIO, Etcd, Milvus, Neo4j |
| Application server | `docker-compose.app.yml` + `.env.app` | Web, API, Worker, Sandbox Provisioner |
| Developer host | `.env.host.dev.template` copied to root `.env` | Host-native uv/pnpm processes |

Run the following commands from the repository root.

Generate only the required configuration:

```powershell
.\scripts\split-deploy\Initialize-SplitConfig.ps1 -Scope Infra
.\scripts\split-deploy\Initialize-SplitConfig.ps1 -Scope App
.\scripts\split-deploy\Initialize-SplitConfig.ps1 -Scope Host
```

Start and validate infrastructure:

```powershell
.\scripts\split-deploy\Start-Infra.ps1 -Pull
.\scripts\split-deploy\Test-Infra.ps1
```

Start and validate the independent application server:

```powershell
.\scripts\split-deploy\Start-App.ps1
.\scripts\split-deploy\Test-App.ps1
```

Start host-native development services in four terminals:

```powershell
.\scripts\split-deploy\Start-HostService.ps1 -Service Sandbox
.\scripts\split-deploy\Start-HostService.ps1 -Service Api
.\scripts\split-deploy\Start-HostService.ps1 -Service Worker
.\scripts\split-deploy\Start-HostService.ps1 -Service Web
```

Never commit `.env.infra`, `.env.app`, the root `.env`, or `web/.env.local`. Stop scripts intentionally preserve volumes; do not add `--volumes` to routine shutdown commands.

Detailed Chinese guides are stored under `docs/vibe/`:

- `2026-07-15-split-infrastructure-and-application-deployment-plan.md`
- `2026-07-16-application-server-deployment-guide.md`
- `2026-07-16-host-native-local-development-guide.md`
- `2026-07-16-sandbox-deployment-and-local-development-plan.md`
- `2026-07-16-conda-host-development-environment-guide.md`
