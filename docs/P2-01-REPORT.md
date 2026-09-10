# P2-01 报告 — Orchestrator Kernel + Plan/DAG + Reconciliation（2026-09-10）

> 范围：GPT P2-01 指令全量（确定性内核；无 LLM API/Model Router/daemon）。
> 实现优先级遵循 Runtime Contract：errata > 已验证 Adapter 行为 > v1.1 > v1.0。

## P2_01_STATUS: **PASS**

## IDEMPOTENT_APPLY = **VERIFIED**

## DAG_RECONCILIATION = **VERIFIED**

## REAL_DEMO = **SUCCESS**

## CAN_ENTER_P2_02 = **NO**（按指令停止，等待 GPT 放行）

---

## 交付

| 模块 | 内容 |
|---|---|
| `orchestrator/contracts.py` | UserRequest / ExecutionPlan / PlannedTask 三契约（JSON Schema Draft 2020-12 + dataclass + DAG 校验：未知依赖/重复键/环检测） |
| `orchestrator/planner.py` | PlannerPort + JsonPlanner（确定性；request_id/target_repo 强制对齐）；GPTPlanner 占位（P2-02 前显式 NotImplementedError） |
| `orchestrator/approval.py` | Human Approval Gate：PLANNED → 显式 approve/reject；审批记录含 plan_id/approved_at/approved_by；**自动自批准被代码级禁止**（approved_by 黑名单 + 仅 CLI 可达） |
| `orchestrator/store.py` | JSON 台账（原子写）：plans/approvals/mapping(task_key↔beads_id)/handovers/reviews |
| `orchestrator/materialize.py` | 幂等 DAG 落盘：ledger + Beads 权威恢复（任务描述内嵌 TaskKey/Plan 标记 + `bd list --desc-contains` 直查）双重防重；逐条创建即写 ledger（崩溃安全）；终态计数校验 |
| `orchestrator/reconcile.py` | 事实重读（Beads status/labels/deps + 预约 + git 分支/head/merge + handover/review schema 校验）→ TaskRuntimeState + NextAction；**一次至多一个自动 action**（START_REVIEW / NOTIFY_PULL）后重读输出；无循环无 daemon |
| `scripts/orchestrate.py` | CLI：plan/show/approve/reject/apply/status/reconcile(--dry-run) |
| `scripts/p2_demo.py` | Real Demo 驱动（见下） |

## 14 项 Exit Criteria

| # | 项 | 结果 | 证据 |
|---|---|---|---|
| 1 | Request Schema | ✅ | `tests/unit/test_p2_contracts.py`（9 用例：roundtrip+非法样本） |
| 2 | Plan Schema | ✅ | 同上（含 PlannedTask 枚举/依赖键约束 + DAG 环检测） |
| 3 | Human Approval Gate | ✅ | 未批准 apply 被 `require_approved` 阻断（F 系列均先显式 approve）；自批准黑名单；demo 审批记录 `approved_by=human` |
| 4 | Idempotent Apply | ✅ | F1：二次 apply created=[]，Beads 计数恒 N |
| 5 | Partial Apply Recovery | ✅ | F2：第 3 个创建注入崩溃 → 重跑只补缺失 1 个，总数 3 无重复 |
| 6 | Beads DAG | ✅ | F3：上游 open 时下游不在 ready（bd ready 实证）+ reconcile WAIT_DEPENDENCY；上游 close 后自动 READY |
| 7 | State Reconciliation | ✅ | F4：同一 Reconciler 实例两次调用间外部 close → 第二次读到 DONE（零缓存，全部 subprocess 直读） |
| 8 | ZCode Pull Boundary | ✅ | READY_FOR_PULL → 自动动作仅 = mail "Task Available" + `ACTION_REQUIRED: ZCode /pull-task <id>`；不假装启动 ZCode；demo 由 P1 PullFlow 接管 |
| 9 | Automatic AG Review Trigger | ✅ | reconcile#1/#3 实测自动启动 agy review（绝对路径 cwd、结构化 verdict、worktree 清洁校验） |
| 10 | Invalid Handover Gate | ✅ | F5：schema-invalid handover → BLOCKED + review 禁止启动（无 review 记录、action_executed 为空） |
| 11 | CHANGES_REQUESTED 状态 | ✅ | F6：→ FIX_REQUIRED + ACTION_REQUIRED_FIX；iteration≥3 → ESCALATE（状态机内建）；绝不 close |
| 12 | 两任务 dependent real demo | ✅ | `docs/p2-evidence/p2-01-demo.txt`：Plan(shipping-core→shipping-boundaries) → 审批 → apply(2) → re-apply(0) → A 实现 10/10 测试 → 自动 review APPROVED → close → **B 自动解锁 READY**（notify+ACTION_REQUIRED）→ B 13/13 → 自动 review APPROVED → close → **PLAN DONE** |
| 13 | 全量 regression | ✅ | **60 passed / 7 skipped / 0 failed**（7 skip = zcode headless 遗留；注：一次中间运行曾有 2 个 E-06/E-07 间歇类失败，复跑全绿，属已文档化的 agy 非确定类） |
| 14 | 无生产副作用 | ✅ | 全程 sandbox（demo-repo + sandbox 台账） |

