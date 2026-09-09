# Version Matrix（版本矩阵）

> 状态说明：**VERIFIED = 本机实测（2026-09-09，P0）**，标记为稳定事实——后续 spec 变更（v1.1+）不改变这些环境结论。
> NOT_INSTALLED = 未安装，等待 `docs/INSTALLATION-PROPOSAL.md` 获批后安装再实测。

## 1. 本机基础环境（全部 VERIFIED）

| Component | Installed Version | Path | Status | Notes |
|---|---|---|---|---|
| Windows | 11 23H2 (10.0.22631) | — | ✅ VERIFIED | 系统区域 zh-CN |
| Windows PowerShell | 5.1.22621.6133 | C:\Windows\System32\WindowsPowerShell\v1.0 | ✅ VERIFIED | 仅兼容目标 |
| PowerShell 7 (pwsh) | 7.6.4 | D:\Software\PowerShell\7 | ✅ VERIFIED | **项目脚本统一用 pwsh 7** |
| Git | 2.55.0.windows.3 | D:\Software\git\install\Git\cmd | ✅ VERIFIED | worktree/porcelain 全功能可用 |
| Python | 3.13.15 (scoop) | D:\Software\scoop\apps\python313\current | ✅ VERIFIED | `py` 启动器损坏（exit 112，scoop 未注册），**一律用 `python`/`uv`** |
| pip | 26.2.1 | （随 Python） | ✅ VERIFIED | — |
| uv | 0.12.10 | D:\Software\hermes\bin; scoop shims 亦有 | ✅ VERIFIED | 缓存/工具目录全在 D 盘（`UV_CACHE_DIR=D:\Software\scoop\persist\uv\cache` 等）✅ |
| Node.js | 24.19.0 (nvm-windows) | D:\Software\nvm\nodejs | ✅ VERIFIED | nvm 目录在 D 盘 |
| npm | 11.17.0 | 同上 | ✅ VERIFIED | — |
| pnpm | 11.7.0 | — | ✅ VERIFIED | 共享 store 可行（v1.1 §7.2 基础具备） |
| Docker | — | — | ❌ 未安装 | P0 不需要 |
| sqlite3 CLI | — | — | ❌ 未安装 | Python sqlite3 模块 3.50.4 够用 |
| Maven `~/.m2` | 存在 | C:\Users\<user>\.m2 | ⚠️ 用户既有 | 在 C 盘；本项目**不迁移不动**，新共享缓存提案见 INSTALLATION-PROPOSAL |

### 已知环境坑（VERIFIED 教训）

1. **uv 全局镜像 403**：用户环境变量 `UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple` 对部分 wheel（jsonschema/pytest/mcp）间歇返回 403，且环境变量优先级高于项目 `pyproject.toml` 的 index 配置。**解决**：命令行前缀 `UV_DEFAULT_INDEX=https://pypi.org/simple`（仅子进程生效，不改全局）。已固化进 scripts/bootstrap.ps1。
2. **git 全局身份缺失**：全局无 user.name/email（demo-repo 这类独立仓库需各自 `git config` 仓库级身份，否则 commit 静默失败）。
3. unittest 报告走 stderr；git porcelain 输出正斜杠（断言需 `Path.as_posix()`）。

## 2. 协作组件（P0 待安装/待实测）

| Component | 实施选择 | 最新稳定版（官方） | 本机状态 | 建议锁定 | 关键证据 |
|---|---|---|---❌ — | — | — |

