# Archery MCP 架构与设计说明

> 版本：v0.1.0
> 适用 Archery：v1.10.0（实测版本）

本文面向想理解或参与开发 archery-mcp 的人，说明目标、架构、安全边界与开发约定。使用说明见 [README](../README.md)。

## 1. 目标与范围

`archery_mcp` 是一个统一的 Archery MCP Server。它将 Archery Web 功能封装为 MCP 工具，当前实现在线查询域和上线 SQL 检查域，后续计划增加工单查询等域能力。对 MCP 客户端暴露的服务名与配置名均为 `archery_mcp`。

### 1.1 查询目标

查询目标（实例、数据库、模式）由环境变量配置并在服务端锁定：

| 配置项 | 说明 |
| --- | --- |
| `ARCHERY_INSTANCE_NAME` | Archery 实例名，必填，须在账号可访问列表内 |
| `ARCHERY_DB_NAME` | 数据库名，必填 |
| `ARCHERY_SCHEMA_NAME` | 模式名，可选，PostgreSQL 通常为 `public` |

目标在服务端锁定；MCP 工具不接收对应入参，不能由模型调用时任意切换目标。

### 1.2 单一 MCP 配置原则

当前和后续能力都注册在一个 FastMCP Server 中：`archery_mcp`。

```text
Claude Code / Cursor / 其他 MCP 客户端
                 │
                 │ 一条 MCP Server 配置
                 ▼
            archery_mcp
             ├── 查询工具（当前）
             ├── sql_check 工具（当前）
             └── 工单查询工具（后续）
```

用户不用为 `query`、`sql_check` 或工单工具分别配置 MCP。新增工具后，只需更新同一个包并重启 MCP 客户端。

## 2. 为什么复用 Archery Web 接口

Archery v1.10.0 的 REST API 可以管理实例、工单和用户，但在线查询功能并未完整暴露在 `/api/v1/` 下。因此当前实现复用 Archery 在线查询页自身使用的 Web 会话端点；上线 SQL 检查则直接调用 Archery 的 REST 检查端点：

| 功能 | 方法 | 路径 |
| --- | --- | --- |
| 获取 CSRF Cookie | GET | `/login/` |
| 用户名密码登录 | POST | `/authenticate/` |
| TOTP 两步验证 | POST | `/api/v1/user/2fa/verify/` |
| 获取表列表 | GET | `/instance/instance_resource/` |
| 获取表结构 | POST | `/instance/describetable/` |
| 执行在线查询 | POST | `/query/` |
| 查询历史 | GET | `/query/querylog/` |
| 当前账号可访问实例清单 | GET | `/group/user_all_instances/` |
| SQL 平台检查 | POST | `/api/v1/workflow/sqlcheck/` |

`/api/v1/workflow/sqlcheck/` 对应 Archery 源码 `sql_api/api_workflow.py` 的 `ExecuteCheck`：入参 `instance_id`、`db_name`、`full_sql`，返回 `ReviewSet` 格式（`rows` 中每条语句一行，`errlevel` 0 通过 / 1 警告 / 2 错误，另有 `error_count`、`warning_count`、`syntax_type`）。账号需要在系统配置 `api_user_whitelist`（API 用户白名单）内，并拥有 `sql.sql_submit` 权限。

Archery 升级后端点行为可能变化。若升级导致失败，见第 8.4 节排查步骤。

## 3. 架构与职责

```text
MCP Client
  │ tools/call
  ▼
archery_mcp.server
  │  创建唯一 FastMCP 实例并注册全部业务域工具
  ├── tools/query.py
  │     查询域 MCP 工具：参数组织和结果格式
  ├── tools/sql_check.py
  │     组合内置规范检查与平台检查，合并结果与建议
  ├── sql_rules.py
  │     上线 SQL 内置规范静态规则，结果格式对齐 Archery ReviewResult
  ├── tools/workflow.py              # 后续新增
  ▼
archery_mcp.client.ArcheryClient
  │  登录、CSRF、TOTP、Session 续期、响应解析
  ▼
Archery v1.10.0
  │  权限、SQL 校验、行数、超时、数据脱敏、审计
  ▼
Production database
```

职责约束：

- `client.py`：只负责 Archery HTTP 会话和端点调用；
- `tools/`：每个业务域独立一个模块，负责 MCP 的工具声明、参数组织和结果转换；
- `server.py`：只创建 FastMCP 实例，并调用每个域的 `register_*_tools(mcp)`；
- `config.py`：集中读取和校验环境变量；
- `tests/`：分别验证配置、会话客户端和工具域行为，全部使用 Mock，不访问真实 Archery。

