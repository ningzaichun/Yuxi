# Skills 开发指南

本文档说明如何为 Yuxi 设计、开发、安装、测试和发布 Skill。它同时覆盖随源码发布的内置 Skill、通过管理页上传的外置 Skill，以及从远程仓库安装的外置 Skill。

## 1. 先选择 Skill、Tool 或 MCP

Skill 的核心作用是告诉 Agent 在什么场景下，按什么工作流使用已有能力。不要把所有扩展都实现成 Skill。

| 扩展方式 | 适用场景 | 代码运行位置 | 发布方式 |
| --- | --- | --- | --- |
| Skill | 工作流、领域规则、提示说明、沙盒脚本、工具组合 | 指令由 Agent 执行；脚本在 Sandbox 中执行 | 内置随 Yuxi 发布，或外置安装 |
| 后端 Tool | 需要访问 Yuxi 内部对象、当前用户权限、数据库或可信后端服务 | API/Worker 进程 | 随 Yuxi 后端发布 |
| MCP | 独立外部系统，希望单独部署和动态接入 | MCP Server | 独立部署，在扩展管理中配置 |

选择规则：

- 只是教 Agent 如何组合现有能力：使用 Skill。
- 需要确定性处理用户文件，但不需要 Yuxi 内部权限数据：使用 Skill 内的 Sandbox 脚本。
- 需要数据库、知识库权限、运行时上下文或后端密钥：实现受控后端 Tool，再由 Skill 声明依赖。
- 能力属于可独立部署的外部系统：优先实现 MCP，再由 Skill 声明 MCP 依赖和使用顺序。

Skill 不是任意后端代码插件。上传或远程安装的脚本不会进入 API/Worker 进程，只能在隔离的 Sandbox 中由 Agent 显式执行。

## 2. 当前架构和运行链路

Yuxi 的 Skills 使用“文件系统存内容、数据库存索引”的结构：

```text
内置源码目录 / 上传文件 / 远程仓库
  → 校验 SKILL.md 和目录
  → 内容写入 ${save_dir}/skills/<slug>
  → 元数据写入 skills 表
  → Agent 配置选择 context.skills
  → 运行前按用户权限过滤并展开 Skill 依赖闭包
  → 复制到 ${save_dir}/threads/<skills_thread_id>/skills
  → 只读挂载到 Sandbox /home/gem/skills
  → Agent 读取 <slug>/SKILL.md 激活该 Skill
  → 后续模型请求开放该 Skill 的 Tool/MCP 依赖
```

主要代码位置：

- `backend/package/yuxi/agents/skills/buildin/`：内置 Skill 源码。
- `backend/package/yuxi/agents/skills/buildin/__init__.py`：`BuiltinSkillSpec` 注册表。
- `backend/package/yuxi/agents/skills/service.py`：存储、解析、安装草稿、权限、依赖和内置同步。
- `backend/server/routers/skill_router.py`：上传、远程安装、确认草稿和管理接口。
- `backend/package/yuxi/agents/middlewares/skills.py`：提示词注入、依赖闭包、激活和工具/MCP 门控。
- `backend/package/yuxi/agents/context.py`：`context.skills` 默认值和权限过滤。
- `backend/package/yuxi/agents/backends/sandbox/backend.py`：线程 Skill 快照和 Sandbox 接入。
- `backend/package/yuxi/agents/backends/skills_backend.py`：非 Sandbox 文件工具的只读 Skill 边界。
- `backend/package/yuxi/agents/skills/remote_install.py`：远程发现和下载。

内置 Skill 的源码目录不是正式运行目录。应用启动或管理员同步内置 Skills 时，`init_builtin_skills()` 会把源码复制到 `${save_dir}/skills/<slug>`，并创建或更新 `skills` 表记录。

## 3. Skill 目录与 `SKILL.md` 协议

最小目录只有一个根级 `SKILL.md`：

