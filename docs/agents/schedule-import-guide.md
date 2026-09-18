# 排期外部 JSON 导入指南

本指南说明如何使用独立 MPP Bridge 把 Microsoft Project 文件转换为正式来源 JSON，再交给 Yuxi，以及如何验证导入、审查和能力边界。Yuxi API/Worker 本身不读取 MPP、不依赖 Windows 或 Microsoft Project，也不负责把结果写回 MPP；需要 Project Desktop 的处理只发生在独立 Bridge 中。

业务用户如何阅读审查与 CPM 结果，见[排期审查与 CPM 重算用户及测试手册](./schedule-cpm-user-guide.md)；接口、存储和运维细节见[排期审查模块操作与维护手册](./schedule-audit.md)。

## 1. 接入边界

推荐链路：

```text
MPP / Project XML / 第三方系统
  ↓ MPP Bridge 或已验收的外部转换服务提取
带 schema_version 的来源 JSON
  ↓ POST /api/schedule/imports
来源契约校验 → Adapter → 严格 Canonical Schedule（v2.2-v2.8）
  ↓
Yuxi Audit / 页面 / Candidate / Delivery / Agent
```

Yuxi 只接收已提取的 JSON。仓库正式 MPP Bridge 使用 Windows、PowerShell 7.5+ 和 Microsoft Project Desktop COM；其他转换服务可以采用不同环境，但必须输出同一正式 Schema 并独立证明来源保真。Yuxi 核心服务不依赖这些组件。

当前注册的来源版本：

```text
microsoft_project_interchange_v1.1       # 正式维护，新增接入使用
microsoft_project_interchange_mock_v1.1  # 仅保留旧水泵站协议回归兼容
```

正式版本用于外部转换服务提交其实际提取到的 Microsoft Project 字段；旧 mock 版本不再用于新接入。两者都不代表 Yuxi 可以直接读取任意 MPP，也不允许把不同结构伪装成 v1.1。

### 1.1 当前契约的事实来源

`microsoft_project_interchange_v1.1` 是当前受维护的来源边界，Adapter 为 `microsoft_project_interchange_v1_1@1.6.0`。事实来源按以下顺序判断：

1. Pydantic 模型：`backend/package/yuxi/schedule/contracts/import_v1.py`；
2. Adapter：`backend/package/yuxi/schedule/importers/microsoft_project_interchange_v1_1.py`；
3. 正式来源版本的回归测试；旧水泵站样例只验证兼容路由；
4. 本指南中的业务边界说明。

正式 Import JSON Schema 已发布在 `backend/package/yuxi/schedule/contracts/schemas/microsoft_project_interchange_v1_1.schema.json`，MPP Bridge 分发包内同步包含 `schemas/microsoft_project_interchange_v1_1.schema.json`。Schema 与 Pydantic 模型共同构成结构事实来源；Pydantic 另负责跨字段、身份引用和父子环等语义校验。外部团队必须使用正式 `schema_version`，并在新增字段或改变解释时升级来源协议或 Adapter，不能只复制水泵站样例后自行扩展。

### 1.2 使用正式 MPP Bridge 投影

先对来源 MPP 执行 Bridge Extract 或 Roundtrip，取得通过门禁的 `mpp_bridge_snapshot_v1`，再执行：

```powershell
pwsh -File tools/mpp-bridge/src/Project-MppBridgeSnapshotToYuxi.ps1 `
  -SnapshotPath <snapshot.json> `
  -OutputDirectory <new-isolated-output-directory>
```

输出目录包含可提交给 Yuxi 的 `microsoft-project-interchange-v1.1.json` 和字段处置证据 `projection-report.json`。Projector 会验证 Snapshot Schema、Semantic Hash、unsupported 列表和 roundtrip eligibility；非空 Status Date 等 v1.1 无法表达的事实会明确阻断，不会静默丢弃。同一 Snapshot 重复投影的 Interchange 字节和 SHA-256 必须一致。