| Component | 实施选择 | 官方最新稳定 | 本机状态 | 建议锁定 | 关键事实 |
|---|---|---|---|---|---|
| **Beads (`bd`)** | Go 原版（gastownhall/beads，原 steveyegge） | v1.2.2（2026-08-15；v1.2.0/1 被撤回；1.3.0-rc 为预发布不入稳定渠道） | ❌ NOT_INSTALLED | **v1.2.2** | ⚠️ PyPI 包 `beads` 是 2019 年同名无关项目，**禁 pip install beads**；Windows 官方装法 install.ps1 / releases zip；命令：`bd create/dep add/ready --json/update --claim(原子)/close`；ID 格式 `bd-a1b2`；多写并发需 `bd init --server`；v1.1 §8.3 的 `--status open`/`label` 子命令**官方未证实**→ 装后实测 |
| **MCP Agent Mail** | 待选型（见 docs/agent-mail-selection.md） | Python 版 v0.3.2（2026-04-16）/ Rust 版 v0.3.31+（当前主推，mcpagentmail.com 指向） | ❌ NOT_INSTALLED | 装后记录 | Python 安装器会以 `br`（Beads Rust）偷换 `bd` → 必须 `--skip-beads`；Rust 版 Windows 一等支持（install.ps1 + msvc zip）；预约 TTL 默认 3600s；Git Guard = pre-commit 路径级拦截 |
| **Antigravity CLI (`agy`)** | Google 官方 | 持续更新 | ❌ NOT_INSTALLED（桌面 IDE 已装但无 CLI） | 装后记录 | headless 确认：`agy -p --output-format json --model --effort low/medium/high --conversation`；装后需一次性浏览器 OAuth；已知 worktree 检测 Bug（antigravity-cli#68）需实测；stdout 必须逐行读 |

## 3. ZCode（✅ 本机已装，P0 实测对象）

| Component | Version | Path | Status | 关键事实 |
|---|---|---|---|---|
| ZCode 桌面版 | 0.16.5 | D:\Software\zcode\install\ZCode\ZCode.exe | ✅ VERIFIED | 本协作系统当前宿主 |
| ZCode CLI | 0.16.5 | D:\Software\zcode\install\ZCode\resources\glm\zcode.cjs（**不在 PATH**，经 `node` 调用） | ✅ DISCOVERED → 待 10 项验证定 VERIFIED | headless 能力实测存在：`-p/--print`、`--prompt`、`--cwd`、`--mode build/edit/plan/yolo`、`--max-turns`、`--allowed-tools`、`--disallowed-tools`、`--resume`、`--json`、`--surface`。与 v1.0"ZCode 无稳定 headless"前提不符 → **仅记录证据，P0 不改 Pull 架构**，升级与否 P1 由 GPT 裁决 |
| ZCode MCP | — | C:\Users\<user>\.zcode\cli\config.json（mcp.servers: playwright / dbhub / context7） | ✅ VERIFIED（本会话 3 个 MCP server 实际加载） | 接入 agent-mail/beads-mcp 时将新增条目（届时需授权+备份） |
| ZCode Subagent | — | 内置（Explore/architect-reviewer/project-module-expert 等） | ✅ VERIFIED（本会话实际调度过 3 个 Explore 子代理） | — |
| AGENTS.md 注入 | — | C:\Users\<user>\.zcode\AGENTS.md（全局）+ 项目级 | ✅ VERIFIED（全局规范已加载进本会话） | — |

## 4. 证据 URL（外部调研，2026-09-09）

- Beads repo/releases/install：https://github.com/gastownhall/beads · https://github.com/gastownhall/beads/releases · https://raw.githubusercontent.com/gastownhall/beads/main/docs/getting-started/installation.md
- PyPI 陷阱：https://pypi.org/pypi/beads/json（无关项目）· https://pypi.org/project/beads-mcp/
- Beads Rust：https://github.com/Dicklesworthstone/beads_rust
- Agent Mail Python：https://github.com/Dicklesworthstone/mcp_agent_mail · CHANGELOG 同仓库
- Agent Mail Rust：https://github.com/Dicklesworthstone/mcp_agent_mail_rust · https://mcpagentmail.com/
- Antigravity CLI：https://antigravity.google/docs/cli/install/ · https://antigravity.google/docs/cli/headless/ · worktree Bug: https://github.com/google-antigravity/antigravity-cli/issues/68
- ZCode 官方文档：https://zcode.z.ai/en/docs/welcome（官方未文档化 CLI headless——本机实测为准）
