---
name: 销售 CSV 报表 Demo
slug: sales-csv-report
description: "分析销售 CSV 并生成 Markdown 报表。当用户上传包含 date、product、amount 列的销售 CSV，并要求统计销售额、产品表现或每日趋势时使用。"
---

# 销售 CSV 报表 Demo

使用本 Skill 对结构固定的销售 CSV 做确定性汇总，并把最终 Markdown 报表交付给用户。

## 输入要求

CSV 必须使用 UTF-8 或 UTF-8 BOM 编码，并包含以下列：

- `date`：订单日期。
- `product`：产品名称。
- `amount`：销售金额。

允许存在其他列，但脚本不会读取。不要猜测缺失字段，不要跳过或自行修复非法金额。

## 操作流程

1. 在 `/home/gem/user-data/uploads` 或 `/home/gem/user-data/workspace` 中确认用户提供的 CSV 路径。
2. 进入 Skill 目录：

   ```bash
   cd /home/gem/skills/sales-csv-report
   ```

3. 运行报表脚本，并把结果写入 outputs：

   ```bash
   python scripts/build_report.py \
     --input /home/gem/user-data/uploads/sales.csv \
     --output /home/gem/user-data/outputs/sales-report.md
   ```

4. 如果脚本返回错误，向用户说明具体数据问题，不要生成看似成功的替代报告。
5. 脚本成功后调用 `present_artifacts`，传入 `/home/gem/user-data/outputs/sales-report.md`。
6. 最终回复简要说明报表已经生成，并概括报告中的关键指标。

## 输出内容

脚本生成的报告固定包含：

- 有效订单数。
- 总销售额。
- 平均订单金额。
- 销售额最高产品。
- 每日销售额汇总。
- 产品销售额汇总。

## 关键约束

- Skill 目录只读，不能修改其中的 `SKILL.md` 或脚本。
- 最终文件必须写入 `/home/gem/user-data/outputs/`。
- 不要把临时文件或输入 CSV 调用 `present_artifacts`。
- 金额解析、汇总和排序以脚本输出为准，不要由模型重新计算。
