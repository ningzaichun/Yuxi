# 排期外部 JSON 导入指南

本指南说明外部业务系统如何把已经从 MPP 或其他排期来源提取完成的版本化 JSON 交给 Yuxi，以及如何验证导入、审查和能力边界。Yuxi 不读取 MPP，不依赖 Windows 或 Microsoft Project，也不负责把结果写回 MPP。

业务用户如何阅读审查与 CPM 结果，见[排期审查与 CPM 重算用户及测试手册](./schedule-cpm-user-guide.md)；接口、存储和运维细节见[排期审查模块操作与维护手册](./schedule-audit.md)。

## 1. 接入边界

推荐链路：

```text
MPP / Project XML / 第三方系统
  ↓ 由外部转换服务提取
带 schema_version 的来源 JSON
  ↓ POST /api/schedule/imports
来源契约校验 → Adapter → 严格 canonical_schedule_v2.2
  ↓
Yuxi Audit / 页面 / Candidate / Delivery / Agent
```

Yuxi 只接收已提取的 JSON。转换服务可以运行在 Windows、Linux 或其他环境；只有读取 MPP 的那一环可能需要 Microsoft Project、COM 或第三方 SDK，Yuxi 核心服务不依赖这些组件。

当前注册的来源版本只有：

```text
microsoft_project_interchange_mock_v1.1
```

它用于当前水泵站案例及对应协议回归，不代表已经支持任意 Microsoft Project 文件。新增真实来源格式时，必须注册独立 `schema_version` 和版本化 Adapter，不能把不同结构伪装成当前 mock 版本。

### 1.1 当前契约的事实来源

当前 Import 契约仍是项目内部、面向 mock 协议回归的实现契约，尚未冻结为可长期兼容的对外标准。事实来源按以下顺序判断：

1. Pydantic 模型：`backend/package/yuxi/schedule/contracts/import_v1.py`；
2. Adapter：`backend/package/yuxi/schedule/importers/microsoft_project_interchange_v1_1.py`；
3. 水泵站完整样例及 `manifest.json`；
4. 本指南中的业务边界说明。

当前没有单独发布稳定的 Import JSON Schema。外部团队在正式对接前，必须先与 Yuxi 维护者冻结来源版本、必填字段、枚举、语义映射和兼容策略；不能只复制水泵站样例后自行扩展并视为长期协议。

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
    "schema_version": "microsoft_project_interchange_mock_v1.1"
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

当前 mock 来源文档至少包含 `source`、`semantics`、`project`、`calendars`、`tasks` 和 `dependencies`；`statistics`、`capabilities`、`validation` 只是来源自报信息，不作为 Yuxi 结论。完整嵌套字段、枚举和限制以 Pydantic 模型为准。

来源文档允许未定义字段，但必须满足以下规则：

- `schema_version` 必须存在且为非空字符串；
- 已定义的核心字段缺失、类型错误或枚举错误会被拒绝；
- Task、Dependency 和 Calendar 引用必须有效；
- Task 父子层级不能成环；
- 未定义字段会保存在私有来源对象中，但不会自动参与 Audit 或 CPM；
- 来源自报 Statistics、Capabilities 和 Validation 不作为 Yuxi 结论。

通过上述契约只代表“JSON 结构可被当前 Adapter 处理”，不代表 MPP 提取过程已经可信，也不代表每个已定义字段都已参与计算。当前 mock Adapter 会读取 `opened_after_save` 和 `project_recalculated_after_reopen`；任一字段为 `false` 时，来源审查以 `SOURCE_FIDELITY_INVALID` 阻断，normalization report 同步记录该原因。但 Yuxi 仍不会独立打开 MPP、验证 COM 过程或证明这两个布尔声明为真；这些事实仍由外部转换服务和案例 manifest 负责。

### 2.2 兼容：直接提交严格 Canonical

已经能够稳定生成完整 `canonical_schedule_v2.2` 的调用方，可以继续使用：

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
  "source_schema_version": "microsoft_project_interchange_mock_v1.1",
  "adapter_id": "microsoft_project_interchange_v1_1",
  "adapter_version": "1.0.0",
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

Capability 是“是否允许执行某项 Yuxi 能力”的事实来源；normalization report 只说明字段如何保留、忽略或标记为 unsupported。两者出现矛盾时必须停止验收并记录缺陷，不能任选一个有利结论继续。当前 mock Adapter 已对 `SOURCE_FIDELITY_INVALID` 和 `MILESTONE_UNSUPPORTED` 做条件化报告，但仍不能仅凭 `unsupported_semantics` 列表替代 Capability 判定。

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
| Audit 日期检查 | 16 checked、3 skipped、0 violation |
| 来源审查 | 允许 |
| CPM 重算 | 阻断，原因 `MILESTONE_UNSUPPORTED` |
| 资源能力 | 阻断，来源没有 Assignment |

水泵站案例验证的是该固定样例的导入、规范化、Audit 和阻断行为。它的 manifest 记录了 MPP、JSON 和观测文件哈希，但 Yuxi 导入接口本身不会复算 MPP 哈希。由于包含 4 个零工期里程碑，它不用于验证当前 CPM 成功主路径；提取证据为 false、无里程碑和外部身份变化由独立单元回归覆盖。

## 6. 推荐测试流程