```text
my-skill/
└── SKILL.md
```

需要确定性脚本或较长资料时再增加对应目录：

```text
my-skill/
├── SKILL.md
├── scripts/
│   └── process.py
├── references/
│   └── domain-rules.md
└── assets/
    └── template.md
```

- `scripts/`：需要重复执行的确定性处理程序。
- `references/`：只在具体任务需要时读取的领域资料。
- `assets/`：生成产物时复用的模板或静态资源。
- 不要为了形式创建空目录、Skill 自己的 README、安装指南或 changelog。

### 3.1 Frontmatter

Yuxi 的 `SKILL.md` 必须以 YAML frontmatter 开头：

```markdown
---
name: 销售 CSV 报表
slug: sales-csv-report
description: "分析销售 CSV 并生成 Markdown 报表。当用户上传包含 date、product、amount 列的销售 CSV，并要求统计销售额、产品表现或每日趋势时使用。"
---
```

| 字段 | 必填 | 规则 |
| --- | --- | --- |
| `name` | 是 | 面向用户的展示名，非空，最长 128 字符 |
| `slug` | 建议必填 | 稳定唯一标识，只能使用小写字母、数字和单短横线分段，最长 128 字符 |
| `description` | 是 | 同时说明能力和触发场景，供 Agent 选择是否读取 Skill |
| `tool_dependencies` | 否 | 外置 Skill 需要的后端 Tool slug 列表 |
| `mcp_dependencies` | 否 | 外置 Skill 需要的 MCP Server slug 列表 |
| `skill_dependencies` | 否 | 外置 Skill 需要可读的其他 Skill slug 列表 |

为兼容旧格式，省略 `slug` 时系统会把 `name` 当作 slug 校验。这要求 `name` 也符合英文 slug 规则，因此新 Skill 应显式填写 `slug`，把展示名称和稳定标识分开。

### 3.2 正文写法

正文应使用可执行的祈使句，至少回答：

1. 什么输入可以使用该 Skill。
2. Agent 必须按什么顺序操作。
3. 成功产物写到哪里、如何交付。
4. 哪些情况必须明确失败。
5. 哪些行为禁止猜测、跳过或静默回退。

只保留 Agent 不容易自行推断的领域规则。详细 Schema、长示例或参考资料放到 `references/`，并在 `SKILL.md` 中说明何时读取。

## 4. 三种业务实现形态

### 4.1 纯指令型

适合评审清单、写作规范、研究流程和已有工具编排。目录只需要 `SKILL.md`，不包含可执行代码。

验收重点是：触发条件明确、步骤有顺序、成功和失败边界清楚。

### 4.2 Skill + Sandbox 脚本

适合 CSV 汇总、格式转换、模板渲染和本地文件检查。把确定性逻辑放进 `scripts/`，让 Agent 通过 terminal 执行。

这种形态不应访问 Yuxi 数据库、API 进程内对象或后端环境。输入来自 `/home/gem/user-data/uploads` 或 `/home/gem/user-data/workspace`，结果写入 `/home/gem/user-data/workspace` 或 `/home/gem/user-data/outputs`。

### 4.3 Skill + Tool/MCP/其他 Skill

适合需要内部权限数据或外部系统的业务流程。Skill 只描述调用顺序和业务判断，可执行能力由受控 Tool 或 MCP 提供。

例如知识库 Skill 依赖知识库后端 Tools；Agent 读取 Skill 后，Tools 才对后续模型请求可见。不要在 Sandbox 脚本中绕过这条权限边界。

## 5. 开发内置 Skill

内置 Skill 随 Yuxi 源码发布，适合默认提供、需要版本化并由项目维护的能力。

### 5.1 先定义契约并写失败测试

开始实现前明确：

- 用户会如何触发。
- 输入文件或参数格式。
- 输出产物和交付方式。
- 非法输入和外部依赖失败时的行为。
- 明确不做的能力。

