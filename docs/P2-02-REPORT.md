# P2-02 报告 — GPT Planner Integration（2026-09-10）

> 目标：GPTPlanner 从占位实现升级为真实 Planner（OpenAI Responses API / gpt-5.6-sol），
> 安全汇入 P2-01 已验证 pipeline；禁止扩张为通用 Agent。
> 范围遵守：MODEL_ROUTER/FALLBACK/GPT_6_ASTRA/兼容网关/工具调用/RAG/daemon/UI 全部 OUT_OF_SCOPE。

## P2_02_STATUS: **PARTIAL**（代码与安全测试全绿；Real API Smoke 因 key 未配置 BLOCKED_API_KEY_MISSING，待用户填 `.env` 后单指令补跑）

## OPENAI_RESPONSES_API = **PENDING_REAL_CALL**（客户端实现完成；本地验证路径全绿；真实调用未发生）

## GPT_STRUCTURED_PLAN = **VERIFIED（本地层）**——strict json_schema 输出 → 本地 Draft 2020-12 → DAG → policy 四层全绿（T1-T10, 15 用例）；真实模型输出待 key

## PLAN_SAFETY_GATE = **VERIFIED**

## REAL_API_SMOKE = **BLOCKED_API_KEY_MISSING**

## CAN_ENTER_P2_03 = **NO**（等 Real Smoke 补跑 + GPT Gate）

---

## 实现（对照指令逐条）

| 指令项 | 落实 |
|---|---|
| §1 Provider/Model 固定 | `gpt-5.6-sol` + `reasoning.effort=high`；无 Router/无 fallback/无换模型路径（`AEO_OPENAI_*` 仅模型名/effort/超时/次数） |
| §2 Secret Contract | 仅 `OPENAI_API_KEY`（env→项目 .env 顺序加载，不打印不落 evidence）；`.env.example` 五项占位（key 为空）；.env 已 gitignore；openai==3.11.0 经 uv 项目级安装（零系统级） |
| §3 Responses API Contract | `store=false`；无任何 tools（web/file/shell/computer/MCP/function 全不传）——GPT 仅 Planner |
| §4 Structured Output | `text.format=json_schema strict=true` + **API 子集 GPT_PLAN_DRAFT_SCHEMA**（仅语义字段 summary/tasks/risks/assumptions；不用 pattern，全 additionalProperties:false）；本地再过完整 `ExecutionPlan.from_dict`（Draft 2020-12）→ `validate_dag` → policy——API schema 不替代本地契约 |
| §5 Host-Authoritative | request_id/plan_id/target_repo/requires_human_approval 全本地注入（T2 实证投毒 draft 被完全覆盖）；retry 复用同一 plan_id |
| §6 审批红线 | GPTPlanner/CLI 无 approve/materialize 调用路径；成功输出 `STATUS = PLANNED / HUMAN_APPROVAL_REQUIRED = YES`（T9：plan 成功后 Beads delta=0、无 approval 记录） |
| §7 Runtime Policy | `orchestrator/policy.py`：任务 1..8、key 唯一、deps 全存在、无环、路径 repo-relative（拒绝对绝对路径/`..`/盘符/`.git/`/`.beads/`/orchestrator 状态目录）、**executor=zcode + reviewer=antigravity 硬编码**（V1 执行现实）；路径 grounding：已存在文件须在 tracked list，新文件父目录须已知 |
| §8 Repo Grounding | `RepoContext`：HEAD + tracked files（≤600 行）+ README（≤4000 字符）+ 总量 ≤24000 字符硬预算；无 RAG/embedding/vector/检索 agent |
| §9 Prompt Contract | `prompts/gpt-planner-v1.md`（版本化；角色/原则/grounding/能力边界/禁止项；不含 JSON Schema）；evidence 记录 prompt_version/model/effort |
| §10 Retry Policy | `MAX_TOTAL_API_CALLS=2`（env 可配但默认 2）；仅"可修复语义违规"（contract/dag/policy/parse）触发一次 repair retry，prompt 附上轮 violations、要求完整重生成；**代码零偷修**；API 错误同耗总额度；二次失败 → `GPTPlannerError` + PLAN_NOT_SAVED（T6/T7/T8） |
| §11 失败原子性 | save_plan(PLANNED) 是全链最后一步；任何失败零持久化（T3/T7/T8 断言 Store 无痕） |
| §12 Observability | PlanEvidence：request/plan/response id、model、effort、prompt_version、attempt、latency、tokens、validation_result、error_category；**绝不记录 key/头/CoT**；evidence 落 `sandbox/planner-evidence/`（gitignored） |
| §13 JsonPlanner Oracle | 保留并共用同一下游（同 Store/ApprovalGate/Materializer/Reconciler）；无第二套 flow；CLI json 模式回归通过 |
| §14 CLI | `--planner json|gpt`（json 默认保留，`--plan-doc` 载入确定性文档）；gpt 模式支持 `--plan-id` 预分配稳定 id |

## 测试矩阵（T1-T10 → `tests/unit/test_gpt_planner.py`，15 用例全绿）

