"""回归测试：查询 SQL 不经过本地内容、敏感字段或返回值遮蔽策略。"""

import inspect

from archery_mcp.tools.query import query


def test_query_tool_does_not_apply_local_sql_guard_or_sensitive_masking():
    source = inspect.getsource(query)
    assert "sql_guard" not in source
    assert "masked_columns" not in source
    assert "api_key" not in source
