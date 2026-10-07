"""对生产 Archery 做查询冒烟验证：登录 → 表清单 → 表结构 → SELECT *。"""

import asyncio
import os
import sys

from archery_mcp.client import ArcheryClient
from archery_mcp.config import Settings


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
    settings = Settings.from_env()
    print(f"目标：{settings.instance_name} / {settings.db_name} / {settings.schema_name}")

    client = ArcheryClient(settings)
    try:
        tables = await client.list_tables()
        print(f"表清单（{len(tables)} 张）：{tables[:8]}{' ...' if len(tables) > 8 else ''}")

        described = await client.describe_table("app_users")
        print("app_users 字段：", described["column_list"])

        sql = "SELECT * FROM app_users ORDER BY id LIMIT 5"
        data = await client.query(sql, settings.query_default_limit)
        print("查询列：", data.get("column_list"))
        for row in (data.get("rows") or [])[:5]:
            print("  ", row)
        print("冒烟通过 ✅")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