Projector 保留项目、日历、任务层级、来源日期、工期、依赖、Lag、约束和 Deadline，派生稳定项目 ID 及默认日/周工作分钟；Bridge 运行元数据和冗余显示字段只记录在字段处置报告中。它不会向来源 JSON 复制 Bridge statistics、capabilities 或 validation，Yuxi Adapter 必须自行重算。

### 1.3 责任、重试与清理策略

- MPP Bridge 或外部转换服务负责读取 MPP、保存后重开并重算、提供 MPP 哈希、时区和来源实际具备的字段；Yuxi API 不会再次启动 COM 验证提取过程。使用正式 Bridge 时，以其 Manifest、Snapshot、Projection Report 和 Roundtrip Report 作为外部证据链。
- Yuxi Schedule 维护者负责来源契约、Adapter 1.6、Canonical 映射、Capability、私有存储和版本兼容；Adapter 不合成来源没有提供的 inactive、Baseline、实际进度、Assignment 或费率。
- `422` 先修复来源并使用新 `request_id`；`409` 不覆盖旧幂等身份；存储型 `500` 使用完全相同的信封和 `request_id` 原样重试。
- 生产来源对象不自动删除，按项目数据保留策略成组清理数据库 Snapshot、Audit/Issue、Candidate 和 MinIO 对象；集成测试只能在隔离环境运行，并按测试用户和 Snapshot ID 清理，禁止只删除数据库或只删除对象存储的一侧。

## 2. 两种提交方式

人工导入时，优先在“排期审查”页面点击“导入排期”，选择不超过 10 MiB 的 JSON 文件。页面会根据根对象的 `schema_version` 自动选择 `/imports` 或 `/snapshots`，生成 `request_id`，并预填外部项目、快照和版本身份；用户确认后再提交。页面不接受 MPP，也不会在浏览器中解析 MPP。

系统间集成仍应直接调用下述 API，并由上游提供稳定的外部身份。

### 2.1 推荐：提交版本化来源 JSON

调用：

```http
POST /api/schedule/imports
Authorization: Bearer <token>
Content-Type: application/json
```

信封：

```json
{
  "request_id": "water-pump-import-20260814-001",
  "external_project_id": "water-pump-station-001",
  "external_snapshot_id": "water-pump-station-001-v1.1",
  "external_revision": "v1.1",
  "document": {
    "schema_version": "microsoft_project_interchange_v1.1"
  }
}
```

`document` 必须是完整来源文档，上例只展示版本字段。当前案例的完整结构可参考仓库根目录：

```text
Microsoft_Project_水泵站排期_MOCK_v1.1/
  Microsoft_Project_水泵站排期_MOCK_v1.1.json
  manifest.json
  README.md
```

信封字段的用途：

| 字段 | 必填 | 用途 |
| --- | --- | --- |
| `request_id` | 是 | 当前用户下的幂等键 |
| `external_project_id` | 是 | 外部系统中的稳定项目身份，用于版本归组 |
| `external_snapshot_id` | 是 | 外部系统中的来源快照身份 |
| `external_revision` | 是 | 外部系统中的可读版本号 |
| `document` | 是 | 带 `schema_version` 的完整来源 JSON |

正式来源文档至少包含 `source`、`semantics`、`project`、`calendars`、`tasks` 和 `dependencies`；`statistics`、`capabilities`、`validation` 只是来源自报信息，不作为 Yuxi 结论。完整嵌套字段、枚举和限制以 Pydantic 模型为准。

来源文档允许未定义字段，但必须满足以下规则：

- `schema_version` 必须存在且为非空字符串；
- 已定义的核心字段缺失、类型错误或枚举错误会被拒绝；
- Task、Dependency 和 Calendar 引用必须有效；
- Task 父子层级不能成环；
- 未定义字段会保存在私有来源对象中，但不会自动参与 Audit 或 CPM；
- 来源自报 Statistics、Capabilities 和 Validation 不作为 Yuxi 结论。

