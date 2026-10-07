"""上线 SQL 内置规范检查（本地静态规则）。

结果格式对齐 Archery 的 ReviewResult（sql/engines/models.py）：
errlevel 0=通过、1=警告、2=错误；每条语句一行结果。

规则依据：
- Archery 上线检查语义（sql/engines/pgsql.py execute_check：驳回 SELECT；
  sql/engines/mysql.py execute_check：DDL/DML 分离）；
- 团队上线规范：上线 SQL 不使用复杂语法、不需要保证事务、尽量不用临时表。

仅做静态规范检查；账号权限、高危语句正则等平台规则以 Archery
``/api/v1/workflow/sqlcheck/`` 的返回为准，不在本地重复。
"""

from __future__ import annotations

import re
from typing import Any

# 语句主类型（与 Archery ReviewSet.syntax_type 一致：0 其他 / 1 DDL / 2 DML）
_SYNTAX_OTHER, _SYNTAX_DDL, _SYNTAX_DML = 0, 1, 2

_DDL_VERBS = ("create", "alter", "drop", "truncate", "rename", "comment", "grant", "revoke")
_DML_VERBS = ("insert", "update", "delete", "merge", "select")


def _segments(text: str) -> list[tuple[str, str]]:
    """把 SQL 文本切成 (kind, value) 片段。

    kind：code（含空白与分号）、single（单引号字符串）、double（双引号标识符）、
    dollar（$tag$ 美元引号体，含定界符）、line（-- 注释）、block（块注释）。
    """
    segments: list[tuple[str, str]] = []
    buffer: list[str] = []
    index, length = 0, len(text)

    def flush() -> None:
        if buffer:
            segments.append(("code", "".join(buffer)))
            buffer.clear()

    while index < length:
        char = text[index]
        if char == "'":
            flush()
            cursor = index + 1
            while cursor < length:
                if text[cursor] == "'":
                    if cursor + 1 < length and text[cursor + 1] == "'":
                        cursor += 2
                        continue
                    cursor += 1
                    break
                cursor += 1
            else:
                cursor = length
            segments.append(("single", text[index:cursor]))
            index = cursor
        elif char == '"':
            flush()
            cursor = index + 1
            while cursor < length:
                if text[cursor] == '"':
                    if cursor + 1 < length and text[cursor + 1] == '"':
                        cursor += 2
                        continue
                    cursor += 1
                    break
                cursor += 1
            else:
                cursor = length
            segments.append(("double", text[index:cursor]))
            index = cursor
        elif char == "$" and (match := re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", text[index:])):
            tag = match.group(0)
            flush()
            end = text.find(tag, index + len(tag))
            end = length if end == -1 else end + len(tag)
            segments.append(("dollar", text[index:end]))
            index = end
        elif text.startswith("--", index):
            flush()
            end = text.find("\n", index)
            end = length if end == -1 else end
            segments.append(("line", text[index:end]))
            index = end
        elif text.startswith("/*", index):
            flush()
            end = text.find("*/", index + 2)
            end = length if end == -1 else end + 2
            segments.append(("block", text[index:end]))
            index = end
        else:
            buffer.append(char)
            index += 1
    flush()
    return segments


def split_statements(sql: str) -> list[str]:
    """按分号切分 SQL；字符串、标识符、美元引号和注释内的分号不参与切分。"""
    statements: list[str] = []
    current: list[str] = []
    for kind, value in _segments(sql):
        if kind == "code" and ";" in value:
            pieces = value.split(";")
            # 每个 ';' 前的片段结束一条语句；最后一段残留与后续片段同属一条语句
            for piece in pieces[:-1]:
                current.append(piece)
                statements.append("".join(current))
                current = []
            current.append(pieces[-1])
        else:
            current.append(value)
    if current:
        statements.append("".join(current))
    return [statement.strip() for statement in statements if statement.strip()]


def strip_comments(sql: str) -> str:
    """去除 -- 和块注释，保留语句其余部分。"""
    kept = [value for kind, value in _segments(sql) if kind not in ("line", "block")]
    return re.sub(r"\s+", " ", "".join(kept)).strip()


def _strip_parens(text: str) -> str:
    """去掉引号与括号内的内容（忽略其深度），用于定位 CTE 之后的主动词。"""
    kept: list[str] = []
    depth = 0
    for kind, value in _segments(text):
        if kind != "code":
            continue
        for char in value:
            if char == "(":
                depth += 1
            elif char == ")":
                depth = max(0, depth - 1)
            elif depth == 0:
                kept.append(char)
    return "".join(kept)


def _main_verb(statement: str) -> str | None:
    """返回语句的第一个动词；对 CTE 语句取去掉括号后最后一个 AS 之后的首个动词。"""
    stripped = _strip_parens(statement)
    tokens = re.findall(r"[A-Za-z_]+", stripped)
    lowered = [token.lower() for token in tokens]
    if not lowered:
        return None
    if lowered[0] == "with":
        as_index = len(lowered) - 1 - lowered[::-1].index("as") if "as" in lowered else -1
        for token in lowered[as_index + 1 :]:
            if token in _DDL_VERBS or token in _DML_VERBS:
                return token
        return None
    return lowered[0]


def _syntax_type_of(verb: str | None) -> int:
    if verb in _DDL_VERBS:
        return _SYNTAX_DDL
    if verb in _DML_VERBS:
        return _SYNTAX_DML
    return _SYNTAX_OTHER


def _statement_issues(statement: str) -> list[tuple[str, int, str]]:
    """对单条语句返回 (规则码, errlevel, 提示) 列表。"""
    low = statement.lower()
    verb = _main_verb(statement)
    issues: list[tuple[str, int, str]] = []

    if verb == "select":
        issues.append(
            (
                "select_forbidden",
                2,
                "上线 SQL 仅支持 DML/DDL 语句，查询请使用 query 工具（Archery 平台也会驳回 SELECT）",
            )
        )
    if verb in ("update", "delete") and not re.search(r"\bwhere\b", low):
        issues.append(("missing_where", 2, "UPDATE/DELETE 语句缺少 WHERE 条件，将影响全表数据"))
    if re.match(r"^create\s+(or\s+replace\s+)?(procedure|function|trigger|event)\b", low) or re.match(
        r"^do\b", low
    ):
        issues.append(
            (
                "complex_block",
                2,
                "上线 SQL 不使用复杂语法：存储过程/函数/触发器等请改写为简单 DML/DDL，必要时与 DBA 确认",
            )
        )
    if re.match(
        r"^(begin|start\s+transaction|commit|rollback|savepoint|release\s+savepoint|end)\b", low
    ) or re.match(r"^set\s+(autocommit|transaction|session\s+characteristics)\b", low):
        issues.append(
            (
                "explicit_transaction",
                1,
                "上线 SQL 无需保证事务：移除 BEGIN/COMMIT 等事务控制语句，平台会逐条执行并自动提交",
            )
        )
    if re.search(r"\bcreate\s+(global\s+|local\s+)?temp(orary)?\s+table\b", low) or re.search(
        r"\bpg_temp\b", low
    ):
        issues.append(("temp_table", 1, "尽量不使用临时表：如需中间结果请评估 WITH 子查询或直接 JOIN"))
    if (
        re.match(r"^with\b", low)
        or re.search(r"\bover\s*\(", low)
        or re.match(r"^lock\s+table\b", low)
        or re.search(r"\bcreate\s+((global|local)\s+|unlogged\s+|temp(orary)?\s+)*table\b[^;]*\bas\b", low)
    ):
        issues.append(
            ("complex_syntax", 1, "上线 SQL 不使用复杂语法：请拆分为简单语句（避免 CTE/窗口函数/CREATE TABLE AS 等）")
        )
    if re.match(r"^(truncate|drop\s+(database|schema))\b", low):
        issues.append(("high_risk", 1, "高危操作：TRUNCATE/DROP 不可回滚，请确认影响范围并准备回滚方案"))
    return issues


def review_release_sql(sql: str) -> dict[str, Any]:
    """对整批上线 SQL 做内置规范检查，返回 Archery ReviewSet 风格的结果。"""
    pairs = [(raw, strip_comments(raw)) for raw in split_statements(sql)]
    effective = [(raw, plain) for raw, plain in pairs if plain]
    rows: list[dict[str, Any]] = []
    syntax_types: set[int] = set()

    if not effective:
        rows.append(
            {
                "id": 1,
                "rule": ["empty_sql"],
                "errlevel": 2,
                "stagestatus": "规范驳回",
                "errormessage": "未提供有效的 SQL 语句",
                "sql": sql.strip(),
                "affected_rows": 0,
            }
        )
        return {"rows": rows, "error_count": 1, "warning_count": 0, "statement_count": 0, "syntax_type": _SYNTAX_OTHER}

    for line, (statement, plain) in enumerate(effective, start=1):
        issues = _statement_issues(plain)
        errlevel = max((level for _, level, _ in issues), default=0)
        rows.append(
            {
                "id": line,
                "rule": [code for code, _, _ in issues],
                "errlevel": errlevel,
                "stagestatus": "规范驳回" if errlevel == 2 else "规范警告" if errlevel == 1 else "规范通过",
                "errormessage": "；".join(message for _, _, message in issues) if issues else "None",
                "sql": statement,
                "affected_rows": 0,
            }
        )
        syntax_types.add(_syntax_type_of(_main_verb(plain)))

    if _SYNTAX_DDL in syntax_types and _SYNTAX_DML in syntax_types:
        rows.append(
            {
                "id": len(rows) + 1,
                "rule": ["ddl_dml_mixed"],
                "errlevel": 1,
                "stagestatus": "规范警告",
                "errormessage": "同一批上线 SQL 混合了 DDL 与 DML 语句，建议分开提交（参考 Archery DDL/DML 分离检查）",
                "sql": "(整批)",
                "affected_rows": 0,
            }
        )

    syntax_type = _SYNTAX_DDL if _SYNTAX_DDL in syntax_types else _SYNTAX_DML if _SYNTAX_DML in syntax_types else _SYNTAX_OTHER
    return {
        "rows": rows,
        "error_count": sum(row["errlevel"] == 2 for row in rows),
        "warning_count": sum(row["errlevel"] == 1 for row in rows),
        "statement_count": len(effective),
        "syntax_type": syntax_type,
    }
