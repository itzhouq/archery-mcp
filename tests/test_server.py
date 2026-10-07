import pytest

from archery_mcp.client import ArcheryError, set_client
from archery_mcp.config import Settings
from archery_mcp.tools import query as query_tools
from archery_mcp.tools import sql_check as sql_check_tools


def make_settings() -> Settings:
    return Settings(
        base_url="http://archery.test",
        username="archery_user",
        password="secret",
        totp_secret="",
        instance_name="prod-app-db",
        db_name="app_db",
        schema_name="public",
        query_default_limit=100,
    )


class FakeClient:
    def __init__(self) -> None:
        self.settings = make_settings()
        self.queries: list[tuple[str, int]] = []
        self.checks: list[str] = []
        self.platform_result: dict | None = {
            "is_execute": False,
            "warning_count": 0,
            "error_count": 0,
            "is_critical": False,
            "syntax_type": 2,
            "rows": [
                {
                    "id": 1,
                    "errlevel": 0,
                    "stagestatus": "Audit completed",
                    "errormessage": "None",
                    "sql": "",
                    "affected_rows": 0,
                }
            ],
        }
        self.platform_error: Exception | None = None

    async def list_tables(self) -> list[str]:
        return ["app_users", "app_orders", "app_payments"]

    async def describe_table(self, table_name: str) -> dict:
        return {
            "column_list": ["column_name", "data_type", "description"],
            "rows": [
                ["id", "bigint", "主键ID"],
                ["api_key", "character varying", "API密钥"],
            ],
        }

    async def query(self, sql: str, limit_num: int) -> dict:
        self.queries.append((sql, limit_num))
        return {
            "column_list": ["id", "api_key", "name"],
            "rows": [[1, "sk-aaa", "示例应用A"], [2, "sk-bbb", "示例应用B"]],
            "query_time": 0.02,
            "is_masked": False,
            "full_sql": sql,
        }

    async def query_history(self, limit: int, offset: int = 0, search: str = "") -> dict:
        return {"total": 1, "rows": [{"id": 9, "sqllog": "select 1"}]}

    async def sql_check(self, full_sql: str) -> dict:
        self.checks.append(full_sql)
        if self.platform_error is not None:
            raise self.platform_error
        return self.platform_result


@pytest.fixture(autouse=True)
def fake_client():
    client = FakeClient()
    set_client(client)
    yield client
    set_client(None)


async def test_list_tables_with_keyword():
    result = await query_tools.list_tables("app_")
    assert result["total"] == 3
    result = await query_tools.list_tables("orders")
    assert result["tables"] == ["app_orders"]


async def test_describe_table_maps_columns():
    result = await query_tools.describe_table("app_users")
    assert result["table"] == "app_users"
    assert result["columns"][0] == {
        "name": "id",
        "type": "bigint",
        "length": None,
        "numeric_precision": None,
        "numeric_scale": None,
        "nullable": None,
        "default": None,
        "comment": "主键ID",
    }


async def test_query_forwards_select_star_and_sensitive_columns_without_masking(fake_client):
    sql = "SELECT *, api_key, api_secret FROM app_users"
    result = await query_tools.query(sql)

    assert fake_client.queries == [(sql, 100)]
    assert result["rows"][0] == {
        "id": 1,
        "api_key": "sk-aaa",
        "name": "示例应用A",
    }
    assert "masked_columns" not in result


async def test_query_forwards_archery_max_limit(fake_client):
    await query_tools.query("SELECT * FROM app_users", limit=0)
    assert fake_client.queries == [("SELECT * FROM app_users", 0)]


async def test_query_rejects_only_invalid_local_limit():
    with pytest.raises(ValueError):
        await query_tools.query("SELECT 1", limit=-1)


async def test_query_history():
    result = await query_tools.query_history(limit=5)
    assert result["total"] == 1
    with pytest.raises(ValueError):
        await query_tools.query_history(limit=0)


async def test_sql_check_passes_clean_sql(fake_client):
    sql = "INSERT INTO app_users (name) VALUES ('示例应用A');"
    result = await sql_check_tools.sql_check(sql)

    assert fake_client.checks == [sql]
    assert result["verdict"] == "passed"
    assert result["error_count"] == 0
    assert result["warning_count"] == 0
    assert result["platform_check"]["status"] == "ok"
    assert result["suggestions"] == []


async def test_sql_check_rejects_team_rule_error(fake_client):
    sql = "SELECT * FROM app_users;"
    result = await sql_check_tools.sql_check(sql)

    assert result["verdict"] == "rejected"
    assert result["error_count"] == 1
    assert result["builtin_rules"]["rows"][0]["errlevel"] == 2
    assert any("query 工具" in tip for tip in result["suggestions"])
    # 平台检查仍会执行
    assert result["platform_check"]["status"] == "ok"


async def test_sql_check_merges_platform_errors(fake_client):
    fake_client.platform_result = {
        "warning_count": 1,
        "error_count": 1,
        "is_critical": True,
        "syntax_type": 2,
        "rows": [
            {"id": 1, "errlevel": 1, "stagestatus": "Audit completed", "errormessage": "表没有主键", "sql": "insert ..."},
            {"id": 2, "errlevel": 2, "stagestatus": "驳回不支持语句", "errormessage": "不支持", "sql": "select 1"},
        ],
    }
    sql = "INSERT INTO app_users (name) VALUES ('x');"
    result = await sql_check_tools.sql_check(sql)

    assert result["verdict"] == "rejected"
    assert result["error_count"] == 1
    assert result["warning_count"] == 1
    assert result["platform_check"]["is_critical"] is True


async def test_sql_check_degrades_when_platform_unavailable(fake_client):
    fake_client.platform_error = ArcheryError(
        "请求 /api/v1/workflow/sqlcheck/ 失败：HTTP 403：You do not have permission"
    )
    sql = "BEGIN;\nCOMMIT;"
    result = await sql_check_tools.sql_check(sql)

    assert result["platform_check"]["status"] == "skipped"
    assert "HTTP 403" in result["platform_check"]["reason"]
    assert "平台检查未执行" in result["summary"]
    # 内置规范结果保留：BEGIN/COMMIT 两条事务警告
    assert result["verdict"] == "warning"
    assert result["warning_count"] == 2
    assert any("平台检查未执行" in tip for tip in result["suggestions"])


async def test_sql_check_rejects_empty_sql():
    with pytest.raises(ValueError):
        await sql_check_tools.sql_check("   ")
