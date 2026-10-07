"""上线 SQL 检查域 MCP 工具。

组合两路检查并合并结果：
1. 内置规范检查（本地静态规则，见 sql_rules.py）；
2. Archery 平台检查（/api/v1/workflow/sqlcheck/，权限与高危正则以平台为准）。

平台检查不可用时优雅降级：保留本地规范结果并说明原因，不阻塞排查。
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..client import ArcheryError, get_client
from ..sql_rules import review_release_sql

_LEVEL_NAMES = {0: "通过", 1: "警告", 2: "错误"}

_SUGGESTIONS: dict[str, str] = {
    "select_forbidden": "查询请改用 query 工具，上线工单只提交 DML/DDL 语句。",
    "missing_where": "UPDATE/DELETE 必须带 WHERE 条件；全表变更需求请单独与 DBA 评估。",
    "complex_block": "上线 SQL 不使用复杂语法：移除存储过程/函数/触发器/DO 块，改为简单语句。",
    "explicit_transaction": "上线 SQL 无需保证事务：去掉 BEGIN/COMMIT/ROLLBACK 等事务控制语句。",
    "temp_table": "尽量不使用临时表：改用 WITH 子查询或直接 JOIN，确需保留时在工单中说明。",
    "complex_syntax": "上线 SQL 不使用复杂语法：拆分 CTE/窗口函数/CREATE TABLE AS 等写法。",
    "high_risk": "TRUNCATE/DROP 等高危操作请先确认影响范围，并准备回滚方案。",
    "ddl_dml_mixed": "DDL 与 DML 分两次提交上线，先结构后数据。",
    "empty_sql": "请提供待上线的 SQL 内容。",
}

_VERDICT_TEXT = {"passed": "通过", "warning": "通过但有警告", "rejected": "存在错误，禁止直接上线"}


def _merge_suggestions(rows: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    suggestions: list[str] = []
    for row in rows:
        for rule in row.get("rule") or []:
            if rule in _SUGGESTIONS and rule not in seen:
                seen.add(rule)
                suggestions.append(_SUGGESTIONS[rule])
    return suggestions


async def sql_check(sql: str) -> dict[str, Any]:
    """检查上线 SQL 是否符合内置规范与 Archery 平台要求。

    检查项（内置规范）：
    - 上线 SQL 仅支持 DML/DDL，不接受 SELECT 查询；
    - 上线 SQL 不使用复杂语法（存储过程/函数/触发器/CTE/窗口函数等）；
    - 上线 SQL 不需要保证事务，不接受 BEGIN/COMMIT 等事务控制；
    - 尽量不使用临时表；
    - UPDATE/DELETE 必须带 WHERE 条件。

    同时调用 Archery 平台的 SQL 检查接口（需要账号在 Archery 的
    API 用户白名单内并有上线权限）；平台检查不可用时保留本地结果并说明原因。

    Args:
        sql: 待检查的上线 SQL，可包含多条语句（分号分隔）
    """
    if not sql or not sql.strip():
        raise ValueError("sql 不能为空")

    team = review_release_sql(sql)
    team_errors, team_warnings = team["error_count"], team["warning_count"]

    platform: dict[str, Any] = {"status": "skipped"}
    platform_errors = platform_warnings = 0
    try:
        payload = await get_client().sql_check(sql)
    except ArcheryError as exc:  # 平台检查失败不阻塞本地规范结果
        platform = {"status": "skipped", "reason": str(exc)}
    else:
        rows = payload.get("rows") or []
        platform_errors = sum(row.get("errlevel") == 2 for row in rows if isinstance(row, dict))
        platform_warnings = sum(row.get("errlevel") == 1 for row in rows if isinstance(row, dict))
        platform = {
            "status": "ok",
            "error_count": platform_errors,
            "warning_count": platform_warnings,
            "is_critical": bool(payload.get("is_critical")),
            "syntax_type": payload.get("syntax_type"),
            "rows": rows,
        }

    error_count = team_errors + platform_errors
    warning_count = team_warnings + platform_warnings
    if error_count > 0:
        verdict = "rejected"
    elif warning_count > 0:
        verdict = "warning"
    else:
        verdict = "passed"

    summary = (
        f"共 {team['statement_count']} 条语句：错误 {error_count} 个，警告 {warning_count} 个"
    )
    if platform["status"] == "skipped":
        summary += "；平台检查未执行"
        suggestions = _merge_suggestions(team["rows"]) + [
            f"平台检查未执行：{platform.get('reason', '未知原因')}。上线前请在 Archery 页面确认平台检查结果。"
        ]
    else:
        suggestions = _merge_suggestions(team["rows"])

    return {
        "verdict": verdict,
        "verdict_text": _VERDICT_TEXT[verdict],
        "summary": summary,
        "statement_count": team["statement_count"],
        "error_count": error_count,
        "warning_count": warning_count,
        "builtin_rules": {
            "rows": team["rows"],
            "error_count": team_errors,
            "warning_count": team_warnings,
            "syntax_type": team["syntax_type"],
        },
        "platform_check": platform,
        "suggestions": suggestions,
    }


def register_sql_check_tools(mcp: FastMCP) -> None:
    """向统一的 Archery MCP Server 注册上线 SQL 检查工具。"""
    mcp.add_tool(sql_check)
