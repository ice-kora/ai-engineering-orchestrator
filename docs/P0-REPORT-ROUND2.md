# P0 报告 · 第二轮（2026-09-09 · 组件实测轮）

> 执行依据：GPT Round-1 Gate（`P0_ROUND1_GATE = PASS_PARTIAL`）+ 5 项安装授权（A1/A2/A3/A4 批准、A5 暂缓）+ Round-2 验证清单。
> 全程遵循：真实证据优先；规范与实现不一致时记录上报、不私改架构。测试基线：**36 passed / 7 skipped**（skip 全部为 ZCode headless 模型运行项，等待用户 `zcode login`）。

## P0 STATUS: **PARTIAL**（唯一残留 = ZCode Headless 待人工登录解锁）

## Actual Version Matrix

| Component | Version | 安装位置 | SHA256 | 数据落盘 |
|---|---|---|---|---|
| Beads bd | **1.2.2**（官方 release zip，原生 Go bd.exe） | D:\Software\ai-orchestrator\beads\ | ✅ 校验通过 | 各仓库 `.beads/`（D 盘 ✓） |
| Agent Mail (Rust) | **0.3.35**（官方 msvc zip：am.exe + mcp-agent-mail.exe，45 tools/8 resources） | D:\Software\ai-orchestrator\agent-mail\ | ✅ 校验通过 | 重定向至 D 盘 storage/config（**首启实测零 C 盘写入** ✓） |
| Antigravity agy | **1.1.28**（官方 install.ps1，C 盘例外已批） | %LOCALAPPDATA%\agy\bin | 安装器校验 | 凭据=Credential Manager（与桌面 IDE 共享，**无需重新 OAuth**） |
| ZCode CLI | 0.16.5（本机既有） | D:\Software\zcode（不在 PATH） | — | headless 被模型配置阻断 |

## Beads Result

**VERIFIED（4/4 + 显式探针）**：version/init(嵌入式 dolt，自带引擎)/create(`bd q`)/`dep add`（子任务正确出/不出 ready 队列）/ready/`update --claim`/`close --reason`（真实语法：reason 是旗标非位置参数）。任务 ID 真实格式 `<repo前缀>-<4位随机>`（如 `demo-repo-ujn`；`bd rename-prefix` 可定制）——**与 v1.1 `^bd-…$` 正则不符，已记录待 GPT 裁决**。`bd -C` 对无 `.beads` 的目录拒绝（init 需以进程 cwd 运行）。server mode：嵌入式模式为默认且自带 dolt 引擎（`bd dolt start` 仅外置模式支持，`bd ping` 25ms ✓）；外置 dolt sql-server 未安装（未授权），记录为限制。附加发现：`--actor`（审计）、`--dolt-auto-commit off|on|batch`（多写策略）。

## Atomic Claim Result

**VERIFIED**：双进程并发 `bd update <id> --claim`（不同 BEADS_ACTOR）→ **恰好一个成功**，败方收到明确错误。旗标语义："sets assignee+in_progress；同 actor 幂等，异 actor 拒绝"。

## Compensation Result

**VERIFIED（v1.1 §8.3 全部成立，无需 xfail）**：`bd update <id> --status open` ✓；`bd label remove/add` ✓（可靠通道：`update --add-label`（可重复）或单 label 的 `label add`；多 label 位置参数形式会把含冒号大写 token 误判为 issue ID——已列为禁用形式）。完整补偿链实测：claim → label(stage:IMPLEMENTING,owner:agent) → 回滚 status open + label 清理 + `stage:READY backoff_until:<ts>` → ready 队列可见。

## Agent Mail Result

**VERIFIED（10/10）**：身份注册/消息/线程（`--thread-id` + inbox 校验）/预约（granted+expires_ts+reason）/renew（--extend-seconds）/release/健康/项目列举/能力矩阵归档。**身份模型 spec-vs-reality**：agent 名必须是随机"形容词+名词"（`SapphireOriole` 型），描述性名（`agent-zcode`/甚至 `QuickSilver`）被拒 → 角色信息只能放 program/model 字段；我们采用"自动生成 + sandbox 记忆文件复用"。**MCP era 不匹配**：python `mcp` 2.2.0 客户端在 initialize 即被服务端拒绝（"negotiated MCP protocol era"）→ P0 验证与未来 Adapter 走 `am` CLI 通道；ZCode 端 MCP 接入（A5）需先验证其客户端 era 兼容。数据落盘：**首启实测全部在 D 盘**（日志明示路径），C 盘默认位复查零写入 ✓（一个非致命 Windows 限制：proactive backup 的原子 no-replace move 不可用，仅 WARN）。

## Lease Conflict Result

**VERIFIED（v1.1 §3.1 语义实证）**：A 持 `src/service/**`（exclusive），B 申请 `src/service/TestService.java` → 返回 `{granted:[], conflicts:[{path, holders:[{agent:A, path_pattern, exclusive, expires_ts}]}]}` + 人读提示 "conflicting reservations were not created"——**整体失败、零部分授予、B 无残留、A 不受影响**。**关键实证：冲突时 exit code 仍为 0** → Adapter 必须解析 JSON 而非信任退出码（已写入测试断言与矩阵）。

## Lease Renew Result

**VERIFIED**：`file_reservations renew --extend-seconds 120` 成功；**TTL 自然过期实测**：60s TTL 的预约在 ~65s 后释放，他方可成功获得同路径（v1.1 §4.3 两阶段自愈的兜底路径成立）。TTL 夹取范围 60..31536000s。force-release：**CLI 未提供**（MCP-only）→ TTL 过期为实际可用兜底，记录待 GPT 知悉。

