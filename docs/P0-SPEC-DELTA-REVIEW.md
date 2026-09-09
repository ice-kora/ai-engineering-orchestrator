# P0 Spec Delta Review — v1.0 → v1.1

| 项 | 值 |
|---|---|
| 评审日期 | 2026-09-09 |
| 评审人 | ZCode (Implementation Lead) |
| 输入 | ① `AI_Engineering_Orchestrator_实施方案_v1.0.docx`（16 章，已全文提取）② `docs/architecture_spec_v1.1.md`（10 节，已全文读取）③ 本会话 P0 计划（v1.0 基线版，已经 `P0_PLAN_GATE = PASS` 连同 GPT 5 项补充）④ 环境核查与 Version Matrix（2026-09-09 实测） |
| 范围声明 | 只分析差异与影响，**不重论宏观架构**（总体架构视为已通过 GPT 裁决） |
| 结论 | **DELTA_REVIEW = PASS** — v1.1 是可靠性补充，不推翻 v1.0；P0 计划按 §4 调整后继续执行 |

> 说明：本文件撰写时 `docs/P0-IMPLEMENTATION-PLAN.md` 尚未落盘（此前处于 Plan Mode），故"原 P0 计划"指本会话中已获 GPT Plan Gate 通过的计划文本；该计划与本文调整项合并后落盘为正式版 P0-IMPLEMENTATION-PLAN.md。

---

## 1. v1.1 相对 v1.0 新增了什么

| # | 主题 | v1.1 内容 | v1.0 对应处 |
|---|---|---|---|
| N1 | 幂等指纹契约（§2） | 所有状态变更携带 `operation_id`(UUIDv7) + `idempotency_key`（`<agent>:<action>:<task_id>:<seq>`）；接入层 24h 滑动去重窗口、PENDING→409、SUCCESS→重放缓存 | v1.0 §7.4 工具清单未定义幂等语义 |
| N2 | Claim + Advisory Lease Saga（§3） | 明确文件预约为**劝告性租约**；reserve 出现任何 conflict ⇒ 整体失败：释放已授予部分 → Beads claim 补偿回 `open` → 指数退避（30s 起，上限 5m）→ 禁止动笔；补偿矩阵 S-01..S-04 | v1.0 §9.2 只说"修改前预约"，未定义冲突时的事务语义 |
| N3 | 心跳与两阶段自愈（§4） | 租约 TTL 300s、心跳 60s、续租步进 +300s、失联阈值 180s；失联**不直接回 READY**：先 `SUSPECT` → 确认租约 EXPIRED 或 force-release 成功 → 才回 READY（重试<3）；否则 `BLOCKED_STALE`。定义 `AgentMailAdapter` 四方法契约（reserve/renew/release/force_release） | v1.0 §11 仅提"预约 TTL + stale cleanup + 人工释放"，无两阶段流程 |
| N4 | 强类型 JSON DTO（§5） | `AgentMessageEnvelope` + `TaskHandoverPayload` + `ReviewReportPayload` + `ArbitrationResultPayload`，JSON Schema Draft 2020-12；task_id/branch/commit 均 有 pattern 约束；v1.0 的自然语言交接被禁止 | v1.0 §6.1 步骤 6"提交 Implementation Summary…"无结构化契约 |
| N5 | 审查熔断与仲裁（§6） | review iteration 硬限 1..3；第 3 次 CHANGES_REQUESTED ⇒ 断路器跳闸 → `ESCALATED` → GPT 仲裁（OVERRULE/UPHOLD/HUMAN 三种裁决，独立 ArbitrationResult） | v1.0 §5/§6 只有 FIX_REQUIRED 循环，无上限、无仲裁 |
| N6 | Worktree 资源策略（§7） | 默认 **PER_AGENT reusable worktree**；**取消强制 checkout main**（`git checkout -B agent/<agent>/<task-id> origin/main`）；pnpm 共享 store + Maven 共享 repo，**禁止长期保留 node_modules 作缓存**；FAILED/SUSPECT/BLOCKED 现场**强制保留**（取证包 + archive 分支 + 72h TTL） | v1.0 §9.3 布局为 per-task worktree（`worktrees/zcode-bd-xxx`），无缓存共享与现场保留 |
| N7 | Beads 粗细粒度映射（§8） | Beads status 保持粗粒度（open/in_progress/blocked/closed），工程细粒度状态（CLAIMED/IMPLEMENTING/SELF_TEST/REVIEW/SUSPECT/ESCALATED/VERIFIED…）全部用 label/metadata 表达；**严禁直改 Dolt/SQLite 底库**，补偿只用 CLI | v1.0 §6 状态机未定义与 Beads 原生 status 的映射 |
| N8 | 故障恢复矩阵（§9） | F-01..F-06：reserve 冲突、心跳丢失、审查熔断、merge 冲突、磁盘水位 <15%、schema 校验失败，各自的自动检测/自愈/兜底 | v1.0 §11 风险表为定性描述 |
| N9 | Pre-P0 Gate Checklist（§10） | 进入编码/安装前 5 项核查（spec 吸收、组件接口对齐、共享缓存真实路径、schema 验证器、git hook 模板） | v1.0 无此前置门 |