脚本行为测试放在 `backend/test/unit/agents/skills/`；注册与同步测试放在已有 Skill 服务测试中。先运行测试，确认它因为 slug 未注册或实现不存在而失败。

### 5.2 创建目录

```text
backend/package/yuxi/agents/skills/buildin/<slug>/
├── SKILL.md
└── scripts/                 # 只有需要时才创建
```

目录名、`SKILL.md` 的 `slug` 和 `BuiltinSkillSpec.slug` 必须一致。

### 5.3 注册 `BuiltinSkillSpec`

在 `backend/package/yuxi/agents/skills/buildin/__init__.py` 的 `BUILTIN_SKILLS` 中追加：

```python
BuiltinSkillSpec(
    slug="sales-csv-report",
    source_dir=_SKILLS_ROOT / "sales-csv-report",
    description="分析销售 CSV，生成包含关键指标、每日趋势和产品汇总的 Markdown 报表。",
    version="2026.07.23",
    tool_dependencies=("present_artifacts",),
)
```

字段含义：

- `slug`：稳定 ID。
- `source_dir`：内置源码目录。
- `description`：注册表描述；非空时覆盖 `SKILL.md` description 写入数据库。
- `version`：内置版本。修改内置内容时同步递增。
- `tool_dependencies`、`mcp_dependencies`、`skill_dependencies`：运行时依赖。

内置注册表中的非空依赖优先于 frontmatter。依赖应只维护在一个主要位置；内置 Skill 推荐以 `BuiltinSkillSpec` 为准。

### 5.4 同步和发现

应用启动会同步内置 Skills；管理员也可调用：

```text
POST /api/system/skills/builtin/sync
```

同步会：

1. 校验 slug、目录和根级 `SKILL.md`。
2. 计算整个目录的 `content_hash`。
3. 替换 `${save_dir}/skills/<slug>` 中的运行副本。
4. 创建或更新 `skills` 表中的名称、描述、版本、依赖和 hash。

内置 Skill 默认全局共享并启用。管理页可以启停，但不能直接编辑文件、修改共享范围或删除；变更必须回到源码和注册表。

## 6. 开发 Sandbox 脚本

脚本应把同一任务中需要重复生成、容易算错或必须稳定复现的逻辑从模型推理中移出。

推荐规则：

- 优先使用 Python 标准库；确需第三方包时，先确认 Sandbox 镜像已有依赖并记录部署要求。
- 使用明确的命令行参数，不依赖当前工作目录猜测输入。
- 从 uploads/workspace 读取，从 workspace/outputs 写入。
- Skill 目录是只读挂载，不要在脚本旁生成缓存或产物。
- 参数错误、编码错误和非法业务行应返回非零退出码，并把具体原因写到 stderr。
- 不要跳过非法行、填默认值或在失败后生成看似成功的产物。
- 不读取 API/Worker 的 `.env`，不尝试直接连接内部数据库。

典型命令：

```bash
python /home/gem/skills/sales-csv-report/scripts/build_report.py \
  --input /home/gem/user-data/uploads/sales.csv \
  --output /home/gem/user-data/outputs/sales-report.md
```

完成后通过 `present_artifacts` 交付 outputs 中的文件。`present_artifacts` 只负责产物展示，不替代输入校验或报表生成。

脚本至少测试：

- 一条主成功路径。
- 支持的编码或格式边界。
- 缺少字段。
- 非法业务值。
- 空数据。
- CLI 能创建父目录并写出最终文件。

## 7. 绑定后端 Tool、MCP 和其他 Skill

外置 Skill 可以在 frontmatter 声明依赖：

```yaml
tool_dependencies:
  - query_internal_data
mcp_dependencies:
  - mcp-server-chart
skill_dependencies:
  - base-reporting
```

内置 Skill 则在 `BuiltinSkillSpec` 中使用同名字段。

依赖设计约束：

