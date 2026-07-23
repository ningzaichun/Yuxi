# 建筑领域知识图谱样例与开发指南实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Yuxi 当前 Milvus + Neo4j 图谱链路提供一套可上传的建筑领域模拟数据、可核验的标准答案和完整的开发维护指南。

**Architecture:** 使用 6 份自然语言 Markdown 模拟同一虚构工程的跨文档知识，使用 CSV 与验证问题作为不上传的人工基线。正式教程放入 VitePress 开发指南，直接引用样例目录，并以当前路由、服务、仓储和检索实现作为代码事实来源。

**Tech Stack:** Markdown、CSV、VitePress、Vue 文档配置、Python 3.12（只读验证）、pnpm 10。

## Global Constraints

- 不修改后端业务代码、数据库结构、API 行为或前端组件行为。
- 所有项目、企业、人员、合同和事件均为虚构数据，并在每份上传文档中明确声明。
- 只有 `01` 至 `06` 的 Markdown 文件用于上传；标准实体、标准关系和验证问题不得上传。
- 正式文档必须与当前 Milvus + Neo4j + PostgreSQL 实现一致，不描述已移除的 LightRAG 运行链路。
- 文档命令不得包含真实 Token、密码或 `.env` 值。
- 正式文档新增后必须更新 `docs/.vitepress/config.mts` 导航和 `docs/develop-guides/changelog.md`。
- 变更完成后按 `code-change-log-assistant` 新建独立变更日志。

---

### Task 1: 创建建筑领域模拟数据与标准答案

**Files:**
- Create: `docs/public/examples/knowledge-graph-construction-demo/README.md`
- Create: `docs/public/examples/knowledge-graph-construction-demo/01-project-overview.md`
- Create: `docs/public/examples/knowledge-graph-construction-demo/02-participants.md`
- Create: `docs/public/examples/knowledge-graph-construction-demo/03-buildings-and-technologies.md`
- Create: `docs/public/examples/knowledge-graph-construction-demo/04-contracts-and-suppliers.md`
- Create: `docs/public/examples/knowledge-graph-construction-demo/05-quality-and-safety-events.md`
- Create: `docs/public/examples/knowledge-graph-construction-demo/06-progress-and-changes.md`
- Create: `docs/public/examples/knowledge-graph-construction-demo/expected-entities.csv`
- Create: `docs/public/examples/knowledge-graph-construction-demo/expected-relations.csv`
- Create: `docs/public/examples/knowledge-graph-construction-demo/validation-questions.md`

**Interfaces:**
- Consumes: `docs/vibe/2026-07-22-knowledge-graph-construction-tutorial-design.md` 中定义的实体、关系和虚构工程。
- Produces: 6 份上传文档，以及供正式指南引用的标准实体、关系和问题基线。

- [x] **Step 1: 创建数据集说明和 6 份上传文档**

每份上传文档首部必须包含：

```markdown
> 演示数据声明：本文档中的项目、组织、人员、合同、事件和数值均为虚构，仅用于 Yuxi 知识图谱功能测试。
```

六份文件分别覆盖项目总体、参建方、单体技术、合同供应、质量安全、进度变更，并让核心项目、单位、人员和单体跨文件重复出现。

- [x] **Step 2: 创建标准实体 CSV**

CSV 表头固定为：

```csv
entity_name,entity_type,aliases,evidence_files,notes
```

至少覆盖 Project、Building、Organization、Person、Role、Technology、Material、Equipment、Contract、Event 十类实体。

- [x] **Step 3: 创建标准关系 CSV**

CSV 表头固定为：

```csv
source,relation,target,evidence_files,notes
```

所有 source/target 必须能在实体 CSV 的 `entity_name` 中找到，关系必须属于设计规格定义的关系集合。

- [x] **Step 4: 创建验证问题**

问题分为单文档事实、跨文档关系、无答案边界三组，并为每个问题写明预期涉及实体、预期证据文件和适合的检索方式。

- [x] **Step 5: 运行样例一致性检查**

Run:

```powershell
@'
import csv
from pathlib import Path

root = Path("docs/public/examples/knowledge-graph-construction-demo")
upload_files = sorted(root.glob("0[1-6]-*.md"))
assert len(upload_files) == 6
for path in upload_files:
    text = path.read_text(encoding="utf-8")
    assert "均为虚构" in text, path

with (root / "expected-entities.csv").open(encoding="utf-8-sig", newline="") as f:
    entities = list(csv.DictReader(f))
with (root / "expected-relations.csv").open(encoding="utf-8-sig", newline="") as f:
    relations = list(csv.DictReader(f))

names = {row["entity_name"] for row in entities}
missing = [row for row in relations if row["source"] not in names or row["target"] not in names]
assert not missing, missing
print(f"upload_files={len(upload_files)}, entities={len(entities)}, relations={len(relations)}")
'@ | python -
```

