# ai-engineering-orchestrator (AEO)

多 Agent 工程协作控制面：**Human → GPT Orchestrator → Beads + MCP Agent Mail → ZCode + Antigravity → Git Worktree → Cross Review → GPT Final Verification**。

本项目是独立的协作控制项目。业务仓库（target repository）作为被管理对象接入，**不复制进本项目**。

## 当前状态（P0 进行中）

| 阶段 | 状态 |
|---|---|
| P0 环境与组件验证 | **PASS / CLOSED**（2026-09-10；ZCode Headless = DEFERRED_PRODUCT_GAP，V1 保持 Pull） |
| P1 双 Agent 协作闭环 | **PASS / CLOSED**（2026-09-10；E2E 统一 ID 闭环 + Saga 补偿实证，docs/P1-REPORT.md） |
| P2-00 Runtime Contract Hardening | **PASS / CLOSED**（2026-09-10；task-scoped release + errata 事实覆盖层） |
| P2-01 Orchestrator Kernel | **PASS / CLOSED**（2026-09-10 GPT Final Gate；含 State-Integrity Hotfix 三项；Final Regression 69/7/1，唯一失败为 KNOWN_FLAKE_WAIVER=ACCEPTED，FLAKE-001 登记） |
| P2-02 LLM Planner | **IN PROGRESS → 实质完成**（双后端：Responses API 版 + **Codex CLI 订阅版（用户现实通道，errata E-09）**；共享安全门；单测 18/18；**Real Smoke = SUCCESS（ChatGPT 订阅，零 API key）**——PLANNED + 审批隔离实证；等待 GPT P2-02 Final Gate） |

运行时事实覆盖层：`docs/runtime-errata-v1.1.md`（spec-vs-reality 实证清单，后续实现以此为准）。

设计基线：
- `AI_Engineering_Orchestrator_实施方案_v1.0.docx`（总体方案，16 章）
- `docs/architecture_spec_v1.1.md`（工程可靠性补充规范）
- `docs/P0-SPEC-DELTA-REVIEW.md`（v1.0 → v1.1 差异评审）

## 快速上手

```powershell
# 环境体检（只读，缺组件会如实报告）
pwsh scripts/healthcheck.ps1

# 初始化项目级依赖（默认只检查不安装任何系统组件）
pwsh scripts/bootstrap.ps1

# P0 全链路演示（需要 bd / Agent Mail 就位，缺件时安全退出）
pwsh scripts/demo.ps1
```

项目级 Python 环境：`uv sync`（生成 `.venv`，含 pytest / jsonschema / mcp）。

## 目录结构

```
config/          # 模型/Agent/策略配置（P2 启用）
orchestrator/    # GPT Orchestrator（P2 启用）
adapters/        # Beads/AgentMail/Antigravity/Git 适配层（P1+ 启用）
prompts/         # 角色提示词（P1+ 启用）
workflows/       # 工作流定义（P1+ 启用）
scripts/         # bootstrap / healthcheck / demo（PowerShell 7）
sandbox/         # P0 测试用独立 git 仓库（demo-repo）
tests/           # unit / integration
docs/            # 方案、规范、计划、报告、证据
templates/       # git hook 等模板
```

P0 原则：**只建立当前需要的结构，不为目录完整创建空代码。**

## 硬约束（详见 AGENTS.md 与 docs/P0-IMPLEMENTATION-PLAN.md）

1. 真实运行证据优先于 v1.0/v1.1 规范推测；不一致时记录证据、提交 GPT 裁决，不私自改架构。
2. 系统级安装一律先出 `docs/INSTALLATION-PROPOSAL.md`，人工授权后才执行；组件统一安装到 `D:\Software\ai-orchestrator\`，禁止默认装 C 盘。
3. 不自动操作生产环境；密钥只进 `.env`，永不入库。
