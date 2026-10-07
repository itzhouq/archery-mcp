"""对生产 Archery 做 SQL 上线检查冒烟验证。

只做检查，不提交工单、不执行 SQL：
1. 内置规范检查（本地规则）；
2. Archery 平台检查（/api/v1/workflow/sqlcheck/，需要账号在 API 白名单并有上线权限）。
"""

import asyncio
import os
import sys

from archery_mcp.tools.sql_check import sql_check


def _require_env() -> None:
    missing = [
        name
        for name in ("ARCHERY_BASE_URL", "ARCHERY_USERNAME", "ARCHERY_PASSWORD")
        if not os.getenv(name)
    ]
    if missing:
        print(f"缺少环境变量：{', '.join(missing)}", file=sys.stderr)
        raise SystemExit(1)


def _print(label: str, result: dict) -> None:
    print(f"== {label}")
    print(f"   {result['summary']}，结论：{result['verdict_text']}")
    for row in result["builtin_rules"]["rows"]:
        level = {0: "通过", 1: "警告", 2: "错误"}.get(row["errlevel"], row["errlevel"])
        print(f"   [内置规范 #{row['id']}] {level} {row['stagestatus']}：{row['errormessage']}")
    platform = result["platform_check"]
    if platform["status"] == "ok":
        for row in platform["rows"]:
            level = {0: "通过", 1: "警告", 2: "错误"}.get(row.get("errlevel"), row.get("errlevel"))
            print(f"   [平台 #{row.get('id')}] {level} {row.get('stagestatus')}：{row.get('errormessage')}")
    else:
        print(f"   [平台] 未执行：{platform.get('reason')}")
    for tip in result["suggestions"]:
        print(f"   建议：{tip}")
    print()


async def main() -> None:
    _require_env()
    cases = {
        "规范 SQL": "INSERT INTO app_users (name) VALUES ('冒烟测试');",
        "带警告与错误的 SQL": (
            "BEGIN;\n"
            "UPDATE app_users SET name = '冒烟测试';\n"
            "SELECT * FROM app_users;"
        ),
    }
    for label, sql in cases.items():
        result = await sql_check(sql)
        _print(label, result)

    print("冒烟通过 ✅（结果仅供参考，请人工确认上述结论是否符合预期）")


if __name__ == "__main__":
    asyncio.run(main())
