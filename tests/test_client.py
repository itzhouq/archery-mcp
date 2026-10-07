import urllib.parse

import httpx
import pytest

import archery_mcp.client as client_module
from archery_mcp.client import (
    ArcheryApiError,
    ArcheryAuthError,
    ArcheryClient,
    ArcheryError,
    UnexpectedResponseError,
    totp_code,
)
from archery_mcp.config import Settings

BASE = "http://archery.test"


def make_settings(**overrides) -> Settings:
    values = {
        "base_url": BASE,
        "username": "archery_user",
        "password": "secret",
        "totp_secret": "",
        "instance_name": "prod-app-db",
        "db_name": "app_db",
        "schema_name": "public",
        "query_default_limit": 100,
    }
    values.update(overrides)
    return Settings(**values)


def cookie_dict(request: httpx.Request) -> dict[str, str]:
    header = request.headers.get("cookie", "")
    return dict(item.split("=", 1) for item in header.split("; ") if "=" in item)


def form(request: httpx.Request) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(request.content.decode())


class FakeArchery:
    """模拟 Archery 登录、查询和会话状态的最小状态机。"""

    def __init__(self, *, twofa: bool = False, expected_otp: str = "") -> None:
        self.twofa = twofa
        self.expected_otp = expected_otp
        self.valid_sessions: set[str] = set()
        self.auth_calls = 0
        self.expire_next = False
        self.fail_csrf_once = False
        self.last_query: dict[str, list[str]] = {}
        self.instance_calls = 0
        self.check_calls = 0
        self.last_check: dict[str, list[str]] = {}
        self.api_whitelisted = True

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        cookies = cookie_dict(request)

        if request.method == "GET" and path == "/login/":
            return httpx.Response(
                200,
                text="<html>login</html>",
                headers=[["Set-Cookie", "csrftoken=token-1; Path=/"]],
            )

        if request.method == "POST" and path == "/authenticate/":
            self.auth_calls += 1
            assert request.headers.get("x-csrftoken") == "token-1"
            body = form(request)
            if body.get("username") != ["archery_user"] or body.get("password") != ["secret"]:
                return httpx.Response(
                    200,
                    json={"status": 1, "msg": "用户名或密码错误，请重新输入！", "data": ""},
                )
            if self.twofa:
                return httpx.Response(
                    200, json={"status": 0, "msg": "ok", "data": "temp-session"}
                )
            self.valid_sessions.add("s-1")
            return httpx.Response(
                200,
                json={"status": 0, "msg": "ok", "data": None},
                headers=[["Set-Cookie", "sessionid=s-1; Path=/"]],
            )

        if request.method == "POST" and path == "/api/v1/user/2fa/verify/":
            if cookies.get("sessionid") != "temp-session":
                return httpx.Response(200, json={"status": 1, "msg": "需先校验用户密码！"})
            body = form(request)
            if body.get("otp") != [self.expected_otp] or body.get("auth_type") != ["totp"]:
                return httpx.Response(200, json={"status": 1, "msg": "验证码错误"})
            self.valid_sessions.add("s-2fa")
            return httpx.Response(
                200,
                json={"status": 0, "msg": "ok"},
                headers=[["Set-Cookie", "sessionid=s-2fa; Path=/"]],
            )

        if request.method == "GET" and path == "/instance/instance_resource/":
            if cookies.get("sessionid") not in self.valid_sessions:
                return httpx.Response(302, headers={"Location": "/login/"})
            if self.expire_next:
                self.expire_next = False
                self.valid_sessions.clear()
                return httpx.Response(302, headers={"Location": "/login/"})
            return httpx.Response(
                200,
                json={
                    "status": 0,
                    "msg": "ok",
                    "data": [["app_users"], ["app_orders"]],
                },
            )

        if request.method == "POST" and path == "/instance/describetable/":
            if cookies.get("sessionid") not in self.valid_sessions:
                return httpx.Response(302, headers={"Location": "/login/"})
            if self.fail_csrf_once:
                self.fail_csrf_once = False
                return httpx.Response(403, text="CSRF verification failed. Request aborted.")
            return httpx.Response(
                200,
                json={
                    "status": 0,
                    "msg": "ok",
                    "data": {
                        "column_list": ["column_name", "data_type", "description"],
                        "rows": [["id", "bigint", "主键ID"]],
                    },
                },
            )

        if request.method == "POST" and path == "/query/":
            if cookies.get("sessionid") not in self.valid_sessions:
                return httpx.Response(302, headers={"Location": "/login/"})
            self.last_query = form(request)
            return httpx.Response(
                200,
                json={
                    "status": 0,
                    "msg": "ok",
                    "data": {
                        "column_list": ["id", "api_key"],
                        "rows": [[1, "sk-real-secret"]],
                        "query_time": 0.0123,
                        "is_masked": False,
                        "full_sql": self.last_query.get("sql_content", [""])[0],
                    },
                },
            )

        if request.method == "GET" and path == "/query/querylog/":
            if cookies.get("sessionid") not in self.valid_sessions:
                return httpx.Response(302, headers={"Location": "/login/"})
            return httpx.Response(
                200,
                json={"total": 1, "rows": [{"id": 7, "sqllog": "select 1"}]},
            )

        if request.method == "GET" and path == "/group/user_all_instances/":
            if cookies.get("sessionid") not in self.valid_sessions:
                return httpx.Response(302, headers={"Location": "/login/"})
            self.instance_calls += 1
            return httpx.Response(
                200,
                json={
                    "status": 0,
                    "msg": "ok",
                    "data": [
                        {"id": 3, "type": 0, "db_type": "pgsql", "instance_name": "prod-app-db"},
                        {"id": 9, "type": 0, "db_type": "mysql", "instance_name": "其他实例"},
                    ],
                },
            )

        if request.method == "POST" and path == "/api/v1/workflow/sqlcheck/":
            if not self.api_whitelisted:
                return httpx.Response(
                    403, json={"detail": "You do not have permission to perform this action."}
                )
            if cookies.get("sessionid") not in self.valid_sessions:
                return httpx.Response(
                    403, json={"detail": "Authentication credentials were not provided."}
                )
            self.check_calls += 1
            self.last_check = form(request)
            return httpx.Response(
                200,
                json={
                    "is_execute": False,
                    "checked": None,
                    "warning": None,
                    "error": None,
                    "warning_count": 0,
                    "error_count": 0,
                    "is_critical": False,
                    "syntax_type": 2,
                    "rows": [
                        {
                            "id": 1,
                            "stage": "CHECKED",
                            "errlevel": 0,
                            "stagestatus": "Audit completed",
                            "errormessage": "None",
                            "sql": self.last_check.get("full_sql", [""])[0],
                            "affected_rows": 0,
                            "sequence": "",
                            "backup_dbname": "",
                            "execute_time": 0,
                            "sqlsha1": "",
                            "backup_time": "",
                            "actual_affected_rows": "",
                        }
                    ],
                    "column_list": None,
                    "status": None,
                    "affected_rows": 0,
                },
            )

        return httpx.Response(404, text="not found")


