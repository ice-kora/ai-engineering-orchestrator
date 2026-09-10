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
