# Yuxi Skills 开发指南与内置 Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一份与当前代码一致的 Yuxi Skills 开发指南，并交付可运行、可测试的内置 `sales-csv-report` Demo Skill。

**Architecture:** Demo 使用 `SKILL.md` 描述业务工作流，使用 Skill 自带的标准库 Python CLI 对销售 CSV 做确定性汇总，再通过已有 `present_artifacts` 工具交付 Markdown 报表。现有 Skill 数据模型、安装接口、权限、运行时激活和前端保持不变。

**Tech Stack:** Python 3.12+、标准库 `argparse/csv/decimal/pathlib`、Pytest、Yuxi `BuiltinSkillSpec`、Markdown、VitePress。

## Global Constraints

- Demo slug 固定为 `sales-csv-report`，版本固定为 `2026.07.23`。
- CSV 必须包含 `date`、`product`、`amount`，编码只支持 UTF-8 与 UTF-8 BOM。
- Demo 不新增第三方依赖、数据库迁移、HTTP 接口、后端 Tool、MCP 或前端组件。
- 非法业务行必须明确失败，不跳过、不猜测、不使用默认值。
- 报表金额保留两位小数；并列最高产品按名称升序选择第一个。
- 业务程序测试在宿主机 `backend` 目录的 uv 环境执行。
- 只修改本计划列出的文件，不处理工作区已有的 `docs/project_analysis/project_overview_2026-07-22.md`。

---

### Task 1: 用失败测试定义销售 CSV 报表契约

**Files:**
- Create: `backend/test/unit/agents/skills/test_sales_csv_report.py`
- Create later in Task 2: `backend/package/yuxi/agents/skills/buildin/sales-csv-report/scripts/build_report.py`

**Interfaces:**
- Consumes: `BUILTIN_SKILLS` 中 slug 为 `sales-csv-report` 的 `source_dir`。
- Produces: 对 `load_sales_rows(Path) -> list[SalesRow]`、`build_report(list[SalesRow]) -> str` 和 `main() -> int` 的行为约束。

- [ ] **Step 1: 创建失败测试**