async def make_client(**kwargs) -> tuple[ArcheryClient, FakeArchery]:
    fake = FakeArchery(**kwargs.pop("fake", {}))
    return ArcheryClient(make_settings(**kwargs), transport=httpx.MockTransport(fake)), fake


async def test_login_and_table_resource_endpoint():
    client, fake = await make_client()
    try:
        assert await client.list_tables() == ["app_users", "app_orders"]
        assert fake.auth_calls == 1
    finally:
        await client.aclose()


async def test_wrong_password_raises():
    client, _ = await make_client(password="bad")
    try:
        with pytest.raises(ArcheryAuthError):
            await client.list_tables()
    finally:
        await client.aclose()


async def test_two_factor_flow():
    client, _ = await make_client(
        fake={"twofa": True, "expected_otp": "287082"},
        totp_secret="GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
    )
    original = client_module.totp_code
    client_module.totp_code = lambda secret, **kwargs: "287082"
    try:
        assert await client.list_tables() == ["app_users", "app_orders"]
    finally:
        client_module.totp_code = original
        await client.aclose()


async def test_two_factor_requires_secret():
    client, _ = await make_client(fake={"twofa": True})
    try:
        with pytest.raises(ArcheryAuthError, match="ARCHERY_TOTP_SECRET"):
            await client.list_tables()
    finally:
        await client.aclose()