不要在工具模块中直接使用 `httpx` 发请求，也不要把 MCP 注册逻辑放进 `client.py`。

## 4. 查询策略和安全边界

### 4.1 Archery 是唯一 SQL 策略执行者

按当前设计，MCP 不：

- 限制只能使用 `SELECT`；
- 拒绝 `SELECT *` 或 `table.*`；
- 拒绝特定字段名；
- 解析或改写 SQL；
- 屏蔽或加密 Archery 返回的任何字段；
- 在本地施加最大返回行数上限。

`query` 将 SQL 和 `limit` 原样传给 Archery。`limit=0` 时，含义与 Archery 在线查询页面相同：交由当前用户在 Archery 的查询权限使用最大限制。

以下能力完全以 Archery 配置和登录账号权限为准：

- 是否可访问目标实例和库；
- SQL 类型和 SQL 检查规则；
- 涉及表的查询权限；
- 查询返回行数限制；
- 查询超时；
- 数据脱敏规则；
- 查询审计与历史记录。

这么设计的理由：本地过滤会制造"看起来安全"的错觉，而权限与脱敏的真正执行点在 Archery。两套规则并存会互相打架，审计也会失去一致依据。

### 4.2 仍由 MCP 锁定的边界

MCP 锁定目标实例、数据库和模式，避免模型通过工具调用跨目标访问；该边界由 `ARCHERY_INSTANCE_NAME`、`ARCHERY_DB_NAME` 和 `ARCHERY_SCHEMA_NAME` 配置控制。

凭据不会写入源代码。密码和 TOTP Secret 只通过 MCP 进程环境变量提供。

### 4.3 部署注意事项

由于结果不做二次遮蔽，使用者必须确认 Archery 的脱敏与查询权限正确配置。建议：

1. 使用专门的 Archery 查询账号，而非管理员账号；
2. 仅分配指定资源组/实例/库的查询权限；
3. 在 Archery 维护数据脱敏规则；
4. 定期审计 Archery 查询历史；
5. 不要将真实密码、TOTP Secret、Cookie 或含敏感结果的日志提交到 Git。

## 5. 工具明细

### 5.1 `list_tables`

列出锁定目标库中的表：

```text
list_tables(keyword="app")
```

### 5.2 `describe_table`

查看表结构：

```text
describe_table(table_name="app_users")
```

### 5.3 `query`

通过 Archery 在线查询提交 SQL：

```json
{
  "sql": "SELECT * FROM app_users ORDER BY id LIMIT 5",
  "limit": 0
}
```

- `limit` 未传：使用 `ARCHERY_QUERY_LIMIT`；
- `limit=0`：交由 Archery 根据账号权限决定最大返回行数；
- SQL、字段、结果均不经过 MCP 二次过滤；
- Archery 拒绝的 SQL 会原样以工具错误返回。

### 5.4 `query_history`

查看当前 Archery 账号的查询历史：

```text
query_history(limit=20, search="app_users")
```

### 5.5 `sql_check`

检查上线 SQL 是否符合内置规范与 Archery 平台要求。检查只读，不提交工单、不执行 SQL：

```json
{
  "sql": "BEGIN;\nUPDATE app_users SET name = 'x';"
}
```

返回结构（节选）：

```json
{
  "verdict": "rejected",
  "verdict_text": "存在错误，禁止直接上线",
  "summary": "共 2 条语句：错误 1 个，警告 3 个",
  "builtin_rules": { "rows": [], "error_count": 1, "warning_count": 2, "syntax_type": 2 },
  "platform_check": { "status": "ok | skipped", "rows": [], "reason": "平台不可用时说明原因" },
  "suggestions": ["..."]
}
```

两路检查：

1. **内置规范检查**（本地静态规则，`sql_rules.py`）：
   - 仅支持 DML/DDL：SELECT 直接驳回（参考 Archery `sql/engines/pgsql.py`）；
   - UPDATE/DELETE 缺少 WHERE 条件：驳回；
   - 复杂块语法（存储过程/函数/触发器/DO 块）：驳回；
   - 事务控制（BEGIN/COMMIT/ROLLBACK/SAVEPOINT 等）：警告——上线 SQL 不需要保证事务，平台逐条自动提交；
   - 临时表（CREATE TEMP TABLE、pg_temp）：警告——尽量不用；
   - 其他复杂语法（CTE/窗口函数/CREATE TABLE AS/LOCK TABLE）：警告；
   - TRUNCATE、DROP DATABASE/SCHEMA：警告——高危操作确认回滚方案；
   - 同一批混合 DDL 与 DML：警告（参考 Archery `ddl_dml_separation`）。

