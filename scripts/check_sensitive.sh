#!/usr/bin/env bash
# 敏感信息扫描：仓库中出现公司/内部标识即失败。
# 用途：防止真实凭据、内网地址、公司业务信息进入公开仓库。
# 说明：本文件自身包含模式串，扫描时排除。

set -euo pipefail

PATTERNS='bloks|bloks\.com|bloks-sql|huawei|华为|sms_|sms_service|zhouquan|itzhouq/my-sites|zhouq218'

hits=$(git grep -inE "$PATTERNS" -- . ':(exclude)scripts/check_sensitive.sh' || true)

if [ -n "$hits" ]; then
    echo "检测到疑似敏感信息，禁止提交："
    echo "$hits"
    echo
    echo "如确认为误报，请在 scripts/check_sensitive.sh 中评估并调整模式。"
    exit 1
fi

echo "敏感信息扫描通过 ✅"
