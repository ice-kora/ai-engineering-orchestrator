# INSTALLATION-PROPOSAL（安装提案 · 停止在提案阶段，等待人工授权）

日期：2026-09-09 · 依据：GPT Final Gate 补充 #1/#3/#4 + D 盘统一安装约束 + 官方安装脚本逐行核读

**状态：⏸ PROPOSED — 未执行任何安装。** 授权后按本章执行；执行人可为 ZCode（Beads/Agent Mail）与用户本人（agy 登录）。

---

## 0. 安装动作三分类（GPT 补充 #3）

| 分类 | 定义 | 本提案中的动作 |
|---|---|---|
| **Project-local dependency** | 仅写入本项目目录，删目录即回滚 | uv `.venv`（已完成）；demo-repo |
| **User-scoped executable** | 写入用户目录/用户 PATH，无需管理员 | **beads、agent-mail、agy 全部属此类** |
| **Machine/System-level change** | 系统 PATH、注册表、服务、驱动 | **无**（本提案不含任何系统级动作） |

统一目标根目录：`D:\Software\ai-orchestrator\`（与用户既有 `D:\Software\` 习惯一致）。

## 1. Beads（`bd` CLI，Go 原版）

| 项 | 内容 |
|---|---|
| 推荐版本 | **v1.2.2**（最新稳定，2026-08-15；v1.2.0/1.2.1 官方已撤回勿用；1.3.0-rc 为预发布不入稳定渠道） |
| 官方来源 | github.com/gastownhall/beads releases（原 steveyegge/beads 已迁移） |
| **默认官方安装路径** | install.ps1：`%LOCALAPPDATA%\Programs\bd`（**C 盘**），写 bd.exe + beads.exe 副本 |
| **自定义目录支持** | ❌ install.ps1 无参数（已逐行核读：无 param 块，仅 `BEADS_INSTALL_SKIP_GOINSTALL`/`BEADS_INSTALL_SOURCE` 环境变量） |
| **推荐方案（D 盘合规）** | **portable 路线**：下载 release 资产 `beads_1.2.2_windows_amd64.zip` + `checksums.txt`，校验 SHA256 后解压到 `D:\Software\ai-orchestrator\beads\`。*这同时满足 GPT 补充 #1"首选原生 bd.exe"（即官方预编译二进制，非 npm shim）* |
| 是否修改 PATH | 安装器：不写 PATH（仅打印 setx 建议）。portable：**不自动改**；建议人工/授权后加入 **User PATH**（仅一条） |
| 用户目录/C 盘写入 | 无（数据在仓库内 `.beads/`；`BEADS_DIR` 可覆盖） |
| 无法避免的 C 盘写入 | 无 |
| 管理员权限 | 不需要 |
| OAuth | 不需要 |
| 卸载/回滚 | 删除 `D:\Software\ai-orchestrator\beads\` + 移除 User PATH 条目 |
| 风险 | ① loopback 防火墙提示可能首次出现（embedded-dolt 本地端口；如有弹窗点允许）；② npm 备选路线的 bd.cmd shim 有 Node spawn EINVAL 坑（Node≥18.20）——**已按 GPT 指示降级为备选**；③ PyPI `beads` 为同名无关项目，永不使用 |

安装命令（授权后执行）：
```powershell
# 1) 下载（版本锁定 v1.2.2）
Invoke-WebRequest https://github.com/gastownhall/beads/releases/download/v1.2.2/beads_1.2.2_windows_amd64.zip -OutFile $env:TEMP\beads.zip
Invoke-WebRequest https://github.com/gastownhall/beads/releases/download/v1.2.2/checksums.txt -OutFile $env:TEMP\beads-checksums.txt
# 2) 校验 SHA256（比对 checksums.txt 对应行）
# 3) 解压至 D:\Software\ai-orchestrator\beads\
# 4) （授权后）将 D:\Software\ai-orchestrator\beads 加入 User PATH
# 5) 验证：bd version == 1.2.2
```

## 2. MCP Agent Mail（选型结论：Rust 版，见 docs/agent-mail-selection.md）

| 项 | 内容 |
|---|---|
| 推荐版本 | **v0.3.35**（2026-09-09；执行时以 releases/latest 为准并回填实际版本） |
| 官方来源 | github.com/Dicklesworthstone/mcp_agent_mail_rust（msvc x86_64 zip + SHA256SUMS[.minisig]） |
| 默认官方安装路径 | install.ps1：`%LOCALAPPDATA%\Programs\mcp-agent-mail`（C 盘） |
| 自定义目录支持 | ✅ `-Dest`（脚本实测有 param：Version/Dest/Force/Verify/NoVerify） |
| **推荐方案（D 盘合规）** | 方案 A（推荐）：**手工解压** zip 到 `D:\Software\ai-orchestrator\agent-mail\`（零 PATH 改动、零安装器副作用）；方案 B：install.ps1 `-Dest D:\...`（会前插 User PATH） |
| 是否修改 PATH | 方案 A：否；方案 B：写 **User PATH** |
| 用户目录/C 盘写入 | **数据默认在用户 profile（C 盘）→ 必须在首次运行前设置环境变量重定向到 D 盘**：`STORAGE_ROOT=D:\Software\ai-orchestrator\agent-mail\storage`、`DATABASE_URL=sqlite+aiosqlite:///D:/Software/ai-orchestrator/agent-mail/storage/storage.sqlite3`、`XDG_CONFIG_HOME=D:\Software\ai-orchestrator\agent-mail\config`（承载 config.env/HTTP_BEARER_TOKEN/sender.token） |
| 无法避免的 C 盘写入 | 方案 A/B 均无强制项；注意：**不要**运行 `am setup`/安装器的 Agent 自动探测（会写 C 盘各 Agent 的 MCP 配置）——ZCode 接入由本提案 §5 单独走授权 |
| 管理员权限 | 不需要 |
| OAuth | 不需要（HTTP Bearer Token 自生成） |
| 卸载/回滚 | 删除安装目录 + 重定向数据目录 + 清 User PATH（如用方案 B）；安装器无 uninstall 子命令 |
| 风险 | ① minisign 验签需本机有 minisign（没有则退化为 SHA256 校验，风险可接受）；② Windows 下 XDG 目录解析路径文档未明示（POSIX 文档为主）→ 装后用 `am`/health_check 实测确认数据落 D 盘再继续；③ 版本迭代快，锁定版本后升级需重跑 8 项能力测试 |

