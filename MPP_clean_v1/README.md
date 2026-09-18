# Microsoft Project 纯计划标准样例 v1

## 交付文件

- `A_road_drainage_clean.mpp`：市政道路雨污分流改造工程，46 个任务。
- `B_substation_clean.mpp`：110kV 变电站一次设备安装与投运工程，62 个任务。
- `A_source_input.json`、`B_source_input.json`：生成 MPP 使用的结构化源数据。
- `A_validation_report.json`、`B_validation_report.json`：Microsoft Project 回读后的逐字段验收报告。
- `A_msproject_oracle.xml`、`B_msproject_oracle.xml`：从最终 MPP 重开、重算后导出的 MSPDI Oracle。
- `validation_summary.json`：两份文件的验收汇总。
- `manifest.json`：最终文件的大小和 SHA-256。
- `verify_package.py`：离线完整性和验收状态校验器。

## 重要来源说明

两份文件是根据业务场景生成的独立技术验收样例，数据分类为 `SYNTHETIC_BUSINESS_LIKE_TEST_DATA`。它们不是同一模板改名，但也不能被表述为客户历史真实项目。

它们满足技术硬条件；“来自两个实际项目、非 MOCK”属于来源真实性条件，任何自动生成文件都无法同时满足。本交付没有伪造该来源。

## Microsoft Project 验证链路

两份文件均实际经过本机 Microsoft Project 16.0：

1. 打开 MSPDI XML。
2. Microsoft Project 自己重新排程。
3. 保存为本地、无密码的 MPP。
4. 关闭 MPP。
5. 再次打开 MPP。
6. 再次计算。
7. 导出 MSPDI XML并逐字段审查。

Project 版本和文件哈希记录在各自的 `validation_report.json` 中。

## 项目 A

项目名称：市政道路雨污分流改造工程。

- 46 个任务，5 个汇总任务，5 个里程碑。
- 最大 WBS 层级为 3。
- 48 条依赖：46 条 FS、2 条 SS。
- 单一有效任务日历，周一至周五，08:00–12:00、13:00–17:00。
- 全部约束为 ASAP。
- Microsoft Project 计算日期：2027-03-01 08:00 至 2027-07-02 17:00。

## 项目 B

项目名称：110kV 变电站一次设备安装与投运工程。

- 62 个任务，12 个汇总任务，14 个里程碑。
- 最大 WBS 层级为 4。
- 65 条依赖：62 条 FS、1 条 SS、1 条 FF、1 条 SF。
- 4 条正 Lag、60 条零 Lag、1 条负 Lag。
- 约束只使用 ASAP、MSO、SNET、FNET、FNLT。
- 包含 1 个 Deadline。
- Microsoft Project 计算日期：2027-06-07 08:00 至 2027-08-16 17:00。

## 硬条件验收结果

两份文件均通过以下检查：

- MPP 文件存在、非空，Microsoft Project 16.0 可无密码重开。
- 本地文件，交付路径短于 259 个字符。
- 项目从开始日期排程。
- 所有任务均为自动计划。
- 所有任务完成率和工时完成率均为 0%。
- Actual Start、Actual Finish 均为空。
- Actual Duration、Actual Work 均为 0。
- Remaining Duration 等于 Duration。
- 未保存任何 Baseline。
- Status Date 为空。
- 资源工作表没有有效业务资源。
- 任务没有有效业务资源分配。
- 不存在跨项目依赖、外部任务、主项目或子项目。
- 不存在汇总任务依赖。
- 日历有完整七天规则，不跨午夜，没有周期性例外和继承环。

## Project 内部空资源占位说明

Microsoft Project 导出的 MSPDI 会自动包含：

- 一个 UID=0、名称为空的内部资源元数据行；
- 每个未分配资源的叶任务对应一个 `ResourceUID=-65535` 的内部 unassigned 记录。

这些记录不表示业务资源，不会在资源工作表中形成具名资源，也不表示任务已分配资源。验收报告同时记录了原始占位数量和业务计数：

- `business_resources = 0`
- `business_assignments = 0`

如果导入程序直接把所有 `<Assignment>` 节点都当成资源分配，应在 Adapter 层过滤 `ResourceUID=-65535`；否则任何正常的“未分配资源任务”都可能被误报。

## 校验

在本目录执行：

```powershell
python verify_package.py
```

校验器会验证文件集合、大小、SHA-256、JSON/XML 可解析、两份 MPP 非空，以及两份验收报告均为 `PASS`。