**术语解构**（认知加速）：
- `Advisory Lease = Advisory(劝告性，依赖协议自觉+Hook 校验) + Lease(有期租约)`
- `Saga Compensation = Saga(长事务编排) + Compensation(反向冲正)`
- `Circuit Breaker = Circuit(回路) + Breaker(跳闸器)`
- `Forensic Retention = Forensic(现场取证) + Retention(留存策略)`

---

## 2. 哪些内容影响当前 P0

| 影响 | 来源 | 对 P0 的具体动作 |
|---|---|---|
| **Worktree 布局改 PER_AGENT** | §7.1 + GPT 补充 | P0 worktree 测试改用 `worktrees/agent-zcode` / `worktrees/agent-antigravity`（reusable），任务分支 `agent/<agent>/<task-id>` 在其内 `checkout -B` 切出；不再按 v1.0 的 per-task worktree 建目录 |
| **不强制 checkout main** | §7.1 | 测试脚本采用 `checkout -B agent/x/bd-y <base>`；沙箱 demo-repo 无 remote，以本地 main 为 base，**记录为沙箱简化**（生产按 origin/main） |
| **现场保留 SOP 纳入测试** | §7.3 | worktree 测试增加"失败现场取证干跑"：`git diff HEAD > failure_artifacts/<id>.patch` + status + log + `archive/failed/<id>-<ts>` 分支（纯 git 能力验证，自动化属 P1） |
| **Pre-P0 Gate Checklist 5 项** | §10 | #1 本文件完成；#3 共享缓存路径本机实测提案；#4 jsonschema Draft 2020-12 实测；#5 pre-commit guard 模板；#2 依赖组件安装授权（先出文档级矩阵） |
| **原子 claim 必须真实并发测试** | GPT 补充 + §3 | `test_beads_lifecycle.py` 设计双进程并发 `bd update --claim`，断言恰好一个成功（待 bd 安装后执行） |
| **预约按 advisory 语义测试** | §3.1 | 冲突测试断言语义改为：conflict 非空 ⇒ 整体失败信号（待 Agent Mail 安装后执行） |
| **组件原生 TTL/renew/release/force-release 优先验证** | GPT 补充 | 纳入 Agent Mail 测试清单；不自研 lease metadata DB |
| **ZCode Headless 并发隔离** | GPT 补充 | P0-ZCODE-HEADLESS 增至 10 项（9 单进程 + 1 并发隔离） |
| **D 盘安装约束** | GPT 补充 | INSTALLATION-PROPOSAL 增加路径矩阵（默认路径/自定义支持/D 盘目标/PATH 范围/C 盘不可避免写入/卸载） |

---

## 3. 原 P0 计划无需修改的部分

