<#
.SYNOPSIS
  ai-engineering-orchestrator 全量健康检查（只读）。
.DESCRIPTION
  检查：基础工具 / ZCode CLI / Beads / Agent Mail / Antigravity / Git Worktree /
  共享缓存路径 / Pre-P0 Gate 状态。缺失组件如实报告并以非零码退出。
#>
[CmdletBinding()]
param()
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot

$script:FailCount = 0
function Check($name, $ok, $detail, [switch]$Optional) {
    if ($ok) { Write-Host ("  [OK]    {0,-22} {1}" -f $name, $detail) -ForegroundColor Green }
    elseif ($Optional) { Write-Host ("  [INFO]  {0,-22} {1}" -f $name, $detail) -ForegroundColor DarkGray }
    else {
        Write-Host ("  [MISS]  {0,-22} {1}" -f $name, $detail) -ForegroundColor Yellow
        $script:FailCount++
    }
}

Write-Host "=== AEO healthcheck ===" -ForegroundColor Cyan

# --- 基础工具 ---
foreach ($t in @('git','python','node','uv','pwsh')) {
    $c = Get-Command $t -ErrorAction SilentlyContinue
    Check $t ($null -ne $c) ($(if ($c) { & $t --version 2>$null | Select-Object -First 1 } else { 'not found' }))
}

# --- ZCode CLI（不在 PATH，按已知位置探测） ---
$zcodeCjs = 'D:\Software\zcode\install\ZCode\resources\glm\zcode.cjs'
$zcodeOk = Test-Path $zcodeCjs
Check 'zcode-cli' $zcodeOk ($(if ($zcodeOk) { "v0.16.5 @ $zcodeCjs (headless BLOCKED: model config, see p0-evidence)" } else { 'zcode.cjs not found' }))

# --- 协作组件 ---
foreach ($t in @('bd','am','mcp-agent-mail','agy')) {
    $c = Get-Command $t -ErrorAction SilentlyContinue
    $ver = $(if ($c) { try { (& $t --version 2>$null | Select-Object -First 1) } catch { 'installed' } } else { '' })
    Check $t ($null -ne $c) ($(if ($c) { $ver } else { 'NOT_INSTALLED (see docs/INSTALLATION-PROPOSAL.md)' }))
}

# --- Agent Mail HTTP 服务（若配置） ---
$httpUp = $false
try {
    $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8765/mcp/' -Method Get -TimeoutSec 2 -ErrorAction Stop
    $httpUp = $true
} catch {}
Check 'agent-mail-http' $httpUp ($(if ($httpUp) { '127.0.0.1:8765/mcp/ reachable' } else { 'not running (optional; stdio mode needs no server)' })) -Optional

# --- Worktree（demo-repo） ---
$demoWt = "$root\sandbox\demo-repo"
$wtOk = (Test-Path "$demoWt\.git")
Check 'demo-repo' $wtOk ($(if ($wtOk) { (git -C $demoWt branch --show-current) } else { 'sandbox not initialized (run pytest tests/integration/test_git_worktree.py)' }))

# --- 共享缓存路径（D 盘约束） ---
$shared = 'D:\Software\ai-orchestrator\shared-cache'
Check 'shared-cache-dir' (Test-Path $shared) "$(if (Test-Path $shared) {'exists'} else {'not created yet (proposal only, P1)'}) @ $shared" -Optional

# --- Pre-P0 Gate checklist 摘要（P0 终态，2026-09-10）---
Write-Host "`n--- Pre-P0 Gate (v1.1 §10) — FINAL ---"
Write-Host "  #1 spec absorbed      : OK      (docs/P0-SPEC-DELTA-REVIEW.md = PASS)"
Write-Host "  #2 component interface: VERIFIED (bd v1.2.2 + agent-mail v0.3.35 real-tested; see docs/P0-REPORT-ROUND2.md)"
Write-Host "  #3 shared cache paths : PROPOSED (docs/INSTALLATION-PROPOSAL.md §4; activates with first JS/Java target repo in P1+)"
Write-Host "  #4 schema validator   : OK      (pytest tests/unit 8/8)"
Write-Host "  #5 pre-commit guard   : TEMPLATE READY (templates/hooks/)"
Write-Host "  P0_TECHNICAL_GATE = PASS | ZCODE_HEADLESS = DEFERRED_PRODUCT_GAP | awaiting GPT P1 Gate"

Write-Host "`nResult: $($script:FailCount) missing/blocking item(s)." -ForegroundColor $(if ($script:FailCount -gt 0) { 'Yellow' } else { 'Green' })
exit $(if ($script:FailCount -gt 0) { 1 } else { 0 })
