# 排期审查模块操作与维护手册

排期审查模块用于接收业务系统转换后的 `canonical_schedule_v2.2`，保存不可变来源快照，并由 Yuxi 独立执行确定性审查。它不会修改来源计划，也不会把来源转换器的 Validation 当作 Yuxi 审查结论。

## 能力边界

当前版本支持：

- 幂等提交、查询和隔离排期快照；
- 任务层级、依赖网络、零 Lag 日期关系和管理完整性审查；
- Statistics、Capability、Issue、证据和直接上下游查看；
- 对单一统一项目日历、FS/SS/FF/SF 零/正 Lag、ASAP/SNET/FNET、手工/locked 活动任务执行正向和反向计算，自底向上滚动汇总任务日期，输出总浮时、自由浮时和关键标识，并生成只读 Candidate；
- 通过具备 Schedule 工具的智能体解释已有 Issue。

当前版本不支持：

- 计算负 Lag 日期关系；
- 负 Lag、多日历、日历例外、非法约束组合或实际进度的重算；
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

页面不编辑或应用来源计划。符合最小 CPM Profile 的来源可生成只读重算 Candidate；范围外输入只展示结构化阻断原因。

### 4. 生成受限 CPM 重算 Candidate

页面中的“生成重算 Candidate”使用 Profile
`yuxi-forward-unified-calendar-fs-ss-ff-sf-positive-lag-snet-fnet-manual-summary-rollup-reverse-float-critical-v7`。当前计算单一无继承项目日历下
FS/SS/FF/SF 零/正 Lag、ASAP/SNET/FNET、手工/locked 活动任务的最早和最晚开始/完成、总浮时、自由浮时与关键标识，并按直接子任务自底向上滚动汇总任务结果；正 Lag 与浮时按统一项目日历的工作
分钟推进，支持多个工作时段、午休、周末
和非工作时间归位。来源任务日期和 `source_calculation` 不变，Yuxi 日期只保存在 Candidate 的
`engine_result`，人工接受后可从 Delivery 的 `simulation_result` 查看同一结果。

包含多日历或日历例外、负 Lag、汇总依赖、非活动任务、非法层级、SNET/FNET 非法组合或实际进度的
输入返回结构化 `blocked`，不生成近似 Candidate。S3 零 Lag、S4 正 Lag、SS/FF/SF、SNET/FNET、手工/locked、汇总滚动与反向浮时的
七套 Microsoft Project 黄金样例均已确认并通过门禁；这只证明当前受限 Profile，不代表生产适用或跨项目通用。

仓库提供 `backend/test/data/schedule/microsoft_project_s3_golden_case.json` 作为最小人工对照输入。本机
Microsoft Project 16.0 已通过独立 COM 会话建立案例并计算日期，结果保存在 `external_observation`；
`backend/scripts/capture_ms_project_schedule_golden_observation.ps1` 可重复执行同一过程。脚本只向标准输出
返回观测 JSON，不保存 MPP、不修改夹具 expected，也不会把 Yuxi 输出传给 Microsoft Project。

S3/S4 七套黄金文件均已将独立
Microsoft Project 观测原样回填到 `expected.task_dates`，并记录确认人、确认时间和 Microsoft Project
版本。禁止把 Yuxi 的计算结果直接填入 expected；如需重新取证，使用以下 Windows PowerShell 命令：

```powershell
& 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' `
  -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File .\backend\scripts\capture_ms_project_schedule_golden_observation.ps1