## ZCode Headless Result

**DISCOVERED（BLOCKED — 产品级缺口，终局 2026-09-10）**。用户已完成 `zcode login`，但全部内置 provider 授权状态为 `oauth_provider_inactive` / `coding_plan_not_entitled`（coding-plan-cache 实证）；config 形态矩阵 6 种全部实测（字符串 `model.main` 可通过模型检查，但 provider baseURL 无任何配置载体；catalog 在独立运行时不加载；桌面端为进程内注入、无可镜像接口）。详见 `docs/p0-evidence/zcode-headless/INVESTIGATION.md` 终局补充。解锁三选项：① 官方支持/文档化（推荐）② 桌面设置探测 ③ 接受 Pull 模式现状（P0 Exit 不依赖此项，v1.0 基线本为 Pull）。已完成的解析级实证：flag 矩阵（接受 `--prompt/-p/--json/--cwd/--mode/--disallowed-tools`；拒绝 `--max-turns/--allowed-tools/--settings`——help/解析器漂移）；无效参数非零退出 ✓。`Model config is missing` 阻断持续（`model.main` 配置试验无效已还原）。解锁路径：终端执行 `node D:\Software\zcode\install\ZCode\resources\glm\zcode.cjs login` 完成浏览器授权后重跑 `pytest tests/integration/test_zcode_headless.py`。

## ZCode Concurrent Isolation Result

**NOT RUNNABLE**（依赖 headless 解锁；测试代码就绪：双进程不同 cwd/worktree、标记隔离、session id 比对、kill 一方不影响另一方）。

## Antigravity Headless Result

**VERIFIED（9/9）**：version 1.1.28；headless JSON 信封（status/response/usage/conversation_id）✓；stdout/stderr 分离 ✓；exit code 可靠（正常 0/坏参数非 0）✓；`--cwd` 生效（标记文件落位）✓；timeout 安全终止 ✓；`--json-schema` 结构化输出（17+25 → `structured_output.sum=42`）✓；stream-json NDJSON 事件流（init/step_update/result）✓；stdin：`-p -` 不支持，无 `-p` 管道 stdin 支持（实证归档）✓（作为发现记录）。凭据与桌面 IDE 共享，未执行新 OAuth。

## Worktree Compatibility Result

**VERIFIED（写入安全）+ #68 Bug 确认（git 感知不可靠）**：在 worktree 内运行 agy，文件创建**正确落在 worktree、零泄漏到父检出**（写入探针实证）；但 `git rev-parse --show-toplevel` 两次运行分别返回**父仓库路径**与**"not a git repository"**——google-antigravity/antigravity-cli#68 以两种变体确认。**P3 Adapter 设计约束：不得依赖 agy 的 git 感知，必须传绝对路径 + 事后校验落点。**

## JSON Schema Result

**VERIFIED**：Draft 2020-12 验证器 8/8（v1.1 Envelope 正反用例）；agy `--json-schema` 结构化输出实测合规。

## Full Demo Result

**SUCCESS（exit 0，`docs/p0-evidence/demo-round2-run.txt`）**：`demo-repo-ujn` 单一任务 ID 贯穿五处——Beads 任务（含依赖的 review 子任务）→ Mail thread（Start/Review: APPROVED 两消息）→ 预约 reason → 分支 `agent/zcode/demo-repo-ujn` → commit `[demo-repo-ujn]`；链路含原子 claim、label、worktree 内 unittest（1 test OK）、merge（ort 策略）、双任务 close、预约释放。自测失败分支的状态机出口（FIX_REQUIRED、现场保留）已在脚本中实现。

## Remaining Blockers

| # | Blocker | 解法 |
|---|---|---|
| R1 | ZCode headless 产品级缺口（login 已做、授权未激活、配置无载体） | 三选项见 ZCode Headless Result；**P0 Exit 不依赖此项** |
| R2 | SECURITY-001 凭证轮换（用户执行） | 见 docs/security-findings.md（本地细节在 local/security/） |
| R3 | SECURITY-002 历史清理（O1 filter-repo / O2 重建 / O3 接受残留+轮换） | 等人工裁决；本轮已做到"增量零新增"（写入时脱敏根修） |
| R4 | A5（ZCode MCP 接入 agent-mail） | GPT 明示暂缓；且需先验证 ZCode MCP 客户端 era 兼容 |

## Evidence

- 测试：`pytest tests` → **36 passed / 7 skipped**（本轮会话；7 skip = ZCode login 待人工）
- Beads：`docs/p0-evidence/beads/{server-mode-probe,claim-label-compensation}.txt`
- Agent Mail：`docs/p0-evidence/agent-mail/tool-matrix.md`（live 记录）
- agy：`docs/p0-evidence/agy/*.txt`（9 项 + worktree 判定 + stream-json 分析）
- Demo：`docs/p0-evidence/demo-round2-run.txt`；复核命令在文末（git log / bd ready / am active）
- 安全校验：提交前泄露扫描 CLEAN（用户名/令牌/内网串零命中）；archive() 写入时脱敏根修
- Git：commits `f2473a5`（R2 实测与 demo）→ `8457d45`（脱敏根修），均已推送 GitHub
- 环境不变量：uv 镜像 403 对策、`.beads`/storage 全 D 盘、pnpm 11.7.0 在机（共享缓存 P1 提案不变）

## Can Enter P1

**NO**（固定，等待 GPT Final P0 Gate；技术上仅差 R1 一项人工登录 + 其后 9+1 验证）
