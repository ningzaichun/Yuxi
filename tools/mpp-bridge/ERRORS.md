# MPP Bridge 错误码与恢复

所有正式命令都 fail-closed。Extract/Build 的普通失败写入严格 `error.json`，超时由包装器写入 `timeout.json`；Roundtrip 会保留已完成阶段的产物用于诊断，但非 PASS 不得提交 Yuxi 或作为生产成功。

## 输入与环境

| 错误码 | 含义 | 恢复动作 |
| --- | --- | --- |
| `MPP_BRIDGE_INPUT_EXTENSION_INVALID` | Extract 输入不是 `.mpp` | 选择真实 MPP 文件 |
| `MPP_BRIDGE_BUILD_OUTPUT_EXTENSION_INVALID` | Build 输出不是 `.mpp` | 使用新的 `.mpp` 路径 |
| `MPP_BRIDGE_BUILD_OUTPUT_EXISTS` / `MPP_BRIDGE_PACKAGE_OUTPUT_EXISTS` | 目标已存在 | 改用新的输出路径；工具不会覆盖 |
| `MPP_BRIDGE_PROJECT_PATH_TOO_LONG` | Project 将接触的路径超过 259 字符 | 缩短 `OutputDirectory` 和父目录后重跑 |
| `MPP_BRIDGE_PREEXISTING_PROJECT_PROCESS` | 运行前已有 `WINPROJ` | 保存并正常退出用户自己的 Project，再重跑；工具不会终止它 |
| `MPP_BRIDGE_COM_PROCESS_NOT_OBSERVED` | COM 启动后未发现 Project 进程 | 确认桌面版 Project 已安装、位数匹配且当前为交互登录会话 |
| `MPP_BRIDGE_EXTRACT_TIMEOUT` / `MPP_BRIDGE_BUILD_TIMEOUT` | 单阶段超过硬超时 | 查看控制目录的 `timeout.json` 与最后阶段；确认 owned 进程已清理后提高超时或修复输入 |
| `MPP_BRIDGE_EXTRACT_CHILD_FAILED` / `MPP_BRIDGE_BUILD_CHILD_FAILED` | 隔离子进程失败 | 读取控制目录的 `stderr.log` 及运行目录的 `error.json` |
| `MPP_BRIDGE_EXTRACT_CHILD_RETURNED_NO_RESULT` / `MPP_BRIDGE_BUILD_CHILD_RETURNED_NO_RESULT` | 子进程未返回结果 | 查看 stdout/stderr；不要把缺失结果视为成功 |

## Extract

| 错误码 | 含义 |
| --- | --- |
| `MPP_BRIDGE_SOURCE_MODIFIED` | 原始 MPP 的执行前后 SHA-256 不一致 |
| `MPP_BRIDGE_DUPLICATE_CALENDAR_NAME` / `MPP_BRIDGE_CALENDAR_NAME_MISSING` | 日历身份无法稳定解释 |
| `MPP_BRIDGE_DEFAULT_CALENDAR_UNKNOWN` | 项目默认日历不在提取日历集合中 |
| `MPP_BRIDGE_DUPLICATE_TASK_UNIQUE_ID` | 来源任务 Unique ID 重复 |
| `MPP_BRIDGE_DEPENDENCY_TASK_UNKNOWN` | 依赖引用未知任务 |
| `MPP_BRIDGE_COM_PROCESS_RESIDUAL` | 本次 Extract 新增的 Project 进程未退出 |
| `MPP_BRIDGE_EXTRACT_FAILED` | 未分类 COM/读取失败；以 `error.stage` 和 `message` 定位 |

Extract 可以成功输出含 `unsupported_semantics` 的观测 Snapshot，但此时 `roundtrip_eligible=false`，不得继续 Build。

## Build

| 错误码 | 含义 |
| --- | --- |
| `MPP_BRIDGE_BUILD_SNAPSHOT_SCHEMA_INVALID` | Snapshot 不符合严格 Schema |
| `MPP_BRIDGE_BUILD_SEMANTIC_HASH_INVALID` | Snapshot Semantic Hash 不可复算 |
| `MPP_BRIDGE_BUILD_SNAPSHOT_NOT_ELIGIBLE` | Snapshot 含 blocker 或不可 roundtrip |
| `MPP_BRIDGE_BUILD_CALENDAR_UNSUPPORTED` | 日历继承、重复例外或其他未冻结日历语义 |
| `MPP_BRIDGE_BUILD_CALENDAR_SET_MISMATCH` | Project 保存后的日历集合与输入不一致 |
| `MPP_BRIDGE_BUILD_TASK_STATE_UNSUPPORTED` | 手工任务或进度状态不在 v1 Build 范围 |
| `MPP_BRIDGE_BUILD_DUPLICATE_TASK_ID` | Bridge Task ID 重复 |
| `MPP_BRIDGE_BUILD_PARENT_UNKNOWN` | 父任务引用未知 |
| `MPP_BRIDGE_BUILD_DEPENDENCY_TASK_UNKNOWN` | 依赖引用未知任务 |
| `MPP_BRIDGE_BUILD_PROJECT_START_FAILED` | 项目开始日期写入失败 |
| `MPP_BRIDGE_BUILD_TASK_COUNT_CHANGED_AFTER_REOPEN` | 保存重开后任务数量漂移 |
| `MPP_BRIDGE_BUILD_COM_PROCESS_RESIDUAL` | 本次 Build 新增的 Project 进程未退出 |
| `MPP_BRIDGE_BUILD_FAILED` | 未分类写入/保存失败；目标与 staging 会清理 |

