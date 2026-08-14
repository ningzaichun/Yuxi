# MSP-006-SUMMARY-ROLLUP v2 — 嵌套汇总 Oracle

状态：

- Microsoft Project 外部观测：**通过**
- v2 独立包自动校验：**通过**
- Yuxi 仓库 `canonical_schedule_v2.2` 工程门禁：**待 Yuxi 侧适配后启用**

本包不会覆盖 v1。v1 继续作为单层汇总 Oracle；v2 增加自底向上的两层汇总滚算。

## 结构

```text
总项目                         task:1 / SUMMARY
├─ 基础施工阶段                task:2 / SUMMARY
│  ├─ 场地准备                 task:3 / TASK / 2d
│  └─ 基坑开挖                 task:4 / TASK / 3d / task:3 FS
└─ 测量复核                    task:5 / TASK / 1d / task:3 SS
```

最大大纲层级为 3，共 5 个任务、2 个汇总任务。

## Microsoft Project 实测

| 对象 | 开始 | 完成 | 滚算来源 |
|---|---|---|---|
| 基础施工阶段 `task:2` | 2026-09-07 08:00 | 2026-09-11 17:00 | `task:3`、`task:4` |
| 总项目 `task:1` | 2026-09-07 08:00 | 2026-09-11 17:00 | `task:2`、`task:5` |
| 项目摘要 | 2026-09-07 08:00 | 2026-09-11 17:00 | 全计划 |

日期来自 Microsoft Project 16.0.17928.20148 保存、关闭、重新打开并重新计算后的 COM 观测，不是理论手写值。

## 文件

- `source/case_006_nested_summary_rollup_v2.mpp`：可重放的 Project 源计划。
- `oracle_raw.json`：Project 原始观测。
- `input.json`：自包含的 v2 Oracle 输入，汇总日期未作为输入。
- `expected.json`：冻结的 v2 期望结果与两条显式汇总断言。
- `yuxi_v6_output_template.json`：独立校验要求的 Yuxi v6 输出格式。
- `verify_yuxi_v6.py`：比较项目日期、5 个任务、父子关系、层级、汇总标记与两层滚算。
- `capture_msproject.ps1`：带 `FileOpenEx → FileOpen`、`ActiveProject → Projects` 和等待回退的复采集入口。
- `canonical_schedule_v2.2_adapter_requirements.json`：Yuxi 仓库接入要求，不伪造现有契约。
- `manifest.json`：文件大小和 SHA-256。

## 运行

Yuxi v6 读取 `input.json`，根据模板生成 `yuxi_v6_output.json`：

```powershell
python verify_yuxi_v6.py expected.json yuxi_v6_output.json
```

复采集必须使用 Windows PowerShell 5.1，并在机器上安装 Microsoft Project：

```powershell
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe `
  -NoProfile -ExecutionPolicy Bypass `
  -File .\capture_msproject.ps1
```

脚本默认写 `oracle_recapture.json`，不会修改 `expected.json`。

## Yuxi 仓库接入边界

这里没有将自定义 JSON 冒充 `canonical_schedule_v2.2`。Yuxi 开发人员应根据仓库内权威 Schema 完成适配，并满足：

- 保留 `parent_task_id`、`outline_level`、`summary` 与两个汇总任务；
- 汇总任务不可被扁平化成普通活动任务，也不可注入虚构工期；
- expected 映射到仓库现有 `task_dates/engine result`；
- v6 Profile 由仓库维护者命名并冻结；
- 门禁必须分别验证 `task:2` 和 `task:1` 的直接子级滚算。
