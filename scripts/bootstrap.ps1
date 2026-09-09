<#
.SYNOPSIS
  ai-engineering-orchestrator 项目依赖初始化。
.DESCRIPTION
  默认只做检查与项目内初始化（.venv），绝不静默安装任何系统级组件。
  -InstallSystem 开关才会进入逐项人工确认的组件安装流程（且只打印命令，
  实际执行仍需对每一步回复 Y；安装方案见 docs/INSTALLATION-PROPOSAL.md）。
.NOTES
  pwsh 7+；uv 镜像 403 问题：本项目固定走官方 PyPI（命令级覆盖，不改全局）。
#>
[CmdletBinding()]
param(
    [switch]$InstallSystem
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [OK]   $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "    [WARN] $msg" -ForegroundColor Yellow }
function Write-Err2($msg) { Write-Host "    [FAIL] $msg" -ForegroundColor Red }

# ---------- 1. 基础工具检查 ----------
Write-Step "基础工具检查"
foreach ($tool in @('git', 'python', 'node', 'npm', 'uv', 'pwsh')) {
    $cmd = Get-Command $tool -ErrorAction SilentlyContinue
    if ($cmd) { Write-Ok "$tool -> $($cmd.Source)" }
    else      { Write-Err2 "$tool 未找到（P0 必需，请先安装）" }
}

# ---------- 2. 项目级 Python 环境 ----------
Write-Step "项目级 Python 环境（.venv，官方 PyPI 源）"
$env:UV_DEFAULT_INDEX = 'https://pypi.org/simple'  # 仅本进程，规避 tuna 镜像 403
if (-not (Test-Path "$root\pyproject.toml")) {
    Write-Err2 "缺少 pyproject.toml"
} else {
    uv sync
    if ($LASTEXITCODE -eq 0) { Write-Ok ".venv 就绪（uv run pytest 可用）" }
    else                     { Write-Err2 "uv sync 失败（检查网络/镜像）" }
}

# ---------- 3. 组件存在性（不安装，只报告） ----------
Write-Step "协作组件存在性"
$components = [ordered]@{
    'bd'               = 'Beads 任务系统（docs/INSTALLATION-PROPOSAL.md §1）'
    'am'               = 'Agent Mail 运维 CLI（提案 §2，Rust 版）'
    'mcp-agent-mail'   = 'Agent Mail MCP server（提案 §2）'
    'agy'              = 'Antigravity CLI（提案 §3，需 C 盘例外豁免）'
}
$missing = @()
foreach ($k in $components.Keys) {
    if (Get-Command $k -ErrorAction SilentlyContinue) { Write-Ok "$k 已安装" }
    else { Write-Warn2 "$k 未安装 —— $($components[$k])"; $missing += $k }
}

# ---------- 4. 系统级安装（默认拒绝，显式开关 + 逐项确认） ----------
if ($InstallSystem) {
    Write-Step "系统级安装流程（逐项确认；命令以 docs/INSTALLATION-PROPOSAL.md 为准）"
    if ($missing.Count -eq 0) { Write-Ok '无缺失组件'; }
    foreach ($m in $missing) {
        $answer = Read-Host "是否现在安装 [$m] ？(Y/N)"
        if ($answer -eq 'Y' -or $answer -eq 'y') {
            Write-Warn2 "按 docs/INSTALLATION-PROPOSAL.md 对应章节手动执行后重跑 healthcheck（本脚本不代执行下载/解压）"
        } else {
            Write-Host "    跳过 [$m]"
        }
    }
} else {
    Write-Step "提示：加 -InstallSystem 进入组件安装确认流程（默认仅检查）"
}

Write-Host "`nbootstrap 完成。运行 pwsh scripts/healthcheck.ps1 查看全量状态。"
exit 0