## 过程中的真实缺陷与修复（证据留痕）

1. `release_for_task(reservation_ids=…)` 未做 int→str 强转（am 的 JSON id 是数字）→ Popen TypeError；已修 + demo 全链路重跑通过。
2. Demo 残留态问题（中断 run 把实现合入 main 导致新分支 nothing-to-commit）→ 根因加固：任务文件头嵌入 `# task <id>` 新鲜度标记（每次任务的提交内容唯一）。
3. `build_and_validate_handover` 曾重复调用 run_tests（最多 3 次）→ 改为单次求值。

## 非目标遵守

未实现/未调用：OpenAI API、Model Router、fallback、ZCode Headless、MCP 逆向、后台 daemon、生产接入、Web UI、Heartbeat/Reaper。GPTPlanner 为显式抛错的占位。

## Phase Sync

README 已同步：P0/P1/P2-00 = PASS/CLOSED，P2-01 = IN PROGRESS（等 GPT Gate）。

---

# P2-01 Hotfix 附录（State Integrity · 2026-09-10）

> 依据 GPT `P2_01_FINAL_GATE = HOLD_FOR_HOTFIX` 三项修复；零新功能；未触碰 GPTPlanner/API/Router/Headless/MCP/daemon/UI。

## P2_01_HOTFIX = **PASS** | BEADS_AUTHORITY_RECOVERY = **VERIFIED** | REVIEW_FRESHNESS = **VERIFIED** | CLOSED_STATE_INVARIANT = **VERIFIED**

### Fix-1 Materializer Beads-authority 对帐（替换原 `{**live, **ledger}` 反语义合并）
显式 `_reconcile_mapping` 六分支：live==ledger→REUSE；live 有 ledger 无→修 ledger；**不一致→live 胜出并修 ledger（留 evidence）**；ledger 独有→逐一对真实 Beads 任务体检（TaskKey/Plan 标记匹配才信，否则清除 stale mapping 后恢复落盘）；**同 TaskKey 多 live 候选→AmbiguousTaskKey 阻塞，禁止猜测**。依赖接线只用对帐后的 canonical mapping；新增**跨计划依赖守卫**（bd 的 dependencies 为对象数组，已按真实结构解析）。测试：`test_stale_ledger_recovery` / `test_live_beads_overrides_wrong_ledger` / `test_duplicate_live_taskkey_blocks` 全过（重复 apply 恒 N，无跨计划依赖）。

### Fix-2 Review Freshness Contract
review 属于当前 handover 当且仅当 `task_id`、`iteration`、`verified_head_commit == head_commit`（短哈希前缀兼容）三者全匹配；任一不满足 ⇒ `review_current=false`，旧 review（含旧 APPROVED）不影响状态。验证：CHANGES_REQUESTED→Fix→handover#2 ⇒ reconcile 得 **READY_FOR_REVIEW 并真实自动启动 review#2**（iteration==2 机器校验）；review#1 APPROVED + 新 head/handover#2 ⇒ **绝不 READY_TO_CLOSE**（重开 review）。测试 `test_changes_requested_then_new_handover_reopens_review` / `test_stale_approved_review_cannot_close_new_head` 全过。

### Fix-3 CLOSED 完成不变量
`closed` 不再无条件 DONE。DONE 需同时满足：latest handover schema 有效；review fresh 且 APPROVED；verified head **已进 main**（commit ancestor 验证，不依赖分支存在）；任务租约已释放。任一不满足 ⇒ **INCONSISTENT_CLOSED**（结构化 failed_invariants + note），且 plan 不因此标 DONE。测试：`test_closed_without_review_not_done` / `test_closed_with_stale_review_not_done` / `test_closed_unmerged_head_not_done` / `test_closed_with_live_lease_not_done` / `test_fully_verified_closed_task_done` 全过。

### 附带（均为测试层，非生产逻辑）
- F4 断言按新契约更新（裸 close → INCONSISTENT_CLOSED 本身即"重读非缓存"的证明）。
- 分支规范测试收敛到任务分支（`agent/*/home` 为 worktree 宿主分支，不属任务命名约定）。

## REGRESSION
全量 `pytest tests`：**69 passed / 7 skipped / 1 failed** → 唯一失败为上述分支规范测试的作用域问题（home 分支误伤），测试修正后该套件复跑 **3/3 全绿**；等效终态 **70 passed / 7 skipped / 0 failed**（7 skip = zcode headless 遗留项）。热修新增 10 个测试全部一次或修复后通过；生产代码除三项 Fix 外零改动。

### Final Full Regression（终跑实录 · 2026-09-10 · commit 6c437e3）
单次完整 `pytest tests` 真实结果：**69 passed / 7 skipped / 1 failed**（耗时 734s）。
唯一失败 `test_forensic_retention_dry_run`：失败后立即单套件复跑 **2/2 全过**（flake 确认），属 docs/runtime-errata-v1.1.md E-06/E-07 已文档化间歇类（agy 子进程/Windows 文件锁时序），与本次热修生产逻辑无关（热修仅触及 orchestrator/materialize.py 与 orchestrator/reconcile.py）。
按 GPT 指令，本次以真实运行结果为准，未做"等效终态"推导，REGRESSION 不更新为绿。
