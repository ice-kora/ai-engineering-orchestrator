# P2-04 报告 — Run Controller / Resume / Semi-Automatic E2E（2026-09-10）

> 目标：用户只需"start/continue"，系统推进到下一个必须由人处理的边界。
> RunController 是控制层，不是新状态机：每步重读权威事实（Beads/Git/Mail/Store），
> 只调用已验证的 Materializer/Reconciler/ApprovalGate 组件。

## P2_04_STATUS: **PASS**

## RUN_CONTROLLER = **VERIFIED**（bounded pump：max_steps≤10 + wall_time 限制 + 同 fingerprint 重复⇒STALLED）

## HUMAN_BOUNDARY = **VERIFIED**（PLANNED→审批边界；READY_FOR_PULL→ZCode 行动提示；IMPLEMENTING→等待执行者；FIX_REQUIRED→修复提示；READY_TO_CLOSE→人工 merge+close（P2-04 禁自动 merge）；FINAL_FIX_REQUIRED→REPLAN；ESCALATED→人工决策——全部结构化 RunResult.action_required）

## CRASH_RESUME = **VERIFIED**（C1 partial apply→幂等恢复（T10 新进程实测 zero dup）；C2 review history/latest 不一致→`repair_review_latest` 从不可变历史修复、不重调 reviewer（T11）；C3 final gate decision 已存/status 未迁→重放 host transition、不重调 Codex（T12）；C4 notification 幂等（T13））

## REVIEW_PARTIAL_WRITE_RECOVERY = **VERIFIED**

## FINAL_GATE_REPLAY_RECOVERY = **VERIFIED**

## DUPLICATE_SIDE_EFFECT_GUARD = **VERIFIED**（apply 幂等（T2/T10）；review history 不可变（append-only+conflict）；notification 不重复（T13）；final gate 不重调（T12））

## RUN_SNAPSHOT = **VERIFIED**（`run-status` 只读投影：plan_status + 每 task runtime_state/next_action/review_iteration/lease/merge/human_boundary + final_gate verdict + journal_length；零引擎构造零外部调用（T15 spy 断言））

## REAL_PAUSE_RESUME_DEMO = **SUCCESS**（`docs/p2-evidence/p2-04-demo.txt`）

## TARGETED_REGRESSION = **41 passed / 0 failed**（P2-04 T1-T18 18 + P2-03 T1-T15 15 + P2-03 hotfix 8；含 underscore-strip 修复后确认）

## CAN_ENTER_P2_05 = **NO**（按指令停止，等待 GPT P2-05 Gate）

---

## 实现

| 模块 | 内容 |
|---|---|
| `orchestrator/journal.py` | `append_run_event`（append-only `run_events/<plan_id>/<seq>.json`）；`run_events` 读取；`repair_review_latest`（C2 恢复：从不可变历史重建 latest 视图，零 reviewer 重调） |
| `orchestrator/run_controller.py` | `RunController.continue_run(plan_id, max_steps=10, max_wall_seconds=300)` — bounded pump：<br>① pre-loop `_recover()`：C2 review latest 修复 + C3 final-gate decision 重放<br>② 循环：读 status → PLANNED=审批边界 / APPROVED or APPLIED-incomplete=幂等 apply（C1）/ 其他=Reconciler.reconcile_plan(execute=True)（one-step invariant 不变）<br>③ stall 检测：同 fingerprint 连续 2 次⇒STALLED<br>④ human boundary：READY_FOR_PULL/IMPLEMENTING/FIX_REQUIRED/READY_TO_CLOSE/FINAL_FIX_REQUIRED/ESCALATED/INCONSISTENT_CLOSED → 结构化 pause<br>⑤ 终态：DONE / REJECTED → RunResult<br>⑥ `snapshot(plan_id)`：只读 PlanSnapshot 投影 |
| `scripts/orchestrate.py` 扩展 | `start <request.json> [--planner codex\|gpt\|json]`（默认 codex，**绝不自动 approve**）；`continue <plan_id> [--max-steps N]`（bounded pump，RunResult 单行 JSON）；`run-status <plan_id>`（只读快照，零引擎构造）；存量低层命令保留 |
| `RunResult` | 结构化：plan_id / outcome(PAUSED\|DONE\|FAILED\|STALLED) / plan_status / steps_executed / last_action / human_action_required / action_required{type,task_id,task_key,instruction} / recovery_notes |
| 修复（过程中发现） | FinalGateContextBuilder 剥离 review 的 `_` 前缀元数据字段后再 schema 校验（AG review 附带 `_worktree_clean_after_review` 等内部标注，strict additionalProperties:false 会误拒） |

