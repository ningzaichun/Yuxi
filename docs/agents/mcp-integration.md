# MCP 集成

MCP（Model Context Protocol）是扩展智能体能力的重要方式。系统支持通过管理界面动态配置 MCP 服务器，无需修改代码。

内置 MCP 服务器以代码为事实源：系统启动时会自动补齐缺失项，并用代码中的最新连接与展示字段覆盖数据库定义；是否“已添加”以及工具级禁用列表仍保留数据库状态。

## 支持的传输协议

| 协议 | 说明 | 适用场景 |
|------|------|----------|
| Streamable HTTP | 流式 HTTP 连接 | 远程 MCP 服务 |
| SSE | Server-Sent Events | 标准 HTTP 长连接 |
| Stdio | 标准输入输出 | 本地进程 |

## 配置示例

### 远程 MCP 服务

```json
{
    "name": "custom-remote-mcp",
    "transport": "streamable_http",
    "url": "https://example.com/mcp"
}
```

### 本地 Python 进程

```json
{
    "name": "mysql-mcp-server",
    "transport": "stdio",
    "command": "uvx",
    "args": ["mysql_mcp_server"],
    "env": {
        "MYSQL_HOST": "localhost",
        "MYSQL_DATABASE": "your_database"
    }
}
```

## 本机 Blender（第三方插件）

Blender 中安装并启用第三方 `MCP for Blender` 插件，点击连接按钮，默认监听本机 `9876` 端口。
保持 Blender 打开。在项目根目录的 PowerShell 中启动独立 HTTP 服务：

```powershell
uvx --python 3.11 --from mcp-for-blender==2.0.0 python scripts/blender-mcp/serve.py
```

启动脚本复用第三方 MCP 的工具与连接逻辑，仅将其通过 `127.0.0.1:9191/mcp` 提供给 Yuxi。
在“扩展管理 → MCP”新增服务，标识填 `blender-local`，传输类型选择 `streamable_http`，
URL 填 `http://127.0.0.1:9191/mcp`。此地址适用于 API、Worker 与 Blender 同机运行的宿主机开发环境。
官方 Blender Lab 插件是另一套实现，不与这里的第三方插件混用。

在智能体配置中选择该 MCP 和支持工具调用的模型。先发送“读取当前场景对象，不修改场景”验证链路，
确认对话中出现实际工具结果，再测试建模。MCP 连接测试发现零个工具时会报告失败。
此 MCP 操作的是本机 Blender，保存路径属于本机，不能直接当作 Yuxi Sandbox 附件路径使用。

## 服务器管理

管理界面使用“添加 / 移除”语义管理 MCP 服务器：

- 已添加：`enabled=true`，会加载到运行时缓存并可供 Agent 使用
- 可添加：`enabled=false`，记录保留但不会进入运行时

## 工具管理

MCP 工具支持粒度控制：管理员可以单独启用或禁用某个 MCP 服务器下的特定工具，实现精细化的权限管理。
