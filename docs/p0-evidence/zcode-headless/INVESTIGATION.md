# P0-ZCODE-HEADLESS 调查报告（DISCOVERED → BLOCKED）

日期：2026-09-09 · 对象：ZCode CLI v0.16.5（`D:\Software\zcode\install\ZCode\resources\glm\zcode.cjs`，不在 PATH，经 node 调用）

## 结论

**ZCODE_HEADLESS = DISCOVERED（未达 VERIFIED）**——CLI 及 headless 参数面真实存在，但非交互执行被未文档化的模型提供方配置要求阻断。9+1 验证项未能执行（测试套件以 BLOCKED_MODEL_CONFIG 跳过，解锁后自动重跑）。

## 事实链（全部有原始证据）

1. **发现**：`zcode.cjs --help` 列出完整 headless 面：`-p/--print`、`--prompt`、`--cwd`、`--mode build/edit/plan/yolo`、`--max-turns`、`--allowed-tools`、`--disallowed-tools`、`--resume`、`--json`、`--settings`、`--surface`（见 run1 附带 help 全文）。与 v1.0 方案"ZCode 无稳定 headless"前提不符 → 已记录为重大新事实。
2. **参数解析器实测矩阵**（flag-matrix-summary.txt）：
   - ACCEPTED：`--prompt <text>`、`-p`（位置参数）、`--json`、`--cwd <path>`、`--mode yolo`、`--disallowed-tools <list>`
   - REJECTED（报 `Unknown option`，尽管 --help 有列出）：`--max-turns <n>`、`--allowed-tools <list>`、`--settings <path>`
   - **判定：0.16.5 存在 help/解析器漂移**——帮助文本描述的能力集大于实际可用集。
3. **运行时阻断**：解析通过后立即退出（exit 1），stderr：
   `Error: Model config is missing. Create C:\Users\<user>\.zcode\cli\config.json with an explicit model provider before running ZCode.`
   （verbose 栈：requireRuntimeModelConfig → resolveAppRuntimeConfig → createZCodeApp → runPrompt，见 probe-blocked.txt）
4. **配置 schema 追查**（从安装包内键枚举与桌面端随附文件目录提取，非逆向网络协议）：
   - config.json 支持 `model.main` / `model.lite` / `model.available`（modelRef 形态 `{providerId, modelId, variant?}`）与 `modelProviderOptions`
   - 内置模型目录：`resources/model-providers/models_catalog_*.json` 含 `zai`（GLM-5.3 等 23 个模型，anthropic 兼容端点 api.z.ai）、`bigmodel`、`zai-coding-plan` 等 10 个 provider
5. **试验（已完全还原）**：向 config.json 增补 `"model": {"main": {"providerId": "zai", "modelId": "glm-5.3"}}` → 仍报同样错误 → 说明仅 model ref 不足，还需 provider 凭证接线（OAuth 共享登录态或 modelProviderOptions 凭证）。**已从备份完整恢复原配置**（恢复后 keys = plugins/mcp，备份文件已删除，零残留）。
6. **官方文档**：zcode.z.ai 未文档化 CLI headless 与 config schema（web 调研确认）。

## 解锁候选（需人工/GPT 裁决，P0 不再盲试）

| 候选 | 说明 | 成本 |
|---|---|---|
| A. `zcode login`（推荐首选） | CLI 自带的 Z.AI OAuth 登录（支持 `--no-browser` 打印 URL）；登录态"shared"（与桌面端共享语义），可能同时落盘 provider 接线 | 用户点一次浏览器授权 |
| B. `modelProviderOptions` 凭证 | config.json 中为 provider 配 API key（形态未文档化，需官方确认） | 需要 API key + schema |
| C. 等 ZCode 官方文档化 headless | 最稳妥但不可控 | 时间 |

## 对架构的影响

- **P0 不改架构**：ZCode 保持 v1.0/v1.1 的 Pull Executor 定位。
- 本调查构成 GPT 裁决"P1 是否升级 ZCode 为 Push/Headless Executor"的输入之一，但**在解锁并跑通 9+1 验证之前，该提案不成立**。
- 附加发现：`--disallowed-tools` 可用而 `--allowed-tools` 不可用——工具限制验证项（check 7）解锁后应改用 denylist 方式。

## 证据文件

- `run1-minimal-cwd-json.txt`（首次运行 + help 全文 + Unknown option --max-turns）
- `probe-blocked.txt`（Model config is missing 原始输出）
- `flag-matrix-summary.txt`（9 个 flag 的接受/拒绝矩阵）
- `version.txt`、`exit-bad-args.txt`