## 18 项测试（`tests/integration/test_p2_04.py`）

T1 PLANNED→审批边界不 apply ✓ | T2 APPROVED→幂等 apply ✓ | T3 READY_FOR_PULL→结构化 pause + ZCode 指令 ✓ | T4 READY_FOR_REVIEW→自动 AG review ✓ | T5 READY_TO_CLOSE→pause 不自动 merge ✓ | T6 全 DONE→final gate→DONE ✓ | T7 FOLLOWUP→FINAL_FIX_REQUIRED+pause ✓ | T8 max_steps 有界停止 ✓ | T9 无进展→STALLED ✓ | T10 新进程 partial apply 恢复零重复 ✓ | T11 review latest 修复不重调 reviewer ✓ | T12 final decision 重放不重调 Codex ✓ | T13 notification 不重复 ✓ | T14 journal 不可变 ✓ | T15 run-status 只读零调用 ✓ | T16 RunResult schema ✓ | T17 低层 CLI 回归 ✓ | T18 P2-03 回归 ✓

## Real Pause/Resume E2E Demo

`scripts/p2_04_demo.py`（全程 subprocess CLI；JsonPlanner 确定性单任务——P2-04 测控制器链路，Codex planner 已在 P2-02 独立验证）：

```
START (json planner) → PLANNED
CONTINUE → PAUSED: HUMAN_APPROVAL_REQUIRED
APPROVE (human)
CONTINUE → PAUSED: ACTION_REQUIRED_PULL (ZCode /pull-task demo-repo-jtv6)
  fixture implements (20/20 tests) + handover
CONTINUE → auto AG review → APPROVED → PAUSED: READY_TO_CLOSE
  fixture merges + closes
CONTINUE (new process) → READY_FOR_FINAL_GATE → REAL Codex Final Gate
  → APPROVED (5 requirements all SATISFIED) → host invariant OK → DONE
=== SIMULATED CRASH after close ===
CONTINUE (new process) → DONE (idempotent; zero side effects)
```

## 非目标遵守

未实现：ZCode headless 逆向、自动实现、默认自动 merge、自动风险豁免、自动审批、daemon/Windows service/后台调度器、Web UI/Board UI、RAG、Model Router、OpenAI API、大规模并发、生产仓库接入、部署。

---

# P2-04 Final Hotfix 附录 — C3 Decision Replay Integrity（2026-09-11）

## P2_04_FINAL_HOTFIX = **PASS**

## 修复
`RunController._recover` 的 C3 路径在 `_apply_final_decision_transition` 之前新增
`_validate_persisted_decision(plan_id, decision)` 全量重验：
1. `decisions.validate_decision(decision, "final_gate")` 完整 schema 契约
2. verdict 独立枚举校验（防篡改绕过 schema）
3. `findings[].task_key` grounding：空或属于当前 Plan（同正常路径规则）
非法 ⇒ **fail-closed**：plan → ESCALATED + `INVALID_PERSISTED_FINAL_GATE_DECISION` 入 recovery_notes；坏 evidence **保留不删**（人工取证）；Codex 零调用。

## T19-T22（`tests/integration/test_p2_04_hotfix.py`，4/4）
T19 valid APPROVED → replay → invariant → **DONE**，Codex calls=0 ✓
T20 `{"verdict":"APPROVED"}` only → schema invalid → **ESCALATED**，evidence 保留，Codex=0 ✓
T21 合 schema 但引用未知 task_key → grounding invalid → **ESCALATED** ✓
T22 valid FOLLOWUP_REQUIRED → **FINAL_FIX_REQUIRED**，Codex=0 ✓
（原 T12 保留不变，全过）

## PERSISTED_DECISION_SCHEMA_REPLAY = **VERIFIED** | PERSISTED_DECISION_GROUNDING = **VERIFIED** | INVALID_REPLAY_FAIL_CLOSED = **VERIFIED** | CODEX_RECALL_ON_REPLAY = **0**
## TARGETED_REGRESSION = **45 passed / 0 failed**
## P2_04_READY_FOR_FINAL_GATE = **YES**
