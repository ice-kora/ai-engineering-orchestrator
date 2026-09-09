# Agent Mail actual capability matrix (am CLI v0.3.35, live run)

- **messaging/thread**: `am mail send/inbox --thread-id (JSON verified)`
- **file_reservation**: `am file_reservations reserve --ttl --exclusive --reason (granted+expires_ts)`
- **conflict_check**: `reserve returns {granted:[], conflicts:[holders]} — whole-acquire-fails, EXIT 0 nonetheless`
- **ttl**: `--ttl seconds (clamp 60..31536000); natural expiry verified in test_ttl_expiry_frees_path`
- **renew**: `am file_reservations renew --extend-seconds`
- **release**: `am file_reservations release [--paths|--ids]`
- **force_release**: `NOT in CLI v0.3.35 (MCP-only) — TTL expiry is the fallback (v1.1 §4.3)`
- **git_guard**: `am guard install/check (presence verified)`
- **http_attestation**: `mcp-agent-mail serve --no-tui on 127.0.0.1:8765; CLI reads attest against it`