```

在 `backend` 目录运行：

```powershell
.\.venv\Scripts\python.exe scripts\verify_schedule_golden_case.py
```

当前 S3 命令应返回退出码 `0`、`gate_status=PASSED` 和 `external_observation_status=PASSED`。
退出码 `3` / `gate_status=PENDING` 表示新夹具的人工 expected 尚未确认；`1` / `FAILED` 表示夹具或日期
不一致。S4 的每个新语义切片仍需单独建立和确认对应 expected。

S4 首个正 Lag 切片的输入保存在
`backend/test/data/schedule/microsoft_project_s4_positive_lag_golden_case.json`。语义仅限统一项目日历、
FS 正 Lag 和 ASAP 自动任务，Lag 按项目日历的工作分钟推进；Microsoft Project 16.0 黄金 expected 覆盖
`+120m` 跨夜间、`+600m` 跨完整工作日和 `+480m` 跨周末/午休边界。使用同一校验脚本的
`--case test/data/schedule/microsoft_project_s4_positive_lag_golden_case.json` 参数复核，当前同样应返回
`PASSED`；负 Lag 继续明确 blocked。

### 5. 使用 Agent 解释 Issue

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
| `LAG_CALENDAR_POLICY_UNSPECIFIED` | 来源 Audit 缺少非零 Lag 日期检查语义 | blocker |

零 Lag 日期关系使用以下精确定义：

| 类型 | 条件 |
|---|---|
| FS | `successor.start >= predecessor.finish` |
| SS | `successor.start >= predecessor.start` |
| FF | `successor.finish >= predecessor.finish` |
| SF | `successor.finish >= predecessor.start` |

第一阶段来源 Audit 对非零 Lag 统一记为 skipped，不执行近似判断或“明显不合理”回退。这与 v2 CPM
引擎按统一项目日历计算 FS 正 Lag 是两个独立能力边界：引擎可重算受支持输入，不表示来源日期已经通过
非零 Lag 合规检查。

## 查询 API

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/schedule/snapshots` | 提交并审查 Snapshot |
| GET | `/api/schedule/snapshots` | 分页列出当前用户的 ready Snapshot |
| GET | `/api/schedule/snapshots/{id}` | 读取 Snapshot 元数据和规范化正文 |
| GET | `/api/schedule/snapshots/{id}/audit` | 读取 Statistics、Capability 和摘要 |
| GET | `/api/schedule/snapshots/{id}/issues` | 按分类、等级分页读取 Issue |
| GET | `/api/schedule/issues/{issue_id}` | 读取 Issue、任务和直接上下游证据 |
| POST | `/api/schedule/snapshots/{id}/recalculate-automatic-downstream` | 生成最小正向重算 Candidate 或返回 blocked |
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

### 第二个及后续真实案例预检

新案例提交前先运行结构预检。预检只校验 `canonical_schedule_v2.2` 并输出内容指纹、数量、树深度、关系类型/Lag 分布、汇总依赖所在端和相对基线的结构差异；不会输出项目名、任务名或对象 ID，也不会提交 Snapshot、生成 Candidate 或应用 Delivery。

```powershell
cd backend
& '.venv\Scripts\python.exe' scripts/preflight_schedule_case.py `
  'D:\private\case-b.json' `
  --case-type real `
  --baseline 'test\data\schedule\schedule_v2_2_sanitized.json'
```

`--case-type` 必须显式选择：

- `real`：独立真实业务来源，才有资格进入业务迁移验收；
- `sanitized`：脱敏回归样例，只能验证工程协议；
- `synthetic`：合成边界样例，只能验证工程协议。

当暂时没有第二个真实案例时，可以生成确定性的合成案例 S，继续验证协议迁移能力：

```powershell
cd backend
& '.venv\Scripts\python.exe' scripts/generate_synthetic_schedule_case.py `
  --output test/data/schedule/schedule_v2_2_synthetic_case_s.json
& '.venv\Scripts\python.exe' scripts/preflight_schedule_case.py `
  test/data/schedule/schedule_v2_2_synthetic_case_s.json `
  --case-type synthetic `
  --purpose protocol-regression `
  --baseline test/data/schedule/schedule_v2_2_sanitized.json
```

预检现在区分两个结论：`engineering_protocol_precheck_passed` 用于工程协议回归，`automated_migration_precheck_passed` / `business_migration_eligible` 用于真实业务迁移。结构合格的 `synthetic` 案例可以让协议回归命令返回 `0`，但业务迁移仍返回 `3`，并明确输出 `CANNOT_REPLACE_INDEPENDENT_REAL_CASE_B`。生成数据自身声明 `format=SYNTHETIC_TEST_DATA`；即使命令行错误改标为 `real`，也会被 `DECLARED_SYNTHETIC_SOURCE` 阻断。不得用它关闭案例 B 或 G2/生产通用性门禁；案例 B 缺失不阻塞 S3 工程迭代。

脱敏 Fixture 与案例 A 结构一致，可作为不暴露私有内容的结构基线，但不能计作第二个真实案例。案例 B 自动预检通过还不代表业务验收通过；仍需人工确认来源确实独立、工作台任务名称可读，并通过同一页面完成 `Issue → 确认 → Candidate → Audit/Diff → Decision → Delivery → 新 Source`。

脚本退出码：`0` 表示所选 `--purpose` 的预检通过，`2` 表示 JSON 或契约无效，`3` 表示契约有效但所选用途的门禁未通过。真实 Snapshot 提交流水线必须使用默认的 `business-migration`，只允许真实案例退出码 `0` 进入提交阶段；`protocol-regression` 只用于自动测试或受控工程验证。

