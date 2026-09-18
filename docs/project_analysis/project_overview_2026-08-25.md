# 📊 项目分析报告

生成时间：2026-08-25 14:05:38

分析范围：Yuxi 排期审查、MPP Bridge、CPM/Candidate、Schedule Agent 与受控测试链路。结论基于当前仓库代码、正式文档和 2026-08-25 本地验证结果，不把未实现能力或测试样例外推为生产能力。

## 🧱 技术栈

| 层级 | 技术 | 在本模块中的用途 | 代码依据 |
| --- | --- | --- | --- |
| MPP 转换 | PowerShell 7.5+、Microsoft Project 16.0 COM | `.mpp` 提取、创建新 MPP、保存重开、Roundtrip 和 Yuxi 投影 | `tools/mpp-bridge/src/*.ps1`、`tools/mpp-bridge/README.md` |
| 后端 | Python 3.12+、FastAPI、Pydantic v2 | HTTP 契约、来源适配、Canonical、审查、CPM、Candidate | `backend/pyproject.toml`、`backend/server/routers/schedule_router.py` |
| 数据访问 | SQLAlchemy、PostgreSQL 16 | Snapshot、Audit、Issue、Decision、Optimization、Candidate 持久化 | `backend/package/yuxi/storage/postgres/models_schedule.py` |
| 对象存储 | MinIO | 保存来源 JSON、Canonical Snapshot 和 Candidate 文档 | `backend/package/yuxi/schedule/storage.py` |
| Agent/异步 | LangGraph、ARQ、Redis | Schedule 证据工具、Agent Run、事件和任务消费 | `backend/package/yuxi/agents/toolkits/schedule/tools.py`、`docker-compose.yml` |
| 前端 | Vue 3、Vite、Ant Design Vue、Less | 导入、来源审阅、CPM/Candidate、依赖工作台和甘特展示 | `web/package.json`、`web/src/views/ScheduleView.vue` |
| 测试 | pytest、pytest-asyncio、Ruff、Node assert、VitePress | 单元、API、E2E、前端转换与文档门禁 | `backend/test`、`web/src/utils/__tests__`、`docs/package.json` |

MPP Bridge 与 Yuxi 运行时刻意分离：Bridge 只能在安装了 Microsoft Project 的 Windows 交互式会话中运行；Yuxi API、Worker 和 Web 不加载 COM，也不直接解析 `.mpp`。

## 🗂️ 项目结构

| 目录/文件 | 职责 |
| --- | --- |
| `tools/mpp-bridge/src` | 独立 MPP CLI；Extract、Build、Compare、Roundtrip、Project Yuxi、版本发现和打包 |
| `tools/mpp-bridge/schemas` | 13 个严格 JSON Schema，覆盖 Snapshot、Manifest、Error、Identity Map、Diff、Roundtrip、Projection 和 Package |
| `tools/mpp-bridge/tests` | 无 COM 契约测试与需要 Microsoft Project 的真实集成矩阵 |
| `backend/package/yuxi/schedule/contracts` | Canonical v2.2–v2.8、外部导入、审查、优化和 Delivery 契约 |
| `backend/package/yuxi/schedule/importers` | `microsoft_project_interchange_v1.1` 与 Canonical 各版本到领域模型的 Adapter |
| `backend/package/yuxi/schedule/audit` | 固定规则集、能力计算、稳定 Issue Key 与确定性排序 |
| `backend/package/yuxi/schedule/forward_engine.py` | 工作日历 CPM、约束、进度、资源和 inactive Profile 的统一计算入口 |
| `backend/package/yuxi/schedule/work_calendar.py` | 工作时间加减、反向移动、例外和日历继承解析 |
| `backend/package/yuxi/schedule/resource_analyzer.py` | 来源/重算日期上的资源超配和 Assignment 成本分析 |
| `backend/package/yuxi/schedule/goal_optimizer.py` | 仅在用户明确授权的工期组合内进行目标完工优化 |
| `backend/package/yuxi/schedule/delivery_adapter.py` | 受控 Canonical 副本应用器；当前只支持 v2.2 依赖规范化 |
| `backend/package/yuxi/services/schedule_*` | 导入、幂等、恢复、审查、Decision、Candidate、Delivery 和验收证据用例层 |
| `backend/package/yuxi/repositories/schedule_repository.py` | 排期六类业务表的 owner 隔离与事务访问 |
| `backend/server/routers/schedule_router.py` | 20 个 `/api/schedule` HTTP 入口 |
| `backend/package/yuxi/agents/toolkits/schedule` | 4 个只读 Schedule Agent 证据工具 |
| `web/src/views/ScheduleView.vue` | 排期审查主页面和 Candidate/Dependency Workbench 交互 |
| `web/src/components/schedule` | JSON 导入弹窗和来源/候选对比甘特 |
| `web/src/apis/schedule_api.js` | 所有 Schedule HTTP 调用的前端边界 |
| `backend/test/unit/schedule` | 244 个测试函数，参数化后当前执行为 287 项 |
| `backend/test/integration/api` | 18 个 Schedule API 场景、1 个 A/B 实际导入门禁 |
| `backend/test/e2e/test_schedule_audit_agent_e2e.py` | Import→Audit→Agent→Schedule 工具的真实 Worker E2E |

