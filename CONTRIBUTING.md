# 贡献指南

感谢关注 archery-mcp！欢迎通过 Issue 讨论问题，通过 Pull Request 提交改进。

## 开发环境

```bash
git clone https://github.com/itzhouq/archery-mcp
cd archery-mcp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## 开发流程

1. Fork / 建分支；
2. 修改代码，保持现有分层：`client.py` 只管 Archery HTTP 会话，`tools/` 只管 MCP 工具声明与结果转换，环境变量只进 `config.py`；
3. 添加或更新测试（`tests/`，全部使用 MockTransport / Fake，不访问真实 Archery）；
4. 运行检查：

```bash
python -m pytest -q          # 全部通过
ruff check src tests scripts # 无告警
bash scripts/check_sensitive.sh  # 敏感信息扫描通过
```

5. 提交 PR，描述改动动机与验证方式。

## 约定

- **不要提交真实凭据**（密码、TOTP Secret、Cookie、内网地址）。`scripts/check_sensitive.sh` 会在 CI 中扫描，命中即失败；
- 新增 MCP 工具时：在 `tools/` 新建模块，实现 `register_*_tools(mcp)`，并在 `server.py` 注册，同时补充 `docs/ARCHITECTURE.md` 与 README 工具表格；
- 结果格式尽量对齐 Archery 的 `ReviewResult`（`errlevel` 0/1/2），减少使用者理解成本；
- 提交信息使用英文或中文均可，建议遵循 Conventional Commits（`feat:` / `fix:` / `docs:` …）。

## 报告 Bug

请附上：MCP 客户端类型、Archery 版本（页脚可见）、脱敏后的错误信息与复现步骤。**不要贴真实表名、数据或凭据。**
