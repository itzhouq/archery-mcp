"""统一 Archery MCP Server 入口。"""

from __future__ import annotations

import logging
import sys

from mcp.server.fastmcp import FastMCP

from .config import Settings
from .tools.query import register_query_tools
from .tools.sql_check import register_sql_check_tools

mcp = FastMCP(
    "archery_mcp",
    instructions=(
        "Archery MCP Server。提供锁定目标实例、数据库和模式的在线查询工具，"
        "以及上线 SQL 检查工具（内置规范 + Archery 平台检查）。"
        "查询权限、合法性、行数上限、超时和数据脱敏由 Archery 平台执行。"
        "后续会增加 SQL 工单工具，但客户端始终只需配置此 MCP Server。"
    ),
)

register_query_tools(mcp)
register_sql_check_tools(mcp)


def main() -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    Settings.from_env()
    mcp.run()
