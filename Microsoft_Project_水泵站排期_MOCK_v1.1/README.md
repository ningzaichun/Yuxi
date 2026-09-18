# Microsoft Project 水泵站排期 MOCK v1.1

这是用于接口、甘特图、WBS、CPM 和汇总滚算测试的合成案例。业务数据为 MOCK，但所有开始、完成和汇总日期均由 Microsoft Project 16.0 创建计划后实际计算，并在保存、关闭、重新打开和再次计算后导出。

## 数据规模

- 21 行任务，其中 5 个汇总任务、16 个叶子任务、4 个里程碑
- 最大 WBS 层级 3
- 19 条依赖：16 条 FS、3 条 SS
- 3 条正 Lag
- 周一至周五工作，08:00–12:00、13:00–17:00
- 项目日期：2026-10-12 08:00 至 2026-11-13 17:00
- 所有任务 `Estimated=false`，Project 工期列不显示 `?`

## 使用边界

- 可以测试：JSON 解析、WBS 展示、甘特图、FS/SS、正 Lag、多前置任务、CPM、汇总滚算。
- 不用于：资源均衡和成本优化；本案例的 resources/assignments 明确为空。
- 不覆盖：FF、SF、夜班、节假日、实际进度和约束日期，应使用独立案例测试。

主要文件：

- `Microsoft_Project_水泵站排期_MOCK_v1.1.json`：直接交给案例程序的 JSON。
- `Microsoft_Project_水泵站排期_MOCK_v1.1_no_milestones.json`：去里程碑变体，把 4 个里程碑改为 480 分钟活动任务（计划日期保留），用于跑通 CPM 与工期目标优化主链路；汇总滚算时长保留原始观测值，不作为 Project 重算结果。
- `source/water_pump_station_schedule_mock_v1_1.mpp`：生成和计算该 JSON 的 Project 源文件。
- `project_observation_raw.json`：Microsoft Project COM 原始观测，便于审计。
- `manifest.json`：文件大小和 SHA-256。