- Tool slug 应对应已注册的 Tool。
- MCP slug 应对应已启用的 MCP Server。
- Skill 依赖应存在、已启用且共享范围不窄于父 Skill。
- 不允许依赖自身；运行时会检测循环，但设计阶段仍应避免循环和深依赖链。

通过管理接口修改已安装 Skill 的依赖时，`update_skill_dependencies()` 会验证以上约束。上传或远程安装的 confirm 流程当前只解析并持久化 frontmatter 依赖，不调用同一验证函数；无效 Tool 会在运行时找不到实例，无效 MCP 会记录不可用警告，无效 Skill 会在闭包展开时跳过。因此，外置 Skill 上线前必须在目标环境检查依赖选项并完成真实激活验证，不能把安装成功当作依赖可用。

`skill_dependencies` 的语义容易误解：依赖闭包只会把依赖 Skill 加入提示词和只读文件范围。读取父 Skill 的 `SKILL.md` 只激活父 Skill 自己声明的 Tool/MCP；如果流程还要使用依赖 Skill 的 Tool/MCP，Agent 必须再读取那个依赖 Skill 自己的 `SKILL.md`。

## 8. 上传外置 Skill

上传支持 ZIP 或单个 `SKILL.md`，采用 prepare draft 和 confirm 两阶段：

```text
POST /api/skills/import/prepare
  → 返回 draft_id、解析结果、可选共享范围

POST /api/skills/install-drafts/{draft_id}/confirm
  → 正式复制内容并创建 skills 表记录
```

也可在确认前放弃：

```text
DELETE /api/skills/install-drafts/{draft_id}
```

ZIP 必须且只能包含一个根级 Skill 入口；系统会拒绝绝对路径和 `..` 路径穿越。若 slug 已存在，prepare 阶段会生成 `-v2`、`-v3` 等可用 slug，并在确认时同步改写 frontmatter。

草稿默认一小时过期。确认时仍会再次检查 slug、路径、权限和占用情况，不能把 prepare 成功当作安装已经完成。

## 9. 安装远程 Skill

远程安装同样采用两阶段：

```text
POST /api/skills/remote/list
  → 发现来源中的 Skills

POST /api/skills/remote/prepare
  → 下载所选 Skills 并生成草稿

POST /api/skills/install-drafts/{draft_id}/confirm
  → 确认共享范围并正式安装
```

`remote_install.py` 使用隔离的临时 HOME 调用 `npx skills`，从临时 `.agents/skills` 中提取内容；临时目录不是 Yuxi 的正式存储。只有 confirm 成功后，内容才进入 `${save_dir}/skills/<slug>` 和 `skills` 表。

远程来源和社区 Skill 应按不可信输入审查：

- 检查 `SKILL.md` 是否诱导访问不必要的数据。
- 阅读所有脚本和命令。
- 检查依赖的 Tool/MCP 是否超出业务范围。
- 不安装要求读取后端密钥或绕过权限的脚本。
- 在非生产环境完成真实调用验证后再扩大共享范围。

## 10. 权限、共享范围和启停

`skills` 表使用 `source_type`、`share_config` 和 `enabled` 控制来源与可见性：

| 字段 | 可选值或含义 |
| --- | --- |
| `source_type` | `builtin`、`upload`、`remote` |
| `share_config.access_level` | `global`、`department`、`user` |
| `enabled` | 是否可进入 Agent 配置和运行时 |

- 管理员可为外置 Skill 选择全局、部门或用户范围。
- 普通用户安装时只能使用个人范围。
- 创建者可以管理自己的非内置 Skill。
- 内置 Skill 固定为全局范围；管理员只能启停。
- Agent 配置和运行时始终再次按当前用户权限过滤，不能仅依赖前端隐藏。

## 11. 运行时选择、依赖闭包和动态激活

`context.skills` 有两种不同语义：

