# Yuxi Skills 开发指南与内置 Demo 设计

## 1. 背景

Yuxi 已具备内置、上传和远程三类 Skill 的安装、管理、权限过滤和运行时激活能力，但现有开发文档缺少一条面向后续开发者的完整实现路径。当前 `docs/agents/skills-management.md` 主要面向使用和管理，其中关于沙盒脚本执行的说明也与当前实现不一致。

本次工作需要补齐正式开发指南，并新增一个可运行、可测试、无外部依赖的内置 Demo Skill，作为后续业务 Skill 开发的参考实现。

## 2. 目标

1. 新增正式的 Yuxi Skills 开发指南，覆盖扩展方式选择、目录结构、内置注册、外部安装、依赖、权限、运行时、测试、调试和发布。
2. 新增内置 `sales-csv-report` Skill，展示 `SKILL.md`、沙盒脚本、内置注册、工具依赖和产物交付的完整链路。
3. 修正现有 Skills 管理文档中关于沙盒脚本不可执行的过时说明。
4. 为 Demo 的确定性脚本和内置注册行为补充单元测试。

## 3. 非目标

- 不新增数据库表或迁移。
- 不新增或修改 Skills HTTP 接口。
- 不新增前端页面、表单或专用工具渲染组件。
- 不新增后端业务 Tool、MCP 服务或外部 API。
- 不为 Demo 引入第三方 Python 依赖。
- 不修改现有 Skill 的运行时激活机制。

## 4. 总体方案

### 4.1 正式开发指南

新增 `docs/develop-guides/skills-development.md`，并加入 VitePress 的“开发文档”导航。文档至少覆盖：

1. Skill、内置 Tool、MCP 的选择边界。
2. Skill 的文件系统内容与数据库索引模型。
3. `SKILL.md` frontmatter、正文和资源目录规范。
4. 纯工作流型、沙盒脚本型、后端 Tool 驱动型三种实现方式。
5. 内置 Skill 的目录、`BuiltinSkillSpec` 注册、版本与启动同步。
6. ZIP / `SKILL.md` 上传的解析草稿、确认安装和共享范围。
7. GitHub、skills.sh、ModelScope 等远程来源的安装流程和限制。
8. Agent 配置、权限过滤、Skill 依赖闭包、读取激活和 Tool/MCP 门控。
9. 单元测试、集成测试、端到端验证、格式化与 Lint。
10. 更新、启停、删除、常见故障和提交前 Checklist。

文档只描述仓库当前真实行为；管理端和运行时说明分别引用现有实现，不创造新的兼容层。

### 4.2 内置 Demo Skill

新增目录：

```text
backend/package/yuxi/agents/skills/buildin/sales-csv-report/
├── SKILL.md
└── scripts/
    └── build_report.py
```

`SKILL.md` 负责：

- 定义“分析销售 CSV 并生成 Markdown 报表”的触发场景。
- 要求输入 CSV 包含 `date`、`product`、`amount` 三列。
- 指导 Agent 在 `/home/gem/skills/sales-csv-report` 下运行脚本。
- 要求报告写入 `/home/gem/user-data/outputs/`。
- 要求脚本成功后调用 `present_artifacts` 展示最终产物。
- 约束 Agent 不猜测缺失数据、不自行修复非法金额、不输出伪造结论。

`build_report.py` 负责确定性计算和文件输出，不负责自然语言推理。

## 5. Demo 脚本契约

### 5.1 CLI

```bash
python scripts/build_report.py \
  --input /home/gem/user-data/uploads/sales.csv \
  --output /home/gem/user-data/outputs/sales-report.md
```

### 5.2 输入

- 文件格式：CSV。
- 编码：UTF-8 或 UTF-8 BOM。
- 必填列：
  - `date`：作为每日汇总分组键，不额外解释日期语义。
  - `product`：产品名称，去除首尾空白后不能为空。
  - `amount`：使用 `Decimal` 解析的销售金额。
- 允许存在额外列，但 Demo 不读取额外列。
- 空行可以由 `csv.DictReader` 正常处理；没有有效数据行时失败。

### 5.3 输出

Markdown 报表固定包含：

1. 数据概览。
2. 有效订单数。
3. 总销售额。
4. 平均订单金额。
5. 销售额最高产品。
6. 按日期升序排列的每日销售额表格。
7. 按销售额降序排列的产品销售额表格。

金额统一保留两位小数。最高产品出现并列时，按产品名称升序选择第一个，保证输出稳定。

### 5.4 失败行为

以下情况返回非零退出码，并把清晰错误信息写入标准错误：

- 输入文件不存在或不是普通文件。
- 输入文件不是 `.csv`。
- 缺少任一必填列。
- 没有有效数据行。
- `product` 为空。
- `amount` 为空、不是合法数字或不是有限数值。
- 输出路径不是 `.md`。

脚本不跳过非法业务行，不用默认值替代错误数据，也不覆盖输入文件。

## 6. 内置注册

在 `backend/package/yuxi/agents/skills/buildin/__init__.py` 中新增 `BuiltinSkillSpec`：

- `slug`: `sales-csv-report`
- `source_dir`: `_SKILLS_ROOT / "sales-csv-report"`
- `version`: `2026.07.23`
- `tool_dependencies`: `("present_artifacts",)`
- 不声明 MCP 或其他 Skill 依赖

API 和 Worker 启动时沿用现有 `init_builtin_skills()` 完成目录复制、内容哈希计算和数据库同步。管理员也可以使用现有 `/api/system/skills/builtin/sync` 接口手动同步。

## 7. 测试设计

### 7.1 脚本单元测试

新增 `backend/test/unit/agents/skills/test_sales_csv_report.py`，通过 `importlib` 从内置 Skill 目录加载脚本，覆盖：

1. 正常数据生成稳定汇总。
2. UTF-8 BOM 表头可正确读取。
3. 缺少必填列时报错。
4. 非法金额时报错。
5. 空数据时报错。
6. CLI 成功写出 Markdown 文件。

测试使用 `tmp_path` 构造输入和输出，不访问网络、数据库或真实 Sandbox。

### 7.2 注册回归

更新 `backend/test/unit/services/test_skill_service.py`，断言：

- `sales-csv-report` 存在于内置 Skill 规格。
- 名称、版本和 `present_artifacts` 依赖正确。
- `SKILL.md` 与脚本文件存在。

### 7.3 文档验证

- 检查 VitePress 导航链接对应的 Markdown 文件存在。
- 使用 `git diff --check` 检查空白和补丁格式。
- 文档内容与当前 API 路由、Skill 运行时和管理行为交叉核对。

## 8. 文档同步

需要修改：

- `docs/.vitepress/config.mts`：增加 Skills 开发指南导航。
- `docs/agents/skills-management.md`：修正脚本执行与虚拟挂载说明。
- `docs/develop-guides/changelog.md`：记录新增指南和 Demo。
- `docs/change_logs/change_YYYY-MM-DD_HH-mm-ss.md`：记录本次真实变更。

## 9. 验收标准

- 正式开发指南能够让不了解当前实现的开发者完成内置 Skill 开发，并理解上传和远程 Skill 的安装链路。
- `sales-csv-report` 能被 `list_builtin_skill_specs()` 正确解析和注册。
- Demo 脚本能从符合契约的 CSV 生成稳定 Markdown 报表。
- 所有约定失败输入都明确失败，不产生看似成功的报告。
- Agent 读取 Demo 的 `SKILL.md` 后能按说明运行脚本，并通过 `present_artifacts` 交付结果。
- 相关单元测试、格式化、Lint 和文档检查通过。
- 不引入数据库、接口、前端或第三方依赖变更。