新 Source 回流后，在原 Candidate Drawer 点击“查看回流验收证据”。只读接口 `GET /api/schedule/candidates/{candidate_snapshot_id}/acceptance-evidence` 聚合基础 Snapshot、Candidate、Decision、最新 Snapshot、Patch 和两次 Audit，返回：

- `pending`：尚未发现新的 Source Snapshot；
- `passed`：16 项检查全部通过，包括 Candidate 有效且已接受、未运行 Engine、基础 Source Hash 未变、基础与回流对象 Hash 均匹配数据库记录、回流使用新的 request/external snapshot/external revision/source snapshot 身份、Patch 准确落地、目标问题消失、无统计/能力漂移、无新增 Blocker、旧 Candidate 已过期；
- `failed`：已存在新 Snapshot，但至少一项检查失败。

验收证据只读，不会自动接受 Candidate、应用 Delivery 或修改 Source。它要求四类版本身份全部变化，避免把同项目任意一个较新的 Snapshot 误认成本次 Delivery 回流；案例 B 业务验收要求该状态为 `passed`，同时保留业务人员对来源独立性、任务名称可读性和关系合理性的人工确认。

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

S3 零 Lag、S4 正 Lag、SS/FF/SF、SNET/FNET 与手工/locked task 已按 v5 受限 Profile 完成；两层嵌套汇总任务日期滚动已按 v6 Profile 完成；反向日期、总浮时、自由浮时和关键标识已按 v7 Profile 完成。案例 B 和完整生产治理仍在 G2 关闭。

### 如何建立 Microsoft Project expected（SS/FF/SF 示例）

SS/FF/SF 黄金夹具位于
`backend/test/data/schedule/microsoft_project_s4_relation_types_golden_case.json`，目标 Profile 为
`yuxi-forward-unified-calendar-fs-ss-ff-sf-positive-lag-snet-fnet-v4`。夹具使用统一项目日历和 8 个合成任务，
分别覆盖 SS/FF/SF 的零 Lag 与 `+120m`，不包含真实业务数据。

从仓库根目录独立重放 Microsoft Project：

```powershell
& 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' `
  -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File .\backend\scripts\capture_ms_project_schedule_golden_observation.ps1 `
  -CasePath .\backend\test\data\schedule\microsoft_project_s4_relation_types_golden_case.json
```

脚本新建不保存的 Project，按夹具设置日历、任务、关系和 Lag，调用 Project 自身重算，并输出：

- `task_relations`：Project 实际接受的 Predecessors，例如 `2SS+120 分钟工时`；
- `task_dates`：每个任务的 Start/Finish；
- Microsoft Project 版本、日历和捕获时间。

脚本不调用 Yuxi 引擎、不读取 Yuxi 结果、不修改夹具，也不保存 MPP。重放后先校验 observation：

```powershell
Set-Location backend
& '.venv\Scripts\python.exe' scripts\verify_schedule_golden_case.py `
  --case test/data/schedule/microsoft_project_s4_relation_types_golden_case.json `
  --observation-only
```

当前 expected 已人工确认；完整门禁（不带 `--observation-only`）应返回退出码 `0`、
`gate_status=PASSED`、`external_observation_status=PASSED`。Microsoft Project 16.0 黄金结果为：

| 任务 | 关系 | 工期 | Start | Finish |
|---|---|---:|---|---|
| A-种子任务 | 无 | 480m | 2026-09-01 08:00 | 2026-09-01 17:00 |
| B-关系前置任务 | A FS+0m | 480m | 2026-09-02 08:00 | 2026-09-02 17:00 |
| C-SS零Lag | B SS+0m | 240m | 2026-09-02 08:00 | 2026-09-02 12:00 |
| D-SS正Lag | B SS+120m | 240m | 2026-09-02 10:00 | 2026-09-02 15:00 |
| E-FF零Lag | B FF+0m | 240m | 2026-09-02 13:00 | 2026-09-02 17:00 |
| F-FF正Lag | B FF+120m | 240m | 2026-09-02 15:00 | 2026-09-03 10:00 |
| G-SF零Lag | B SF+0m | 240m | 2026-09-01 13:00 | 2026-09-02 08:00 |
| H-SF正Lag | B SF+120m | 240m | 2026-09-01 15:00 | 2026-09-02 10:00 |

以上时间均为 `Asia/Shanghai`。本次建立 expected 的过程为：

