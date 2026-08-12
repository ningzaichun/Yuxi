# 排期审查模块操作与维护手册

排期审查模块用于接收业务系统转换后的 `canonical_schedule_v2.2`，保存不可变来源快照，并由 Yuxi 独立执行确定性审查。它不会修改来源计划，也不会把来源转换器的 Validation 当作 Yuxi 审查结论。

## 能力边界

当前版本支持：

- 幂等提交、查询和隔离排期快照；
- 任务层级、依赖网络、零 Lag 日期关系和管理完整性审查；
- Statistics、Capability、Issue、证据和直接上下游查看；
- 通过具备 Schedule 工具的智能体解释已有 Issue。

当前版本不支持：

- 计算非零 Lag 日期关系；
- CPM、关键路径、总浮时或自由浮时重算；
- 自动修改任务日期、依赖、日历或约束；
- 资源均衡、成本优化或 MPP 回写；
- 把 Candidate 直接应用为生效计划。

因此，审查结果中的“68 条已检查、22 条未检查”不能表述成“90 条依赖全部验证通过”。

## 角色与数据归属

快照按认证用户的 `uid` 隔离。提交、列表、详情、审查、Issue 和 Agent 工具都使用同一归属边界。无权访问和资源不存在统一返回 `404`，避免泄露其他用户的数据是否存在。

业务系统需要多人读取同一批数据时，当前应使用明确的服务账号提交和查询。模块暂不提供部门共享。

## 业务操作流程

### 1. 在业务系统生成 Canonical Snapshot

业务系统或转换服务先把来源计划转换为 `canonical_schedule_v2.2`。结构契约以 Pydantic 模型和随代码导出的 JSON Schema 为准：

- 模型：`backend/package/yuxi/schedule/contracts/canonical_v2_2.py`
- Schema：`backend/package/yuxi/schedule/contracts/schemas/canonical_schedule_v2_2.schema.json`

日期时间必须包含时区偏移。Task、Dependency、Calendar 和 Resource ID 必须唯一，引用必须存在，任务父子层级不能成环。单次最多 5,000 个任务和 25,000 条依赖，请求正文最大 10 MiB。

### 2. 提交来源快照

请求：

```http
POST /api/schedule/snapshots
Authorization: Bearer <token>
Content-Type: application/json
```

```json
{
  "request_id": "schedule-import-20260811-001",
  "external_project_id": "project-001",
  "external_snapshot_id": "project-001-v22",
  "external_revision": "V2.2",
  "snapshot": {
    "schema_version": "canonical_schedule_v2.2"
  }
}
```

`snapshot` 必须是完整 Canonical 对象，上例只展示版本字段。

幂等规则：

- 同一用户、同一 `request_id`、相同 Snapshot 内容：返回原结果和 `200`；
- 同一用户、同一 `request_id`、不同 Snapshot 内容：返回 `409`；
- 首次创建成功：返回 `201`；
- 存储失败：记录为 `failed`，使用同一请求可恢复，不产生新 Snapshot ID；
- 并发提交同一请求时只有一个执行者，其他请求复用最终结果。

`creating` 使用 60 秒执行租约。若 API 进程在上传或最终落库前异常退出，同一请求在租约到期后可接管原 Snapshot ID；正常的并发请求不会重复上传。HTTP 请求被主动取消时会立即释放租约并标记为 `failed`。

Yuxi 的内容哈希只基于通过校验后的 `snapshot`，不包含请求信封字段，也不信任来源 `source.sha256` 作为幂等依据。

### 3. 在页面查看结果

登录后从左侧导航进入“排期审查”：

1. 在左侧选择来源快照；
2. 查看任务、依赖、开放起点/终点和日期检查统计；
3. 查看 Capability。Capability 阻断和 Issue 严重等级是两个维度；
4. 按等级或分类过滤 Issue；
5. 点击“证据”查看对象、确定性证据和直接上下游；
6. 点击“Agent 解释”进入具备两个 Schedule 工具的智能体。

