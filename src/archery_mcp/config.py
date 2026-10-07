"""Archery MCP Server 配置。

查询目标在服务端配置中固定，工具调用不允许客户端切换实例、数据库或模式。
查询 SQL 的权限、行数、超时和脱敏策略完全委托 Archery 平台。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

DEFAULT_SCHEMA_NAME = "public"
DEFAULT_QUERY_LIMIT = 100


class ConfigurationError(ValueError):
    """环境变量缺失或取值不合法。"""


@dataclass(frozen=True, slots=True)
class Settings:
    """运行时配置；目标实例、数据库和模式在服务端锁定。"""

    base_url: str
    username: str
    password: str
    totp_secret: str
    instance_name: str
    db_name: str
    schema_name: str
    query_default_limit: int

    @classmethod
    def from_env(cls) -> "Settings":
        base_url = os.getenv("ARCHERY_BASE_URL", "").rstrip("/")
        username = os.getenv("ARCHERY_USERNAME", "")
        password = os.getenv("ARCHERY_PASSWORD", "")
        instance_name = os.getenv("ARCHERY_INSTANCE_NAME", "")
        db_name = os.getenv("ARCHERY_DB_NAME", "")

        missing = [
            name
            for name, value in {
                "ARCHERY_BASE_URL": base_url,
                "ARCHERY_USERNAME": username,
                "ARCHERY_PASSWORD": password,
                "ARCHERY_INSTANCE_NAME": instance_name,
                "ARCHERY_DB_NAME": db_name,
            }.items()
            if not value
        ]
        if missing:
            raise ConfigurationError(f"缺少必填环境变量：{', '.join(missing)}")

        parsed_url = urlparse(base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ConfigurationError("ARCHERY_BASE_URL 必须是合法的 HTTP(S) 地址")

        return cls(
            base_url=base_url,
            username=username,
            password=password,
            totp_secret=os.getenv("ARCHERY_TOTP_SECRET", ""),
            instance_name=instance_name,
            db_name=db_name,
            schema_name=os.getenv("ARCHERY_SCHEMA_NAME", DEFAULT_SCHEMA_NAME),
            query_default_limit=_read_query_limit(
                os.getenv("ARCHERY_QUERY_LIMIT")
                or os.getenv("ARCHERY_QUERY_MAX_ROWS")
            ),
        )


def _read_query_limit(raw_value: str | None) -> int:
    if raw_value in {None, ""}:
        return DEFAULT_QUERY_LIMIT
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ConfigurationError("ARCHERY_QUERY_LIMIT 必须是非负整数") from exc
    if value < 0:
        raise ConfigurationError("ARCHERY_QUERY_LIMIT 必须是非负整数")
    return value
