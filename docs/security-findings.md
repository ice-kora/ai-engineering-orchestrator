# 安全发现记录（Security Findings）

> 本项目 P0 阶段只**记录**安全问题，不做修复/轮换（修复需人工决策与授权）。

## SECURITY-001：ZCode MCP 配置中明文数据库凭证

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-09-09 |
| 发现位置 | `C:\Users\<user>\.zcode\cli\config.json` → `mcp.servers.dbhub` 的 DSN 字段 |
| 影响组件 | `@bytebase/dbhub` MCP Server |
| 凭证形态 | `<REDACTED_DSN><INTERNAL_IP>:3306/<DB_NAME>`（**<DB_USER> 账号**，内网 MySQL） |
| 明文披露 | 本文档不打印密码明文；脱敏记法：`<REDACTED_SECRET>`（<REDACTED>，含 1 个特殊字符，URL 编码形式存在于 DSN） |
| **有效性** | **已实测有效（REAL & VALID）**：2026-09-09 通过该配置发起只读探测 `<PROBE_STATEMENT>` 成功返回（见 docs/security-findings.md）。这不是失效的历史凭证，而是当前可用的生产级内网数据库 <DB_USER> 口令。 |

### 风险评估

1. **泄露面**：明文存在于用户目录 JSON 配置中，任何能读取该文件的进程/工具（包括所有被授权读取用户目录的 MCP 服务与 Coding Agent 会话）都可获得内网数据库 <DB_USER> 口令。
2. **爆炸半径**：`root` 账号 + 内网 IP（<INTERNAL_IP>）。若该实例承载真实业务数据（<DB_NAME> 疑似 RuoYi 框架库），拿到口令即具备全库读写与 DDL 权限。
3. **次生风险**：该口令若在其他环境复用（常见坏习惯），风险外溢。

### 建议后续动作（均需人工执行，P0 不处理）

1. 将 DSN 中的口令改为环境变量引用（dbhub 支持的环境变量形式以官方文档为准），配置文件只留占位。
2. **轮换该 MySQL <DB_USER> 口令**（既然已进入 AI 工具配置链路，应视为暴露面扩大）。
3. 为 Agent 场景创建最小权限只读账号替代高权限账号。
4. 确认 <INTERNAL_IP>:3306 不应对无关网段开放。

### 状态

- 记录：✅（本文件）
- 修复：❌ 未执行（等待人工决策）
- 凭证轮换：❌ 未执行（等待人工决策）