T1 有效 draft→有效 Plan ✓ | T2 host 字段投毒无效 ✓ | T3 非法 DAG 不入 Store ✓ | T4 六类危险路径全拒 ✓ | T5 角色组合 policy 拦截（含正向对照）✓ | T6 repair retry 恰 2 次调用 + 修复 prompt 带 violations ✓ | T7 双败后无第三次调用、零持久化、审批不可达 ✓ | T8 refusal/不完整 fail-closed ✓ | T9 审批隔离（Beads delta=0）✓ | T10 key 缺失 fail-closed（BLOCKED 类）✓
JsonPlanner 回归：CLI json 模式 + 既有 P2 套件（contracts/orchestrator/F1-F6/hotfix）全绿。

## Real API Smoke（§16）

执行 `scripts/p2_smoke_gpt.py`（需求="为订单模块增加取消订单原因校验…"，sandbox repo）：
**`REAL_API_SMOKE = BLOCKED_API_KEY_MISSING`**（exit 3，fail closed：未发任何请求、未保存任何状态）。
解锁方式：`.env` 填 `OPENAI_API_KEY=<你的key>` → 重跑该脚本（预期：PLANNED + Beads delta 0 + approval None + 脱敏 evidence），随后 `orchestrate show plan-smoke-cancel-reason` 人工查看。

## 回归

全量 `pytest tests` 结果见本次运行（附录于后）。

### 全量回归（2026-09-10 · 含 P2-02 全部新套件）
`pytest tests` 单次完整运行：**85 passed / 7 skipped / 0 failed**（7 skip = ZCode Headless 遗留项；本轮无 FLAKE-001 复发）。

---

# P2-02 附录 — Codex CLI 后端（订阅兼容路线 · 2026-09-10）

## 背景（errata E-09）
GPT 指令的 Provider 前提（OpenAI API + key）与用户现实（ChatGPT 订阅、无 key、不用 API 计费）不符。经用户明确指示，采用官方订阅通道：**Codex CLI**（`@openai/codex` 0.154.0，npm 项目外全局装于 D 盘 nvm；`codex login status` = Logged in using ChatGPT，订阅内授权，零 key 零按量计费）。

## 实现
- `orchestrator/planner_base.py`（新）：**后端无关的共享编排**——host-authoritative 组装、Draft 2020-12 契约、DAG、runtime policy 四层门、重试≤2（repair prompt 携带 violations、零代码偷修）、fail-closed、脱敏 evidence。GPTPlanner 与 CodexCLIPlanner 仅在 `_invoke`（UserRequest→Draft 传输层）不同，**下游完全同一条 P2-01 pipeline**（§13 合规）。
- `orchestrator/codex_planner.py`：`codex exec -s read-only --ephemeral --skip-git-repo-check --ignore-rules -c model_reasoning_effort="high" --output-schema <plan-draft> -o <last> -`（stdin 全量 prompt；`-o` 文件取结构化最终消息；不传 `-C`——grounding 只来自固定 RepoContext，保持与 API 后端同等确定性）。规划探针实测：`--output-schema` 严格约束最终消息形状。
- CLI：`orchestrate plan --planner codex|gpt|json`；smoke：`scripts/p2_smoke_gpt.py [codex|gpt]`（默认 codex）。

## Real API Smoke（Codex/ChatGPT 订阅 · SUCCESS）
需求"为订单模块增加取消订单原因校验…"：**第一次尝试即 valid**（31.8s，effort=high）→ 单任务 `implement-cancel-reason-validation`（paths=src/order.py + tests/test_order.py，新文件落在既有 src/tests 模块边界，policy grounding 通过；单任务 deps=[] 合理）→ PLANNED；**Beads delta=0、approval=None**（审批隔离实测）。evidence 脱敏存 sandbox/planner-evidence（plan-smoke-cancel-reason.json）。
（Responses API 后端：保留、功能完整、单元绿；标记 NOT_AVAILABLE 直至有 key。）

## 测试
新增 `tests/unit/test_codex_planner.py`（命令形状/read-only+ephemeral 断言、rc≠0 fail-closed、最终消息非 JSON fail-closed）：3/3；T 矩阵（后端无关共享层）15/15 保持；合计 18/18。

## P2-02 终态（P2-03 §0 修正后的权威表述）
- `P2_02_STATUS = PASS`；**`P2_02 = CLOSED`**
- **`PLANNER_BACKEND = CODEX_CLI_SUBSCRIPTION`**（用户现实主路线）
- **`CODEX_CLI_REAL_SMOKE = SUCCESS`**
- **`OPENAI_RESPONSES_API = NOT_USED`**（更正早前 "VERIFIED_VIA_CODEX" 的错误表述：Codex CLI 订阅通道与 Responses API 是不同传输层，后者未验证也不再要求；其代码保留为 optional dormant backend）
- `GPT_STRUCTURED_PLAN = VERIFIED`（真实模型输出通过全部本地门）
- `PLAN_SAFETY_GATE = VERIFIED`