2. **平台检查**（`/api/v1/workflow/sqlcheck/`）：先用 `/group/user_all_instances/` 把配置的实例名解析为 `instance_id`（结果缓存），再提交检查。高危语句正则（`critical_ddl_regex`）、账号权限等以平台为准。平台检查失败（白名单、权限、网络等）时结果标记为 `skipped` 并附原因，本地规范结果正常返回。

## 6. 安装与开发环境

### 6.1 前置条件

- Python 3.11+；
- 可以访问 Archery；
- Archery 账号有在线查询权限；
- 若账号启用 Google 身份验证器，需要对应的 base32 TOTP Secret；
- MCP 客户端，例如 Claude Code。

### 6.2 开发安装

```bash
git clone https://github.com/itzhouq/archery-mcp
cd archery-mcp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

### 6.3 环境变量

见 [README 配置项](../README.md#配置项) 与 [.env.example](../.env.example)。

## 7. 运行与验证

### 7.1 运行测试

```bash
python -m pytest -q
```

### 7.2 查询冒烟验证

会登录 Archery、获取表清单、查询表结构，然后以 `SELECT *` 查询：

```bash
ARCHERY_BASE_URL="https://archery.example.com" \
ARCHERY_USERNAME="你的 Archery 用户名" \
ARCHERY_PASSWORD="你的 Archery 密码" \
python scripts/smoke_readonly.py
```

### 7.3 上线 SQL 检查冒烟

只做检查，不提交工单、不执行 SQL：

```bash
ARCHERY_BASE_URL="https://archery.example.com" \
ARCHERY_USERNAME="你的 Archery 用户名" \
ARCHERY_PASSWORD="你的 Archery 密码" \
python scripts/smoke_sql_check.py
```

### 7.4 MCP stdio 验证

```bash
ARCHERY_BASE_URL="https://archery.example.com" \
ARCHERY_USERNAME="你的 Archery 用户名" \
ARCHERY_PASSWORD="你的 Archery 密码" \
python scripts/smoke_mcp_stdio.py
```

脚本会启动 `python -m archery_mcp`，初始化 MCP 会话并调用查询工具。

## 8. 后续开发

### 8.1 调整内置规范

1. 规则集中在 `sql_rules.py` 的 `_statement_issues()`，一条规则对应一个规则码与建议文案（`tools/sql_check.py` 的 `_SUGGESTIONS`）；
2. 保持结果格式与 Archery `ReviewResult` 一致（errlevel 0/1/2，每条语句一行）；
3. 在 `tests/test_sql_rules.py` 补充规则用例后运行 `python -m pytest -q`；
4. 平台侧规则（高危正则、账号权限）不要在本地重复实现，以 Archery 配置为准。

### 8.2 增加工单能力

工单能力建议独立放入：

```text
src/archery_mcp/tools/workflow.py
```

写操作、DDL 工单提交、审核、执行等高影响能力必须在工具描述和实现中设置明确确认机制：先检查 SQL、比较环境、处理兼容与回滚说明，再由用户确认具体 SQL 后执行。

### 8.3 增加新目标库

当前配置锁定单一目标。若未来需要多目标支持，不应允许模型传任意连接信息；应在 `config.py` 使用目标白名单，将 `target` 映射到固定的实例、数据库、模式组合，并为每个目标单独配置 Archery 权限和脱敏策略。

### 8.4 Archery 升级排查

若 Archery 升级导致失败：

1. 浏览器确认在线查询还能使用；
2. 执行 `scripts/smoke_readonly.py`；
3. 检查 `/login/`、`/authenticate/`、`/query/` 等端点和字段是否变化；
4. 同步修改 `client.py` 的模拟测试和真实冒烟脚本；
5. 运行完整测试后再恢复 MCP 服务。

## 9. 目录结构

```text
archery-mcp/
├── pyproject.toml
├── README.md
├── LICENSE / SECURITY.md / CONTRIBUTING.md / CHANGELOG.md
├── server.json                # MCP Registry 登记描述文件
├── .env.example / .mcp.json.example
├── docs/
│   └── ARCHITECTURE.md        # 本文
├── scripts/
│   ├── smoke_readonly.py
│   ├── smoke_sql_check.py
│   ├── smoke_mcp_stdio.py
│   └── check_sensitive.sh     # 敏感信息扫描（CI 使用）
├── src/archery_mcp/
│   ├── __init__.py / __main__.py
│   ├── config.py
│   ├── client.py
│   ├── server.py
│   ├── sql_rules.py
│   └── tools/
│       ├── query.py
│       └── sql_check.py
└── tests/
    ├── test_config.py
    ├── test_client.py
    ├── test_server.py
    ├── test_sql_guard.py
    └── test_sql_rules.py
```
