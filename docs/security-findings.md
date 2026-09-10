# 安全发现记录（Security Findings）

> 本文件为公开仓库脱敏版本。详细技术细节与原始证据仅保留于本地 `local/security/`（已 gitignore，永不推送至公共仓库）。

## SECURITY-001

Type:
Plaintext credential stored in local Agent configuration.

Severity:
High

Status:
Confirmed

Remediation:
Move secrets outside repository/configuration where possible,
rotate exposed credential,
and use a least-privilege account.

Follow-up Cleanup Tasks:
- Remove legacy dbhub MCP configuration from local agent configuration
- Remove legacy DSN / credential references
- Confirm database connection is no longer accessible or used by Agents

---

## SECURITY-002

Git history sanitation completed.
Status: RESOLVED.
Detailed evidence retained locally.
