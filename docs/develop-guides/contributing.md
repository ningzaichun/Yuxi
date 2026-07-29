# 参与贡献

感谢你对 Yuxi 的兴趣。我们欢迎 Issue、文档改进、Bug 修复、测试补充以及新功能贡献。

根目录的 [CONTRIBUTING.md](../../CONTRIBUTING.md) 是 GitHub 入口页，本页是唯一维护完整贡献流程的文档。

<a href="https://github.com/xerrors/Yuxi/contributors">
    <img src="https://contributors.nn.ci/api?repo=xerrors/Yuxi" alt="贡献者名单">
</a>

## 开始之前

提交前建议先完成以下检查：

- 搜索已有 [Issues](https://github.com/xerrors/Yuxi/issues)
- 对较大的功能改动，先发 Issue 讨论设计和边界
- 保持一次 PR 只解决一个明确问题，避免把无关重构混在一起

## 开发原则

本项目默认遵循以下开发原则：

- 避免过度设计，只做当前需求直接需要的改动
- 不额外添加“顺手优化”、兼容层或未来需求抽象
- 尽量复用现有实现，保持代码简单、聚焦、可维护
- 只在系统边界做必要校验，不为不可能发生的内部场景增加复杂度

## 开发环境

日常开发采用“远程基础设施 + 本机源码进程 + 本机 Docker Sandbox”：API、Worker、Web 在宿主机热重载运行，本机 Compose 只启动 `sandbox-provisioner`，由它按线程创建 Sandbox Runtime。

完整初始化、启动、健康检查和停止命令统一维护在[本地开发指南](./local-development.md)。开始开发前先确认：

- Web：`http://127.0.0.1:5173`
- API：`http://127.0.0.1:5050`
- Sandbox Provisioner：`http://127.0.0.1:8002/health`

不要同时启动生产 Compose 中的同名服务。

## 贡献流程

### 1. Fork 仓库

在 GitHub 上 Fork 本仓库到你的个人账户。

### 2. 创建分支

请使用语义明确的分支名，例如：

```bash
git checkout -b feature/amazing-feature
git checkout -b fix/chat-stream-interrupt
git checkout -b docs/update-contributing-guide
```

### 3. 开发与验证

按项目规范完成代码、测试与文档更新。开发完成后，至少完成：

- 检查
- 测试
- Lint
- 必要的端到端验证

如果现有测试脚本不足以覆盖你的改动，应补充对应测试，测试脚本优先放在 `backend/test`。

### 4. 提交代码

```bash
git commit -m "feat: add knowledge graph import flow"
```

### 5. 推送并发起 Pull Request

```bash
git push origin feature/amazing-feature
```

创建 PR 时，请写清楚：

- 修改内容
- 修改原因
- 影响范围
- 验证方式

如果涉及 UI 改动，建议附上截图或录屏。

## 前端贡献规范

前端目录位于 `web/`，提交前请遵循以下约束：

- 包管理器使用 `pnpm`
- 所有 API 接口定义统一放在 `web/src/apis`
- Icon 优先使用 `lucide-vue-next`
- 样式使用 `less`
- 非特殊情况必须优先复用 [web/src/assets/css/base.css](../../web/src/assets/css/base.css) 中的颜色变量

界面设计和样式约束可参考 [design.md](./design.md)。

## 后端贡献规范

后端目录位于 `backend/`，提交时请注意：

- Python 风格尽量保持 pythonic
- 优先使用较新的语法，兼容目标为 Python 3.12+
- 业务程序测试在宿主机 uv 环境运行；Sandbox 行为在本机 Docker 中验证

示例：

```bash
uv run pytest backend/test/unit
```

测试脚本建议放在 `backend/test` 下。

## 质量检查

提交前请至少完成以下检查：

### 格式化与静态检查

```bash
make format
make lint
```

如果测试依赖管理员账户，可从项目根目录的 `.env` 中读取相关配置。

## 文档维护要求

代码改动后，请同步检查是否需要更新文档。

- 通用开发文档位于 `docs/`
- 文档导航定义在 `docs/.vitepress/config.mts`
- 未完成规划、未来里程碑或已知问题更新 [roadmap.md](./roadmap.md)；已完成的用户可见变更或发布说明更新 [changelog.md](./changelog.md)
- 若确需新增仅开发者可见的说明文档，放在 `docs/vibe/`

## 提交信息规范

推荐使用清晰、可检索的提交前缀：

```text
feat: 添加新功能
fix: 修复 bug
docs: 更新文档
refactor: 代码重构
test: 添加测试
chore: 构建过程或辅助工具的变动
```

## 智能体

如果是 Agent 提交的 PR（如 Claude Code、Codex 等），请在 PR 标题最后添加 🤖 标志。

并在 PR 正文中添加

```
<details>
<summary>贡献说明</summary>

本 PR 由 [Agent Name] 自动生成，且没有人工干预。
</details>
```

## 反馈渠道

- Bug 反馈：<https://github.com/xerrors/Yuxi/issues>
- 功能讨论：<https://github.com/xerrors/Yuxi/discussions>

感谢每一位贡献者的投入。