1. 总体架构与数据流（Human → GPT → Beads+Mail → ZCode/AG → Worktree → Review → Verify）——v1.1 定位声明明确"不推翻 v1.0"。
2. 角色分工与权限矩阵（v1.0 §5）；实现者不得自审的硬规则。
3. 状态机主干（v1.0 §6）——v1.1 §8.2 是它到 Beads 的**映射方案**，不是替换；`SUSPECT`/`ESCALATED` 为新增异常支线，主干不变。
4. 统一 ID 约定——v1.1 §5 的 pattern（`bd-[0-9a-z]{6,10}`、`agent/[a-z0-9_-]+/bd-[0-9a-z]+`）与 v1.0 §6.2 完全一致。
5. ZCode Pull / Antigravity Push 的模式划分（P0 不改；ZCode headless 证据按 GPT 指示仅记录 + 验证，升级与否留待 P1 裁决）。
6. Beads = Task State Authority、Agent Mail = 通信/锁/审计的职责边界。
7. Demo 选题（calculator + 测试 + review 消息链）。
8. 安全红线（不碰生产、密钥不入库、系统级安装审批门）。

---

## 4. P0 步骤必须调整的部分

| 原步骤（v1.0 基线） | 调整后 | 依据 |
|---|---|---|
| Worktree 测试用 per-task 目录 `worktrees/zcode-test`、`worktrees/ag-test` | PER_AGENT reusable：`worktrees/agent-zcode`、`worktrees/agent-antigravity`；任务分支在其内切出 | §7.1 |
| worktree 内先 checkout main 再拉分支 | 取消；直接 `checkout -B agent/<agent>/<task-id> <base>` | §7.1 |
| 测试后清理 worktree | 增加失败现场取证干跑（patch/status/log/archive 分支），正常路径才清理 | §7.3 |
| 无前置门 | 执行 §10 Pre-P0 Gate Checklist（5 项，本文件即 #1 的产出） | §10 |
| Beads 测试只验证 CRUD/claim | 增加真实并发原子 claim 测试 + §8.3 补偿命令可用性实测 | GPT 补充 + §8.3 |
| Agent Mail 测试以"能预约/能冲突"为限 | 按 GPT 8 项能力清单逐项测（reservation/conflict/TTL/renew/release/force-release/Git Guard/messaging+thread） | GPT 补充 |
| ZCode headless 9 项验证 | 增至 10 项（并发隔离） | GPT 补充 |
| 安装提案按组件简单列出 | 三分类 + D 盘路径矩阵 + 强制 C 盘组件的替代方案与升级裁决 | GPT 补充 |

---

## 5. 哪些属于 P1/P2，不应提前实现

| 项 | 归属 | 理由 |
|---|---|---|
| Saga 编排运行时（S-01..S-04 自动补偿代码） | P1（Adapter 层） | v1.1 只定义契约；P0 仅验证补偿所依赖的原子 CLI 能力存在 |
| 心跳协程 / Reaper / 两阶段自愈状态机实现 | P1 | P0 仅验证 Agent Mail 原生 TTL/renew/force-release 是否足以支撑（GPT 决策：不自研 lease metadata DB） |
| 审查熔断器实现 | P1/P2 | P0 无 review 循环运行时 |
| DTO 接入层与校验拦截（F-06） | P1 | P0 只验证 jsonschema 库对 Draft 2020-12 的支持（Gate #4） |
| 幂等服务端去重窗口（409/重放缓存） | P2（Orchestrator service） | 依赖 Orchestrator 存在 |
| AgentMailAdapter/BeadsAdapter 最终协议实现 | P1 | GPT 冻结已解除，但 P0 边界仍排除完整 Adapter |
| pnpm store / .m2 共享缓存的实际配置 | P0 仅提案（Gate #3），实施随 P1 首个真实 target repo | demo-repo 是纯 Python stdlib，无 node/java 依赖 |
| 清理脚本（72h TTL 回收、磁盘水位 F-05） | P1+ | 需要失败现场真实发生后的运维节奏 |

---

## 6. Spec-vs-Reality 核验矩阵（真实证据优先）

v1.1 若干命令/接口属**规范推测**，官方文档未证实。原则：装组件后实测；不一致 → 记录证据 → 上报 GPT 裁决 → 不私改架构、不绕过官方接口。