```python
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from yuxi.agents.skills.buildin import BUILTIN_SKILLS


def _load_report_module():
    spec_item = next((item for item in BUILTIN_SKILLS if item.slug == "sales-csv-report"), None)
    assert spec_item is not None, "sales-csv-report builtin skill spec not found"
    script_path = spec_item.source_dir / "scripts" / "build_report.py"
    module_spec = importlib.util.spec_from_file_location("sales_csv_report_build_report", script_path)
    assert module_spec is not None and module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = module
    module_spec.loader.exec_module(module)
    return module


def _write_csv(path: Path, content: str, *, encoding: str = "utf-8") -> Path:
    path.write_text(content, encoding=encoding)
    return path


def test_build_report_summarizes_sales(tmp_path: Path) -> None:
    module = _load_report_module()
    input_path = _write_csv(
        tmp_path / "sales.csv",
        "date,product,amount\n"
        "2026-07-01,Alpha,100.00\n"
        "2026-07-01,Beta,50\n"
        "2026-07-02,Alpha,25.50\n",
    )

    report = module.build_report(module.load_sales_rows(input_path))

    assert "- 有效订单数：3" in report
    assert "- 总销售额：175.50" in report
    assert "- 平均订单金额：58.50" in report
    assert "- 销售额最高产品：Alpha（125.50）" in report
    assert "| 2026-07-01 | 150.00 |" in report
    assert "| Alpha | 125.50 |" in report


def test_load_sales_rows_accepts_utf8_bom(tmp_path: Path) -> None:
    module = _load_report_module()
    input_path = _write_csv(
        tmp_path / "sales.csv",
        "date,product,amount\n2026-07-01,Alpha,10\n",
        encoding="utf-8-sig",
    )

    rows = module.load_sales_rows(input_path)

    assert rows[0].date == "2026-07-01"
    assert rows[0].product == "Alpha"
    assert str(rows[0].amount) == "10"


def test_load_sales_rows_rejects_missing_columns(tmp_path: Path) -> None:
    module = _load_report_module()
    input_path = _write_csv(tmp_path / "sales.csv", "date,product\n2026-07-01,Alpha\n")

    with pytest.raises(ValueError, match="CSV 缺少必填列: amount"):
        module.load_sales_rows(input_path)


def test_load_sales_rows_rejects_invalid_amount(tmp_path: Path) -> None:
    module = _load_report_module()
    input_path = _write_csv(tmp_path / "sales.csv", "date,product,amount\n2026-07-01,Alpha,oops\n")

    with pytest.raises(ValueError, match="第 2 行 amount 不是合法有限数值"):
        module.load_sales_rows(input_path)


def test_load_sales_rows_rejects_empty_data(tmp_path: Path) -> None:
    module = _load_report_module()
    input_path = _write_csv(tmp_path / "sales.csv", "date,product,amount\n")

    with pytest.raises(ValueError, match="CSV 没有有效数据行"):
        module.load_sales_rows(input_path)


def test_main_writes_markdown_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_report_module()
    input_path = _write_csv(tmp_path / "sales.csv", "date,product,amount\n2026-07-01,Alpha,10\n")
    output_path = tmp_path / "report.md"
    monkeypatch.setattr(
        sys,
        "argv",
        ["build_report.py", "--input", str(input_path), "--output", str(output_path)],
    )

    assert module.main() == 0
    assert output_path.exists()
    assert "- 总销售额：10.00" in output_path.read_text(encoding="utf-8")
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```powershell
Set-Location backend
uv run --group test pytest test/unit/agents/skills/test_sales_csv_report.py -q
```

Expected: collection 或加载阶段失败，原因是 `sales-csv-report` 尚未注册或脚本尚不存在。

---

### Task 2: 实现最小可运行 Demo Skill

**Files:**
- Create: `backend/package/yuxi/agents/skills/buildin/sales-csv-report/SKILL.md`
- Create: `backend/package/yuxi/agents/skills/buildin/sales-csv-report/scripts/build_report.py`
- Modify: `backend/package/yuxi/agents/skills/buildin/__init__.py`
- Test: `backend/test/unit/agents/skills/test_sales_csv_report.py`

**Interfaces:**
- Consumes: CSV 文件路径和 Markdown 输出路径。
- Produces: `SalesRow(date: str, product: str, amount: Decimal)`、`load_sales_rows()`、`build_report()`、`write_report()`、`main()`，以及内置 Skill 元数据。

- [ ] **Step 1: 注册内置 Skill 的最小规格**

在 `BUILTIN_SKILLS` 追加：

```python
BuiltinSkillSpec(
    slug="sales-csv-report",
    source_dir=_SKILLS_ROOT / "sales-csv-report",
    description="分析销售 CSV，生成包含关键指标、每日趋势和产品汇总的 Markdown 报表。",
    version="2026.07.23",
    tool_dependencies=("present_artifacts",),
),
```

- [ ] **Step 2: 编写 `SKILL.md`**

Frontmatter 固定为：

```yaml
---
name: 销售 CSV 报表 Demo
slug: sales-csv-report
description: "分析销售 CSV 并生成 Markdown 报表。当用户上传包含 date、product、amount 列的销售 CSV，并要求统计销售额、产品表现或每日趋势时使用。"
---
```

正文按顺序要求 Agent：

1. 在 `/home/gem/user-data/uploads` 或 workspace 中确认输入 CSV。
2. 执行 `/home/gem/skills/sales-csv-report/scripts/build_report.py`。
3. 输出到 `/home/gem/user-data/outputs/sales-report.md`。
4. 脚本失败时原样说明数据问题，不修复或跳过非法行。
5. 成功后调用 `present_artifacts`。

- [ ] **Step 3: 实现标准库脚本**

```python
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

_REQUIRED_COLUMNS = frozenset({"date", "product", "amount"})
_CENT = Decimal("0.01")


@dataclass(frozen=True)
class SalesRow:
    date: str
    product: str
    amount: Decimal


def _required_text(value: str | None, *, field_name: str, row_number: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"第 {row_number} 行 {field_name} 不能为空")
    return normalized


