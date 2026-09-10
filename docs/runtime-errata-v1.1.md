# Runtime Errata v1.1 — spec-vs-reality 已实证清单（事实覆盖层）

> 定位：不重写 v1.0/v1.1；本文件记录 **P0/P1 真实运行证实**的规范偏差。后续实现（P2+）遇到与本清单冲突的规范条文时，以本清单为准；如需推翻本清单条目，必须附新的运行证据并经 GPT 裁决。
> 每条均可在 `docs/p0-evidence/`、`docs/p1-evidence/` 或 git 历史中复核。

| # | 主题 | 规范假设（v1.0/v1.1） | 运行时事实 | 影响/决策 |
|---|---|---|---|---|
| E-01 | Beads Task ID 格式 | `^bd-[0-9a-z]{6,10}$`（v1.1 §5 字面） | bd 1.2.2 生成 `<repo-prefix>-<rand4>`（如 `demo-repo-ujn`）；前缀可 `bd rename-prefix` 定制 | 所有 schema 正则与文档改用 `[a-z0-9][a-z0-9-]*-[a-z0-9]{3,8}`（`adapters/handover.py` 已生效）；branch 正则随动 |
| E-02 | Agent Mail 身份名 | "注册 zcode-agent / antigravity-agent"（v1.0 §六.B） | v0.3.35 强制自动生成"形容词+名词"名，描述性名（含 QuickSilver）被拒 | 角色经 program/model 字段表达；身份持久化于 sandbox identity store（`adapters/agent_mail.py`） |
| E-03 | 预约冲突信号 | 冲突 ⇒ 操作失败（隐含非零退出码） | 冲突时 **exit code 仍为 0**；且**多路径部分冲突会产生 partial grants**（granted 与 conflicts 并存，2026-09-10 实证 id=70 案例）——“整体失败”是 workflow 层规则，不是平台行为 | Adapter 一律解析 JSON 判定成功（`ReservationResult.success`）；补偿分支仅释放**本次** granted ids（`release_reservations`），严禁 agent 级全量释放（P2-00 hotfix + `test_partial_grant_compensation.py`） |
| E-04 | force-release | v1.1 §4.2 契约含 force_release_reservation | am CLI v0.3.35 **无** force-release 子命令（MCP-only） | 释放兜底 = TTL 自然到期（P0 实测 60s TTL ~65s 释放）；两阶段自愈以"等过期"为实际路径 |
| E-05 | MCP 接入 | Agent Mail 以 MCP server 方式接入各 Agent | stdio 端自定义握手：python SDK 与裸 JSON-RPC（5 个协议版本串）全部在 initialize 被拒；无兼容旋钮 | `ZCODE_AGENT_MAIL_MCP = FALLBACK_CLI`：全部通信走 am CLI Adapter；MCP 兼容研究挂起（不猜协议/不改服务端） |
| E-06 | agy worktree git 感知 | headless 可在 worktree 内正常工作 | `git rev-parse --show-toplevel` 经 agy 间歇性错误（返回父仓库 / 报非仓库 / 偶尔正确）；antigravity-cli#68 多形态 | Adapter 纪律：绝对路径 cwd + 事后验证文件落点；绝不信任 agy 自报 git root |
| E-07 | agy 工具执行可靠性 | headless 回复 SUCCESS 即视为执行 | 偶发回复 "DONE"（SUCCESS, 1 turn）但**未实际执行**写操作 | 一切写类调用必须事后验证结果（文件存在/git 状态），缺失则重试一次（已固化于测试与 review adapter 的 dirty 校验） |
| E-08 | ZCode Headless | v1.0 假设"无稳定 headless"→ P0 探索升级可能 | CLI 0.16.5 headless 参数面存在但被产品级缺口阻断：`zcode login` 后全部内置 provider `oauth_provider_inactive`/`coding_plan_not_entitled`；provider `baseURL` 无任何配置载体（6 种形态实测）；桌面端为进程内注入 | `ZCODE_HEADLESS = DEFERRED_PRODUCT_GAP`；V1 固定 ZCode = Pull Executor；解锁仅走官方渠道（证据 INVESTIGATION.md） |

## 附：非规范类运行时事实（编码约定）

- bd：`close --reason "…"`（消息是旗标）；`-C` 拒绝无 `.beads` 目录（init 需进程 cwd）；label 多位置参数形式会把冒号+大写 token 误判为 issue ID（用 `update --add-label` 或单 label `label add`）；`--actor` 审计、`--dolt-auto-commit off|on|batch` 多写策略。
- am：clap 严格参数序（选项在位置参数前）；`reserve` 无 `--json` 旗标（默认 JSON）；`release --ids/--paths` 支持任务级精确释放（P2-00 实证：按 id 释放恰一条、他任务存活）；`list` 表含 ID/PATTERN/AGENT/EXPIRES/REASON（task→id 映射源）。
