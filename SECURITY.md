# 安全策略

## 报告漏洞

如果你发现了安全漏洞，请**不要**在公开 Issue 中披露。

请使用 GitHub 的[私密安全报告](https://github.com/itzhouq/archery-mcp/security/advisories/new)提交，我会尽快（通常 72 小时内）确认并跟进处理。

## 漏洞范围

特别关注以下类别的问题：

- 凭据泄漏：密码 / TOTP Secret 被写入日志、错误信息或结果返回；
- 越权访问：MCP 工具能够绕过 `ARCHERY_INSTANCE_NAME` 等配置访问未授权目标；
- SQL 注入到 Archery 会话管理：登录态、CSRF 处理中的缺陷；
- 依赖风险：`httpx` / `mcp` 等依赖的已知漏洞。

## 设计边界（不属于漏洞）

以下行为是**设计决策**，不是漏洞：

- 本 MCP 不在本地过滤 SQL 或遮蔽返回数据，权限、行数、超时与脱敏完全由 Archery 平台执行（见 [README 安全模型](README.md#安全模型与免责声明)）；
- 拥有 Archery 账号凭据的用户能访问该账号在 Archery 中有权限的一切数据——请使用最小权限账号。

## 支持的版本

| 版本 | 支持状态 |
| --- | --- |
| 0.1.x | ✅ 支持 |
| < 0.1 | ❌ 不支持 |