Expected: exit code `0`，输出 6 个上传文件，且实体和关系数量均大于 0。

- [x] **Step 6: 提交样例数据**

```powershell
git add docs/public/examples/knowledge-graph-construction-demo
git commit -m "docs(图谱): 添加建筑领域模拟数据"
```

### Task 2: 编写正式知识图谱开发指南

**Files:**
- Create: `docs/develop-guides/knowledge-graph-development.md`

**Interfaces:**
- Consumes: Task 1 的样例文件、标准答案和验证问题；当前 `knowledge_router.py`、`milvus_graph_service.py`、`milvus.py`、PostgreSQL 模型和前端页面。
- Produces: 面向管理员和开发者的单一正式教程入口。

- [x] **Step 1: 编写架构与前置条件**

明确当前链路为 Yuxi 自研图增强 RAG：Milvus 保存 Chunk/图向量，Neo4j 保存拓扑，PostgreSQL 保存状态和来源；图谱构建由 API 进程内 `Tasker` 执行。

- [x] **Step 2: 编写样例实操教程**

按“准备 Schema → 创建 Milvus 知识库 → 上传 3 份试建 → 配置 LLM 抽取器 → 构建 → 对照标准答案 → 上传剩余文档 → 启用图检索”的顺序给出界面步骤。

- [x] **Step 3: 编写 API 示例**

PowerShell 使用 `$YUXI_API_BASE`、`$YUXI_ADMIN_TOKEN`、`$YUXI_KB_ID`，不得使用 `$HOME` 或写入真实密钥；展示 status/config/index/reset/subgraph 接口。

- [x] **Step 4: 编写维护、排障与代码地图**

覆盖增量文件、Schema/模型变化、重建、删除、并发限流、跨存储一致性、图检索未启用、API 重启，以及抽取器、图服务、图向量、仓储、路由、前端和测试入口。

- [x] **Step 5: 扫描占位符与敏感信息**

Run:

```powershell
rg -n "TBD|TODO|真实密码|sk-[A-Za-z0-9]" docs/develop-guides/knowledge-graph-development.md
```

Expected: 无输出。

### Task 3: 接入文档导航并记录变更

**Files:**
- Modify: `docs/.vitepress/config.mts`
- Modify: `docs/develop-guides/changelog.md`
- Include: `docs/vibe/2026-07-22-knowledge-graph-construction-tutorial-implementation-plan.md`
- Create: `docs/change_logs/change_YYYY-MM-DD_HH-mm-ss.md`

**Interfaces:**
- Consumes: Task 1 和 Task 2 的已完成文件。
- Produces: 文档站入口和可审计变更记录。

- [x] **Step 1: 增加 VitePress 导航**

在“开发指南”中将以下入口放在“本地开发”之后：

```ts
{ text: '知识图谱开发', link: '/develop-guides/knowledge-graph-development' },
```

- [x] **Step 2: 更新开发变更记录**

在当前版本开发记录中增加一条，说明新增建筑领域模拟数据、标准答案、完整构建教程和维护指南，不宣称修改图谱算法。

- [x] **Step 3: 新建结构化变更日志**

按 `code-change-log-assistant` 固定模板记录新增、配置和文档影响范围、原因、注意事项及验证命令。

- [x] **Step 4: 提交教程、导航和记录**

```powershell
git add docs/develop-guides/knowledge-graph-development.md docs/.vitepress/config.mts docs/develop-guides/changelog.md docs/change_logs
git add -f docs/vibe/2026-07-22-knowledge-graph-construction-tutorial-implementation-plan.md
git commit -m "docs(图谱): 添加知识图谱开发指南"
```

### Task 4: 完整验证

**Files:**
- Verify: `docs/public/examples/knowledge-graph-construction-demo/*`
- Verify: `docs/develop-guides/knowledge-graph-development.md`
- Verify: `docs/.vitepress/config.mts`

**Interfaces:**
- Consumes: Task 1 至 Task 3 的全部产物。
- Produces: 可交付的样例数据和可构建文档站。

- [x] **Step 1: 运行样例一致性检查**

重新执行 Task 1 Step 5，Expected: exit code `0`。

- [x] **Step 2: 检查 Markdown 链接与变更格式**

```powershell
git diff HEAD~2 --check
rg -n "knowledge-graph-development" docs/.vitepress/config.mts docs/develop-guides/changelog.md
```

Expected: `git diff --check` 无错误，导航和变更记录均有匹配。

- [x] **Step 3: 构建 VitePress**

```powershell
pnpm --dir docs run build
```

Expected: exit code `0`，VitePress 构建完成且无断链错误。

- [x] **Step 4: 核对最终变更范围**

```powershell
git status --short --untracked-files=all
git log -3 --oneline
```

Expected: 本任务文件均已提交；此前未跟踪的 `docs/project_analysis/project_overview_2026-07-22.md` 仍保持未跟踪且未被提交。
