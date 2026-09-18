# Yuxi 复杂排期测试套件 v1

这是一组用于验证排期导入、日历计算、依赖关系、汇总滚算、进度、约束、资源和防御性校验的黄金案例。每个目录均包含：

- `input.json`：测试输入；
- `expected.json`：冻结的期望结果；
- `actual_template.json`：被测程序应输出的结构模板。

先检查交付包自身是否完整：

```powershell
python verify_package.py
```

## 覆盖矩阵

| 案例 | 核心覆盖 | 期望状态 |
|---|---|---|
| C01 | FS、SS、FF、SF、正 Lag、负 Lag | SUCCEEDED |
| C02 | 多日历、停工日、周日补班、跨周末 | SUCCEEDED |
| C03 | 三级 WBS、嵌套汇总、并行分支、多前置 | SUCCEEDED |
| C04 | SNET、MS、FNLT、Deadline、要求完成日期 | SUCCEEDED_WITH_ISSUES |
| C05 | Baseline 0、状态日期、已完成、进行中、未开始 | SUCCEEDED |
| C06 | 资源、Assignment、超配和成本 | SUCCEEDED_WITH_ISSUES |
| C07 | 循环、悬空依赖、错误里程碑、无效日历/资源 | VALIDATION_FAILED |
| C08 | Microsoft Project 实测的 21 任务综合计划 | SUCCEEDED |

## Oracle 边界

- C08 的日期来自 Microsoft Project 16.0 保存、关闭、重开和重算后的既有观测，目录内附 `source.mpp`。
- C01–C07 来自包内冻结语义的确定性参考实现，不应声称是 Microsoft Project 观测。
- 所有日期精确到分钟，比较容差为 0。
- Lag 以工作分钟保存，默认使用后续任务日历。
- 汇总任务按直接子级最早开始和最晚完成自底向上滚算。

## 接入方法

1. 读取各案例的 `input.json`；
2. 将其映射到项目内的 `canonical_schedule_v2.2` 或真实引擎 DTO；
3. 将引擎输出转换成 `actual_template.json` 所示结构；
4. 保存为 `<actual-root>/<CASE_ID>/actual.json`；
5. 执行：

```powershell
python verify_suite.py --suite-root . --actual-root <actual-root>
```

验证为严格结构比较。任务和问题数组需要保持与 expected 相同的排序；建议 Adapter 按 `task_id`、问题严重度/代码排序后输出。

## 重要说明

- C04 中“固定开始但网络冲突”是故意设置，用于检查硬约束冲突是否被报告。
- C06 只检测资源超配，不自动执行资源平衡；成本为工时乘小时费率。
- C07 必须失败；如果引擎排出了日期，说明输入校验存在缺口。
- 开放起点和开放终点通过 `boundary_whitelist` 明确标记，不应为消除警告而添加虚假依赖。
