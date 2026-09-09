# P0 报告（第一轮 · 2026-09-09）

> 执行人：ZCode (Implementation Lead) · 依据：v1.0 docx + architecture_spec_v1.1 + GPT Final Gate Decisions（5 项补充已吸收）
> 原则执行情况：真实运行证据优先于规范推测；两处规范与现实的偏差已按"记录证据、上报 GPT、不私改"处理（见 §ZCode Headless 与 §Known Issues）。

## P0 STATUS: **PARTIAL**

## Environment

Win11 23H2 (10.0.22631) · PowerShell 5.1+7.6.4 · Git 2.55.0.windows.3 · Python 3.13.15 (scoop) · uv 0.12.10 · Node 24.19.0/npm 11.17.0 (nvm, D 盘) · pnpm 11.7.0 · Docker 无（不需要）· sqlite3 CLI 无（Python sqlite3 3.50.4 够用）· `py` 启动器损坏（用 `python`/`uv`）· D 盘 184G 可用。完整矩阵：`docs/version-matrix.md`（标记为稳定事实，不因后续 spec 作废）。

## Installed Components

已就绪：Git、Python+uv、Node、pnpm、pwsh7、ZCode 桌面+CLI 0.16.5、项目级 .venv（pytest/jsonschema/mcp，官方 PyPI 源）。
未安装（Blocker，提案待批）：Beads bd v1.2.2、Agent Mail Rust v0.3.35、Antigravity agy（见 `docs/INSTALLATION-PROPOSAL.md`）。

## Version Matrix

见 `docs/version-matrix.md`。要点：Beads 锁 v1.2.2（**PyPI `beads` 为同名无关项目，禁 pip 安装**）；Agent Mail 推荐 Rust v0.3.35（Python 版 v0.3.2 已停更且非 Windows 原生）；ZCode CLI 0.16.5 headless 参数面实测存在。

## Tests Performed

`uv run pytest tests` → **15 passed / 14 skipped**（14 个 skip 全部为组件未装的 BLOCKED-BY-DESIGN，装后自动生效）。
- unit：Envelope Schema（Draft 2020-12）8/8 PASS
- integration/worktree：PER_AGENT 独立性 + 现场保留 2/2 PASS
- integration/zcode-headless：就绪探针 3/3 PASS（版本/flag 矩阵/exit code），7 项模型运行测试 SKIP-BLOCKED
- integration/beads、agent-mail、conflict、unified-id：SKIP-BLOCKED（就绪）

## Beads Test

**BLOCKED（未安装）**。测试就绪：`tests/integration/test_beads_lifecycle.py`（create→dep add→ready→**双进程并发原子 claim（断言恰一胜者）**→update→close + v1.1 §8.3 补偿命令核验，后者若缺失将以 xfail 记录证据交 GPT）。

## Agent Mail Test

**BLOCKED（未安装）**。测试就绪：`test_agent_mail.py`（8 能力实测矩阵，落盘 tool-matrix.md）、`_mail.py`（stdio client，数据重定向到 sandbox，零 C 盘写入）。选型完成：**Rust 版**（`docs/agent-mail-selection.md`）。

## File Reservation Conflict Test

**BLOCKED（依赖 Agent Mail）**。用例就绪：`test_file_reservation_conflict.py` —— `src/service/**`（agent-zcode）vs `src/service/TestService.java`（agent-antigravity），断言：明确冲突信号 + 失败方不得残留部分授予 + 不影响对方租约（v1.1 §3.1 advisory 语义）。

## Git Worktree Test

**PASS（2/2，真实执行）**：
1. PER_AGENT 复用 worktree（`worktrees/agent-zcode`、`worktrees/agent-antigravity`，v1.1 §7.1）共存；任务分支 `checkout -B agent/<agent>/<task-id> <base>`（不强制 main）；运行期唯一标记证明：未提交修改对 main 与兄弟 worktree 均不可见（隔离 ✓）；分支可 diff；worktree 内 `python -m unittest` 独立运行 3 tests OK；两分支先后 merge 入 main 无冲突（合并 ✓）。
2. 现场保留 SOP 干跑（v1.1 §7.3）：`failure_artifacts/bd-wt0003.{patch,status,log}`（含未提交 diff——用 intent-to-add 捕获新文件——与未跟踪清单）+ `archive/failed/bd-wt0003-<ts>` 归档分支，之后才做常规（非 force）清理。
证据：`sandbox/demo-repo/`（git log/branch 可复核）。

## Antigravity Headless Test

**BLOCKED（未安装）**。官方能力已核实（`agy -p --output-format json --model --effort --conversation`，文档级证据入 version-matrix）。安装提案：官方 install.ps1（强制 C 盘 `%LOCALAPPDATA%\agy\bin`——**需你明示豁免**）+ 一次性浏览器 OAuth。已知风险待实测：worktree 检测 Bug（antigravity-cli#68）。

