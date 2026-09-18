# MPP Bridge

MPP Bridge 是独立于 Yuxi 运行进程的 Windows CLI，用于在 Microsoft Project `.mpp` 与严格、版本化的结构化快照之间进行双向转换。

当前状态：`M3 PASSED / M4 PASSED / Y0 PROJECTOR PASSED`。通用 Extract、Build、字段级 Roundtrip Comparator、版本发现、可复现打包和 Bridge Snapshot → Yuxi Interchange 确定性投影已完成；Yuxi API/UI 组合验收与生产门禁尚未完成，生产结论保持 `PRODUCTION_NO_GO`。

## 安装前提与版本

- Windows 10/11 64 位交互式登录会话；
- PowerShell 7.5+；
- 已安装并注册桌面版 Microsoft Project 16.0；
- Bridge 与 Project 必须在同一用户交互会话运行，API/Worker/服务账户不是受支持拓扑；
- 运行目录应尽量短，Project 接触的完整路径不得超过 259 字符。

版本和无启动 COM 的环境发现：

```powershell
pwsh -File tools/mpp-bridge/src/Get-MppBridgeVersion.ps1
```

输出通过严格 `mpp_bridge_version_v1` Schema，包含 Bridge/PowerShell/OS 版本、Project COM 注册状态及全部命令和契约列表。完整错误码与恢复动作见 [ERRORS.md](ERRORS.md)。

## M1 Extract 命令

在安装了 Microsoft Project Desktop 的 Windows 交互式登录会话中运行：

```powershell
pwsh -File tools/mpp-bridge/src/Invoke-MppBridgeExtract.ps1 `
  -InputMpp <source.mpp> `
  -OutputDirectory <isolated-output-directory> `
  -Timezone Asia/Shanghai `
  -TimeoutSeconds 600
```

包装器会在独立子 `pwsh` 中执行 Extract，并设置硬超时。超时时只终止本次启动的子进程和新增 `WINPROJ`，在 `control-<uuid>/timeout.json` 中记录最后阶段；成功或普通失败的 stdout/stderr 也保存在该控制目录。非交互式服务会话或受限沙箱可能因 Microsoft Project COM 无法访问桌面登录会话而返回 `0x80070520`，这类环境不属于支持的执行拓扑。

每次运行创建独立 `extract-<uuid>` 目录。原始输入只用于复制和执行前后 SHA-256；`original-byte-backup.mpp` 从不交给 COM，Microsoft Project 只打开 `working-copy.mpp`。成功产物包括 `snapshot.json` 和 `manifest.json`，失败产物包括严格 `error.json`，`progress.json` 用于长时间 COM 调用的阶段诊断。

集成矩阵：

```powershell
pwsh -File tools/mpp-bridge/tests/integration/Test-RepositoryFixtures.ps1 `
  -OutputDirectory tools/mpp-bridge/.m1-runs/matrix `
  -TimeoutSeconds 600
```

该命令依次提取仓库内三份 MPP，并验证 Schema、引用完整性、Artifact Hash、不可触碰字节备份、Semantic Hash、源文件不变和 COM 进程清理。

## M2 Build 命令

```powershell
pwsh -File tools/mpp-bridge/src/Invoke-MppBridgeBuild.ps1 `
  -SnapshotPath <snapshot.json> `
  -OutputMpp <new-output.mpp> `
  -OutputDirectory <isolated-artifact-directory> `
  -TimeoutSeconds 600
```

Build 只接受通过严格 Snapshot Schema、Semantic Hash 可复算且 `roundtrip_eligible=true` 的输入；目标 MPP 必须不存在。成功产物包括全新 MPP、`identity-map.json`、`build-report.json` 和 Build Manifest。失败或超时不会保留目标 MPP，包装器只终止本次子 `pwsh` 和新增 `WINPROJ`，并清理 staging 文件。

当前支持无继承的一个或多个项目日历、每周七天工作模式与最多五段工作区间、任意默认日历、任务日历、一次性停工/补班例外，以及 Active/Inactive 自动任务；支持任务层级、汇总滚动、里程碑、FS/SS/FF/SF、Lag、MSO/SNET/FNET/FNLT 等受支持约束和 Deadline 写入。日历继承、重复例外、手工任务、进度/Baseline、Resource/Assignment、Cost 等未冻结语义会明确拒绝，不会套用默认日历或生成近似 MPP。

仓库三文件 Build 矩阵：

```powershell
pwsh -File tools/mpp-bridge/tests/integration/Test-BuildRepositoryFixtures.ps1 `
  -OutputDirectory tools/mpp-bridge/.m2-runs/mx `
  -TimeoutSeconds 600
```

矩阵对每份 MPP 执行 `Extract → Build → 保存/关闭/重开/重算 → Extract`，验证全部 Artifact Schema/Hash，并通过 Identity Map 对齐新旧任务身份，比较项目、完整日历、任务层级/WBS、任务核心字段与依赖。可用 `-FixtureId msp-006|water-pump|c08` 单独重跑失败项。Microsoft Project COM 仍受传统路径长度限制；Bridge 会在 Project 接触的路径超过 259 字符时于启动 COM 前返回 `MPP_BRIDGE_PROJECT_PATH_TOO_LONG`，建议集成测试和生产运行目录保持简短。