## 🧩 功能模块

### 1. MPP Bridge

已完成：

- 只读复制来源 MPP，Microsoft Project 仅接触工作副本；执行前后复算来源 SHA-256；
- 提取项目、日历、任务层级/WBS、自动/Inactive 任务、里程碑、四类依赖、Lag、约束和 Deadline；
- 从合格 Snapshot 创建全新 MPP，拒绝覆盖来源或既有目标；
- 保存、关闭、重开、重算和反向提取；
- 使用 Identity Map 对齐 Project 重分配后的 Task ID/Unique ID；
- 字段级 Comparator、`PASS/FAIL/UNSUPPORTED`、严格错误产物和 COM 进程清理；
- 确定性打包和 Bridge Snapshot→Yuxi Interchange 投影。

未完成：

- Bridge v1 不支持 Resource/Assignment、Cost、Baseline、完整 Actual/Remaining、跨午夜日历、日历继承和复杂手工任务；
- 不能在 Linux、API 服务账户或无交互桌面会话中运行；
- 还不是 Microsoft Project 全字段无损备份/恢复工具。

### 2. 来源协议与 Canonical

已完成：

- 正式外部协议 `microsoft_project_interchange_v1.1`；未知扩展字段保存在私有来源对象，并在 Normalization Report 中标注未参与审查/计算；
- Adapter v1.6 根据来源能力投影到 Canonical v2.2–v2.5 或 v2.8；
- 来源 JSON 与 Canonical 分别保存、分别计算 Hash；来源自报 statistics/capabilities/validation 不被信任，Yuxi 会重新计算；
- Canonical v2.2–v2.8 均有强类型模型和发布 Schema。

版本能力不是简单累加：

| Canonical | 主要能力 |
| --- | --- |
| v2.2 | 基础活动、依赖、单一日历语义和来源审查 |
| v2.3 | 里程碑和边界角色 |
| v2.4 | 日历例外、多/任务日历、后续任务 Lag 日历 |
| v2.5 | MSO/FNLT、Deadline、required finish |
| v2.6 | Status Date、完成/进行中、Remaining、Baseline 0 |
| v2.7 | 强类型 Resource/Assignment、超配和成本；继承自 v2.5，不含 v2.6 进度组合 |
| v2.8 | Resource/Assignment + inactive；仍未融合 v2.6 进度/Baseline |

### 3. 确定性审查

固定规则集覆盖：契约统计一致性、自依赖、重复关系、依赖环、开放起止、汇总任务依赖、inactive 依赖、零/非零 Lag 日期违规、资源超配、Deadline/FNLT/required finish、Baseline/Status Date/里程碑缺失和资源语义分类。

审查结果带稳定 Issue Key、严重等级、对象引用、证据、建议和固定排序；Agent 只读取已持久化证据，不能把语言模型推断冒充确定性审查。

### 4. CPM 与资源分析

已完成的受限 Profile 包括：