安装命令（授权后执行，方案 A）：
```powershell
# 1) 下载 v0.3.35 的 mcp-agent-mail-x86_64-pc-windows-msvc.zip + SHA256SUMS
# 2) 校验 SHA256
# 3) 解压至 D:\Software\ai-orchestrator\agent-mail\
# 4) 设置上述三个重定向环境变量（User 级，需授权；或经 .env 由服务启动脚本注入）
# 5) 验证：.\am.exe --version；mcp-agent-mail stdio 启动 health_check
```

## 3. Antigravity CLI（`agy`）

| 项 | 内容 |
|---|---|
| 推荐版本 | latest（官方持续更新渠道；装后回填实际版本） |
| 官方来源 | https://antigravity.google/cli/install.ps1（官方文档页 antigravity.google/docs/cli/install/） |
| 默认官方安装路径 | `%LOCALAPPDATA%\agy\bin`（**C 盘，强制**） |
| 自定义目录支持 | ❌ 仅 `--skip-path`（跳过 shell profile PATH 追加）与 `--skip-aliases`；无目录参数（raw 脚本 octet-stream 仅部分可读，按无处理） |
| **推荐方案** | `irm https://antigravity.google/cli/install.ps1 | iex` **不带** `--skip-path`（默认仅写用户级 shell profile PATH，非 System PATH）；**接受 C 盘例外**（见风险③） |
| 是否修改 PATH | 用户级 shell profile（PowerShell $PROFILE）；非 System PATH |
| 用户目录/C 盘写入 | 二进制 `%LOCALAPPDATA%\agy\bin`；配置 `~/.gemini/antigravity-cli/settings.json`；凭证入 **Windows 凭据管理器** |
| 无法避免的 C 盘写入 | 上述全部（binary/config/credential）——**属"官方安装器强制 C 盘且无法可靠自定义"情形：按规则记录安装限制，不强行规避，留人工裁决是否豁免** |
| 管理员权限 | 不需要 |
| OAuth | **需要一次性浏览器 OAuth**（人工完成；CI 备选：settings.json `modelProvider:"gemini"` + `GEMINI_API_KEY` 环境变量，注意仅设 key 无效必须两者都设） |
| 卸载/回滚 | 先 `agy` 内 `/logout` 清凭据管理器 → 删 `%LOCALAPPDATA%\agy` → 清 profile PATH 行（官方无 uninstall 命令） |
| 风险 | ① C 盘例外需你明示豁免；② worktree 内仓库检测 Bug（google-antigravity/antigravity-cli#68）→ 装后必须实测（P0 原 D 项扩展）；③ headless stdout 必须逐行读、权限软拒绝仍 exit 0 → Adapter（P3）须按此设计 |