1. 将 `external_observation.task_dates` 原样复制到 `expected.task_dates`；
2. 将状态改为 `CONFIRMED_BY_MS_PROJECT`；
3. 填写确认人、确认时间和 Microsoft Project 版本；
4. 运行 `--observation-only`，返回 `PASSED` 和退出码 `0`；
5. 实现对应 Profile 后去掉 `--observation-only` 运行同一门禁，Yuxi 与 expected 一致并返回 `PASSED`。

禁止手算日期后填写 expected，也禁止从 Yuxi 输出复制 expected。后续手工/locked task 等切片沿用相同流程；
若重放产生日期漂移、关系回显不含预期语义，先调查 Microsoft Project 输入和语义，不开始对应切片编码。

### SNET/FNET 已确认 observation

SNET/FNET 夹具位于
`backend/test/data/schedule/microsoft_project_s4_constraints_golden_case.json`，目标 Profile 为
`yuxi-forward-unified-calendar-fs-ss-ff-sf-positive-lag-snet-fnet-v4`。夹具覆盖 SNET/FNET 分别晚于和早于
FS 依赖下界的组合，用于确认最终日期取约束与依赖中的更严格下界。

重放命令：

```powershell
& 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' `
  -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File .\backend\scripts\capture_ms_project_schedule_golden_observation.ps1 `
  -CasePath .\backend\test\data\schedule\microsoft_project_s4_constraints_golden_case.json
```

脚本除关系和任务日期外，还回显 Microsoft Project 实际保存的约束：ASAP=`0`、SNET=`4`、FNET=`6`。
当前 Microsoft Project 16.0 observation 已三次稳定重放：

| 任务 | 约束 | FS 依赖 | Start | Finish |
|---|---|---|---|---|
| A-种子任务 | ASAP | 无 | 2026-09-01 08:00 | 2026-09-01 17:00 |
| B-ASAP依赖基线 | ASAP | A FS+0m | 2026-09-02 08:00 | 2026-09-02 12:00 |
| C-SNET晚于依赖 | SNET 2026-09-03 13:00 | A FS+0m | 2026-09-03 13:00 | 2026-09-03 17:00 |
| D-SNET早于依赖 | SNET 2026-09-01 13:00 | A FS+0m | 2026-09-02 08:00 | 2026-09-02 12:00 |
| E-FNET晚于依赖 | FNET 2026-09-04 12:00 | A FS+0m | 2026-09-04 08:00 | 2026-09-04 12:00 |
| F-FNET早于依赖 | FNET 2026-09-01 17:00 | A FS+0m | 2026-09-02 08:00 | 2026-09-02 12:00 |

以上时间均为 `Asia/Shanghai`。在 `backend` 目录运行：

```powershell
& '.venv\Scripts\python.exe' scripts\verify_schedule_golden_case.py `
  --case test/data/schedule/microsoft_project_s4_constraints_golden_case.json `
  --observation-only
```

用户已于 `2026-08-13T15:41:21+08:00` 确认上表、统一日历、6 个任务工期、5 条 FS 关系，
以及 Project 回显的约束类型和日期；observation 已原样回填 expected。完整 v4 门禁当前返回退出码 `0`、
`gate_status=PASSED`、`external_observation_status=PASSED`。后续切片仍须建立各自的
Microsoft Project observation/expected，不能复用本表推断语义。

### 手工/locked task 与冲突已确认 observation

下一切片夹具位于
`backend/test/data/schedule/microsoft_project_s4_manual_locked_golden_case.json`，目标 Profile 暂定为
`yuxi-forward-unified-calendar-fs-ss-ff-sf-positive-lag-snet-fnet-manual-v5`。该夹具冻结两条 Project 原生行为：

- 无入边手工任务保留用户输入日期，并作为自动后续任务的 FS 锚点；
- 手工任务输入日期早于 FS 依赖下界时，Project 将其调整到依赖下界，自动后续任务继续从调整后日期传播。

Microsoft Project 16.0 COM 已三次独立重放一致：

| 任务 | 模式 | FS 依赖 | 输入 Start | Project Start | Project Finish |
|---|---|---|---|---|---|
| A-自动种子任务 | 自动 | 无 | - | 2026-09-01 08:00 | 2026-09-01 17:00 |
| B-无入边手工日期锚点 | 手工 | 无 | 2026-09-03 08:00 | 2026-09-03 08:00 | 2026-09-03 12:00 |
| C-手工锚点后的自动任务 | 自动 | B FS+0m | - | 2026-09-03 13:00 | 2026-09-03 17:00 |
| D-手工任务早于依赖冲突 | 手工 | A FS+0m | 2026-09-01 08:00 | 2026-09-02 08:00 | 2026-09-02 12:00 |
| E-冲突手工任务后的自动任务 | 自动 | D FS+0m | - | 2026-09-02 13:00 | 2026-09-02 17:00 |

