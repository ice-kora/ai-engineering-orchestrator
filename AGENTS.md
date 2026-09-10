# 本项目 Agent 协作规范（ai-engineering-orchestrator）

本文件约束所有在本仓库工作的 Coding Agent（ZCode / Antigravity / 未来执行器）。

## 1. 项目定位

多 Agent 协作控制面（Control Plane），不是业务仓库。业务项目作为 target repository 被管理，路径在仓库外（如 `D:\workspace\company\xxx`）。

## 2. 设计基线与最高原则

- 基线：`AI_Engineering_Orchestrator_实施方案_v1.0.docx` + `docs/architecture_spec_v1.1.md`。
- **真实运行证据优先**：组件实际行为与规范不一致时，记录证据（命令 + 输出），上报 GPT Orchestrator 裁决；严禁私自改架构或绕过组件官方接口。
- 严禁绕过 CLI 直改 Beads 底层 Dolt/SQLite；严禁调用未经证实的虚构接口（v1.1 §1.2/§8.1）。

## 3. 职责与角色（v1.0 §5）

- L1 GPT Orchestrator：拆解/派工/裁决/最终验收（不大量写码）。
- L2 ZCode：Repo 理解、后端、DB、复杂重构（V1 Pull 模式）。
- L2 Antigravity：UI、浏览器/E2E、独立 Review（Push 模式，headless `agy -p`）。
- **实现者不得做自己任务的最终 Reviewer。**
- **ZCode = Pull Executor**（V1 固定；ZCode Headless = DEFERRED_PRODUCT_GAP，不做逆向探索，errata E-08）。
- **Agent Mail = am CLI Adapter**（`adapters/agent_mail.py`）；MCP compatibility 暂不继续研究（errata E-05，FALLBACK_CLI）。

## 4. 统一标识约定（强制）

- Beads Task ID 是唯一任务主键。**实际格式为 bd 生成的 `<repo-prefix>-<rand4>`（如 `demo-repo-ujn`）**，以 Beads 实际输出与 `adapters/handover.py` 的校验正则 `[a-z0-9][a-z0-9-]*-[a-z0-9]{3,8}` 为准（见 docs/runtime-errata-v1.1.md E-01），不写死 `^bd-…$`。
- Agent Mail `thread_id` == Beads Task ID；消息 subject 前缀 `[bd-xxx]`。
- 文件预约 `reason` == Beads Task ID。
- Git 分支：`agent/<agent-name>/<task-id>`（pattern `^agent/[a-z0-9_-]+/bd-[0-9a-z]+$`）。
- Commit message 包含 `[bd-xxx]`。

## 5. 状态机与映射（v1.1 §8.2）

工程状态机：`BACKLOG → PLANNING → READY → CLAIMED → IMPLEMENTING → SELF_TEST → REVIEW → (FIX_REQUIRED→IMPLEMENTING) → VERIFIED → MERGED → DONE`；异常支线 `SUSPECT`、`ESCALATED`。

Beads 只存粗粒度（`open` / `in_progress` / `blocked` / `closed`）；细粒度阶段（stage:CLAIMED 等）用 label/metadata 表达——**前提是实测证明 label 能力存在**（见 docs/P0-SPEC-DELTA-REVIEW.md 核验矩阵）。

## 6. 文件预约（Advisory Lease 语义）

- 预约是**劝告性租约**，不是硬分布式锁。
- reserve 返回任何 conflict ⇒ 本次申请整体失败：释放已授予部分 → 补偿 Beads claim（回 `open`）→ 退避（30s 起指数退避，上限 5m）→ **禁止动笔写代码**（v1.1 §3.1）。
- 心跳失联 180s 不直接回 READY：先 `SUSPECT`，确认租约 EXPIRED 或 force-release 成功后才可重新领取。

## 7. Worktree 策略（v1.1 §7）

- 默认 `PER_AGENT reusable worktree`（`worktrees/agent-zcode`、`worktrees/agent-antigravity`）；高冲突任务后续才用 PER_TASK。
- 不要求先 checkout main；在专属 worktree 内 `git checkout -B agent/<agent>/<task-id> <base>`。
- 依赖缓存共享：pnpm 共享 store、Maven 共享 local repo；**不共享/长期保留 node_modules**。
- FAILED / SUSPECT / BLOCKED 的 worktree **严禁立即 `--force remove`**：先取证（patch + status + log → `failure_artifacts/`）、打 `archive/failed/<task-id>-<ts>` 分支，保留 72h 后再清理。

## 8. Review 熔断（v1.1 §6）

普通 review iteration 仅 1..3；第 3 次仍 CHANGES_REQUESTED ⇒ `ESCALATED` → GPT 仲裁（独立 ArbitrationResult，不复用 ReviewReport）。不存在第 4 轮普通 review。

## 9. 安装与环境红线

- 任何系统级安装：先提案（`docs/INSTALLATION-PROPOSAL.md`）→ 人工授权 → 执行。分三类：Project-local / User-scoped / Machine-level。
- 新组件统一装 `D:\Software\ai-orchestrator\<component>\`；禁止未经允许默认装 C 盘、写 System PATH、在 C 盘建大缓存。
- API Key / 密码只进 `.env`（已 gitignore）；严禁硬编码、严禁提交。
- 不操作生产服务器/数据库/发布；高危动作必须 Human Approval。

## 10. 阶段纪律（更新于 2026-09-10）

- 当前阶段状态（2026-09-10 更新）：P0/P1 = PASS/CLOSED；P2-00 = PASS/CLOSED；**P2-01 = PASS/CLOSED**（Final Gate PASS，KNOWN_FLAKE_WAIVER=ACCEPTED）；**P2-02 = APPROVED/WAITING 指令**（LLM Planner 接入；涉及真实 API 调用与密钥配置，须按 GPT 详细指令执行）。
- 规范与运行时冲突时以 `docs/runtime-errata-v1.1.md` 为事实覆盖层（推翻需新证据 + GPT 裁决）。
- 所有验证结论必须附证据（实际命令 + 输出摘要 + 文件路径），存 `docs/p0-evidence/`、`docs/p1-evidence/`。
