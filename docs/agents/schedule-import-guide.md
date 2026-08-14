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

## 2. 两种提交方式

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

来源文档允许未定义字段，但必须满足以下规则：

- `schema_version` 必须存在且为非空字符串；
- 已定义的核心字段缺失、类型错误或枚举错误会被拒绝；
- Task、Dependency 和 Calendar 引用必须有效；
- Task 父子层级不能成环；
- 未定义字段会保存在私有来源对象中，但不会自动参与 Audit 或 CPM；
- 来源自报 Statistics、Capabilities 和 Validation 不作为 Yuxi 结论。

### 2.2 兼容：直接提交严格 Canonical

已经能够稳定生成完整 `canonical_schedule_v2.2` 的调用方，可以继续使用：

```http
POST /api/schedule/snapshots
```

该入口保持严格，不允许未知字段，也不经过来源 Adapter。不要为了让外部文档通过校验而把所有来源扩展字段塞进 Canonical；需要保留来源原文时应使用 `/imports`。

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

1. **外部接入成功**：来源 JSON 已校验、适配并保存；
2. **来源审查允许**：Yuxi 能基于内部 Canonical 执行确定性 Audit；
3. **CPM 重算允许**：当前完整语义落在受支持的 CPM Profile 内。

接入成功不等于全部来源字段都参与了审查，也不等于 CPM 可以运行。页面中的“外部 JSON 接入边界”会独立显示三个状态。

## 4. 幂等、双对象和双哈希

同一用户下，`request_id` 是导入幂等键：

- 同一 `request_id`、相同来源文档：返回原结果；
- 同一 `request_id`、来源文档变化：返回 `409 SCHEDULE_IDEMPOTENCY_CONFLICT`；
- 即使两个不同来源文档生成相同 Canonical，只要来源哈希不同，仍视为冲突；
- `/imports` 和 `/snapshots` 不能交叉复用同一个 `request_id`。

私有对象存储中分别保存：

```text
{owner_uid}/{schedule_snapshot_id}/source-document.json
{owner_uid}/{schedule_snapshot_id}/snapshot.json
```

`source_document_sha256` 用于来源追溯和导入幂等，`canonical_snapshot_sha256` 用于内部版本、Audit 和 Candidate。普通用户接口不会返回 `source-document.json` 原文或对象路径。

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

水泵站案例验证的是导入、规范化、Audit 和阻断行为。由于包含 4 个零工期里程碑，它不用于验证当前 CPM 成功主路径。

## 6. 推荐测试流程

### 6.1 契约与 Adapter 单元测试

在 `backend` 目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest test/unit/schedule/test_schedule_import_adapter.py
```

重点验证缺少必填字段失败、未知字段保留、未知引用和父子环拒绝、适配结果确定性，以及来源声明不参与 Yuxi 结论。

### 6.2 真实 API、PostgreSQL 和 MinIO

```powershell
.\.venv\Scripts\python.exe -m pytest test/integration/api/test_schedule_router.py
```

Import 用例覆盖首次 `201`、重放 `200`、冲突 `409`、双对象、双哈希、用户隔离和来源对象私有访问。

### 6.3 Import 到 Agent 端到端测试

```powershell
.\.venv\Scripts\python.exe -m pytest test/e2e/test_schedule_audit_agent_e2e.py -m e2e
```

该用例导入水泵站来源 JSON，读取 Audit 和 Issue，再验证 Agent 不会把来源 Validation 或 normalization report 中 ignored/unsupported 的字段说成已参与审查或计算。测试结束会清理临时 Agent、对话、双对象和数据库记录。

### 6.4 页面验收

1. 用同一账号导入案例并打开“排期审查”；
2. 核对来源版本和 Adapter 版本；
3. 确认“外部接入”为已接入；
4. 确认“来源审查”为允许；
5. 确认“CPM 重算”为阻断且原因是 `MILESTONE_UNSUPPORTED`；
6. 核对 preserved、ignored 和 unsupported 的数量；
7. 确认页面没有展示来源未知字段值；
8. 点击“Agent 解释”，确认只生成草稿且不会自动发送。

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

错误路径使用 JSON Pointer，例如 `/document/project/default_calendar_id`。响应和日志不会回显无效输入值或完整来源正文。

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

- 来源原文只保存在私有对象存储，不提供普通用户读取接口；
- 页面只显示扩展字段数量、原因代码和影响对象数量，不显示未知字段值；
- 日志不得记录 Token、来源全文、Notes 或未知字段值；
- 当前 mock Adapter 和单个水泵站案例不代表任意 Microsoft Project 项目可生产接入；
- 独立真实案例、正式来源协议、回写治理和 G2 仍需单独验收；
- 当前生产结论继续保持 `PRODUCTION_NO_GO`。