重放与证据校验：

```powershell
& 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' `
  -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File .\backend\scripts\capture_ms_project_schedule_golden_observation.ps1 `
  -CasePath .\backend\test\data\schedule\microsoft_project_s4_manual_locked_golden_case.json

Set-Location backend
& '.venv\Scripts\python.exe' scripts\verify_schedule_golden_case.py `
  --case test\data\schedule\microsoft_project_s4_manual_locked_golden_case.json `
  --observation-only
```

用户已于 `2026-08-13T16:30:48+08:00` 确认 observation，expected 已从 Project 结果原样回填。
完整 v5 门禁当前返回退出码 `0`、`gate_status=PASSED`、`external_observation_status=PASSED`。这里的
`locked_task_ids` 不是 Microsoft Project 原生字段，而是 Yuxi 重算请求的授权边界：locked 日期不得移动，
为满足依赖必须移动 locked task 时 Candidate 为 `invalid` 且 Delivery 不允许应用。普通手工任务按 Project
基线计算，Source 日期仍不原地修改。

### 汇总任务日期滚动已通过 v6 门禁

第六套黄金夹具位于
`backend/test/data/schedule/microsoft_project_s4_summary_rollup_golden_case.json`，外部 Oracle 包位于
`MSP-006-SUMMARY-ROLLUP-v2_Yuxi-v6/`。v6 保留 v5 的活动任务正向计算，再按 `outline_level`
从深到浅处理汇总任务：开始时间取直接子任务最早开始，完成时间取直接子任务最晚完成。汇总任务不参与依赖，
来源汇总日期和 `source_calculation` 只作为来源事实保留，不作为引擎答案。

在 `backend` 目录运行：

```powershell
& '.venv\Scripts\python.exe' scripts\verify_schedule_golden_case.py `
  --case test\data\schedule\microsoft_project_s4_summary_rollup_golden_case.json
```

当前返回退出码 `0`、`gate_status=PASSED`、`external_observation_status=PASSED`。门禁覆盖 5 个任务、
2 个嵌套汇总任务、最大层级 3 和两条直接子级滚动断言；生产重算 Candidate 的 `task_dates` 同时包含
活动任务和汇总任务，Source 保持不变。

### 反向日期、浮时和关键标识已通过 v7 门禁

第七套黄金夹具位于
`backend/test/data/schedule/microsoft_project_s4_reverse_float_critical_golden_case.json`。夹具使用 5 个自动活动任务构造
FS 零 Lag 分叉/汇合网络，专门区分关键长分支和“短工作 → 短评审”非关键分支。Microsoft Project 16.0
在两个全新 COM 会话中的 task dates、slack 和 critical 字段完全一致；COM `TotalSlack`、`FreeSlack`
数值按工作分钟记录。短工作结果为总浮时 480、自由浮时 0，短评审为 480/480，启动、关键长分支和汇合交付
总浮时均为 0 且关键标识为 true。

在 `backend` 目录运行：

```powershell
& '.venv\Scripts\python.exe' scripts\verify_schedule_golden_case.py `
  --case test\data\schedule\microsoft_project_s4_reverse_float_critical_golden_case.json
```

当前返回退出码 `0`、`gate_status=PASSED`、`external_observation_status=PASSED`。v7 完整继承 v6 的关系类型、
正 Lag、约束、手工/locked 和汇总滚动能力；第七套夹具只隔离验证新增反向与浮时语义，不缩小生产输入范围。
活动任务从候选项目完成日期沿依赖图反向计算，`critical = total_slack_minutes <= 0`；汇总任务结果按直接子任务
自底向上投影，汇总任务仍不参与依赖。Source 和 `source_calculation` 保持不变。

排期审查页面会把 v7 Candidate 的任务名称/WBS、来源计划、最早/最晚日期、总浮时、自由浮时和关键标识
投影为结构化表格，并单独统计关键活动任务。原始日期 Patch 收入默认折叠的技术明细，用户无需阅读 JSON 即可
完成方案审阅。页面只展示后端 `engine_result`，不在浏览器中计算排期；受支持样例的浏览器主链路已确认
Candidate 可审阅、关键/非关键结果可区分，且重算前后 Source 内容哈希一致。
