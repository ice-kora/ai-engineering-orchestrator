# 安全发现记录（Security Findings）

> 本文件为公开仓库脱敏版本。详细技术细节与原始证据仅保留于本地 `local/security/`（已 gitignore，永不推送至公共仓库）。
> 状态同步来源：GPT 状态同步（2026-09-10）。

## SECURITY-001

Type:
Plaintext credential stored in local Agent configuration.

Severity:
High

Status:
**LEGACY_CONFIG_CLEANUP**（对应数据库已停止使用；不再要求密码轮换作为 Gate）

Remediation:
~~Move secrets outside repository/configuration where possible, rotate exposed credential, and use a least-privilege account.~~
→ 按 GPT 裁决调整：轮换不再作为 Gate（数据库已停用），收敛为遗留配置清理。

Follow-up Cleanup Tasks（2026-09-10 已执行）:
- [x] Remove legacy dbhub MCP configuration from local agent configuration（已从 `~/.zcode/cli/config.json` 移除 `mcp.servers.dbhub`；剩余 server：playwright、context7）
- [x] Remove legacy DSN / credential references（ZCode 三个配置面复查：`cli/config.json`、`v2/config.json`、`v2/setting.json` 全部 0 命中 dbhub/mysql/内网 IP）
- [x] Confirm database connection is no longer accessible or used by Agents（配置已无该连接入口；新会话不再加载 dbhub。本机留有清理前备份 `config.json.pre-dbhub-cleanup`（含旧凭证），由用户择期删除）

---

## SECURITY-002

Git history sanitation completed (by Antigravity, commits `53f81d7`/`0f9ca46`, 2026-09-10).
Status: **RESOLVED**.
Detailed evidence retained locally.
