# P1 报告 · 双 Agent 基础协作闭环（2026-09-10）

> 范围：GPT P1 指令（人工触发闭环；无 Orchestrator/自动调度/Model Router/自动 merge 生产/心跳后台）。
> 全部结论基于真实运行；证据在 `docs/p1-evidence/`、`docs/p0-evidence/`（组件级）与 git 历史。

## P1 STATUS: **PASS**

## ZCODE_AGENT_MAIL_MCP: **FALLBACK_CLI**

依据（P1-00 探针，不猜协议、不改服务端）：
1. python MCP SDK 2.2.0：initialize 即被拒（P0 发现）。
2. 裸 JSON-RPC 探针：5 个 protocolVersion（2025-03-26 → 2026-06-18）**全部**被同一错误拒绝（"negotiated MCP protocol era"）——该 stdio 端使用自定义握手，非标准版本协商问题。
3. `am flags`/`am config` 无任何协议兼容旋钮。
4. `am` CLI 为厂商认可通道（P0 10/10 实测）。→ P1 通信全部走 `AgentMailAdapter`（am CLI 封装），MCP 接入保留为后续升级项（若要验证 ZCode 自身客户端，可在新会话配置后 `/mcp list`——模板已备未激活）。

## P1-01/02：Pull Task + Claim/Lease Saga — PASS

- 入口：`scripts/pull_task.py`（`.zcode/commands/pull-task.md` 提供 slash 命令文档）；`adapters/pull_flow.py` 实现。
- 链路：`bd ready --json` → 选任务（优先级）→ **原子 claim**（`--claim --actor`）→ 从任务描述 `Paths:` 约定解析目标路径 → `am reserve`（**结构化 JSON 判定**：`granted` 非空且 `conflicts` 空；绝不依赖 exit code）→ 成功则 `checkout -B agent/zcode/<task-id>` 进入 PER_AGENT worktree 并输出执行上下文。
- **Saga 补偿实测**（`tests/integration/test_p1_saga.py` PASS）：预置冲突租约后 pull ⇒ 释放本次部分授予 + `bd update --status open` + label 清理 + `backoff_until:<ts>` 标签 + 退出码 2 + **CODING FORBIDDEN**；Beads 状态回到 open、无残留 owner/stage 标签、对方租约不受影响。

## P1-03：Handover JSON — PASS

`adapters/handover.py`：TaskHandoverPayload / ReviewReportPayload（Draft 2020-12）构建+校验；真实两轮 handover 均一次通过校验（含 task_id/iteration/git_context(分支+base/head)/affected_files/test_evidence/deliverable_summary/known_risks）。差异记录：task_id 正则采用 bd 真实格式 `<repo-prefix>-<rand4>`（v1.1 字面 `^bd-…$` 的偏差已随 P0 报告上报）。

## P1-04：Antigravity 独立 Review — PASS

`adapters/antigravity.py` + `review_flow.py`：评审在 agent-antigravity 专属 worktree **detached head**（只读上下文）执行；绝对路径传入（#68 对策）；`--json-schema` 结构化输出（task_id/head/verdict 被 schema 钉死）；**事后验证**评审未污染 worktree（两轮 `worktree_clean=True`）。评审提示词内嵌验收标准 + handover JSON + base..head diff。

## P1-05：真实闭环 — PASS（`demo-repo-4j2`）

| 步骤 | 结果 |
|---|---|
| Task Created / Ready | `bd create`（Paths+Acceptance 写入描述）→ ready 队列 ✓ |
| ZCode Pull / Atomic Claim | PullFlow → claim OK（evidence 带 actor）✓ |
| Reservation | granted=2（src/shopping_cart.py, tests/…）conflicts=0，reason=task_id ✓ |
| Worktree | `agent/zcode/demo-repo-4j2`（base=main@2de0b243）✓ |
| Implementation | v1（蓄意缺失折扣）自测 4/4 ✓ |
| Handover JSON | iteration=1 schema-valid，mail thread 发送 ✓ |
| **Review #1** | **CHANGES_REQUESTED，2 findings**（评审员真实抓住缺失折扣）✓ |
| Fix | v2 实现折扣 + 3 个测试（含 100 边界/低于100），自测 6/6 ✓ |
| **Re-review** | **APPROVED，0 findings**（iteration=2）✓ |
| Release / Close | 预约释放、merge --no-ff、`bd close` ✓ |
| 统一 ID | `demo-repo-4j2` 贯穿 Beads/Mail thread/Reservation reason/Branch/2×Commit/2×Handover/2×ReviewReport ✓ |

全程证据：`docs/p1-evidence/p1-05-loop.txt` + `mail-inbox-reviewer.json`（评审员收件箱原始 JSON）+ demo-repo git log。

## 测试与回归

`pytest tests` → **43 passed / 7 skipped / 0 failed**（7 skip = P0 遗留的 zcode headless 产品缺口项，P1 范围外）。
新增：`tests/unit/test_handover_schema.py`（6 用例）、`tests/integration/test_p1_saga.py`（真实补偿）。
回归修正 1 处：agy worktree 写入探针改为"泄漏=硬失败、缺失=重试一次"（实证 agy 偶发回复 DONE 但不执行工具——这本身是 Adapter 必须"事后验证落点"的第 4 个证据，已写入测试注释与报告）。

## P1 Exit Criteria（10/10）

1. Pull Task 稳定执行 ✓ 2. Saga 通过 ✓ 3. conflict 补偿实测 ✓ 4. Handover Schema 通过 ✓ 5. AG Review 通过 ✓ 6. CHANGES_REQUESTED→Fix→Approved 通过 ✓ 7. 预约正确释放 ✓ 8. 任务正确 Close ✓ 9. Task ID 全链统一 ✓ 10. 无生产副作用（全程 sandbox）✓

## 已知限制与遗留

- agy 间歇性行为（#68 三形态 + 偶发工具不执行）⇒ Adapter 纪律：绝对路径 + 事后验证 + 重试一次（已固化在代码与测试）。
- Agent Mail 身份名为自动生成形容词+名词（角色经 program/model 表达；identity store 持久化）。
- 预约 TTL=900s 固定；续租（renew）P1 未纳入流程（P0 已验证能力）。
- ZCode↔Agent Mail 走 am CLI（FALLBACK_CLI）；升级 MCP 需 ZCode 端兼容性验证。

## CAN_ENTER_P2: **NO**（按指令：完成即停，等待 GPT P2 Gate）
