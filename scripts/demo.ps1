<#
.SYNOPSIS
  P0 全链路演示：Task → Claim → File Reservation → Worktree → Code → Test →
  Review Message → Release Reservation → Close Task（统一 bd-xxx ID）。
.DESCRIPTION
  前置：bd / Agent Mail 已按 docs/INSTALLATION-PROPOSAL.md 安装。
  任一组件缺失时打印缺失清单并安全退出（退出码 2），不做部分演示。
#>
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$demo = "$root\sandbox\demo-repo"

Write-Host "=== AEO P0 demo ===" -ForegroundColor Cyan

# --- 0. 前置检查 ---
$missing = @()
foreach ($t in @('bd')) { if (-not (Get-Command $t -ErrorAction SilentlyContinue)) { $missing += $t } }
if (-not (Get-Command 'mcp-agent-mail' -ErrorAction SilentlyContinue -and `
          (Get-Command 'am' -ErrorAction SilentlyContinue))) { $missing += 'agent-mail' }
if ($missing.Count -gt 0) {
    Write-Host "缺失组件：$($missing -join ', ')" -ForegroundColor Yellow
    Write-Host "按 docs/INSTALLATION-PROPOSAL.md 安装授权后重试。流程设计见 docs/P0-IMPLEMENTATION-PLAN.md P0-12。"
    exit 2
}
if (-not (Test-Path "$demo\.git")) {
    Write-Host "初始化 demo-repo（首次）..."
    $env:UV_DEFAULT_INDEX = 'https://pypi.org/simple'
    uv run pytest tests/integration/test_git_worktree.py -q
}

# --- 1. Beads：创建任务 + 依赖 ---
Write-Host "`n[1/7] Beads: create task (demo) in $demo" -ForegroundColor Cyan
# NOTE: bd 旗标（-C / init --skip-agents / dep add 方向）为文档推断，P0-09 安装后核验修正
bd -C $demo init --skip-agents 2>$null   # 幂等
$taskOut = bd -C $demo create "P0 demo: implement calculator add(a,b) + tests" -p 1
Write-Host $taskOut
$taskId = ($taskOut | Select-String -Pattern 'bd-[0-9a-z]{4,10}').Matches[0].Value
$reviewOut = bd -C $demo create "P0 demo: cross-review [${taskId}]" -p 1
$reviewId = ($reviewOut | Select-String -Pattern 'bd-[0-9a-z]{4,10}').Matches[0].Value
bd -C $demo dep add $reviewId $taskId   # review blocked-by impl
Write-Host "impl=$taskId review=$reviewId (review depends on impl)"

# --- 2. Ready → 原子 Claim ---
Write-Host "`n[2/7] Beads: ready queue -> atomic claim" -ForegroundColor Cyan
bd -C $demo ready
bd -C $demo update $taskId --claim
if ($LASTEXITCODE -ne 0) { throw "claim failed (atomicity guard fired?)" }

# --- 3. Agent Mail：注册 + 文件预约（reason=taskId）---
Write-Host "`n[3/7] AgentMail: register agents + reserve files (reason=$taskId)" -ForegroundColor Cyan
Write-Host "（由 Python MCP client 执行：register zcode-agent/antigravity-agent, thread_id=$taskId, reserve src/calculator.py tests/test_calculator.py）"
$env:UV_DEFAULT_INDEX = 'https://pypi.org/simple'
uv run python tests/integration/test_task_id_unification.py --demo-claim $taskId

# --- 4. Worktree：agent/zcode/<task-id> ---
Write-Host "`n[4/7] Git: worktree branch agent/zcode/$taskId" -ForegroundColor Cyan
git -C $demo worktree add worktrees/agent-zcode -b agent/zcode/home main 2>$null
git -C (Join-Path $demo 'worktrees\agent-zcode') checkout -B "agent/zcode/$taskId" main

# --- 5/6. 实现计算器 + 自测（worktree 内）---
Write-Host "`n[5/7] Code: calculator + unittest in worktree" -ForegroundColor Cyan
$wt = Join-Path $demo "worktrees\agent-zcode"
Set-Content -Path "$wt\src\calculator.py" -Value "def add(a, b):`n    return a + b`n" -Encoding utf8
Set-Content -Path "$wt\tests\test_calculator.py" -Value "import unittest`nfrom src.calculator import add`n`nclass T(unittest.TestCase):`n    def test_add(self):`n        self.assertEqual(add(1, 2), 3)`n" -Encoding utf8
python -m unittest discover -s tests -t . -v
if ($LASTEXITCODE -ne 0) { throw "self-test FAILED -> FIX_REQUIRED (per state machine)" }
git -C $wt add -A; git -C $wt commit -m "[$taskId] implement calculator add + tests"

# --- 7. Review 消息 → 释放预约 → 关闭任务 ---
Write-Host "`n[6/7][7/7] Review msg -> release reservation -> close (thread=$taskId)" -ForegroundColor Cyan
$env:UV_DEFAULT_INDEX = 'https://pypi.org/simple'
uv run python tests/integration/test_task_id_unification.py --demo-close $taskId
git -C $demo merge --no-ff "agent/zcode/$taskId" -m "merge [$taskId]"
bd -C $demo close $taskId "demo complete: tests pass, review ack, merged"
Write-Host "`n=== demo 完成：统一 ID 贯穿 Beads/Mail/Reservation/Branch/Commit ===" -ForegroundColor Green