- FS/SS/FF/SF，零/正/负 Lag；
- 单日历、多/任务日历和结构化例外；
- 汇总滚动、最早/最晚日期、总/自由浮时和关键任务；
- MSO、SNET、FNET、FNLT、Deadline 和 required finish 的安全口径；
- COMPLETED、IN_PROGRESS、Status Date、Remaining 和 Baseline 偏差；
- Resource/Assignment 的只读超配与两位小数成本；
- inactive 来源事实保留和计算排除；
- 不支持语义返回结构化 blocker，不生成近似日期。

资源分析只报告，不移动任务；没有资源均衡算法和资源成本优化算法。

### 5. Candidate 与人工决策

已完成三类 Candidate：

1. 依赖规范化：业务在 Workbench 中把汇总/inactive 依赖明确下沉到 active 叶子任务；
2. 自动正向重算：生成独立 Candidate，展示日期、浮时、关键路径和冲突；
3. 工期目标优化：只枚举用户明确授权的 1–10 个任务工期组合，可选择尽早完工或满足目标日期。

所有 Candidate 均绑定来源内容 Hash 和当前项目版本；用户可接受或拒绝，旧来源出现后 Candidate 会显示过期。接受只记录态度，不修改 Source。

当前 Delivery 只有 `dependency_normalization + canonical_schedule_v2.2` 可以应用到新的 Canonical 副本。自动重算、工期优化、v2.3+ 组合和 MPP 来源均返回 `DELIVERY_ADAPTER_UNAVAILABLE`。

### 6. API、存储和安全

- 20 个 Schedule 路由覆盖 Import/Snapshot/Audit/Issue/Workbench/Optimization/Candidate/Delivery/Agent；
- owner UID 是读取和写入边界，跨用户资源表现为不可见；
- `owner_uid + request_id` 提供幂等，内容或身份变化返回冲突；
- creating/ready/failed、执行 token 和租约支持并发、取消、失败与超时恢复；
- PostgreSQL 保存索引和业务状态，MinIO 保存来源、Canonical 与 Candidate；
- 10 MiB 请求限制、稳定错误码和底层异常脱敏已有回归；
- 普通用户没有删除 Snapshot 的产品接口，试点数据清理由测试门禁或管理员一致性流程负责。

### 7. 页面和 Agent

页面已实现：JSON 导入、来源类型与双 Hash、外部接入/来源审查/CPM 三状态、任务表、轻量甘特、Issue 筛选、依赖工作台、重算、目标优化、Candidate 对比、资源/成本证据、接受/拒绝、Delivery 和回流验收证据。

Agent 侧有 4 个只读工具：整份计划上下文、Audit 摘要、单 Issue 证据和工期目标优化边界。Agent 可以解释和组织已有证据，但不能创建 Candidate、代替授权或自行写回来源。

## 🔄 核心流程

### MPP 到 Yuxi

```text
原始 MPP（只读）
  → Bridge 工作副本
  → Extract Snapshot + Manifest + Semantic Hash
  → Build 新 MPP + 重开重算
  → 反向 Extract + Identity Map + Comparator
  → PASS 后投影 microsoft_project_interchange_v1.1
  → POST /api/schedule/imports
  → Adapter Normalize
  → Canonical + Audit
  → PostgreSQL 元数据 / MinIO 双对象
```

### 审查到 Candidate

```text
不可变 Source Snapshot
  → 固定 Audit Rules / Capability
  → Issue 或 CPM 门禁
  → Dependency Decision / Forward Recalculation / Goal Optimization
  → 独立 Candidate + Candidate Audit + Comparison
  → 用户接受或拒绝
  → Delivery（多数类型当前只读、不可自动应用）
```

### Agent 审查

```text
页面绑定 Snapshot ID + 内容 Hash
  → Worker 执行 LangGraph Agent
  → 只读 Schedule Tools
  → 读取持久化 Audit/Issue/Capability
  → 返回解释与证据导航
```

## 🧠 架构设计

本模块是 Yuxi 单体业务包中的分层领域模块，同时包含一个进程外 Windows Bridge：

