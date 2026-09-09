# P0 实施计划（正式版 · v1.1 吸收 + GPT Plan Gate 5 项补充）

| 项 | 值 |
|---|---|
| 文档版本 | 1.0（2026-09-09） |
| 状态 | **执行中**（GPT `P0_PLAN_GATE = PASS` 已获批准；本文为批后落盘正式版） |
| 基线 | v1.0 实施方案 docx + `docs/architecture_spec_v1.1.md` + `docs/P0-SPEC-DELTA-REVIEW.md`（差异评审 PASS） |
| 边界 | 不进 P1；不实现 Orchestrator/Router/完整 Adapter/自动调度/自动 Merge/生产接入；系统级安装一律先提案后授权 |

---

## 1. 当前环境检查结果（实测 2026-09-09，稳定事实）

| Component | Status | Version | Path | Issue |
|---|---|---|---|---|
| Windows 11 23H2 | ✅ | 10.0.22631 | — | — |
| PowerShell 5.1 / pwsh 7 | ✅ | 5.1.22621 / 7.6.4 | System32; Program Files | 脚本统一 pwsh 7 |
| Git | ✅ | 2.55.0.windows.3 | D:\Software\git | worktree 全功能实测通过 |
| Python | ✅ | 3.13.15 (scoop) | D:\Software\scoop\... | `py` 启动器损坏（exit 112）→ 用 `python`/`uv` |
| pip / uv / pnpm | ✅ | 26.2.1 / 0.12.10 / 11.7.0 | uv 缓存在 D 盘 | uv 全局镜像（tuna）403 → 命令级覆盖官方源 |
| Node / npm | ✅ | 24.19.0 / 11.17.0 | D:\Software\nvm | — |
| Docker / sqlite3 CLI | ❌ 未装 | — | — | P0 不需要（Python sqlite3 3.50.4 够用） |
| ZCode 桌面 + CLI | ✅ | 0.16.5 | D:\Software\zcode（CLI 不在 PATH） | headless 见 §2 P0-ZCODE-HEADLESS |
| Beads `bd` | ❌ 未装 | — | — | **Blocker**（提案见 INSTALLATION-PROPOSAL §1） |
| Agent Mail | ❌ 未装 | — | — | **Blocker**（选型 Rust v0.3.35，提案 §2） |
| Antigravity `agy` | ❌ 未装 | — | 桌面版已装无 CLI | **Blocker**（C 盘例外需豁免，提案 §3） |

已知环境坑（已固化对策）：uv 镜像 403（命令级 `UV_DEFAULT_INDEX` 官方源覆盖）；git 全局无身份（各仓库本地 config）；`~/.m2` 在 C 盘（用户既有，不动）。
安全：SECURITY-001（dbhub 明文高权限数据库口令，实测有效）→ `docs/security-findings.md`，P0 不处理。

## 2. P0 技术验证计划

> 状态值：✅ 本轮已过 / ⏳ 脚本就绪待组件安装 / 🧊 已按 GPT 指令解除冻结但属 P1+ 不提前实现

