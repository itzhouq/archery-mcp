"""查询域 MCP 工具。

查询权限、SQL 合法性、行数上限、执行超时和脱敏均由 Archery 平台负责。
本模块只把目标实例、数据库和模式锁定为服务配置，避免跨目标查询。
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..client import get_client


async def list_tables(keyword: str = "") -> dict[str, Any]:
    """列出当前配置的 Archery 目标库中的表，可按关键字过滤表名。

    Args:
        keyword: 可选的表名过滤关键字，大小写不敏感
    """
    tables = await get_client().list_tables()
    if keyword:
        lowered = keyword.lower()
        tables = [name for name in tables if lowered in name.lower()]
    return {"tables": tables, "total": len(tables)}


async def describe_table(table_name: str) -> dict[str, Any]:
    """查看当前配置目标库中指定表的字段、类型、默认值和注释。

    Args:
        table_name: Archery 中可访问的表名
    """
    result = await get_client().describe_table(table_name)
    columns = [str(name) for name in result["column_list"]]
    fields: list[dict[str, Any]] = []
    for row in result["rows"]:
        record = dict(zip(columns, row))
        fields.append(
            {
                "name": record.get("column_name"),
                "type": record.get("data_type"),
                "length": record.get("character_maximum_length"),
                "numeric_precision": record.get("numeric_precision"),
                "numeric_scale": record.get("numeric_scale"),
                "nullable": record.get("is_nullable"),
                "default": record.get("column_default"),
                "comment": record.get("description"),
            }
        )
    return {"table": table_name, "columns": fields}


async def query(sql: str, limit: int | None = None) -> dict[str, Any]:
    """通过 Archery 在线查询执行 SQL。

    SQL 是否可执行、是否只读、是否包含敏感字段、最大返回行数、查询超时及
    结果脱敏均由 Archery 的权限和策略控制。本 MCP 不修改 SQL，也不屏蔽
    Archery 返回的字段。

    Args:
        sql: 待提交给 Archery 在线查询的 SQL
        limit: 可选查询行数。未传时使用 ARCHERY_QUERY_LIMIT；传 0 时交给 Archery 使用其最大权限限制
    """
    client = get_client()
    effective_limit = client.settings.query_default_limit if limit is None else limit
    if effective_limit < 0:
        raise ValueError("limit 必须是非负整数")

    data = await client.query(sql, effective_limit)
    columns = [str(name) for name in (data.get("column_list") or [])]
    rows = [
        dict(zip(columns, row)) if isinstance(row, (list, tuple)) else row
        for row in (data.get("rows") or [])
    ]
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "limit": effective_limit,
        "query_time_seconds": data.get("query_time"),
        "masked_by_archery": data.get("is_masked"),
        "full_sql": data.get("full_sql") or sql,
    }


async def query_history(limit: int = 20, search: str = "") -> dict[str, Any]:
    """查看当前 Archery 账号自己的在线查询历史。

    Args:
        limit: 返回条数，必须为正整数
        search: 按 SQL 内容、用户名或别名过滤的关键字
    """
    if limit < 1:
        raise ValueError("limit 必须为正整数")
    payload = await get_client().query_history(limit=limit, search=search)
    return {"total": payload.get("total"), "rows": payload.get("rows") or []}


def register_query_tools(mcp: FastMCP) -> None:
    """向统一的 Archery MCP Server 注册查询工具。"""
    mcp.add_tool(list_tables)
    mcp.add_tool(describe_table)
    mcp.add_tool(query)
    mcp.add_tool(query_history)