- **Anti-corruption Layer**：MPP Bridge 与 Interchange Adapter 隔离 Microsoft Project COM 和内部 Canonical；
- **版本化契约**：外部来源、Canonical、Engine Profile、Candidate、Delivery 和 Evidence 均显式版本化；
- **不可变快照模型**：Source 不原地修改，所有计算结果以 Candidate 派生；
- **能力驱动**：页面和服务根据 Capability 决定能否重算，而不是尝试失败后回退；
- **Fail-closed**：未知语义、Hash 漂移、版本不匹配和 Oracle 差异会阻断后续阶段；
- **分层持久化**：PostgreSQL 管理身份、状态和索引，MinIO 保存完整文档；
- **人机职责分离**：确定性引擎产生事实，Agent 解释事实，用户负责业务授权和接受/拒绝。

### 当前业务成熟度

| 业务场景 | 当前程度 | 结论 |
| --- | --- | --- |
| A/B 指定 MPP 的提取、重建、语义往返 | 已完成并获批受控测试 | 可进入 UAT |
| A/B Bridge→Yuxi Import→Audit→CPM→Candidate 审阅 | 技术闭环完成 | 可在冻结范围内测试 |
| 常规自动任务、层级、里程碑、四类依赖、多日历、常用约束 | 受限可用 | 输入符合 Profile 时可计算 |
| 进度/Status Date/Remaining/Baseline | 单独 Profile 已实现 | 未与资源/inactive 全组合融合 |
| 资源冲突和 Assignment 成本 | 只读分析已实现 | 不含自动均衡和成本优化 |
| 汇总/inactive 依赖治理 | 人工 Workbench 闭环已实现 | 需要业务明确选择叶子任务 |
| AI 排期审查 | 证据解释闭环已实现 | 不能替代业务合理性判断 |
| 500 活动 Suite 页面 | L06 基线通过 | 不是业务代表性大型项目证明 |
| 任意外部 MPP | 未实现通用保证 | `NO_GO` |
| Yuxi Candidate→新 MPP | 未实现 | `NO_GO` |
| 全量生产 | 未完成组合、规模和交付门禁 | `UNRESTRICTED_PRODUCTION_NO_GO` |

当前总体状态是：`APPROVED_FOR_CONTROLLED_AB_TESTING`，而不是 `Y4 PASSED` 或生产可用。

## 🌐 外部依赖

| 依赖 | 必要场景 | 风险/边界 |
| --- | --- | --- |
| Microsoft Project Desktop 16.0 | Bridge Extract/Build/Roundtrip | 仅 Windows 交互会话；COM、路径长度和进程生命周期敏感 |
| PowerShell 7.5+ | Bridge CLI | 需要与 Project 同一用户会话 |
| PostgreSQL | Yuxi Snapshot/Audit/Candidate 状态 | 失败时保持稳定错误码并支持租约恢复 |
| MinIO | 来源、Canonical、Candidate 文档 | 与数据库需一致性清理；普通用户无删除接口 |
| Redis + ARQ Worker | Schedule Agent E2E | 纯 HTTP Audit/CPM 不依赖 Agent Worker，AI 审查依赖 |
| Vue/Web 浏览器 | 人工 UAT 和 Candidate 审阅 | 大型页面目前仍使用全量 DOM |

Milvus、Neo4j、MinerU 等 Yuxi 平台依赖不是排期确定性引擎的直接依赖。

## ⚠️ 风险与技术债

1. **Canonical 组合能力碎片化**
   - 问题：v2.6 进度/Baseline 与 v2.7/v2.8 资源/inactive 是分支继承，不能表达完整组合。
   - 影响：真实项目常同时包含状态日期、资源、成本、Baseline 和 inactive，当前只能 fail-closed。
   - 建议：必须由新的真实 MPP 需求驱动发布新的组合 Canonical 和 Bridge Schema，不应无版本拼接现有模型。

2. **没有 Candidate→MPP Delivery Adapter**
   - 问题：Bridge Build 只能从 Bridge Snapshot 创建 MPP；Yuxi Candidate 不能反投影为 Bridge Snapshot。
   - 影响：系统目前是“分析与审阅平台”，不是排期变更闭环系统。
   - 建议：先冻结可写字段、审批、基础 Hash、冲突检测和回流验收，再实现只创建新 MPP 的 Adapter。

