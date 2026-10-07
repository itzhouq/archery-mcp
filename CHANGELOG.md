# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [0.1.1] - 2026-10-07

### Added

- `server.json`（MCP Registry 登记描述文件）与 Registry 自动发布工作流（GitHub OIDC）；
- README 增加 `mcp-name` 归属验证令牌（MCP 官方 Registry 对 PyPI 包的校验要求）。

## [0.1.0] - 2026-10-07

首个公开版本。

### Added

- 5 个 MCP 工具：`list_tables` / `describe_table` / `query` / `query_history` / `sql_check`；
- 锁定单一查询目标（实例 / 数据库 / 模式）的服务端配置，工具调用不可切换；
- `sql_check` 双路检查：内置规范静态规则 + Archery 平台检查（`/api/v1/workflow/sqlcheck/`），平台不可用时优雅降级；
- Archery Web 会话登录（CSRF / TOTP 两步验证 / 会话续期）；
- 46 个单元测试（全部 Mock，不访问真实 Archery）。