通过上述契约只代表“JSON 结构可被当前 Adapter 处理”，不代表 MPP 提取过程已经可信，也不代表每个已定义字段都已参与计算。当前 mock Adapter 会读取 `opened_after_save` 和 `project_recalculated_after_reopen`；任一字段为 `false` 时，来源审查以 `SOURCE_FIDELITY_INVALID` 阻断，normalization report 同步记录该原因。但 Yuxi 仍不会独立打开 MPP、验证 COM 过程或证明这两个布尔声明为真；这些事实仍由外部转换服务和案例 manifest 负责。

### 2.2 兼容：直接提交严格 Canonical

已经能够稳定生成完整 `canonical_schedule_v2.2` 至 `canonical_schedule_v2.8` 的调用方，可以使用：

```http
POST /api/schedule/snapshots
```

该入口保持严格，不允许未知字段，也不经过来源 Adapter。不要为了让外部文档通过校验而把所有来源扩展字段塞进 Canonical；需要保留来源 JSON 数据语义副本及扩展字段时应使用 `/imports`。

## 3. 成功响应与三种状态

首次导入成功返回 `201`，相同内容的幂等重放返回 `200`。响应包含：

```json
{
  "schedule_snapshot_id": "<opaque-id>",
  "idempotent_replay": false,
  "source_schema_version": "microsoft_project_interchange_v1.1",
  "adapter_id": "microsoft_project_interchange_v1_1",
  "adapter_version": "1.6.0",
  "source_document_sha256": "sha256:<source-hash>",
  "canonical_snapshot_sha256": "sha256:<canonical-hash>",
  "normalization_report": {
    "preserved_fields": [],
    "ignored_for_audit": [],
    "ignored_for_calculation": [],
    "unsupported_semantics": []
  },
  "capabilities": {}
}
```

必须分别判断三种状态：

1. **外部接入成功**：来源 JSON 已通过当前契约、完成适配并保存；它不证明 MPP 提取正确；
2. **来源审查允许**：Yuxi 能基于 Adapter 生成的内部 Canonical 执行确定性 Audit；它不审查外部提取程序本身；
3. **CPM 重算允许**：当前完整语义落在受支持的 CPM Profile 内。

接入成功不等于全部来源字段都参与了审查，也不等于 CPM 可以运行。页面中的“外部 JSON 接入边界”会独立显示三个状态。

Capability 是“是否允许执行某项 Yuxi 能力”的事实来源；normalization report 只说明字段如何保留、忽略或标记为 unsupported。两者出现矛盾时必须停止验收并记录缺陷，不能任选一个有利结论继续。当前 Adapter 1.6 只有在来源任务真实携带 `active: false` 时才输出 `canonical_schedule_v2.8`，不会从日期、完成百分比或任务名称猜测 inactive；否则对可追溯的 MSO/FNLT 约束码、Deadline 或 required finish 输出 v2.5，结构化日历例外或多/任务日历输出 v2.4，仅包含里程碑时输出 v2.3，无上述语义时输出 v2.2。来源没有明确边界字段时，Adapter 不猜测 `boundary_role`。v2.5 使用 Engine Profile v12，单一项目日历例外使用 v10，多/任务日历与可解析继承使用 v11；未知父日历、继承环、无工作时间和冲突例外仍明确阻断。

页面溯源区会展示来源类型、提取方式、来源 JSON SHA-256、Canonical SHA-256 和 Adapter。MPP 计划日期标为“Microsoft Project 来源计划日期”，测试套件的 `YUXI_TEST_SUITE_ADAPTER` 与 `YUXI_TEST_SUITE_ENGINE_REFERENCE` 均标为“Suite Reference 测试参考日期”，重算/优化结果标为“Yuxi 候选计算日期”；Candidate 同时显示基础 Source Hash，并明确不覆盖 Source。Engine Reference 是通过冻结 Oracle 门禁后的 Yuxi 测试参考计算，不是 Microsoft Project 来源事实。`cpm_recalculation` 被阻断时，重算与目标优化按钮会禁用并展示原因。