## 4. 共享缓存（v1.1 §7.2 / Pre-P0 Gate #3 提案）

本机实测：pnpm **11.7.0 已装**；`~/.m2` 已存在于 C 盘（用户既有，**不迁移不动**）；D 盘 184G 可用；uv 缓存已在 D 盘（`UV_CACHE_DIR=D:\Software\scoop\persist\uv\cache`）✅。

| 缓存 | 提案路径（D 盘） | 启用方式 | 时机 |
|---|---|---|---|
| pnpm store | `D:\Software\ai-orchestrator\shared-cache\.pnpm-store` | `pnpm config set store-dir <path>`（全局用户配置；或项目级 .npmrc） | P1 首个含 JS 依赖的 target repo 时 |
| Maven local repo | `D:\Software\ai-orchestrator\shared-cache\.m2\repository` | 构建命令 `-Dmaven.repo.local=<path>` 或该 target repo 的 .mvn/maven.config | P1 首个 Java target repo 时（既有 `~/.m2` 保持不动，新缓存独立累积） |
| uv / Agent 缓存 | 已在 D 盘 / 随组件重定向 | 无需动作 | ✅ 已合规 |

P0 的 demo-repo 为纯 Python stdlib，无 node/java 依赖 → 本节仅提案不实施。

## 5. ZCode MCP 接入 Agent Mail（后续步骤，非本轮）

安装完成后，将 agent-mail MCP server 加入 `C:\Users\<user>\.zcode\cli\config.json` 的 `mcp.servers`（stdio 命令指向 D 盘 exe + 注入重定向环境变量）。**该文件为用户全局配置且含敏感 DSN——执行前单独请求授权，先备份。**（P1 协作闭环需要）

## 6. 汇总：需要人工授权的动作清单

| # | 动作 | 范围 | 修改面 |
|---|---|---|---|
| A1 | 下载+校验+解压 beads v1.2.2 到 D 盘 | User-scoped | 新目录，无 PATH |
| A2 | （可选）`D:\Software\ai-orchestrator\beads` 加入 User PATH | User PATH | +1 条目 |
| A3 | 下载+校验+解压 agent-mail v0.3.35 到 D 盘 + 三个重定向环境变量（User 级） | User-scoped | 新目录 + 3 环境变量 |
| A4 | agy 官方安装（**C 盘例外豁免**）+ 一次性浏览器 OAuth | User-scoped | %LOCALAPPDATA%\agy、~/.gemini、凭据管理器、profile PATH |
| A5 | （P1 前）ZCode config.json 增加 agent-mail MCP 条目（先备份） | 用户全局配置 | +1 mcp server |

以上任一项未获授权即保持未安装，对应 P0 验证项维持 BLOCKED。
