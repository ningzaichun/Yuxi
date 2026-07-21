# Yuxi 文档站开发说明

`docs` 目录使用 VitePress 构建 Yuxi 文档站。本文件面向本地维护文档的开发者，说明如何安装依赖、启动开发服务器、构建站点和新增文档页面。

## 环境要求

- Node.js 24+
- pnpm 10.x

从仓库根目录执行以下命令时，先进入文档目录：

```powershell
Set-Location docs
```

## 安装依赖

首次运行或 `package.json`、`pnpm-lock.yaml` 变化后执行：

```powershell
pnpm install
```

不要使用 npm 或 yarn 重新生成锁文件。

## 启动开发服务器

```powershell
pnpm dev --host 127.0.0.1 --port 5174
```

文档站使用 `5174`，避免与 Yuxi 主 Web 默认使用的 `5173` 冲突。启动后访问：

- 文档首页：<http://127.0.0.1:5174/Yuxi/>
- Agent 工具开发指南：<http://127.0.0.1:5174/Yuxi/develop-guides/agent-tool-development>

VitePress 会监听 Markdown、主题和配置文件变化并自动刷新页面。终端会输出实际访问地址；如果修改了端口，以终端输出为准。

局域网内需要由其他设备访问时，可以把监听地址改为：

```powershell
pnpm dev --host 0.0.0.0 --port 5174
```

只在可信网络中使用 `0.0.0.0`。

## 构建生产版本

```powershell
pnpm build
```

构建产物生成在：

```text
docs/.vitepress/dist/
```

构建成功是提交正式文档前的必要检查。依赖版本可能产生语法高亮、chunk 大小或构建插件警告；只要命令退出码为 `0` 且页面正常生成，警告可以单独评估，但不能忽略真正的构建失败。

## 预览生产构建

先执行 `pnpm build`，再运行：

```powershell
pnpm preview --host 127.0.0.1 --port 4173
```

然后根据终端输出访问预览地址。预览模式读取生产构建产物，不提供开发模式的实时热更新。

## 常用目录

```text
docs/
├── .vitepress/
│   └── config.mts          # 站点配置、顶部导航和侧边导航
├── public/                 # Logo、favicon 等静态资源
├── intro/                  # 快速开始
├── agents/                 # 智能体相关文档
├── advanced/               # 高级配置和部署说明
├── develop-guides/         # 开发与贡献规范
├── change_logs/            # 每次代码变更的独立审计记录，不进入站点
├── project_analysis/       # 项目分析记录，不进入站点
├── index.md                # 文档站首页
├── package.json            # VitePress 命令和依赖
└── pnpm-lock.yaml          # pnpm 锁文件
```

`docs/.vitepress/config.mts` 中配置了：

```ts
base: '/Yuxi/'
```

因此本地和部署后的页面地址都带有 `/Yuxi/` 前缀。不要把开发地址误写成仅有 `/develop-guides/...`。

## 新增正式文档

新增面向用户或开发者的正式文档时，按以下顺序执行：

1. 在对应目录创建语义明确的 Markdown 文件。
2. 使用相对链接引用同一文档目录中的其他页面。
3. 在 `docs/.vitepress/config.mts` 中增加对应导航入口。
4. 检查正文引用的源码、图片和文档路径是否真实存在。
5. 根据改动更新 `docs/develop-guides/changelog.md`。
6. 运行 `pnpm build` 验证完整站点。
7. 运行 `git diff --check` 检查空白和补丁格式。

开发者内部的临时分析或需求记录不要直接加入正式导航。只有内容稳定、目标读者明确并经过验证的文档才进入站点目录。

## 文档编写约定

- 标题层级从一个 `#` 开始，正文按 `##`、`###` 逐级展开。
- 命令示例标明实际 Shell，例如 `powershell` 或 `bash`。
- 不在文档中写入真实 API Key、密码、Token、内网地址或测试账号。
- 操作步骤必须能执行，不使用 `TODO`、`TBD` 或未解释的占位符。
- 说明当前代码事实，并在相关实现变化时同步更新文档。
- 避免把完整源码重复粘贴到多个页面；优先解释稳定接口和开发流程。

## 相关文档

- [本地开发指南](./develop-guides/local-development.md)
- [测试规范与工作流](./develop-guides/testing-guidelines.md)
- [Agent 工具开发指南](./develop-guides/agent-tool-development.md)
- [参与贡献](./develop-guides/contributing.md)

## 常见问题

### 端口 5174 已被占用

换用其他端口，例如：

```powershell
pnpm dev --host 127.0.0.1 --port 5175
```

### 找不到 pnpm

确认 Node.js 和 pnpm 已安装：

```powershell
node --version
pnpm --version
```

项目推荐通过根目录的宿主机依赖安装脚本准备开发环境：

```powershell
.\scripts\split-deploy\Install-HostDevDependencies.ps1
```

该命令需要在仓库根目录执行，而不是在 `docs` 目录执行。

### 页面返回 404

先确认 URL 包含 `/Yuxi/` 前缀，再检查：

1. Markdown 文件是否位于 `docs` 目录内。
2. 文件名与导航链接大小写是否一致。
3. `docs/.vitepress/config.mts` 中的链接是否以 `/` 开头且不包含 `.md`。
4. 开发服务器是否在修改配置后成功重新加载。

### 修改后页面没有更新

先查看启动终端是否有 Markdown 或配置解析错误。必要时停止开发服务器并重新运行 `pnpm dev`；修改依赖文件后应重新执行 `pnpm install`。