Canonical v2.6 可由 `/api/schedule/snapshots` 直接提交可追溯的 status date、任务状态、actual start/finish、remaining duration 和 Baseline 0。Profile v13 处理 COMPLETED + NOT_STARTED；存在 IN_PROGRESS 时路由到 Profile v14，固定 actual start，并从 status date 与网络要求边界之后排 remaining work。当前 Microsoft Project Interchange v1.1 来源协议没有这些字段，Adapter 1.6 不会猜测或合成 v2.6。

Canonical v2.7 可由 `/api/schedule/snapshots` 直接提交强类型 WORK/EQUIPMENT Resource 与 Assignment，并提供 units、max units 和标准小时费率。资源 Profile 在 CPM 之后只读输出超配区间和 Assignment 成本数值，不执行自动均衡。当前 Microsoft Project Interchange v1.1 来源协议没有可靠的 Assignment、容量和费率字段，Adapter 1.6 不会猜测或合成 v2.7。

Canonical v2.8 冻结 inactive 事实。inactive 任务保留来源计划日期用于追溯，但不进入 CPM、关键路径、汇总滚动、资源分析或工期优化；其 `calculation_status` 为 `excluded_inactive`。来源 Assignment 不能指向 inactive 任务，inactive 任务也不能携带实际进度事实。依赖触及 inactive 时不会被自动跨接，Capability 以 `INACTIVE_TASK_DEPENDENCIES` 阻断，必须在 Dependency Workbench 中明确选择 active 叶子关系后再生成 Candidate。

## 4. 幂等、双对象和双哈希

同一用户下，`request_id` 是导入幂等键：

- 同一 `request_id`、相同来源文档：返回原结果；
- 同一 `request_id`、来源文档变化：返回 `409 SCHEDULE_IDEMPOTENCY_CONFLICT`；
- 同一 `request_id`、任一外部项目/快照/版本身份变化：返回 `409 SCHEDULE_IDEMPOTENCY_CONFLICT`；
- 即使两个不同来源文档生成相同 Canonical，只要来源哈希不同，仍视为冲突；
- `/imports` 和 `/snapshots` 不能交叉复用同一个 `request_id`。

调用方重试必须保持整个信封完全一致。外部身份不会写入来源或 Canonical 内容哈希，但服务端会独立比较已保存的 `external_project_id`、`external_snapshot_id` 和 `external_revision`。

私有对象存储中分别保存：

```text
{owner_uid}/{schedule_snapshot_id}/source-document.json
{owner_uid}/{schedule_snapshot_id}/snapshot.json
```

三个容易混淆的哈希：

| 字段 | 实际含义 |
| --- | --- |
| `document.source.mpp_sha256` | 外部转换服务声明的 MPP 文件哈希；Yuxi 当前不会读取 MPP，因此不会独立复算 |
| `source_document_sha256` | Yuxi 对 `document` 解析后按键排序、紧凑序列化得到的 JSON 字节计算的哈希 |
| `canonical_snapshot_sha256` | Adapter 生成的严格 Canonical JSON 哈希，用于 Audit、Candidate 和内部版本比较 |

`source-document.json` 保存的是来源 `document` 的 JSON 数据语义副本，不是原始 HTTP 请求字节；空白、缩进和对象字段顺序不会保留。普通用户接口不会返回该对象正文或对象路径。

## 5. 当前水泵站案例的预期结果

导入 `Microsoft_Project_水泵站排期_MOCK_v1.1.json` 后，应得到：

| 检查项 | 预期 |
| --- | --- |
| 任务 | 21 |
| 汇总任务 / 叶子任务 / 里程碑 | 5 / 16 / 4 |
| 依赖 | 19（16 FS、3 SS） |
| 正 Lag / 负 Lag | 3 / 0 |
| 开放起点 / 开放终点 | 1 / 1 |
| Audit 日期检查 | 19 checked、0 skipped、0 violation（正 Lag 已按冻结的统一项目日历工作分钟检查） |
| 来源审查 | 允许 |
| CPM 重算 | 允许，使用 milestone Engine Profile v8 |
| 资源能力 | 阻断，来源没有 Assignment |