## ZCode MCP Test

**PASS（能力实证，本会话即证据）**：MCP（3 个 server 实际加载：playwright/dbhub/context7）、Subagent（本轮实际调度 3 个 Explore 子代理）、AGENTS.md 注入（全局+项目级生效）、Git/PowerShell 调用（全程使用）、CLI headless 参数面存在。附：ZCode 全局 MCP 配置中发现 SECURITY-001。

## Demo Result

**BLOCKED（依赖 bd + Agent Mail）**。`scripts/demo.ps1` 全链路就绪（Task→Claim→Reserve→Worktree→Code→Test→Review→Release→Close，统一 bd-xxx ID）；组件装好后直接运行。Git 侧子链（branch/commit 规范 + 统一 ID 的 git 部分）已由 worktree 测试与 `test_task_id_unification.py` 的 git 部分实测通过。

## Known Issues

1. **ZCode CLI 0.16.5 help/解析器漂移**：`--max-turns`、`--allowed-tools`、`--settings` 在 --help 中列出但解析器拒绝（`Unknown option`）；实测可用：`--prompt/-p/--json/--cwd/--mode/--disallowed-tools`。→ 提交 GPT：影响未来 Push Executor 的工具限制设计（用 denylist 替代 allowlist）。
2. **ZCode headless 模型配置未文档化**：`Model config is missing`；config.json 增补 `model.main={providerId:zai,modelId:glm-5.3}` 无效（试验已完整还原，零残留）。解锁候选：用户执行一次 `zcode login`（OAuth）。
3. uv 全局镜像（tuna）对部分 wheel 403 → 项目内一律 `UV_DEFAULT_INDEX=https://pypi.org/simple` 覆盖（脚本已固化）。
4. git 全局身份缺失（各仓库需本地 config）。
5. v1.1 §8.3 `bd update --status open` / `bd label` 官方未证实（装后实测，缺失则交 GPT 裁决载体）。
6. `~/.m2` 在 C 盘（用户既有，未动；新共享缓存走 D 盘提案）。

## Risks

- 三组件未装 → P0 Exit 未达成（核心风险，等待安装授权）。
- agy 强制 C 盘 → 需豁免决策，否则 P0-13 持续 BLOCKED。
- Agent Mail 数据默认落 C 盘 → 已设计环境变量重定向方案，装时必须先设。
- SECURITY-001：内网 MySQL 高权限账号明文口令（**实测有效**）存于 ZCode 全局配置 → 建议尽快轮换+改环境变量（`docs/security-findings.md`）。
- ZCode headless 若长期无法解锁：不影响 V1 架构（Pull 模式为主设计），仅推迟 Push 化探索。

## P1 Recommendation

1. 先完成 P0 收尾：授权三组件安装（提案已备）→ 跑全部 BLOCKED 测试 → `zcode login` 解锁 headless 7 项 → 重出 P0-REPORT（目标 PASS）。
2. P1 按原计划：人工触发的基础协作闭环（ZCode 会话 Pull task + Agent Mail 预约 + 交叉 review 消息），不改架构。
3. 将两项 spec-vs-reality 证据（ZCode flag 漂移、bd 补偿命令待核验）提交 GPT 做阶段仲裁。
4. ZCode Push/Headless 升级议题：仅在 P0-ZCODE-HEADLESS 达成 VERIFIED 后才具备讨论前提。

## Evidence

| 证据 | 位置 |
|---|---|
| 测试全量结果 | 本轮 `pytest tests` → 15 passed/14 skipped（会话日志） |
| healthcheck 快照 | `docs/p0-evidence/healthcheck-20260909.txt`（exit=1，4 组件缺失如实报告） |
| Schema Gate #4 | `tests/unit/test_envelope_schema.py`（8 用例）+ uv.lock |
| Worktree/现场保留 | `sandbox/demo-repo`（git log --grep "[bd-"、branch --list "archive/failed/*"、failure_artifacts/） |
| ZCode headless 调查 | `docs/p0-evidence/zcode-headless/`：INVESTIGATION.md、flag-matrix-summary.txt（9 flag 接受/拒绝）、probe-blocked.txt（ModelConfigMissing 原始输出）、run1（help 全文+Unknown option）、version.txt、exit-bad-args.txt |
| SECURITY-001 探测 | `docs/security-findings.md`（只读 <PROBE_STATEMENT> 成功=凭证有效，未打印明文） |
| 全局配置试验还原 | INVESTIGATION.md §5（keys 恢复为 plugins/mcp，备份已删） |
| Git 状态 | 2 commits：`1fbbf7a`（脚手架+gate4+worktree）、`cf00c9c`（delta review/选型/提案/脚本/headless 探针/安全记录），working tree clean |

## Can Enter P1: **NO**
（固定为 NO，直至 P0 Exit Criteria 全部完成并由 GPT 进行 Gate Review。）