def _amount(value: str | None, *, row_number: int) -> Decimal:
    normalized = str(value or "").strip()
    try:
        amount = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError(f"第 {row_number} 行 amount 不是合法有限数值") from exc
    if not normalized or not amount.is_finite():
        raise ValueError(f"第 {row_number} 行 amount 不是合法有限数值")
    return amount


def load_sales_rows(input_path: Path) -> list[SalesRow]:
    if input_path.suffix.lower() != ".csv":
        raise ValueError("输入文件必须是 .csv")
    if not input_path.exists() or not input_path.is_file():
        raise ValueError(f"输入文件不存在或不是普通文件: {input_path}")

    try:
        with input_path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            fieldnames = {str(name or "").strip() for name in (reader.fieldnames or [])}
            missing = sorted(_REQUIRED_COLUMNS - fieldnames)
            if missing:
                raise ValueError(f"CSV 缺少必填列: {', '.join(missing)}")

            rows: list[SalesRow] = []
            for row_number, raw in enumerate(reader, start=2):
                if not any(str(value or "").strip() for value in raw.values()):
                    continue
                rows.append(
                    SalesRow(
                        date=_required_text(raw.get("date"), field_name="date", row_number=row_number),
                        product=_required_text(raw.get("product"), field_name="product", row_number=row_number),
                        amount=_amount(raw.get("amount"), row_number=row_number),
                    )
                )
    except UnicodeDecodeError as exc:
        raise ValueError("输入文件必须使用 UTF-8 或 UTF-8 BOM 编码") from exc

    if not rows:
        raise ValueError("CSV 没有有效数据行")
    return rows


def _money(value: Decimal) -> str:
    return format(value.quantize(_CENT, rounding=ROUND_HALF_UP), "f")


def build_report(rows: list[SalesRow]) -> str:
    if not rows:
        raise ValueError("销售数据不能为空")

    daily_totals: dict[str, Decimal] = defaultdict(Decimal)
    product_totals: dict[str, Decimal] = defaultdict(Decimal)
    for row in rows:
        daily_totals[row.date] += row.amount
        product_totals[row.product] += row.amount

    total = sum((row.amount for row in rows), Decimal())
    average = total / Decimal(len(rows))
    top_product, top_amount = sorted(product_totals.items(), key=lambda item: (-item[1], item[0]))[0]

    lines = [
        "# 销售 CSV 报表",
        "",
        "## 数据概览",
        "",
        f"- 有效订单数：{len(rows)}",
        f"- 总销售额：{_money(total)}",
        f"- 平均订单金额：{_money(average)}",
        f"- 销售额最高产品：{top_product}（{_money(top_amount)}）",
        "",
        "## 每日销售额",
        "",
        "| 日期 | 销售额 |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {date} | {_money(amount)} |" for date, amount in sorted(daily_totals.items()))
    lines.extend(
        [
            "",
            "## 产品销售额",
            "",
            "| 产品 | 销售额 |",
            "| --- | ---: |",
        ]
    )
    lines.extend(
        f"| {product} | {_money(amount)} |"
        for product, amount in sorted(product_totals.items(), key=lambda item: (-item[1], item[0]))
    )
    return "\n".join(lines) + "\n"