水泵站案例验证的是该固定样例的导入、规范化、Audit 和 milestone CPM 行为。它的 manifest 记录了 MPP、JSON 和观测文件哈希，但 Yuxi 导入接口本身不会复算 MPP 哈希。包含里程碑时输出 Canonical v2.3；提取证据为 false、无里程碑时保持 v2.2，以及外部身份变化均由独立单元回归覆盖。

## 6. 推荐测试流程

### 6.1 契约与 Adapter 单元测试

在 `backend` 目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest test/unit/schedule/test_schedule_import_adapter.py
```

重点验证缺少必填字段失败、未知字段保留、未知引用和父子环拒绝、适配结果确定性，以及来源声明不参与 Yuxi 结论。

当前回归已覆盖提取证据为 false、无里程碑时报告条件化和同一请求改变信封身份。正式扩展来源格式前还必须为新版本补充来源类型码与关系类型一致性、日历解析策略及其专属语义边界；固定水泵站测试通过不代表任意新来源版本已经验证。

#### 大型 Suite 严格门禁

`Yuxi_大型复杂排期测试套件_v1` 是冻结测试证据，不是生产来源协议。仓库使用 `schedule_engine_large_suite_adapter_v2` 强类型适配契约读取它：顶层 `test_profile` 以及任务 `wbs`、`outline_level` 都是必填字段，未知字段直接拒绝，不使用 `extra="allow"`，也不会丢弃字段后继续运行。冻结文件原有的 `schema_version=schedule_engine_test_input_v1` 保持不变，以免改写历史证据。

在 `backend` 目录运行严格门禁，输出目录必须位于冻结 Suite 之外：

```powershell
.\.venv\Scripts\python.exe scripts\run_schedule_complex_suite.py `
  --suite-root ..\Yuxi_大型复杂排期测试套件_v1 `
  --actual-root <外部结果目录>
```

Runner 会为每个案例生成 `actual.json`、`yuxi_audit.json`、`gate.json`，并在结果根目录生成 `suite_run_summary.json`。`gate_status` 只有三种：`PASSED`、`UNSUPPORTED`、`FAILED`；只有冻结 Oracle 完全一致、执行成功且存在 calculated Engine Result 时，`ui_export_eligible` 才可能为 true。

当前门禁结论如下：

| 案例 | 结论 | 原因 |
| --- | --- | --- |
| L01 | Unsupported | 大型资源冲突 Oracle 尚未对齐；资源 + Baseline 组合尚未冻结 |
| L02 | Unsupported | 冻结 Oracle 的 325 条 Assignment 对冲突仍可逐条重建，但遗漏 135 个“多项合计超配、任意两项不超配”的时间片；另有 4 个负 FF Lag 里程碑在午休/日终反向位移边界不一致。来源和 Oracle 均不改写，分别以累计资源 Oracle 与工作时间边界原因 fail-closed |
| L03 | Unsupported | 跨午夜与 `00:00 → 00:00` 的 24×7 日历尚未支持 |
| L04 | Unsupported | 资源 + Baseline + 实际进度组合尚未冻结 |
| L05 | Passed / Validation Failed | 55 项非法输入与冻结 Oracle 完全一致，Engine 未调用 |
| L06 | Passed | 500 活动、500 Assignment、20 资源、100 条 Assignment 对冲突、500 条两位小数成本及全部日期与冻结 Oracle 完全一致 |

当前大型 Suite 汇总为 2 Passed、4 Unsupported、0 Failed，只有 L06 满足 `ui_export_eligible=true`。L06 的正式 UI 文件已通过页面自身导入校验：从小型快照切换到 673 行表格和 2,615 个甘特元素用时 1.32 秒，页面约 9,958 个 DOM 节点，控制台无 Error/Warning；测试 Snapshot 与对象已清理。该结果只证明当前开发机上的 500 活动 Suite 基线，不代表更大规模或真实业务项目已经通过。页面当前仍为全量 DOM，主页面高度约 88,569px，规模继续增大时应优先评估虚拟化。L02 及其余 Unsupported 案例不会导出，也不能用删资源、改 Oracle 或派生简化样例替代。

冻结包内的历史 `generate_large_suite.py` 和 `adapter_mapping.json` 继续作为原始证据保存，不作为当前执行入口。仓库内重建入口不依赖外部 `work/.../generate_suite.py`，会先验证来源 manifest，再从仓库冻结证据逐字节重建到新的外部目录，并拒绝覆盖：

```powershell
.\.venv\Scripts\python.exe scripts\rebuild_schedule_large_suite.py `
  --source-root ..\Yuxi_大型复杂排期测试套件_v1 `
  --output-root <新的外部目录>