| ID | Goal | Files | Commands（核心） | Risk | Acceptance | 状态 |
|---|---|---|---|---|---|---|
| P0-01 | 环境矩阵落盘 | docs/version-matrix.md | 实测采集 | 版本漂移 | 全项实测有据 | ✅ |
| P0-02 | Spec Delta Review | docs/P0-SPEC-DELTA-REVIEW.md | — | 误读规范 | 5 项分析齐 + 核验矩阵 | ✅ PASS |
| P0-03 | 工程初始化 | .gitignore/.git/README/AGENTS.md/.env.example | git init -b main | — | 首提交成功 | ✅ |
| P0-04 | 项目级 Python 环境 + Gate #4 | pyproject.toml, .venv | uv sync; pytest tests/unit | 镜像 403 | Draft202012Validator 8/8 | ✅ |
| P0-05 | Schema 验证器基座（v1.1 §10 #4） | tests/unit/test_envelope_schema.py | 同上 | — | Envelope 正反用例全过 | ✅ |
| P0-06 | Git Worktree 独立性（PER_AGENT） | tests/integration/test_git_worktree.py, sandbox/demo-repo | git worktree add/-B/merge | 路径分隔符/unittest 发现 | 2/2：隔离+合并+worktree 内测试 | ✅ |
| P0-07 | 现场保留 SOP 干跑（v1.1 §7.3） | 同上（failure_artifacts/） | git diff/status + archive 分支 | — | patch/status/分支留痕 | ✅ |
| P0-08 | ZCode headless 10 项验证 | tests/integration/test_zcode_headless.py | node zcode.cjs --prompt/--json/--cwd... | help/解析器漂移；模型配置未文档化 | 9+1 全过 = VERIFIED | ⚠️ **DISCOVERED→BLOCKED**（flag 矩阵 3/3 过；7 项待 `zcode login` 解锁后重跑） |
| P0-09 | Beads 安装+生命周期+原子 claim 并发 | tests/integration/test_beads_lifecycle.py | bd create/dep add/ready --json/update --claim/close | label/--status 未证实 | 生命周期全过 + 并发 claim 恰一成功 + §8.3 命令核验 | ⏳ 待授权 |
| P0-10 | Agent Mail 安装+8 能力+tool matrix | tests/integration/test_agent_mail.py, test_file_reservation_conflict.py | MCP client 实测 | 数据落 C 盘 | 8 能力逐项过 + 实际 schema 记录 | ⏳ 待授权 |
| P0-11 | 冲突语义（advisory lease） | test_file_reservation_conflict.py | reserve src/service/** vs src/service/TestService.java | — | conflict 非空=整体失败信号 | ⏳ 依赖 P0-10 |
| P0-12 | 统一 ID 全链路 Demo | scripts/demo.ps1, test_task_id_unification.py | Task→Claim→Reserve→Worktree→Test→Review→Release→Close | 组件缺失 | 全链路留痕（bd-xxx 四处一致） | ⏳ 待授权 |
| P0-13 | agy 安装+headless 冒烟+worktree 兼容 | scripts/healthcheck.ps1 内置探针 | agy -p ... --output-format json | C 盘例外；#68 Bug | JSON 输出可解析 + worktree 内可运行 | ⏳ 待授权+豁免 |
| P0-14 | healthcheck / bootstrap / demo 脚本 | scripts/*.ps1 | pwsh scripts/healthcheck.ps1 | — | 缺件如实报告非零退出 | ✅（本轮交付） |
| P0-15 | pre-commit guard 模板（Gate #5） | templates/hooks/pre-commit.agent-guard | 模板 | — | 模板+Windows shim 说明就绪 | ✅（本轮交付） |

🧊 P1+ 延后（v1.1 契约已入档，不提前实现）：Saga 编排、心跳/两阶段自愈、审查熔断、DTO 接入层、幂等去重窗口、Adapter 最终协议、pnpm/.m2 共享缓存实配、清理脚本。

## 3. 当前 Blocker

| # | Blocker | 影响 | 解法 |
|---|---|---|---|
| B1 | bd 未安装 | P0-09/12 | 授权 INSTALLATION-PROPOSAL §1（D 盘 portable v1.2.2） |
| B2 | Agent Mail 未安装 | P0-10/11/12 | 授权提案 §2（Rust v0.3.35 + D 盘重定向） |
| B3 | agy 未安装 | P0-13 | 授权提案 §3（含 C 盘例外豁免 + 人工 OAuth） |
| B4 | ZCode headless 模型配置未文档化 | P0-08 剩余 7 项 | 用户执行一次 `zcode login`（或 GPT 裁决其他接线方式）后重跑套件 |
| B5 | v1.1 §8.3 CLI 能力（--status open / label）官方未证实 | P0-09 的补偿命令设计 | bd 装后实测；不符则记录证据交 GPT 裁决（不私改） |

## 4. 计划创建的文件（本轮实际创建）

`.gitignore`、`README.md`、`AGENTS.md`、`.env.example`、`pyproject.toml`、`uv.lock`
`docs/`：P0-SPEC-DELTA-REVIEW.md、architecture_spec_v1.1.md（用户提供）、version-matrix.md、agent-mail-selection.md、INSTALLATION-PROPOSAL.md、security-findings.md、P0-IMPLEMENTATION-PLAN.md（本文）、P0-REPORT.md、p0-evidence/（security-findings.md、zcode-headless/ 全套）
`tests/unit/test_envelope_schema.py`；`tests/integration/`：test_git_worktree.py、test_zcode_headless.py、test_beads_lifecycle.py、test_agent_mail.py、test_file_reservation_conflict.py、test_task_id_unification.py
`scripts/`：bootstrap.ps1、healthcheck.ps1、demo.ps1；`templates/hooks/pre-commit.agent-guard`
`sandbox/demo-repo/`（独立 git 仓库）、`sandbox/zcode-headless-scratch/`

## 5. 计划修改的文件

- **项目外（本轮实际发生，均已还原/说明）**：`C:\Users\<user>\.zcode\cli\config.json` 曾增补 `model.main` 键做验证试验，**已从备份完整还原**（见 p0-evidence/zcode-headless/INVESTIGATION.md）。
- **项目外（待授权）**：三组件安装 + 环境变量 + （P1 前）ZCode config.json 的 mcp.servers 增条目。
- 项目内：全部为新建文件，无既有代码修改。

## 6. 安装行为

- **仅项目内**（已执行）：uv `.venv`（pytest/jsonschema/mcp，官方源，可删除回滚）。
- **系统级/User-scoped（未执行，全部停在提案）**：见 INSTALLATION-PROPOSAL §6 授权清单 A1–A5；脚本永不静默安装（bootstrap 默认 check-only，`-InstallSystem` 需逐项确认）。
- 统一 D 盘约束：`D:\Software\ai-orchestrator\{beads,agent-mail,antigravity-cli,shared-cache}\`；禁止默认 C 盘/System PATH/C 盘大缓存；agy 为唯一 C 盘例外候选（需明示豁免）。

## 7. 验收标准（对应 v1.0 P0 Exit Criteria）

P0 Exit = "组件全部可独立工作"（Beads、Agent Mail、MCP、AG CLI、Git Worktree + healthcheck + demo repo）。分解：

| 验收项 | 要求 | 当前 |
|---|---|---|
| Git Worktree 独立工作 | PER_AGENT 隔离/合并/现场保留 | ✅ 2/2 |
| 环境与版本矩阵 | 全实测落盘 | ✅ |
| Schema 基座（Gate #4） | Draft 2020-12 实测 | ✅ 8/8 |
| ZCode headless | 9+1 全过 → VERIFIED | ⚠️ DISCOVERED→BLOCKED（B4） |
| Beads 独立工作 | 生命周期+原子 claim+§8.3 核验 | ❌ 待 B1 |
| Agent Mail 独立工作 | 8 能力+冲突信号+tool matrix | ❌ 待 B2 |
| agy 独立工作 | headless JSON+worktree | ❌ 待 B3 |
| 统一 ID 全链路 demo | Task→…→Close 留痕 | ❌ 待 B1+B2 |
| healthcheck/bootstrap/demo | 三脚本交付 | ✅（本轮） |

**综合判定：P0 = PARTIAL；Can Enter P1 = NO**（按指令固定为 NO，直至 Exit Criteria 全过 + GPT Gate Review）。