## M3 Roundtrip 命令（验收中）

```powershell
pwsh -File tools/mpp-bridge/src/Invoke-MppBridgeRoundtrip.ps1 `
  -InputMpp <source.mpp> `
  -OutputDirectory tools/mpp-bridge/.m3-runs `
  -Timezone Asia/Shanghai `
  -TimeoutSeconds 600
```

该命令自动执行 `Extract → Build → Extract → Compare`，输出新 MPP、机器可读 `diff.json`、人工可读 `summary.txt` 和严格 `roundtrip-report.json`，记录 Bridge/Project 版本、输入与输出哈希、指定时区、执行系统时区及源文件不变证据。

也可以对已有的两个 Snapshot 与 Identity Map 单独比较：

```powershell
pwsh -File tools/mpp-bridge/src/Compare-MppBridgeSnapshots.ps1 `
  -LeftSnapshot <source-snapshot.json> `
  -RightSnapshot <rebuilt-snapshot.json> `
  -IdentityMap <identity-map.json> `
  -OutputDirectory <comparison-output-directory>
```

Comparator 通过 Identity Map 对齐任务，不依赖数组顺序或任务名称；Project 分配的新 Task ID/Unique ID 记为 `ALLOWED_REASSIGNMENT`，项目、日历、层级/WBS、日期、工期、约束、Deadline 和依赖/Lag 漂移记为 `BLOCKER`，任一 Snapshot 含 unsupported 或不可 roundtrip 时状态为 `UNSUPPORTED`，绝不返回 PASS。

## Y0 Project Yuxi 命令

只有通过 Snapshot Schema、Semantic Hash 和 `roundtrip_eligible=true` 三重门禁的 Bridge Snapshot 才能投影为 Yuxi 正式来源协议：

```powershell
pwsh -File tools/mpp-bridge/src/Project-MppBridgeSnapshotToYuxi.ps1 `
  -SnapshotPath <snapshot.json> `
  -OutputDirectory <new-isolated-output-directory>
```

成功输出 `microsoft-project-interchange-v1.1.json` 和严格 `projection-report.json`。Projector 不复制 Bridge 自报的 statistics、capabilities 或 validation，Yuxi Adapter 会基于投影后的项目、日历、任务、依赖和约束重新计算这些结论。同一 Snapshot 重复投影必须产生完全相同的 Interchange 字节和 SHA-256；输出已存在时拒绝覆盖。

Bridge v1 不能无损表达为 Interchange v1.1 的来源语义会 fail-closed：Snapshot 含 blocker、不可 roundtrip 或包含非空 Status Date 时不会生成 Yuxi 输入。字段的保留、派生和省略处置记录在 `projection-report.json`，正式来源 Schema 为 `schemas/microsoft_project_interchange_v1_1.schema.json`。

## 可复现打包与测试

```powershell
pwsh -File tools/mpp-bridge/src/Build-MppBridgePackage.ps1 `
  -OutputDirectory tools/mpp-bridge/.m4-runs/package