```

### 6.2 真实 API、PostgreSQL 和 MinIO

```powershell
.\.venv\Scripts\python.exe -m pytest test/integration/api/test_schedule_router.py
```

Import 用例覆盖首次 `201`、重放 `200`、冲突 `409`、双对象、双哈希、用户隔离和来源对象私有访问。

对两份已完成 Projector 的正式项目产物执行 Y0 实际门禁时，在 `backend` 目录设置本地绝对路径后运行：

```powershell
$env:YUXI_Y0_A_INTERCHANGE='<A microsoft-project-interchange-v1.1.json 绝对路径>'
$env:YUXI_Y0_B_INTERCHANGE='<B microsoft-project-interchange-v1.1.json 绝对路径>'
.\.venv\Scripts\python.exe -m pytest test/integration/api/test_y0_ab_live.py -q
```

该门禁验证两项目的 201/200 幂等行为、来源/Canonical 双对象和双哈希、Canonical 版本/统计及来源保真，并在结束时删除本次 MinIO 对象、Snapshot 级联记录和临时用户；未设置两个输入路径时测试会跳过，不会把固定的本地运行目录当成仓库夹具。

其他现有集成测试会写入真实开发用 PostgreSQL 和 MinIO，但尚未统一自动清理 Schedule 快照与对象。只能在隔离的开发测试环境运行；运行后应由维护者按本次测试用户和 Snapshot ID 清理双对象及数据库记录，禁止在共享生产基础设施直接执行。上述 Y0 A/B 门禁和 E2E 用例已有自己的清理流程。

### 6.3 Import 到 Agent 端到端测试

```powershell
.\.venv\Scripts\python.exe -m pytest test/e2e/test_schedule_audit_agent_e2e.py -m e2e
```

该用例导入水泵站来源 JSON，读取 Audit 和 Issue，再验证 Agent 不会把来源 Validation 或 normalization report 中 ignored/unsupported 的字段说成已参与审查或计算。测试结束会清理临时 Agent、对话、双对象和数据库记录。

默认使用当前默认智能体的模型；需要指定可用测试模型时，可在运行前设置 `E2E_MODEL`（格式为 `provider:model`），仅影响本次创建的临时智能体。

### 6.4 正式 Interchange 性能基线

性能脚本测量 `正式 Interchange → Adapter Normalize → Canonical → Audit → CPM` 完整只读链路，不写 PostgreSQL、MinIO 或输出文件。在 `backend` 目录运行：

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_schedule_interchange.py `
  --input <A microsoft-project-interchange-v1.1.json> `
  --input <B microsoft-project-interchange-v1.1.json> `
  --iterations 50 `
  --max-p95-ms 100
```

2026-08-24 本机基线如下；每个案例先预热 2 次，再统计 50 次，Engine 均为 `calculated`：

| 案例 | 规模 | Normalize p95 | Audit p95 | CPM p95 | 端到端 p95 | 门禁 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| A | 46 任务 / 48 依赖 / 2 日历 | 14.353 ms | 3.577 ms | 11.487 ms | 25.382 ms | PASS |
| B | 62 任务 / 65 依赖 / 2 日历 | 16.857 ms | 3.797 ms | 11.821 ms | 31.785 ms | PASS |