- `None`：未显式配置，归一化时选择当前用户可访问且启用的全部 Skills。
- `[]`：显式不选择任何 Skill。

非空列表会过滤掉当前用户无权访问、已禁用或不存在的 slug。

运行时分为三步：

1. **选择与闭包**：`prepare_agent_runtime_context()` 过滤 `context.skills`，递归展开 `skill_dependencies`，生成 `_prompt_skills` 和 `_readable_skills`。
2. **挂载与提示**：可读 Skill 复制到线程快照并只读挂载到 `/home/gem/skills`；中间件把名称、描述和 `SKILL.md` 路径注入模型请求。
3. **激活与门控**：Agent 使用 `read_file` 读取精确路径 `/home/gem/skills/<slug>/SKILL.md` 后，slug 写入 `activated_skills`；后续模型请求才开放该 Skill 的 Tool/MCP 依赖。

本地 Tool 会在构建 Agent 时预先注册到 ToolNode，以保证可执行；`SkillsMiddleware` 只控制它何时对模型可见。MCP 工具在已激活 Skill 需要时加载。

## 12. 测试、同步和真实链路验证

### 12.1 单元测试

在 `backend` 目录运行：

```powershell
uv run --group test pytest `
  test/unit/agents/skills/test_sales_csv_report.py `
  test/unit/services/test_skill_service.py `
  test/unit/middlewares/test_skills_middleware.py `
  test/unit/backends/test_skills_backend.py -q
```

测试职责：

- Demo 脚本测试真实输入输出和错误路径。
- Skill 服务测试注册元数据、frontmatter 解析、安装草稿和权限。
- 中间件测试依赖闭包、激活和 Tool/MCP 门控。
- Backend 测试只暴露选中 Skills 且禁止写入。

### 12.2 格式和静态检查

```powershell
uv run ruff format `
  package/yuxi/agents/skills/buildin/sales-csv-report/scripts/build_report.py `
  test/unit/agents/skills/test_sales_csv_report.py

uv run ruff check `
  package/yuxi/agents/skills/buildin/__init__.py `
  package/yuxi/agents/skills/buildin/sales-csv-report/scripts/build_report.py `
  test/unit/agents/skills/test_sales_csv_report.py `
  test/unit/services/test_skill_service.py
```

### 12.3 真实链路

1. 确认 API、Worker 和 Sandbox Provisioner 使用当前源码运行。
2. 同步内置 Skills，或完成外置 Skill 的 prepare + confirm。
3. 在 Skills 管理页确认来源、版本、依赖、共享范围和启用状态。
4. 在测试 Agent 中显式选择该 Skill，避免“默认全部”掩盖配置问题。
5. 发起明确触发问题，确认 Agent 先读取正确的 `SKILL.md`。
6. 对脚本型 Skill，确认 terminal 从 uploads/workspace 读、向 outputs 写。
7. 确认 `present_artifacts` 能展示最终产物。
8. 对 Tool/MCP 型 Skill，确认依赖在激活前不可见、激活后可调用。

Sandbox 行为必须在本地 Docker Sandbox 中验证；涉及接口或主链路时，再按测试规范补集成或端到端测试。

## 13. 更新与发布

### 内置 Skill

修改源码内容或依赖时：

1. 更新相关测试并完成 RED/GREEN。
2. 修改 Skill 目录。
3. 递增 `BuiltinSkillSpec.version`。
4. 运行内置注册和运行时回归测试。
5. 同步内置 Skills，检查数据库版本与运行副本。
6. 更新正式文档、当前版本 changelog 和独立变更日志。

### 外置 Skill

- 在线编辑适合小范围说明修正，根级 `SKILL.md` 保存时会同步名称和描述。
- 依赖、共享范围和启停通过管理接口单独维护。
- 远程或上传安装不会静默覆盖同 slug Skill；prepare 会生成新的可用 slug。
- 需要保留原 slug 的正式升级，应先制定使用方迁移和回滚方案，再删除旧版本并重新安装，不要直接改数据库或 `${save_dir}/skills`。