| v1.1 假设的能力 | 官方文档证据（2026-09-09 调研） | 实测状态 |
|---|---|---|
| `bd update <id> --claim`（原子认领） | ✅ 官方 README 明示"Atomically claim" | ⏳ 待安装后实测（含并发测试） |
| `bd update <id> --status open`（补偿回滚） | ⚠️ 未在官方 README/安装文档出现（文档仅示例 claim/close） | ⏳ 实测 `bd update --help`；若不支持 → 上报（候选 fallback：`bd reopen` 或其他公开子命令，由 GPT 裁决） |
| `bd label remove/add <id> <labels>`（细粒度状态） | ⚠️ 官方文档未见 `label` 子命令 | ⏳ 实测 `bd --help` 全子命令清单；若不存在 → v1.1 §8.2 的 label 映射需 GPT 重新裁决载体（如 metadata/备注字段） |
| `bd close <id> "msg"` | ✅ 官方 README | ⏳ 待实测 |
| Agent Mail reserve 返回 `{is_granted, lease_id, conflicts, expires_at}`（v1.1 §4.2 契约） | ⚠️ Rust 版工具为 `file_reservation_paths(project, agent, paths, ttl_seconds, exclusive, reason)`，返回结构未在 README 逐字段列出；Python 版类似 | ⏳ 安装后用 MCP client 实测 tool schema 与返回结构，逐字段对齐 Adapter Contract |
| `renew_lease(lease_id, ...)` 签名 | ⚠️ Rust 实际工具为 `renew_file_reservations(...)`（按 agent/project 维度而非 lease_id） | ⏳ 同上；若签名不符 → 记录差异，Adapter 层映射方案交 GPT |
| `force_release_file_reservation` | ✅ Rust 版工具清单中存在 | ⏳ 待实测行为 |
| TTL 默认 3600s / 可自定义 ttl_seconds | ✅ README 明示 | ⏳ 实测含**短 TTL 到期自动失效** |
| Git Guard pre-commit（路径级拦截、`.beads/**` 豁免、AGENT_NAME 识别） | ✅ 两版 README 均有 | ⏳ 实测（含 Windows .cmd/.ps1 shim） |
| pnpm store 硬链接共享 | ✅ pnpm 原生机制；本机 pnpm 11.7.0 已装 | ⏳ P0 仅提案路径（D 盘），P1 实配 |
| jsonschema Draft 2020-12 校验 | Python `jsonschema` 库官方支持 | ✅ 本轮 venv 内用 v1.1 Envelope schema 实测（见 p0-evidence） |

---

## 7. Pre-P0 Gate Checklist 处置（v1.1 §10）

| # | 检查项 | 状态 | 证据/动作 |
|---|---|---|---|
| 1 | 架构与可靠性契约吸收定稿 | ✅ | 本文件 + GPT Plan Gate PASS（5 项补充已吸收） |
| 2 | 组件接口能力与 Adapter Contract 对齐 | ⏳ 阻塞于安装授权 | 本文件 §6 文档级矩阵先行；安装后 MCP tool schema 逐项实测 |
| 3 | 共享缓存物理路径 | ✅（提案） | pnpm 11.7.0 实测在机；`~/.m2` 已存在于 C 盘（用户既有，不迁移不动）；提案 `D:\Software\ai-orchestrator\shared-cache\`（见 INSTALLATION-PROPOSAL §shared-cache）；D 盘 184G 可用 |
| 4 | Schema 验证器 Draft 2020-12 | ✅ | 项目 venv `jsonschema`，用 v1.1 §5.1 Envelope 实测校验通过/失败用例（p0-evidence/gate4-jsonschema.md） |
| 5 | Git Hook 模板 | ✅ | `templates/hooks/pre-commit.agent-guard`（模板 + Windows shim 说明，未全局安装） |

---

## 8. 结论

- v1.1 与 v1.0 **无架构级冲突**，全部为可靠性增强；GPT Final Gate Decisions 与 v1.1 条文一致。
- P0 计划按 §4 调整（PER_AGENT worktree、Gate Checklist、并发 claim 测试、10 项 ZCode headless、D 盘安装矩阵）。
- §6 核验矩阵中 3 项规范推测（`--status open`、`label` 子命令、lease 返回结构）是 P0 组件实测的重点对齐对象，结论将进 P0-REPORT 并提交 GPT。
- **DELTA_REVIEW = PASS，按调整后计划继续 P0。**