```

打包器按稳定路径顺序写入固定时间戳 ZIP，包含正式 Markdown、`src`、`schemas` 和无 COM 单元测试，并生成严格 `package-manifest.json`。相同源码与运行时重复构建应得到相同 ZIP SHA-256；目标 ZIP 已存在时拒绝覆盖。

无 COM 门禁：

```powershell
pwsh -File tools/mpp-bridge/tests/unit/Test-Contracts.ps1
pwsh -File tools/mpp-bridge/tests/unit/Test-Comparator.ps1
pwsh -File tools/mpp-bridge/tests/unit/Test-Projector.ps1
pwsh -File tools/mpp-bridge/tests/unit/Test-Package.ps1
```

需要 Project 的门禁依次使用 M1 Extract 矩阵、M2 Build 矩阵和 M3 Roundtrip 命令。测试期间不得同时打开用户自己的 Microsoft Project 会话。

## 安全边界

- 必须安装桌面版 Microsoft Project，并注册 `MSProject.Application` COM；
- 不允许输入路径与未来 Build 输出路径相同；
- 不原地修改来源 MPP；
- COM 日期必须配合显式时区解释；
- 成功和失败路径都必须关闭文件、退出应用并释放 COM 对象；
- 不会主动终止运行前已经存在的 Microsoft Project 进程；
- blocker 级 unsupported 不得生成近似 MPP 或提交 Yuxi。

## M1 支持范围

M1 通用 Extract 已按以下字段顺序实现：

| 顺序 | 领域 | 字段 |
| --- | --- | --- |
| 1 | Source | 文件哈希、Project 版本、显式时区、保存/重开/重算证据 |
| 2 | Project | 名称、开始、完成、默认日历 |
| 3 | Task | 稳定 Bridge ID、来源 ID/Unique ID、名称、WBS、层级、类型、Active、Auto/Manual、Start/Finish/Duration、Percent Complete |
| 4 | Calendar | 七天周模式、工作区间、例外和有效日历解析 |
| 5 | Dependency | FS/SS/FF/SF、Lag、前后任务引用 |
| 6 | Constraint | ASAP、MSO、SNET、FNET、FNLT、Constraint Date、Deadline |
| 7 | Report | unsupported semantics、roundtrip eligibility、semantic hash |

Resource/Assignment、Baseline、完整 Actual/Remaining、Cost、跨午夜日历和复杂手工任务保持 blocker，除非后续版本显式升级契约。

### 冻结规则

- 技术栈：PowerShell 7.5+、Microsoft Project Desktop COM；COM 属性通过显式反射读取，避免 PowerShell 7 对隐藏属性的绑定差异；
- 时间：命令必须显式接收 IANA 或 Windows 时区 ID，COM 本地时间按指定时区转换为带 Offset 时间；
- 身份：`bridge_task_id` 是比较身份，来源和输出的 ID/Unique ID 进入独立 Identity Map，不要求字面相同；
- 哈希：文件哈希、完整 Snapshot 哈希和排除运行时间等易变字段的 Semantic Hash 分开；
- Diff：字段处置只允许 `BLOCKER`、`ALLOWED_REASSIGNMENT`、`UNSUPPORTED`；存在 blocker 或 unsupported 时不得报告 Roundtrip PASS；
- Unsupported：M1 可以输出观测 Snapshot，但 blocker 会令 `roundtrip_eligible=false`，M2 Build 和 Yuxi Projector 必须拒绝继续；
- 进程：M0/M1 若检测到运行前已有 `WINPROJ`，直接失败，避免连接或关闭用户现有会话。

## M0 退出记录

2026-08-21 实测环境与结论：

- PowerShell `7.5.4`，Windows `10.0.26100`；
- Microsoft Project `16.0`；
- 成功路径完成工作副本打开、计算、保存、关闭、重开、重算、读取 5 个任务和再次保存；
- Snapshot 与 Manifest 通过严格 JSON Schema；
- 模拟失败在工作副本打开后触发，Error Artifact 通过严格 JSON Schema；
- 成功和失败路径的输入 SHA-256 执行前后一致；
- 最终验收输入和不可触碰字节备份 SHA-256 均为 `90b0a3ae05b7b2c2f1b1b24e850246e56c2b84e02d686374e2622611a3b69f2d`；
- Microsoft Project 保存重算后的工作副本 SHA-256 为 `b79a6249892bec4f13d5269147dcab45236c572576ba40c403bd1123ba26e9fc`，证明文件哈希与语义证据必须分层；
- 成功和失败路径均无新 `WINPROJ` 进程残留；
- `original-byte-backup.mpp` 从不交给 COM，用于防止工作副本被 Project 自动规范化后丢失输入字节；
- 技术选型和 M1 字段顺序已冻结并实现，允许进入 M2。

## M1 退出记录

2026-08-24 在 Microsoft Project 16.0 上完成三文件正式矩阵：

| 文件 | 日历 | 任务 | 依赖 | Unsupported | Roundtrip Eligible |
| --- | ---: | ---: | ---: | ---: | --- |
| MSP-006 Summary Rollup v2 | 1 | 5 | 2 | 0 | true |
| 水泵站排期 MOCK v1.1 | 1 | 21 | 19 | 0 | true |
| C08 Microsoft Project Observed | 1 | 21 | 19 | 0 | true |

三份输入执行前后 SHA-256 均不变，各自重复提取的 Semantic Hash 一致；Snapshot 与 Manifest 通过严格 Schema 和集成断言，成功路径及 120 秒模拟超时清理路径最终均无 `WINPROJ` 残留。水泵站实测约 240 秒完成，因此正式包装器和矩阵默认硬超时为 600 秒。

## M2 退出记录

2026-08-24 在 Microsoft Project 16.0 上完成三文件 Build 正式矩阵：

| 文件 | 日历 | 任务 | 依赖 | 反读 Unsupported | 核心语义 |
| --- | ---: | ---: | ---: | ---: | --- |
| MSP-006 Summary Rollup v2 | 1 | 5 | 2 | 0 | PASS |
| 水泵站排期 MOCK v1.1 | 1 | 21 | 19 | 0 | PASS |
| C08 Microsoft Project Observed | 1 | 21 | 19 | 0 | PASS |

三份来源 MPP 的 SHA-256 均保持不变；新 MPP 均可保存、关闭、重开、重算和反向 Extract，Build Manifest、Build Report、Identity Map 及反读 Snapshot/Manifest 均通过严格 Schema 与 Hash 断言，最终 `WINPROJ=0`。

另以派生 Snapshot（不冒充真实 MPP 来源）验证三套日历、六天默认日历、任务日历、一次性停工/补班例外、Inactive 任务，以及 MSO/SNET/FNET/FNLT 和 Deadline 保存重开后保持一致。blocker Snapshot、主动超时和超长路径均在 fail-closed 路径中不保留目标/staging 文件，严格 Error Artifact 通过 Schema，owned `WINPROJ` 无残留。M2 验收通过，允许进入 M3 字段级 Roundtrip Comparator。