## 14. 常见问题

### 新增了内置目录，为什么管理页看不到？

检查是否加入 `BUILTIN_SKILLS`、frontmatter slug 是否一致，以及应用启动或 `/api/system/skills/builtin/sync` 是否完成。

### Skill 已选择，为什么依赖 Tool 仍不可见？

Agent 必须先通过 `read_file` 读取该 Skill 的根级 `SKILL.md`。只进入提示词或只读取其他文件不会激活。

### 父 Skill 依赖另一个 Skill，为什么依赖 Skill 的 Tool 没出现？

依赖闭包只扩大可读范围。还需要读取依赖 Skill 自己的 `SKILL.md`，分别激活它的 Tool/MCP。

### Skill 脚本可以执行吗？

可以。Skill 目录会只读挂载到 Sandbox 的 `/home/gem/skills`，terminal 可以执行其中的脚本，但不能修改脚本目录。产物必须写到 `/home/gem/user-data/workspace` 或 `/home/gem/user-data/outputs`。

### 脚本为什么不能直接读取 Yuxi 数据库？

Sandbox 与后端进程有意隔离。需要内部数据或权限时应实现后端 Tool，由 Tool 使用运行时用户和受控服务访问。

### 上传 prepare 成功，为什么列表中还没有？

prepare 只生成临时草稿。必须调用 confirm 并选择允许的共享范围，才会写入正式存储和数据库。

### 已有会话会自动获得刚更新的 Skill 吗？

不会保证自动刷新。线程使用自己的 Skills 快照；更新后应新建测试会话，或让运行链路重建对应快照。

## 15. 提交前 Checklist

- [ ] 已确认 Skill 比单独的后端 Tool 或 MCP 更适合。
- [ ] 已定义触发场景、输入、输出、失败行为和非目标。
- [ ] 目录名、frontmatter slug 和内置注册 slug 一致。
- [ ] `description` 能让 Agent 判断何时读取 Skill。
- [ ] 正文明确输入路径、操作顺序、产物路径和失败处理。
- [ ] 只有确定性、可重复逻辑才放入 Sandbox 脚本。
- [ ] 需要内部权限数据的能力使用后端 Tool。
- [ ] Tool、MCP 和 Skill 依赖均真实存在且范围匹配。
- [ ] 已看到新行为测试的预期 RED 和 GREEN。
- [ ] 已运行 Skill 服务、中间件和 Backend 回归测试。
- [ ] 已运行 Ruff、`git diff --check` 和对应真实链路验证。
- [ ] 内置内容变更已递增版本并验证同步。
- [ ] 正式文档导航、changelog 和独立变更日志已更新。
- [ ] 未提交密钥、Token、真实业务数据或本地环境文件。

## 16. `sales-csv-report` Demo 代码地图

本项目提供一个最小但完整的内置 Demo：

```text
backend/package/yuxi/agents/skills/buildin/
├── __init__.py
└── sales-csv-report/
    ├── SKILL.md
    └── scripts/
        └── build_report.py

backend/test/unit/
├── agents/skills/test_sales_csv_report.py
└── services/test_skill_service.py
```

Demo 的业务契约：

- 输入为 UTF-8 或 UTF-8 BOM CSV。
- 必填列为 `date`、`product`、`amount`。
- 使用标准库和 `Decimal` 生成订单数、总销售额、平均订单金额、最高产品、每日汇总和产品汇总。
- 非法业务行明确失败，不跳过、不修复、不回退。
- 输出写入 `/home/gem/user-data/outputs/sales-report.md`。
- 成功后使用 `present_artifacts` 交付 Markdown。

它演示的是“Skill + Sandbox 脚本 + 已有产物 Tool”的最小闭环，不新增数据库、接口、前端组件、MCP 或第三方依赖。