页面是只读界面，不提供编辑、优化、重算或应用按钮。

### 4. 使用 Agent 解释 Issue

目标智能体的工具配置必须同时包含：

- `get_schedule_audit`
- `get_schedule_issue_context`

只有规范化运行配置包含两个工具且当前用户可访问的智能体，才会出现在排期页面的选择列表中。

页面进入 Agent 后只预填以下消息，不会自动发送：

```text
请解释排期审查问题 issue_id=<issue-id>，并说明证据、影响和需要工程人员确认的事项。
```

用户发送或清空预填内容后，页面会移除 URL 中的 `agent_id` 与 `schedule_issue_id`，避免刷新后重复消费。

Agent 只能读取已持久化的 Yuxi Audit 和 Issue。它不能自行计算日期、关键路径或 Patch，也不能把来源 Validation 描述成 Yuxi 结论。

## 审查规则

当前规则集版本为 `schedule-audit-mvp-v1`：

| 规则 | 含义 | 默认等级 |
|---|---|---|
| `STATISTICS_MISMATCH` | 来源统计和 Yuxi 独立计算不一致 | warning |
| `SOURCE_CAPABILITY_MISMATCH` | 来源能力声明和 Yuxi 投影不一致 | warning |
| `SELF_DEPENDENCY` | 依赖的前后任务相同 | blocker |
| `DUPLICATE_RELATION` | 前置、后置、类型和 Lag 重复 | warning |
| `DEPENDENCY_CYCLE` | 依赖图存在环 | blocker |
| `OPEN_START` / `OPEN_FINISH` | 叶子任务没有前置或后续 | warning |
| `SUMMARY_TASK_DEPENDENCY` | 依赖涉及汇总任务 | warning |
| `ZERO_LAG_DATE_VIOLATION` | 零 Lag 关系不满足来源日期锚点 | blocker |
| `SEVEN_DAY_WORK_CALENDAR` | 日历七天均为工作日 | warning |
| `BASELINE_MISSING` | 没有 Baseline 0 | warning |
| `STATUS_DATE_MISSING` | 没有状态日期 | warning |
| `MILESTONE_MISSING` | 没有里程碑 | warning |
| `NO_SOURCE_ASSIGNMENTS` | 来源 Assignment 为空 | warning |
| `RESOURCE_SEMANTICS_UNCLASSIFIED` | 资源业务语义未分类 | warning |
| `LAG_CALENDAR_POLICY_UNSPECIFIED` | 非零 Lag 的日历策略未冻结 | blocker |

零 Lag 日期关系使用以下精确定义：

| 类型 | 条件 |
|---|---|
| FS | `successor.start >= predecessor.finish` |
| SS | `successor.start >= predecessor.start` |
| FF | `successor.finish >= predecessor.finish` |
| SF | `successor.finish >= predecessor.start` |

非零 Lag 统一记为 skipped，不执行近似判断或“明显不合理”回退。