## Compare 与 Roundtrip

Comparator 的业务结果在 `diff.json` 中表示：`PASS`、`FAIL` 或 `UNSUPPORTED`。`BLOCKER` 是支持字段漂移，`ALLOWED_REASSIGNMENT` 仅用于 Project 重分配任务 ID，`UNSUPPORTED` 永远不能产生 PASS。

以下错误表示输入 Artifact 自身不可信，应修复 Artifact 或重跑上游，而不是接受差异：

- `MPP_BRIDGE_COMPARE_SNAPSHOT_SCHEMA_INVALID`
- `MPP_BRIDGE_COMPARE_IDENTITY_SCHEMA_INVALID`
- `MPP_BRIDGE_COMPARE_SEMANTIC_HASH_INVALID`
- `MPP_BRIDGE_COMPARE_ARTIFACT_IDENTITY_MISMATCH`
- `MPP_BRIDGE_COMPARE_IDENTITY_MAP_INVALID`
- `MPP_BRIDGE_COMPARE_IDENTITY_MAP_TASK_COUNT_MISMATCH`
- `MPP_BRIDGE_COMPARE_TASK_ID_INVALID`
- `MPP_BRIDGE_COMPARE_PARENT_IDENTITY_MISSING`
- `MPP_BRIDGE_COMPARE_DEPENDENCY_IDENTITY_MISSING`
- `MPP_BRIDGE_COMPARE_DUPLICATE_CALENDAR_NAME`
- `MPP_BRIDGE_COMPARE_DIFF_SCHEMA_INVALID`

Roundtrip 入口还会返回：

| 错误码 | 含义 |
| --- | --- |
| `MPP_BRIDGE_ROUNDTRIP_INPUT_EXTENSION_INVALID` | 输入不是 `.mpp` |
| `MPP_BRIDGE_ROUNDTRIP_SOURCE_UNSUPPORTED` | 来源 Snapshot 不允许 Build |
| `MPP_BRIDGE_ROUNDTRIP_SOURCE_MODIFIED` | 原始 MPP 哈希变化 |
| `MPP_BRIDGE_ROUNDTRIP_REPORT_SCHEMA_INVALID` | 最终 Report 未通过严格 Schema |

## Project Yuxi

| 错误码 | 含义与恢复动作 |
| --- | --- |
| `MPP_BRIDGE_PROJECT_YUXI_SNAPSHOT_SCHEMA_INVALID` | 输入不是严格 Bridge Snapshot；修复或重新 Extract |
| `MPP_BRIDGE_PROJECT_YUXI_SNAPSHOT_SEMANTIC_HASH_INVALID` | Snapshot 语义哈希不可复算；禁止手工修补，重新 Extract |
| `MPP_BRIDGE_PROJECT_YUXI_SNAPSHOT_NOT_ELIGIBLE` | Snapshot 含 blocker 或不可 roundtrip；先消除来源中的未支持语义 |
| `MPP_BRIDGE_PROJECT_YUXI_STATUS_DATE_UNSUPPORTED` | 来源包含 v1.1 无法表达的非空 Status Date；升级协议后再投影 |
| `MPP_BRIDGE_PROJECT_YUXI_OUTPUT_EXISTS` | 目标 Interchange 或 Report 已存在；使用新的隔离输出目录 |
| `MPP_BRIDGE_PROJECT_YUXI_DEFAULT_CALENDAR_INVALID` | 默认日历引用不唯一；重新 Extract 或修复上游缺陷 |
| `MPP_BRIDGE_PROJECT_YUXI_DEFAULT_CALENDAR_HAS_NO_WORKING_TIME` | 默认日历没有工作时段，无法派生日/周工时 |
| `MPP_BRIDGE_PROJECT_YUXI_INTERCHANGE_SCHEMA_INVALID` | 投影结果未通过正式 Yuxi Interchange Schema，禁止提交 API |
| `MPP_BRIDGE_PROJECT_YUXI_REPORT_SCHEMA_INVALID` | 字段处置报告未通过严格 Schema，禁止提交 API |

## Package 与恢复原则

`MPP_BRIDGE_PACKAGE_MANIFEST_SCHEMA_INVALID` 和 `MPP_BRIDGE_VERSION_SCHEMA_INVALID` 表示发布契约自检失败，禁止分发该 ZIP。恢复时保留失败运行目录和结构化证据；不得手工修补中间 MPP 后改报 PASS，不得批量结束用户已有的 Project 进程，也不得复用已存在的输出路径。
