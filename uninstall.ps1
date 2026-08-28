# MACD 监控一键卸载 (Windows PowerShell)
# 一行命令运行(复制到 PowerShell):
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/huigezhi/stock_tracking/main/uninstall.ps1 | iex"

$ErrorActionPreference = "Continue"
$AppDir = Join-Path $env:USERPROFILE "macd-monitor"

Write-Host "=============================================================="
Write-Host "  MACD 监控一键卸载 (Windows)"
Write-Host "  安装目录: $AppDir"
Write-Host "  将删除: 进程 / 安装目录(含config.json等全部数据)"
Write-Host "=============================================================="

# ---------- 1. 终止运行中的 monitor.py / webui.py ----------
$killed = 0
foreach ($name in @("python", "python3", "py")) {
    $procs = Get-CimInstance Win32_Process -Filter "Name like '$name%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "macd-monitor" -and $_.CommandLine -match "(monitor|webui)\.py" }
    foreach ($p in $procs) {
        Write-Host "[卸载] 终止进程 PID $($p.ProcessId): $($p.Name)"
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        $killed++
    }
}
if ($killed -gt 0) { Write-Host "[卸载] 已终止 $killed 个进程" }

# ---------- 2. 删除安装目录 ----------
if (-not (Test-Path (Join-Path $AppDir "macd-monitor\monitor.py"))) {
    Write-Host "[注意] $AppDir 下未找到程序文件, 无需卸载" -ForegroundColor Yellow
    Read-Host "按回车退出"
    exit 0
}

Write-Host "[卸载] 删除安装目录: $AppDir"
try {
    Remove-Item -Recurse -Force $AppDir -ErrorAction Stop
} catch {
    Write-Host "[错误] 删除失败: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "       可能有文件被占用, 请关闭相关程序后重试, 或手动删除 $AppDir" -ForegroundColor Red
    Read-Host "按回车退出"
    exit 1
}

Write-Host ""
Write-Host "=============================================================="
Write-Host "  卸载完成!"
Write-Host "  (Python / git 等系统依赖保留, 不受影响)"
Write-Host "=============================================================="
Read-Host "按回车退出"