## 查询 API

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/schedule/snapshots` | 提交并审查 Snapshot |
| GET | `/api/schedule/snapshots` | 分页列出当前用户的 ready Snapshot |
| GET | `/api/schedule/snapshots/{id}` | 读取 Snapshot 元数据和规范化正文 |
| GET | `/api/schedule/snapshots/{id}/audit` | 读取 Statistics、Capability 和摘要 |
| GET | `/api/schedule/snapshots/{id}/issues` | 按分类、等级分页读取 Issue |
| GET | `/api/schedule/issues/{issue_id}` | 读取 Issue、任务和直接上下游证据 |
| GET | `/api/schedule/agents` | 列出具备两个 Schedule 工具的可访问 Agent |

错误响应统一放在 `detail`：

```json
{
  "detail": {
    "code": "SCHEDULE_CONTRACT_INVALID",
    "message": "排期数据不符合 canonical_schedule_v2.2",
    "errors": [
      {
        "path": "/snapshot/tasks/0/planned_start",
        "code": "INVALID_DATETIME",
        "message": "必须是包含时区偏移的 ISO 8601 日期时间"
      }
    ]
  }
}
```

错误路径使用 JSON Pointer，响应和日志不会回显无效输入值、Notes 或完整计划正文。

## 数据存储与恢复

PostgreSQL 表：

- `schedule_snapshots`：归属、幂等键、内容哈希、对象地址和提交状态；
- `schedule_audit_runs`：规则集、独立统计、Capability 和 Issue 摘要；
- `schedule_issues`：稳定 `issue_key`、证据、对象、消息和排序键。

规范化 Snapshot 保存到私有 MinIO Bucket `schedule-snapshots`：

```text
{owner_uid}/{schedule_snapshot_id}/snapshot.json
```

该 Bucket 不生成公开 URL。Source Snapshot 在应用层不可变，审查流程不会原地修改它。

提交状态：

- `creating`：执行者正在上传和落库；
- `ready`：对象、Audit 和 Issue 已完整提交，可查询；
- `failed`：依赖存储失败，可用相同请求恢复。

列表和详情只展示 `ready` 记录。

若进程异常退出后记录停留在 `creating`，不要手工创建新的 `request_id`。等待 60 秒租约到期后原样重试，服务会复用原 Snapshot ID 和固定 MinIO Object；若状态长期不变，再检查 API 日志、数据库时间和 PostgreSQL 写权限。

## 开发和运维检查

本地开发前先确认宿主机端口：API `5050`、Web `5173`、Sandbox Provisioner `8002`。Schedule 本身不依赖 Sandbox Runtime。

后端验证：

```powershell
cd backend
& '.venv\Scripts\python.exe' scripts/export_schedule_schema.py
& '.venv\Scripts\python.exe' scripts/sanitize_schedule_fixture.py
& '.venv\Scripts\python.exe' -m pytest test/unit/schedule test/unit/toolkits/schedule -q
& '.venv\Scripts\python.exe' -m ruff check package/yuxi/schedule package/yuxi/repositories/schedule_repository.py package/yuxi/services/schedule_audit_service.py server/routers/schedule_router.py
```

前端验证：

```powershell
cd web
pnpm lint
pnpm build
```

涉及真实 PostgreSQL、MinIO 和 HTTP 时，应按本地开发指南启动 API/Web，并运行 Schedule 集成测试。

## 常见问题

### 提交返回 422

按 `detail.errors[].path` 定位字段。重点检查时区、枚举、重复 ID、丢失引用、父子环和数组规模。422 不会创建 Snapshot。

### 相同 request_id 返回 409

调用方重复使用了同一用户下的幂等键，但 Snapshot 内容发生变化。不要覆盖旧请求；为新的业务提交生成新的 `request_id`。

### 页面看不到刚提交的记录

页面只显示 `ready`。检查 `schedule_snapshots.submission_status` 和 `failure_code`，再检查 PostgreSQL 与 MinIO 连接。`failed` 记录应使用原请求重试。

### Agent 列表为空

确认用户可访问目标 Agent，并在该 Agent 的工具配置中同时启用两个 Schedule 工具。只启用其中一个不会进入候选列表。

### 为什么 blocker 仍可查看 Snapshot

`blocker` 阻断具体计算能力，不等于整个 Snapshot 无法保存。只要外部契约边界有效，甘特展示和来源排期审查仍可允许。

## 后续候选方案约束

CandidateSnapshot 暂不冻结为长期稳定外部契约。后续首个可运行版本使用 `schedule_candidate_draft_v0`，并保持以下不变量：

- Source Snapshot 不可变；
- 来源 `source_calculation` 不被覆盖；
- Yuxi 计算写入独立 `engine_result`；
- 历史 Candidate 按原 draft 版本读取；
- 业务端首版只读取、展示和评价 Delivery Package，不依赖 draft 内部字段的长期稳定性。

进入确定性排程开发前，必须先通过第一阶段业务验收，并冻结 Lag 日历、汇总依赖、手工任务、约束、关键任务阈值和 Microsoft Project 黄金样例。
