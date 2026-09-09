<#
.SYNOPSIS
  P0 全链路演示（Round-2 真实语法版）：Beads Task → Claim → Agent Mail 预约 →
  Worktree → Coding → Test → Review Message → Release → Merge → Close，
  统一任务 ID 贯穿 Beads thread / reservation reason / branch / commit。
.DESCRIPTION
  前置：bd v1.2.2（D:\Software\ai-orchestrator\beads）、am v0.3.35 已安装；
        demo-repo 已初始化（pytest tests/integration/test_git_worktree.py）。
  退出码：0 成功；2 组件缺失；3 步骤失败（状态留在现场，按 v1.1 §7.3 不强清）。
#>
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$demo = "$root\sandbox\demo-repo"
$env:UV_DEFAULT_INDEX = 'https://pypi.org/simple'

$bd = 'D:\Software\ai-orchestrator\beads\bd.exe'
$am = 'D:\Software\ai-orchestrator\agent-mail\am.exe'
if (-not (Test-Path $bd)) { $bd = (Get-Command bd -ErrorAction SilentlyContinue).Source }
if (-not (Test-Path $am)) { $am = (Get-Command am -ErrorAction SilentlyContinue).Source }
if (-not $bd -or -not $am) {
    Write-Host "缺失组件：bd=$bd am=$am" -ForegroundColor Yellow
    exit 2
}

# Agent Mail 存储重定向（与测试同一 sandbox 数据面）
$env:STORAGE_ROOT     = "$root\sandbox\agent-mail-test-data\storage"
$env:DATABASE_URL     = "sqlite+aiosqlite:///$($root -replace '\\','/')/sandbox/agent-mail-test-data/storage/storage.sqlite3"
$env:XDG_CONFIG_HOME  = "$root\sandbox\agent-mail-test-data\config"
$PJ = "$($demo -replace '\\','/')"

function BD([string[]]$Argv, [string]$Cwd = $demo) {
    Push-Location $Cwd
    try { & $bd @Argv 2>&1 | ForEach-Object { "$_" } }
    finally { Pop-Location }
}

Write-Host "=== AEO P0 demo (round 2) ===" -ForegroundColor Cyan

# --- 1. Beads：初始化 + 双任务 + 依赖 ---
Write-Host "`n[1/7] Beads: init + create + dep" -ForegroundColor Cyan
BD @('init', '--non-interactive') | Out-Null
$implOut  = BD @('q', 'P0 demo: implement calculator add(a,b) + tests', '-p', '1')
$reviewOut = BD @('q', "P0 demo: cross-review", '-p', '1')
$task = ($implOut  | Select-String -Pattern '[a-z0-9-]+-[a-z0-9]{3,8}' -AllMatches).Matches[0].Value
$review = ($reviewOut | Select-String -Pattern '[a-z0-9-]+-[a-z0-9]{3,8}' -AllMatches).Matches[0].Value
BD @('dep', 'add', $review, $task) | Out-Null
Write-Host "impl=$task review=$review (review blocked-by impl)"

# --- 2. Ready → 原子 Claim（actor=QuickA 角色由 --actor 表达）---
Write-Host "`n[2/7] Beads: ready -> atomic claim" -ForegroundColor Cyan
BD @('ready')
BD @('update', $task, '--claim', '--actor', 'agent-zcode')
if ($LASTEXITCODE -ne 0) { throw "claim failed (atomicity guard fired?)" }
BD @('update', $task, '--add-label', 'stage:IMPLEMENTING', '--actor', 'agent-zcode') | Out-Null

# --- 3. Agent Mail：注册身份 + 预约（reason=task）+ Start 消息（thread=task）---
Write-Host "`n[3/7] AgentMail: identities + reserve(reason=$task) + Start(thread=$task)" -ForegroundColor Cyan
uv run python tests/integration/test_task_id_unification.py --demo-claim $task
if ($LASTEXITCODE -ne 0) { throw "mail claim step failed" }

# --- 4. Worktree：agent/zcode/<task> ---
Write-Host "`n[4/7] Git: worktree branch agent/zcode/$task" -ForegroundColor Cyan
$wt = "$demo\worktrees\agent-zcode"
if (-not (Test-Path $wt)) { git -C $demo worktree add worktrees/agent-zcode -b agent/zcode/home main | Out-Host }
git -C $wt checkout -B "agent/zcode/$task" main

# --- 5/6. 实现 + 自测（worktree 内，任务 ID 注释保证内容新鲜）---
Write-Host "`n[5/7] Code: calculator + unittest (in worktree)" -ForegroundColor Cyan
Set-Content -Path "$wt\src\calculator.py" -Value "# task ${task}`ndef add(a, b):`n    return a + b`n" -Encoding utf8
Set-Content -Path "$wt\tests\test_calculator.py" -Value "import unittest`nfrom src.calculator import add`n`nclass T(unittest.TestCase):`n    def test_add(self):`n        self.assertEqual(add(1, 2), 3)`n        self.assertEqual(add(-1, -2), -3)`n" -Encoding utf8
Push-Location $wt
python -m unittest discover -s tests -t . -v
$testRc = $LASTEXITCODE
Pop-Location
if ($testRc -ne 0) {
    Write-Host "self-test FAILED -> FIX_REQUIRED (现场保留)" -ForegroundColor Yellow
    BD @('update', $task, '--status', 'open', '--actor', 'agent-zcode') | Out-Null
    exit 3
}
git -C $wt add -A
git -C $wt commit -m "[$task] implement calculator add + tests"

# --- 7. Review 消息 → 释放预约 → merge → close ---
Write-Host "`n[6/7][7/7] Review msg -> release -> merge -> close" -ForegroundColor Cyan
uv run python tests/integration/test_task_id_unification.py --demo-close $task
if ($LASTEXITCODE -ne 0) { throw "mail close step failed" }
git -C $demo merge --no-ff "agent/zcode/$task" -m "merge [$task] into main"
BD @('close', $task, '--reason', 'demo complete: tests pass, review ack, merged', '--actor', 'agent-zcode')
BD @('update', $review, '--claim', '--actor', 'agent-antigravity') | Out-Null
BD @('close', $review, '--reason', 'review task closed after impl merge', '--actor', 'agent-antigravity')

Write-Host "`n=== demo 完成：统一 ID [$task] 贯穿 Beads/Mail/Reservation/Branch/Commit ===" -ForegroundColor Green
Write-Host "证据复核：git -C $demo log --oneline -5 ; bd ready ; am file_reservations active $PJ"
exit 0
