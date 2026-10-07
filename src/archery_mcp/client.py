"""Archery v1.10 Web 端点客户端（会话登录 + CSRF）。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import struct
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import Settings


class ArcheryError(RuntimeError):
    """Archery 返回业务错误（status != 0）。"""


class ArcheryAuthError(ArcheryError):
    """登录失败或凭据/两步验证配置不完整。"""


class UnexpectedResponseError(ArcheryError):
    """响应不是预期的 JSON（会话失效、CSRF 校验失败等）。"""


class ArcheryApiError(ArcheryError):
    """Archery REST API 返回错误（DRF detail / 参数校验 / 白名单与权限拒绝）。"""


def totp_code(
    secret_b32: str, *, period: int = 30, digits: int = 6, at: float | None = None
) -> str:
    """按 RFC 6238 生成 TOTP 验证码，secret 为 base32 编码。"""
    normalized = secret_b32.strip().replace(" ", "").upper().rstrip("=")
    key = base64.b32decode(normalized + "=" * (-len(normalized) % 8))
    counter = int((time.time() if at is None else at) // period)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10**digits)).zfill(digits)


class ArcheryClient:
    """维护登录会话的 Archery HTTP 客户端。"""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._host = urlparse(settings.base_url).hostname or ""
        self._http = httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=httpx.Timeout(120.0, connect=15.0),
            follow_redirects=False,
            headers={
                "User-Agent": "archery-mcp/0.1",
                "Referer": f"{settings.base_url}/",
            },
            transport=transport,
        )
        self._logged_in = False
        self._login_lock = asyncio.Lock()
        self._instance_id: int | None = None

    @property
    def settings(self) -> Settings:
        return self._settings

    async def aclose(self) -> None:
        await self._http.aclose()

    def _csrf_headers(self) -> dict[str, str]:
        token = self._http.cookies.get("csrftoken")
        if not token:
            return {}
        return {"X-CSRFToken": token, "X-Requested-With": "XMLHttpRequest"}

    async def _login_locked(self) -> None:
        self._logged_in = False
        self._http.cookies.clear()

        login_page = await self._http.get("/login/")
        login_page.raise_for_status()
        response = await self._http.post(
            "/authenticate/",
            data={
                "username": self._settings.username,
                "password": self._settings.password,
            },
            headers=self._csrf_headers(),
        )
        payload = self._json_payload(response, context="登录")
        if not isinstance(payload, dict) or payload.get("status") != 0:
            message = payload.get("msg") if isinstance(payload, dict) else "登录失败"
            raise ArcheryAuthError(str(message))

        if payload.get("data"):
            self._http.cookies.set(
                "sessionid", str(payload["data"]), domain=self._host, path="/"
            )
            if not self._settings.totp_secret:
                raise ArcheryAuthError(
                    "该账号开启了两步验证：请设置环境变量 ARCHERY_TOTP_SECRET"
                    "（Google 身份验证器对应的 base32 密钥）"
                )
            verify = await self._http.post(
                "/api/v1/user/2fa/verify/",
                data={
                    "engineer": self._settings.username,
                    "auth_type": "totp",
                    "otp": totp_code(self._settings.totp_secret),
                    "key": "",
                    "phone": "",
                },
                headers=self._csrf_headers(),
            )
            verify_payload = self._json_payload(verify, context="两步验证")
            if not isinstance(verify_payload, dict) or verify_payload.get("status") != 0:
                message = verify_payload.get("msg") if isinstance(verify_payload, dict) else "两步验证失败"
                raise ArcheryAuthError(str(message))

        self._logged_in = True

    async def ensure_logged_in(self) -> None:
        if self._logged_in:
            return
        async with self._login_lock:
            if not self._logged_in:
                await self._login_locked()

    @staticmethod
    def _json_payload(
        response: httpx.Response, *, context: str
    ) -> dict[str, Any] | list[Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            snippet = response.text[:200].replace("\n", " ")
            raise UnexpectedResponseError(
                f"{context}失败：HTTP {response.status_code}，非 JSON 响应：{snippet}"
            ) from exc
        if not isinstance(payload, (dict, list)):
            raise UnexpectedResponseError(f"{context}失败：响应类型异常：{type(payload).__name__}")
        return payload

    @staticmethod
    def _looks_logged_out(response: httpx.Response) -> bool:
        if response.is_redirect:
            return "/login" in response.headers.get("location", "")
        return response.status_code in (401, 403) or "text/html" in response.headers.get("content-type", "")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        await self.ensure_logged_in()
        response = await self._http.request(
            method, path, data=data, params=params, headers=self._csrf_headers()
        )
        if self._looks_logged_out(response):
            async with self._login_lock:
                await self._login_locked()
            response = await self._http.request(
                method, path, data=data, params=params, headers=self._csrf_headers()
            )
        if self._looks_logged_out(response):
            raise UnexpectedResponseError(
                f"{path} 返回登录页（HTTP {response.status_code}），自动登录未生效"
            )
        response.raise_for_status()
        return self._json_payload(response, context=f"请求 {path}")

    @staticmethod
    def _require_ok(
        payload: dict[str, Any] | list[Any], *, context: str
    ) -> dict[str, Any]:
        if not isinstance(payload, dict) or payload.get("status") != 0:
            message = payload.get("msg") if isinstance(payload, dict) else payload
            raise ArcheryError(f"{context}失败：{message}")
        return payload

    @staticmethod
    def _flatten_names(resource: list[Any]) -> list[str]:
        names = [str(item[0]) if isinstance(item, (list, tuple)) and item else str(item) for item in resource]
        return [name for name in names if name]

    async def list_tables(self) -> list[str]:
        payload = await self._request(
            "GET",
            "/instance/instance_resource/",
            params={
                "instance_name": self._settings.instance_name,
                "db_name": self._settings.db_name,
                "schema_name": self._settings.schema_name,
                "resource_type": "table",
            },
        )
        return self._flatten_names(self._require_ok(payload, context="获取表列表").get("data") or [])

    async def describe_table(self, table_name: str) -> dict[str, Any]:
        payload = await self._request(
            "POST",
            "/instance/describetable/",
            data={
                "instance_name": self._settings.instance_name,
                "db_name": self._settings.db_name,
                "schema_name": self._settings.schema_name,
                "tb_name": table_name,
            },
        )
        data = self._require_ok(payload, context=f"获取表 {table_name} 结构").get("data") or {}
        return {"column_list": data.get("column_list") or [], "rows": data.get("rows") or []}

    async def query(self, sql: str, limit_num: int) -> dict[str, Any]:
        payload = await self._request(
            "POST",
            "/query/",
            data={
                "instance_name": self._settings.instance_name,
                "db_name": self._settings.db_name,
                "schema_name": self._settings.schema_name,
                "tb_name": "",
                "sql_content": sql,
                "limit_num": str(limit_num),
            },
        )
        return self._require_ok(payload, context="执行查询").get("data") or {}

    async def query_history(self, limit: int, offset: int = 0, search: str = "") -> dict[str, Any]:
        params: dict[str, str] = {"limit": str(limit), "offset": str(offset)}
        if search:
            params["search"] = search
        payload = await self._request("GET", "/query/querylog/", params=params)
        if not isinstance(payload, dict) or "rows" not in payload:
            raise ArcheryError("获取查询历史失败：响应缺少 rows 字段")
        return payload

    async def _request_api(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """调用 Archery REST API（/api/v1/）。DRF 错误响应转换为 ArcheryApiError。"""
        await self.ensure_logged_in()
        response = await self._http.request(method, path, data=data, params=params, headers=self._csrf_headers())
        if response.status_code in (401, 403):
            async with self._login_lock:
                await self._login_locked()
            response = await self._http.request(method, path, data=data, params=params, headers=self._csrf_headers())
        if response.status_code >= 400:
            detail = response.text[:300]
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    detail = payload.get("detail") or payload.get("errors") or detail
            except ValueError:
                pass
            raise ArcheryApiError(f"请求 {path} 失败：HTTP {response.status_code}：{detail}")
        return self._json_payload(response, context=f"请求 {path}")

    async def resolve_instance_id(self) -> int:
        """把配置的实例名解析为 Archery 实例 id（结果缓存）。"""
        if self._instance_id is not None:
            return self._instance_id
        payload = await self._request("GET", "/group/user_all_instances/")
        rows = self._require_ok(payload, context="获取实例列表").get("data") or []
        for row in rows:
            if isinstance(row, dict) and row.get("instance_name") == self._settings.instance_name:
                self._instance_id = int(row["id"])
                return self._instance_id
        raise ArcheryError(
            f"实例 {self._settings.instance_name} 不在当前账号可访问的实例列表中，"
            "请检查 ARCHERY_INSTANCE_NAME 与账号的资源组权限"
        )

    async def sql_check(self, full_sql: str) -> dict[str, Any]:
        """调用 Archery 平台 SQL 检查接口（/api/v1/workflow/sqlcheck/）。"""
        try:
            instance_id = await self.resolve_instance_id()
            payload = await self._request_api(
                "POST",
                "/api/v1/workflow/sqlcheck/",
                data={
                    "instance_id": str(instance_id),
                    "db_name": self._settings.db_name,
                    "full_sql": full_sql,
                },
            )
        except ArcheryError:
            raise
        except httpx.HTTPError as exc:
            raise ArcheryError(f"请求 Archery SQL 检查接口失败：{exc}") from exc
        if not isinstance(payload, dict):
            raise ArcheryApiError("Archery SQL 检查返回了非预期格式")
        return payload


_client: ArcheryClient | None = None


def get_client() -> ArcheryClient:
    global _client
    if _client is None:
        _client = ArcheryClient(Settings.from_env())
    return _client


def set_client(client: ArcheryClient | None) -> None:
    global _client
    _client = client