脚本输出只以 MPP SHA-256 前 12 位标识案例，不打印本地路径或项目名。该结果证明当前 A/B 输入的服务内只读计算性能，不替代 HTTP、数据库、对象存储、Worker 或浏览器性能验收；大型 Suite 浏览器门禁仍受 `ui_export_eligible=0` 阻断。

### 6.5 页面验收

1. 打开“排期审查”，点击“导入排期”，选择案例 JSON；
2. 确认页面识别为“来源 JSON”，核对自动预填的外部身份后提交；
3. 导入成功后确认页面自动选中新快照，并核对来源版本和 Adapter 版本；
4. 确认“外部接入”为已接入；
5. 确认“来源审查”为允许；
6. 确认“CPM 重算”为允许，生成重算 Candidate 后里程碑开始与完成时刻相等；
7. 核对 preserved、ignored 和 unsupported 的数量，并确认 Capability 与报告没有矛盾；
8. 确认页面没有展示来源未知字段值；
9. 点击“Agent 解释”，确认只生成草稿且不会自动发送。

2026-08-25 技术验收已在已登录的新页面完成：生产门禁提示、Bridge 后 JSON 导入说明、Microsoft Project 来源日期标签、三段接入状态、来源/Canonical 双哈希、字段数量脱敏以及表格/Gantt 均正常，新页面无应用控制台错误。真实 Schedule Agent E2E 同轮为 `1 passed`，Worker 实际消费任务且用例完成数据清理。该结果是技术验收，不替代大型真实 UI 性能和业务负责人签字。

## 7. 常见错误

| HTTP | 错误码 | 含义与处理 |
| --- | --- | --- |
| 413 | `SCHEDULE_BODY_TOO_LARGE` | 请求超过 10 MiB；缩减来源文档或拆分项目 |
| 422 | `SCHEDULE_PREFLIGHT_FAILED` | 直接提交 Canonical 时，按 `detail.errors[].object_ref/object_refs/details` 一次修复重复 ID、未知引用或环路；不会创建 Snapshot/Candidate |
| 422 | `SCHEDULE_CONTRACT_INVALID` | 直接提交 Canonical 时，按 `detail.errors[].path` 修复字段类型、枚举、日期或必填字段 |
| 422 | `SCHEDULE_IMPORT_CONTRACT_INVALID` | 按 `detail.errors[].path` 修复必填字段、类型、引用或层级 |
| 422 | `SCHEDULE_IMPORT_VERSION_UNSUPPORTED` | 没有注册对应 `schema_version` 的 Adapter |
| 422 | `SCHEDULE_IMPORT_SEMANTICS_UNSUPPORTED` | 来源使用了当前 Adapter 明确不支持的语义 |
| 409 | `SCHEDULE_IDEMPOTENCY_CONFLICT` | 同一 `request_id` 已对应不同来源；新业务提交应使用新 ID |
| 409 | `SCHEDULE_SUBMISSION_IN_PROGRESS` | 相同请求仍在处理中，稍后原样重试 |
| 500 | `SCHEDULE_DEPENDENCY_FAILURE` | PostgreSQL 或 MinIO 保存失败；使用同一请求重试 |

错误路径使用 JSON Pointer，例如 `/document/project/default_calendar_id`。响应和日志不会回显完整来源正文；部分结构错误消息可能包含用于定位的 Task、Dependency 或 Calendar ID，因此这些 ID 不应承载密码、Token 或其他敏感值。

## 8. 回写 MPP 的责任边界

MPP Bridge 的 Build 能从合格 Bridge Snapshot 创建新的 MPP，并已完成独立 Roundtrip 验收；它不是 Yuxi Delivery Adapter。Yuxi 当前不会直接生成或修改 MPP，也不会把 Candidate 自动反投影为 Bridge Snapshot。后续如需把 Delivery 写回 Microsoft Project，推荐由外部业务系统或独立转换服务完成：

