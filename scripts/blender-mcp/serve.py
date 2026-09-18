"""将第三方 mcp-for-blender 的工具通过本机 HTTP 提供给 Yuxi。"""

from blender_mcp import server


if __name__ == "__main__":
    server.CLI_HOST = "127.0.0.1"
    server.CLI_PORT = 9876
    server.mcp.settings.host = "127.0.0.1"
    server.mcp.settings.port = 9191
    server.mcp.settings.streamable_http_path = "/mcp"
    server.mcp.settings.stateless_http = True
    server.mcp.run(transport="streamable-http")