3. **MPP Bridge 维护成本高**
   - 问题：`Export-MppBridgeSnapshot.ps1` 约 974 行，`Build-MppBridgeProject.ps1` 约 697 行，COM 反射、业务映射和生命周期集中在大脚本中。
   - 影响：字段扩展和错误路径修改容易产生回归。
   - 建议：只在出现真实复用边界时拆分共享 COM 生命周期、Schema/Hash 和日期转换模块，避免碎片化 helper。

4. **Bridge 运行拓扑依赖桌面会话**
   - 问题：无法直接部署为普通 Linux API/Worker 服务。
   - 影响：批处理、并发和运维自动化受到限制。
   - 建议：受控测试阶段先保持单用户串行 CLI；生产化前单独设计 Windows Worker、队列、会话和许可证治理。

5. **大型前端仍是全量 DOM**
   - 问题：L06 产生约 9,958 个 DOM 节点、页面高度约 88,569px。
   - 影响：500 活动通过不代表更大或更复杂真实项目可用。
   - 建议：拿到业务代表性大型 MPP 后重新测量；超阈值再引入表格/甘特虚拟化，不提前重构。

6. **大型 Suite Oracle 仍有真实差异**
   - 问题：L02 冻结 Oracle 可重建 325 条两两冲突，但遗漏 135 个累计超配时间片；另有 4 个负 FF Lag 工作时间边界差异。
   - 影响：L02 必须保持 Unsupported，不能用修改 Oracle 或全局日历语义变绿。
   - 建议：保持双原因门禁，等真实业务口径确定后发布新版本语义。

7. **正式文档存在局部状态漂移**
   - 问题：`tools/mpp-bridge/README.md` 顶部仍写“Yuxi API/UI 组合验收尚未完成”，与当前 Y0–Y4 技术基线事实不一致。
   - 影响：新维护者可能误判实际完成度。
   - 建议：下一次文档切片将 Bridge README 更新为“Bridge/Y0 已完成、A/B 受控测试获批、生产 NO_GO”。

8. **试点数据生命周期需要管理员流程**
   - 问题：普通用户没有 Snapshot 删除接口。
   - 影响：手工 UAT 若不使用自清理工具，可能积累数据库和 MinIO 记录。
   - 建议：每轮 UAT 使用独立身份、记录对象清单并由管理员按一致性流程清理；是否建设用户删除能力应另行立项。

9. **测试证据不等于通用业务证明**
   - 当前实际验证包括 Schedule 单元测试 `287 passed`、Ruff 和文档构建通过；仓库还包含 18 个 API 场景、A/B live gate 和 1 个 Agent E2E。
   - A/B 原包仍自述为 `SYNTHETIC_BUSINESS_LIKE_TEST_DATA`，项目负责人允许其作为两份受控真实项目参与测试，但该授权不能改写 provenance 或证明大型业务代表性。

## 📌 总结

这个模块已经完成从“排期 JSON 展示”到“MPP 受控转换、来源保真、确定性审查、受限 CPM、人工 Candidate 和 AI 证据解释”的完整工程闭环。对于 A/B 两个冻结项目，它已经具备进入受控 UAT 的条件。

当前最准确的产品定位是：**Microsoft Project 来源的受控排期审查与决策辅助系统**。它能可靠地读取受支持来源、发现结构/日期问题、计算已冻结语义、生成不修改 Source 的候选并支持人工审阅；它还不能替代 Microsoft Project 进行通用项目维护，也不能把 Yuxi Candidate 自动写回 MPP。

下一阶段不应继续增加合成兼容逻辑，而应依次完成：A/B UAT 反馈闭环、业务代表性大型 MPP 验证、真实组合语义驱动的 Canonical/Bridge 升级，以及在明确需要时建设 Candidate→新 MPP 的受控 Delivery Adapter。全量生产门禁在这些工作完成前继续保持 `UNRESTRICTED_PRODUCTION_NO_GO`。
