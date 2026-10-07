"""通过 stdio 验证统一 archery_mcp Server 的查询工具。"""

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _require_env() -> None:
    missing = [
        name
        for name in ("ARCHERY_BASE_URL", "ARCHERY_USERNAME", "ARCHERY_PASSWORD")
        if not os.getenv(name)
    ]
    if missing:
        print(f"缺少环境变量：{', '.join(missing)}", file=sys.stderr)
        raise SystemExit(1)


async def main() -> None:
    _require_env()
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "archery_mcp"],
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("注册的工具：", [tool.name for tool in tools.tools])

            result = await session.call_tool(
                "list_tables", {"keyword": "app_users"}
            )
            print("list_tables →", result.content[0].text)

            result = await session.call_tool(
                "describe_table", {"table_name": "app_users"}
            )
            print("describe_table →", result.content[0].text[:220], "...")

            result = await session.call_tool(
                "query",
                {"sql": "SELECT * FROM app_users ORDER BY id LIMIT 3"},
            )
            print("query →", result.content[0].text)
            assert not result.isError, "Archery 查询失败"
            print("MCP stdio 验证通过 ✅")


if __name__ == "__main__":
    asyncio.run(main())
