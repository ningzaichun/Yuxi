# Yuxi 大型复杂排期测试套件 v1

本套件用于验证排期引擎在大任务量、复杂 WBS、多日历、混合依赖、进度更新、资源冲突和错误输入下的正确性与稳定性。

每个案例包含：

- `input.json`：完整测试输入；
- `expected.json`：冻结的确定性参考结果；
- `actual_template.json`：被测系统输出模板；
- `case_profile.json`：案例目标、规模和预期信号。

## 案例

| 案例 | 规模目标 | 主要覆盖 |
|---|---:|---|
| L01 EPC | 195项 | 四级WBS、FS/SS/FF/SF、正负Lag、Baseline、资源与成本 |
| L02 多标段 | 约230项 | 8标段、24作业面、并行DAG、共享资源、跨标段协调 |
| L03 多日历 | 约130项 | 标准班、六天班、夜班、24x7、双班制、停工和补班 |
| L04 进度更新 | 约160项 | Baseline、状态日期、已完成、进行中、Deadline和预测偏差 |
| L05 错误压力 | 约127项 | 循环、悬空依赖、错误里程碑、无效日历、父级和资源 |
| L06 性能DAG | 573项 | 500活动、四级WBS、20资源、500 Assignment、资源争用与规模性能 |

## Oracle 声明

六个案例全部是合成测试数据，日期由冻结的确定性参考实现生成，不是 Microsoft Project 观测。它们适合验证你自己的实现是否符合本包语义，但不能单独证明与 Microsoft Project 完全兼容。

固定语义：

- 时间单位：工作分钟；
- 时间精度：分钟，容差为 0；
- 默认 Lag 日历：后续任务日历；
- 汇总日期：直接子级最早开始与最晚完成，自底向上滚算；
- 合法开放起止点：通过 `boundary_whitelist` 标记；
- 资源冲突：同一时间段 Assignment units 之和大于 max_units；
- 成本：工作小时 × 标准小时费率。

## 使用

完整性检查：

```powershell
python verify_package.py
```

把被测结果保存为 `<actual-root>/<CASE_ID>/actual.json`，然后运行：

```powershell
python verify_suite.py --suite-root . --actual-root <actual-root>
```

校验为严格比较。一分钟、一个问题码或一条资源冲突的差异都会失败。

## 接入建议

先从 L05 校验输入防线，再依次接入 L03、L01、L04、L02，最后运行 L06 性能案例。生产项目使用 `canonical_schedule_v2.2` 时，请参考 `adapter_mapping.json` 做字段映射，不要直接把测试合同名称冒充生产合同。