1. 读取 Yuxi 的受控 Delivery；
2. 校验 Candidate 已接受、来源版本仍为 current、基础哈希一致；
3. 在新的来源副本上应用结果，不覆盖原始 MPP；
4. 由外部服务生成新的 MPP 或来源系统版本；
5. 再提取新的版本化 JSON，通过新的 `request_id` 和外部版本回流 Yuxi；
6. 使用回流验收证据确认 Source、Patch、Audit 和版本身份一致。

这条链路可以在 Yuxi 之外使用 Windows/COM；Yuxi API、Audit、Agent 和页面仍保持跨平台。

Yuxi 当前内置应用器只允许 `dependency_normalization + canonical_schedule_v2.2` 生成新的 Canonical Source 副本。自动重算、工期优化以及 v2.3+ 组合的 Delivery 仍可读取，但返回 `application_allowed=false` 和 `DELIVERY_ADAPTER_UNAVAILABLE`；不得把其中的日期或工期结果近似改写回来源。正式 Microsoft Project Adapter 只负责导入，不负责修改 MPP。

## 9. 安全与发布边界

- 来源 JSON 数据语义副本只保存在私有对象存储，不提供普通用户读取接口；
- 页面只显示扩展字段数量、原因代码和影响对象数量，不显示未知字段值；
- 日志不得记录 Token、来源全文、Notes 或未知字段值；
- 写入、Import 与读取错误响应已有回归，底层异常即使包含私有对象路径、bucket 或 token，也只返回稳定失败码和固定提示；
- 正式来源协议和 Adapter 已建立，但单个水泵站案例仍不代表任意 Microsoft Project 项目可生产接入；
- 项目方已批准 A/B 作为本计划两份独立受控真实项目参与技术验收；2026-08-25 项目负责人进一步以技术与业务双重角色批准 A/B 进入受控测试/UAT。输入包原有 `SYNTHETIC_BUSINESS_LIKE_TEST_DATA` provenance 继续保留；该批准不替代更大规模业务代表性、MPP 回写治理和生产批准；
- 当前生产结论继续保持 `PRODUCTION_NO_GO`。

## 10. A/B 受控试点评审边界

项目负责人已于 2026-08-25 以技术负责人和业务负责人双重角色批准 A/B 精确文件进入受控测试/UAT，但不允许宣布任意 MPP 已生产可用。当前状态为：

```text
APPROVED_FOR_CONTROLLED_AB_TESTING
UNRESTRICTED_PRODUCTION_NO_GO
```

受控试点仅包含：

- 使用已验收的 Bridge 对 A/B 执行 Extract、Build、Roundtrip 和正式 Interchange 投影；
- 将正式 Interchange 导入 Yuxi，执行 Audit、受支持的 CPM 和 Candidate 审阅；
- 始终保持 Source 不变，并展示来源 JSON、Canonical 与 Candidate 的独立身份和 Hash；
- 失败时使用相同 request ID 恢复或放弃本次试点记录，不修改原 MPP。

受控试点明确不包含：

- Resource + Baseline/实际进度组合、跨午夜或 24×7 日历；
- 把大型 Suite 的合成需求当作 A/B 的真实业务需求；
- 把 Yuxi Candidate 自动写回 MPP；
- 把 A/B 的性能与字段结论外推到更大项目或其他来源版本；
- 因 A/B 受控测试获批而对外宣称已生产上线。

2026-08-25 已重新执行 A/B 自清理真实 API 门禁：两项目首次导入 201、同信封重放 200、来源/Canonical 双对象与双哈希通过，随后删除 4 个 MinIO 对象和 2 条 Snapshot 及级联记录，结果为 `1 passed`。

本次批准及其双重角色签署已记录在评审文件中。每轮测试仍必须记录试点文件 SHA-256、Bridge/Adapter/Canonical/Engine 版本、允许操作、明确排除项、故障回退责任人和测试时间窗；启动前检查未完成时不得开始该轮测试。无论 A/B 测试状态如何，当前仍保持 `PRODUCTION_NO_GO`。