def write_report(input_path: Path, output_path: Path) -> Path:
    if output_path.suffix.lower() != ".md":
        raise ValueError("输出文件必须是 .md")
    if input_path.resolve() == output_path.resolve():
        raise ValueError("输出文件不能覆盖输入文件")

    report = build_report(load_sales_rows(input_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="根据销售 CSV 生成 Markdown 报表")
    parser.add_argument("--input", required=True, type=Path, help="输入 CSV 文件")
    parser.add_argument("--output", required=True, type=Path, help="输出 Markdown 文件")
    args = parser.parse_args()

    try:
        output_path = write_report(args.input, args.output)
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(output_path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

实现规则：

- 输入用 `encoding="utf-8-sig", newline=""` 打开。
- 使用 `csv.DictReader`，先验证 `fieldnames` 包含全部必填列。
- `date`、`product` 去除首尾空白后不能为空。
- `amount` 使用 `Decimal`，并调用 `is_finite()`。
- 使用 `defaultdict(Decimal)` 汇总日期和产品金额。
- 日期表按日期文本升序；产品表按 `(-amount, product)` 排序。
- CLI 捕获 `ValueError` 和 `OSError`，将 `Error: <具体错误>` 写入 stderr 并返回 `1`。
- 输出父目录使用 `mkdir(parents=True, exist_ok=True)`，但禁止输入输出 resolve 后相同。

- [ ] **Step 4: 运行脚本测试并确认 GREEN**

Run:

```powershell
Set-Location backend
uv run --group test pytest test/unit/agents/skills/test_sales_csv_report.py -q
```

Expected: 全部通过。

- [ ] **Step 5: 格式化与检查 Demo 代码**

Run:

```powershell
Set-Location backend
uv run ruff format package/yuxi/agents/skills/buildin/sales-csv-report/scripts/build_report.py test/unit/agents/skills/test_sales_csv_report.py
uv run ruff check package/yuxi/agents/skills/buildin/__init__.py package/yuxi/agents/skills/buildin/sales-csv-report/scripts/build_report.py test/unit/agents/skills/test_sales_csv_report.py
```

Expected: 格式化成功，Lint 无错误。

---

### Task 3: 增加内置注册回归测试

**Files:**
- Modify: `backend/test/unit/services/test_skill_service.py`
- Test: `backend/test/unit/services/test_skill_service.py`

**Interfaces:**
- Consumes: `list_builtin_skill_specs()`。
- Produces: 对 Demo slug、展示名、版本、依赖和目录内容的回归保护。

- [ ] **Step 1: 先写注册断言**

新增：

```python
def test_sales_csv_report_builtin_skill_spec():
    specs = {spec["slug"]: spec for spec in svc.list_builtin_skill_specs()}

    report = specs["sales-csv-report"]
    assert report["name"] == "销售 CSV 报表 Demo"
    assert report["version"] == "2026.07.23"
    assert report["tool_dependencies"] == ["present_artifacts"]
    assert report["mcp_dependencies"] == []
    assert report["skill_dependencies"] == []
    assert (report["source_dir"] / "SKILL.md").exists()
    assert (report["source_dir"] / "scripts" / "build_report.py").exists()
```

- [ ] **Step 2: 运行注册测试**

Run:

```powershell
Set-Location backend
uv run --group test pytest test/unit/services/test_skill_service.py::test_sales_csv_report_builtin_skill_spec -q
```

Expected: PASS；如失败，只修正 Demo 注册或文件内容，不修改通用 Skill 服务。

- [ ] **Step 3: 运行 Skill 服务与中间件回归**

Run:

```powershell
Set-Location backend
uv run --group test pytest test/unit/services/test_skill_service.py test/unit/middlewares/test_skills_middleware.py test/unit/backends/test_skills_backend.py -q
```

Expected: 全部通过。

---

### Task 4: 编写正式 Skills 开发指南

**Files:**
- Create: `docs/develop-guides/skills-development.md`
- Modify: `docs/.vitepress/config.mts`
- Modify: `docs/agents/skills-management.md`

**Interfaces:**
- Consumes: 当前 `skill_router.py`、`skills/service.py`、`middlewares/skills.py`、`context.py`、`remote_install.py` 和 Demo。
- Produces: 面向后续开发者的正式说明与有效导航。

- [ ] **Step 1: 新建开发指南**

文档按下列固定章节编写：

1. 先选择 Skill、Tool 或 MCP。
2. 当前架构和运行链路。
3. Skill 目录与 `SKILL.md` 协议。
4. 三种业务实现形态。
5. 开发内置 Skill。
6. 开发沙盒脚本。
7. 绑定后端 Tool、MCP 和其他 Skill。
8. 上传外置 Skill。
9. 安装远程 Skill。
10. 权限、共享范围和启停。
11. 运行时选择、依赖闭包和动态激活。
12. 测试、同步和真实链路验证。
13. 更新与发布。
14. 常见问题。
15. Checklist。
16. `sales-csv-report` Demo 代码地图。

必须明确：

- 内容存 `${save_dir}/skills/<slug>`，索引存 `skills` 表。
- 上传/远程安装经过 prepare draft 和 confirm 两阶段。
- 内置 Skill 由源码目录和 `BuiltinSkillSpec` 管理，不能在管理页直接编辑或删除。
- `context.skills is None` 表示默认全部可访问 Skill，显式 `[]` 表示不选。
- Skill 依赖闭包只扩大可见/可读范围；依赖 Skill 仍需读取自己的 `SKILL.md` 才激活其工具。
- 脚本通过 Sandbox terminal 执行；需要 Yuxi 内部权限数据时应实现后端 Tool。

- [ ] **Step 2: 更新导航**

在“Agent 工具开发”之后增加：

```typescript
{ text: 'Skills 开发', link: '/develop-guides/skills-development' },
```

- [ ] **Step 3: 修正管理文档中的过时说明**

把“Skill 中的脚本仅作为提示词参考，Agent 无法直接执行”替换为：

```markdown
Skill 目录对文件系统工具只读，但选中的 Skill 会复制到线程 Skills 目录并挂载到 Sandbox 的 `/home/gem/skills`。Agent 可以通过 terminal 执行其中的脚本；脚本不能修改 Skill 目录，应把结果写入 `/home/gem/user-data/workspace` 或 `/home/gem/user-data/outputs`。需要访问 Yuxi 后端内部对象或权限数据时，应通过受控后端 Tool，而不是让沙盒脚本读取后端进程环境。
```

- [ ] **Step 4: 校验文档事实和导航**

Run:

```powershell
rg -n "sales-csv-report|prepare|confirm|activated_skills|context.skills|present_artifacts" docs/develop-guides/skills-development.md
rg -n "Skills 开发" docs/.vitepress/config.mts
```

Expected: 每个核心事实至少命中一次，导航命中一次。

---

### Task 5: 更新发布记录并完成最终验证

**Files:**
- Modify: `docs/develop-guides/changelog.md`
- Create: `docs/change_logs/change_2026-07-23_HH-mm-ss.md`

**Interfaces:**
- Consumes: 最终真实 diff 和测试结果。
- Produces: 当前版本开发记录与一次独立中文变更日志。

- [ ] **Step 1: 更新当前版本 changelog**

在 `v0.7.1` 的“开发记录”顶部增加一条，准确记录：

- 新增 Skills 开发指南。
- 覆盖内置、上传、远程安装和运行时门控。
- 新增 `sales-csv-report` Demo。
- Demo 使用标准库脚本生成 Markdown 并通过 `present_artifacts` 交付。
- 修正文档中过时的脚本执行限制。

- [ ] **Step 2: 运行完整相关测试**

Run:

```powershell
Set-Location backend
uv run --group test pytest test/unit/agents/skills/test_sales_csv_report.py test/unit/services/test_skill_service.py test/unit/middlewares/test_skills_middleware.py test/unit/backends/test_skills_backend.py -q
```

Expected: 全部通过。

- [ ] **Step 3: 运行最终格式与静态检查**

Run:

```powershell
Set-Location backend
uv run ruff format --check package/yuxi/agents/skills/buildin/sales-csv-report/scripts/build_report.py test/unit/agents/skills/test_sales_csv_report.py
uv run ruff check package/yuxi/agents/skills/buildin/__init__.py package/yuxi/agents/skills/buildin/sales-csv-report/scripts/build_report.py test/unit/agents/skills/test_sales_csv_report.py test/unit/services/test_skill_service.py
```

Expected: 全部通过。

- [ ] **Step 4: 生成真实变更日志**

按 `code-change-log-assistant` 固定模板创建一个新的时间戳文件，列出实际修改文件、原因、风险和已执行测试。不得覆盖现有日志。

- [ ] **Step 5: 仓库级检查**

Run:

```powershell
git diff --check
git status --short
```

Expected: `git diff --check` 无输出；状态只包含本任务文件和既有未跟踪 `docs/project_analysis/project_overview_2026-07-22.md`。

- [ ] **Step 6: 提交实现**

只暂存本任务文件，提交信息：

```text
feat(skills): 添加销售报表演示与开发指南
```