### 6.1 契约与 Adapter 单元测试

在 `backend` 目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest test/unit/schedule/test_schedule_import_adapter.py
```

重点验证缺少必填字段失败、未知字段保留、未知引用和父子环拒绝、适配结果确定性，以及来源声明不参与 Yuxi 结论。

当前回归已覆盖提取证据为 false、无里程碑时报告条件化和同一请求改变信封身份。正式扩展来源格式前还必须为新版本补充来源类型码与关系类型一致性、日历解析策略及其专属语义边界；固定水泵站测试通过不代表任意新来源版本已经验证。

### 6.2 真实 API、PostgreSQL 和 MinIO

```powershell
.\.venv\Scripts\python.exe -m pytest test/integration/api/test_schedule_router.py
```

Import 用例覆盖首次 `201`、重放 `200`、冲突 `409`、双对象、双哈希、用户隔离和来源对象私有访问。

当前集成测试会写入真实开发用 PostgreSQL 和 MinIO，但尚未统一自动清理 Schedule 快照与对象。只能在隔离的开发测试环境运行；运行后应由维护者按本次测试用户和 Snapshot ID 清理双对象及数据库记录，禁止在共享生产基础设施直接执行。E2E 用例已有自己的清理流程。

### 6.3 Import 到 Agent 端到端测试

```powershell
.\.venv\Scripts\python.exe -m pytest test/e2e/test_schedule_audit_agent_e2e.py -m e2e
```

该用例导入水泵站来源 JSON，读取 Audit 和 Issue，再验证 Agent 不会把来源 Validation 或 normalization report 中 ignored/unsupported 的字段说成已参与审查或计算。测试结束会清理临时 Agent、对话、双对象和数据库记录。

### 6.4 页面验收

1. 打开“排期审查”，点击“导入排期”，选择案例 JSON；
2. 确认页面识别为“来源 JSON”，核对自动预填的外部身份后提交；
3. 导入成功后确认页面自动选中新快照，并核对来源版本和 Adapter 版本；
4. 确认“外部接入”为已接入；
5. 确认“来源审查”为允许；
6. 确认“CPM 重算”为阻断且原因是 `MILESTONE_UNSUPPORTED`；
7. 核对 preserved、ignored 和 unsupported 的数量，并确认 Capability 与报告没有矛盾；
8. 确认页面没有展示来源未知字段值；
9. 点击“Agent 解释”，确认只生成草稿且不会自动发送。

## 7. 常见错误

| HTTP | 错误码 | 含义与处理 |
| --- | --- | --- |
| 413 | `SCHEDULE_BODY_TOO_LARGE` | 请求超过 10 MiB；缩减来源文档或拆分项目 |
| 422 | `SCHEDULE_IMPORT_CONTRACT_INVALID` | 按 `detail.errors[].path` 修复必填字段、类型、引用或层级 |
| 422 | `SCHEDULE_IMPORT_VERSION_UNSUPPORTED` | 没有注册对应 `schema_version` 的 Adapter |
| 422 | `SCHEDULE_IMPORT_SEMANTICS_UNSUPPORTED` | 来源使用了当前 Adapter 明确不支持的语义 |
| 409 | `SCHEDULE_IDEMPOTENCY_CONFLICT` | 同一 `request_id` 已对应不同来源；新业务提交应使用新 ID |
| 409 | `SCHEDULE_SUBMISSION_IN_PROGRESS` | 相同请求仍在处理中，稍后原样重试 |
| 500 | `SCHEDULE_DEPENDENCY_FAILURE` | PostgreSQL 或 MinIO 保存失败；使用同一请求重试 |

错误路径使用 JSON Pointer，例如 `/document/project/default_calendar_id`。响应和日志不会回显完整来源正文；部分结构错误消息可能包含用于定位的 Task、Dependency 或 Calendar ID，因此这些 ID 不应承载密码、Token 或其他敏感值。

## 8. 回写 MPP 的责任边界

Yuxi 当前不会生成或修改 MPP。后续如需把 Delivery 写回 Microsoft Project，推荐由外部业务系统或独立转换服务完成：

1. 读取 Yuxi 的受控 Delivery；
2. 校验 Candidate 已接受、来源版本仍为 current、基础哈希一致；
3. 在新的来源副本上应用结果，不覆盖原始 MPP；
4. 由外部服务生成新的 MPP 或来源系统版本；
5. 再提取新的版本化 JSON，通过新的 `request_id` 和外部版本回流 Yuxi；
6. 使用回流验收证据确认 Source、Patch、Audit 和版本身份一致。

这条链路可以在 Yuxi 之外使用 Windows/COM；Yuxi API、Audit、Agent 和页面仍保持跨平台。

## 9. 安全与发布边界

- 来源 JSON 数据语义副本只保存在私有对象存储，不提供普通用户读取接口；
- 页面只显示扩展字段数量、原因代码和影响对象数量，不显示未知字段值；
- 日志不得记录 Token、来源全文、Notes 或未知字段值；
- 当前 mock Adapter 和单个水泵站案例不代表任意 Microsoft Project 项目可生产接入；
- 独立真实案例、正式来源协议、回写治理和 G2 仍需单独验收；
- 当前生产结论继续保持 `PRODUCTION_NO_GO`。
