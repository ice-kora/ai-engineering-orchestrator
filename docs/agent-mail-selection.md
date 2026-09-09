# MCP Agent Mail 选型报告

日期：2026-09-09 · 决策输入：GPT Final Gate（"不以 MCP tool 数量为验收标准；以实际能力是否存在并通过测试为准"）+ 本机 Windows/D 盘约束 + 官方安装脚本逐行核读（后台调研，证据 URL 见文末）

## 1. 候选与硬事实

| 维度 | **Rust 版**（mcp_agent_mail_rust） | **Python 版**（mcp_agent_mail） |
|---|---|---|
| 最新版本 | **v0.3.35**（2026-09-09 发布，持续高频迭代） | v0.3.2（2026-04-16；活跃开发已迁至 Rust 仓库，原仓库有 RUST_CUTOVER 记录） |
| Windows 原生支持 | ✅ 一等：官方 install.ps1（`-Version/-Dest/-Force/-Verify/-NoVerify`）+ msvc x86_64 预编译 zip | ❌ 无原生路径：install.sh 仅 bash（Git Bash/WSL 勉强），PATH 只写 bash rc 文件 |
| 自定义安装目录 | ✅ `-Dest D:\...`（脚本实测有该参数） | ⚠️ 仅 `--dir`（clone 位置），venv 固定在 clone 内，默认 `~/.local/share`（C 盘） |
| **安装污染 / Beads 耦合** | ✅ **install.ps1 全文 0 处 beads/br/bd 引用**——不碰 Beads，无需 opt-out | ⚠️ 默认 curl 安装 Beads Rust（`br`）并可能动 `bd`；必须 `--skip-beads`；另会探测并改写多家 Agent 的 MCP 配置 |
| MCP Transport | stdio（默认，`mcp-agent-mail`）+ HTTP（`serve`，127.0.0.1:8765 `/mcp/`，`--no-tui` 无头） | FastMCP Streamable HTTP（主）+ `serve-stdio`（次） |
| File Reservation | `file_reservation_paths(project, agent, paths, ttl_seconds, exclusive, reason)`；glob 重叠检测；幂等键 | 同名能力（v0.3.x 引入），实现较旧 |
| Conflict 返回语义 | 返回结构含授予/冲突信息（**装后以 tool schema 实测为准**，对齐 v1.1 §4.2 Adapter Contract） | 同左，需实测 |
| TTL / Renew / Release / Force | `renew_file_reservations` / `release_file_reservations` / `force_release_file_reservation`（TTL 默认 3600s，到期自动清理） | 有基础 TTL/release；renew/force 较新版本才有 |
| Git Guard | `install_precommit_guard`：pre-commit 路径级拦截（wildmatch、`.beads/**` 豁免、AGENT_NAME 识别、AGENT_MAIL_BYPASS=1 旁路、Windows .cmd/.ps1 shim） | 同机制（Python 实现） |
| messaging / thread | send/reply/fetch_inbox/fetch_topic/search/summarize；thread_id 任意字符串（=Beads task id） | 同左 |
| 并发能力 | SQLite（WAL）+ git-backed archive；多进程 stdio + 单 HTTP 服务 | FastMCP 单服务 |
| CLI / 运维 | `am` 运维 CLI（serve-http、beads 视图、robot handoff、setup 按_agent 写配置）；16 屏 TUI | Python CLI（config/set-port 等） |
| 数据落盘可控性 | `STORAGE_ROOT` / `DATABASE_URL` / `XDG_CONFIG_HOME` 环境变量可全部重定向到 D 盘（默认在用户 profile=C 盘，**必须显式重定向**） | clone 目录可控但默认 C 盘；`--dir D:\...` 可规避 |
| 升级/回滚 | 官方 minisign 签名 + SHA256SUMS；回滚=删目录（安装器无 uninstall） | git pull + uv sync；回滚同 |
| 需要管理员权限 | ❌ 不需要 | ❌ 不需要（sudo 仅装 jq） |

## 2. RECOMMENDED_IMPLEMENTATION

**= Rust 版（mcp_agent_mail_rust）**

理由（按权重）：
1. **Windows 一等支持**是本项目的硬约束（Windows First 质量要求）。
2. **安装器对 Beads 零触碰**——天然满足"Beads 与 Agent Mail 独立安装、独立固定版本"，不需要任何 opt-out flag。
3. P0 必测的 8 项能力（reservation/conflict/TTL/renew/release/force-release/Git Guard/messaging+thread）在其当前版本均有对应工具；Python 版同名能力版本更旧且 Windows 安装路径残缺。
4. 维护活跃度：v0.3.35（当日仍有发布）vs v0.3.2（5 个月前，官方已宣布开发重心迁移）。

安装前置确认（GPT Gate 要求，装前必须明确）：
- 版本：**v0.3.35**（装时以 releases/latest 实际为准并回填记录）
- 来源：github.com/Dicklesworthstone/mcp_agent_mail_rust 官方 release zip（msvc x86_64）+ SHA256SUMS（+ minisign 如本机可验证）
- 安装路径：`D:\Software\ai-orchestrator\agent-mail\`（安装器 `-Dest` 或手工解压）
- PATH：安装器默认写 **User PATH**（前插）；手工解压则完全不动 PATH（推荐，按需手动加）
- 全局配置写入：默认数据在用户 profile（C 盘）→ **必须先设** `STORAGE_ROOT` / `DATABASE_URL` / `XDG_CONFIG_HOME` 指向 D 盘再首次运行
- `am setup`/安装器会尝试改写其他 Agent 的 MCP 配置（C 盘用户目录）→ 我们**不用**该功能，ZCode 的 MCP 配置由本项目的安装提案单独走授权
- 回滚：删除安装目录 + 清 User PATH 条目 + 删除重定向的数据目录

**验收口径（装后执行）**：不数 tool 总数；逐项实测 8 能力并把实际 tool 名称/参数/返回结构记录为 tool capability matrix（对齐 v1.1 §4.2 与 P0-SPEC-DELTA-REVIEW §6 核验矩阵）。

## 3. 证据 URL

- Rust releases/install/runbook：https://api.github.com/repos/Dicklesworthstone/mcp_agent_mail_rust/releases/latest · https://raw.githubusercontent.com/Dicklesworthstone/mcp_agent_mail_rust/main/install.ps1 · docs/OPERATOR_RUNBOOK.md（同仓库）
- Python install/CHANGELOG：https://raw.githubusercontent.com/Dicklesworthstone/mcp_agent_mail/main/scripts/install.sh · https://raw.githubusercontent.com/Dicklesworthstone/mcp_agent_mail/main/CHANGELOG.md
- 站点：https://mcpagentmail.com/