async def test_relogin_after_session_expiry():
    client, fake = await make_client()
    fake.expire_next = True
    try:
        assert await client.list_tables() == ["app_users", "app_orders"]
        assert fake.auth_calls == 2
    finally:
        await client.aclose()


async def test_csrf_403_retry():
    client, fake = await make_client()
    fake.fail_csrf_once = True
    try:
        assert (await client.describe_table("app_users"))["rows"] == [
            ["id", "bigint", "主键ID"]
        ]
    finally:
        await client.aclose()


async def test_query_is_forwarded_to_archery_without_local_sql_filtering():
    client, fake = await make_client()
    try:
        sql = "SELECT * FROM app_users;"
        data = await client.query(sql, 0)
        assert data["column_list"] == ["id", "api_key"]
        assert fake.last_query["sql_content"] == [sql]
        assert fake.last_query["limit_num"] == ["0"]
    finally:
        await client.aclose()


async def test_query_history_passthrough():
    client, _ = await make_client()
    try:
        assert (await client.query_history(limit=5, search="app"))["rows"][0]["id"] == 7
    finally:
        await client.aclose()


async def test_sql_check_resolves_instance_and_calls_platform_api():
    client, fake = await make_client()
    try:
        payload = await client.sql_check("INSERT INTO app_users (name) VALUES ('x');")
        assert fake.last_check["instance_id"] == ["3"]
        assert fake.last_check["db_name"] == ["app_db"]
        assert payload["rows"][0]["stagestatus"] == "Audit completed"
        # 实例 id 已缓存，第二次检查不再请求实例清单
        assert fake.instance_calls == 1
        await client.sql_check("UPDATE app_users SET name = 'y' WHERE id = 1;")
        assert fake.instance_calls == 1
        assert fake.check_calls == 2
    finally:
        await client.aclose()


async def test_sql_check_instance_not_accessible():
    client, _ = await make_client(instance_name="不存在的实例")
    try:
        with pytest.raises(ArcheryError, match="不在当前账号可访问的实例列表中"):
            await client.sql_check("INSERT INTO t (a) VALUES (1);")
    finally:
        await client.aclose()


async def test_sql_check_api_whitelist_denied():
    client, fake = await make_client()
    fake.api_whitelisted = False
    try:
        with pytest.raises(ArcheryApiError, match="permission"):
            await client.sql_check("INSERT INTO t (a) VALUES (1);")
        # 白名单拒绝时会重登一次再失败
        assert fake.auth_calls == 2
    finally:
        await client.aclose()


async def test_non_json_response_raises():
    class HtmlAuth(FakeArchery):
        def __call__(self, request):
            if request.url.path == "/authenticate/":
                return httpx.Response(200, text="<html>oops</html>")
            return super().__call__(request)

    client = ArcheryClient(make_settings(), transport=httpx.MockTransport(HtmlAuth()))
    try:
        with pytest.raises(UnexpectedResponseError):
            await client.list_tables()
    finally:
        await client.aclose()


def test_totp_code_rfc6238_vector():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp_code(secret, at=59) == "287082"
    assert totp_code(secret.lower() + "==", at=59) == "287082"
