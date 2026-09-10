# P2-03 报告 — Final Decision / Arbitration Layer（2026-09-10）

> 目标：Codex 从 Planner 升级为语义裁决层（Final Gate + Review Arbitration），
> 分层红线：**事实判断归确定性代码，语义判断才交给模型，人是例外权威**。
> 后端 = Codex CLI + ChatGPT 订阅（errata E-09；无 API key/Router/fallback）。

## P2_03_STATUS: **PASS**

## REQUEST_PERSISTENCE = **VERIFIED**

## FINAL_GATE_CONTRACT = **VERIFIED**

## CODEX_FINAL_GATE = **VERIFIED**

## REVIEW_ARBITRATION = **VERIFIED**

## PLAN_DONE_INVARIANT = **VERIFIED**

## FAILURE_ATOMICITY = **VERIFIED**

## REAL_FINAL_GATE_DEMO = **SUCCESS**（双场景）

## REGRESSION = **102 passed / 7 skipped / 1 failed（唯一失败 = FLAKE-001，已裁决豁免的已知间歇类；隔离复跑 2/2 全过；实录过程见下）**

## CAN_ENTER_P2_04 = **NO**（按指令停止，等待 GPT P2-04 Gate）

---

## 实现（对照指令逐条）

| 指令项 | 落实 |
|---|---|
| §0 P2-02 终态修正 | 报告更正：`OPENAI_RESPONSES_API = NOT_USED`（废弃错误表述 VERIFIED_VIA_CODEX）、`PLANNER_BACKEND = CODEX_CLI_SUBSCRIPTION`、`CODEX_CLI_REAL_SMOKE = SUCCESS`、`P2_02 = CLOSED`；Responses API 代码保留为 optional dormant |
| §3 UserRequest 持久化 | `Store.save_request/request`（`requests/<id>.json`）；同 id 同内容幂等、不同内容 `RequestConflict` 阻塞不覆盖（T9/T10）；CLI 三个 plan 分支均保存 |
| §4 Plan 状态改造 | 新增 `READY_FOR_FINAL_GATE / FINAL_GATE_RUNNING / FINAL_FIX_REQUIRED / ESCALATED`；`all DONE ≠ Plan DONE`——APPLIED→READY_FOR_FINAL_GATE→(gate)→DONE，不允许跳变；历史 Plan 状态兼容 |
| §5 FinalGateContext | `FinalGateContextBuilder`：仅收 request + plan 摘要 + 每任务**机器验证后**事实（acceptance/handover 摘要/受影响文件/测试证据/fresh APPROVED review/verified head）；stale review / invalid handover / 缺证据 ⇒ `FinalGateForbidden`，Codex 不被调用（T4/T5） |
| §6 Decision Contract | `FinalGateDecision`（verdict 3 枚举 + request_coverage[requirement/status/evidence] + findings[severity/task_key/desc/action]，additionalProperties=false）与 `ArbitrationDecision`（REPLAN_REQUIRED / HUMAN_DECISION_REQUIRED / ACCEPT_RISK_RECOMMENDATION + rationale + binding_directives + risk_note）；**双 schema 分层**：本地 Draft 2020-12 全契约 vs 手写 wire 子集（strict 全 required——实证发现 codex strict 模式要求 required 覆盖全部 properties，optional 键（task_key/risk_note）在 wire 层变为必填、模型空值填充） |
| §7 Final Gate 权限 | Codex 只出 decision payload；`APPROVED` 后 host 再跑 `_plan_done_invariant`（每任务 DONE+无租约+fresh APPROVED+head 已 merge）才落 DONE；引擎/决策层零 Beads/Git/Store 写权 |
| §8 FOLLOWUP | → `FINAL_FIX_REQUIRED` + `ACTION_REQUIRED: REPLAN`，findings 存档；**不自动建任务/reopen/改码/批新计划**（demo B 实证 beads delta=1，即仅计划自身任务） |
| §9 Arbitration | iteration≥3 → ESCALATED + `START_ARBITRATION`（reconciler 自动步）；输入=acceptance+历次 review+handover+commits+tests；`ACCEPT_RISK_RECOMMENDATION` 仅推荐——任务不自动 close/不 DONE（T12）；真正豁免须人工 |
| §10 CodexCLIInvoker | 最小共享传输（read-only/ephemeral/stdin/output-schema/-o/timeout/exit-code/脱敏 evidence）；**不是通用 LLM 框架**——Planner 与决策引擎各自保留 prompt/schema/domain |
| §11 安全运行 | 决策调用同 Planner：read-only+ephemeral+neutral cwd+ignore-rules；无 shell/git/repo/Beads/Mail 写、无网络工具 |
| §12 Retry | `MAX_TOTAL_CODEX_CALLS=2`；invalid structured/semantic（contract/**grounding**）→ repair prompt（附违规）→ 完整重生成一次；二次失败 `DecisionError` → Final Gate 场景 plan ESCALATED / 仲裁场景任务保持 ESCALATED；fail closed 无第三次（T7/T8） |
| §13 Prompt | `prompts/final-gate-v1.md` + `prompts/arbitration-v1.md`（版本化；机器事实权威/UNCERTAIN 不猜/无权改状态；final-gate 含 strict task_key 规则） |
| §14 one-step 集成 | 同一 reconcile 至多一个自动 action；优先级 arbitration > review > notify > final-gate；全 DONE 第一次→READY_FOR_FINAL_GATE，**下一次**才 START_FINAL_GATE；无 while-loop、无 daemon |

## T1-T15（`tests/integration/test_p2_03.py`，15/15 全绿）

T1 全 DONE→READY_FOR_FINAL_GATE 非 DONE ✓ | T2 APPROVED→host 不变量→DONE ✓ | T3 FOLLOWUP→FINAL_FIX_REQUIRED+零自动任务 ✓ | T4 证据缺失→Codex 未被调用 ✓ | T5 stale review 被 context 排除（FORBIDDEN）✓ | T6 捏造 task_key→decision 拒绝→ESCALATED ✓ | T7 malformed→repair 恰 2 次调用 ✓ | T8 双 invalid→ESCALATED ✓ | T9 request 幂等 ✓ | T10 同 id 异内容→BLOCKED ✓ | T11 三轮 review→仲裁生成 ✓ | T12 ACCEPT_RISK 不 close ✓ | T13 invoker read-only+ephemeral 命令形状 ✓ | T14 JsonPlanner 回归 ✓ | T15 P2-01 reconcile 回归（PULL 边界不变）✓

## Real Demo（`scripts/p2_03_demo.py`，真实组件全程）

**Mode A（APPROVED→DONE）**：request（取消原因校验 4 要求）→ JsonPlanner plan → approve → apply → PullFlow（claim+reserve）→ 实现+18/18 自测 → handover → reconcile 自动 agy review **APPROVED** → merge/close → reconcile#2 READY_FOR_FINAL_GATE → reconcile#3 **真实 Codex Final Gate（35s）**：逐条需求 SATISFIED 带证据引用 → host 不变量 OK → **DONE**。
**Mode B（FOLLOWUP）**：同一交付，但 request 追加“订单历史 CSV 导出”（无人实现）→ 真实 Codex（38s）判 CSV **MISSING**、其余 SATISFIED → **FINAL_FIX_REQUIRED** + REPLAN 提示 → **Beads 零自动新增**。
过程中两项真实发现并入实现：① agy 评审员实际抓到 v1 测试缺口（normalized-return 未断言）→ demo 真实走 fix→re-review 循环后 APPROVED；② Codex 首次把 findings.task_key 用作"未来任务名"→ 引擎级 grounding retry + prompt strict 规则（本地 guard 兜底保留）。
证据：`docs/p2-evidence/p2-03-demo-{approved,followup}.txt` + `sandbox/orchestrator-state/final_gates/*.json`。

## 分层红线复核（§2）

Reconciler 的全部事实判断（Beads status/dependency/lease/handover validity/review freshness/git head/merge/completion invariant）保持确定性代码；Codex 仅接收已验证事实并输出语义结论；host 校验后执行状态迁移。模型任何越界输出（捏造 task_key / GARBAGE verdict）被拒或触发 fail-closed。

## 全量回归

回归实录（证据优先）：
1. 首次全量 `pytest tests`（P2-03 代码就位后单次完整运行）：**102 passed / 7 skipped / 1 failed**。唯一失败 `test_fully_verified_closed_task_done` 为 P2-01 时代旧断言（plan 直接 DONE）被 P2-03 §4 生命周期正当取代——任务级 DONE 与完成不变量全部仍然成立；属测试语义过时而非生产缺陷（该探针未持久化 UserRequest，按设计 plan 停在 APPLIED+gate deferred）。
2. 已将该测试更新至 P2-03 语义（task DONE ✓ + plan 不再直跳 DONE），单独复跑通过。
3. 更新后全量复跑（单次完整运行，19:30）：**102 passed / 7 skipped / 1 failed**——唯一失败为 **FLAKE-001**（`test_forensic_retention_dry_run`，docs/flake-registry.md 已登记且 GPT 已裁 `KNOWN_FLAKE_WAIVER=ACCEPTED` 的 E-06/E-07 间歇类）；失败后单套件隔离复跑 **2/2 全过**（与 P2-01 终跑同模式），非 P2-03 引入（P2-03 未触及 worktree/agy 路径）。**P2-03 Final Regression（实录）= 102 passed / 7 skipped / 1 failed（唯一失败 = FLAKE-001， waived）**；被更新的 `test_fully_verified_closed_task_done` 本轮通过。

## 非目标遵守

未实现：自动 replan/后续任务创建/自动修码/自动风险豁免/daemon/scheduler/Web·Board UI/Model Router/API provider/RAG/vector DB/大并发/生产接入/部署。
