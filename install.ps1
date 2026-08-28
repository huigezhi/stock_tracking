# MACD 监控 Windows 一键安装 (PowerShell)
# 一行命令运行(复制到 PowerShell / cmd / "运行"窗口):
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/huigezhi/stock_tracking/main/install.ps1 | iex"

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/huigezhi/stock_tracking.git"
$AppDir = Join-Path $env:USERPROFILE "macd-monitor"

Write-Host "=============================================================="
Write-Host "  MACD 监控一键安装 (Windows)"
Write-Host "  安装目录: $AppDir"
Write-Host "=============================================================="

# ---------- 1. 检查 git ----------
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "[错误] 未检测到 git, 请先安装: https://git-scm.com/download/win" -ForegroundColor Red
    Read-Host "按回车退出"
    exit 1
}

# ---------- 2. 检查 python ----------
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "[错误] 未检测到 python, 请先安装: https://www.python.org/downloads/" -ForegroundColor Red
    Write-Host "       安装时勾选 'Add python.exe to PATH'" -ForegroundColor Red
    Read-Host "按回车退出"
    exit 1
}

# ---------- 3. 获取代码 ----------
if (Test-Path (Join-Path $AppDir "macd-monitor\monitor.py")) {
    Write-Host "[更新] 代码已存在, 拉取最新版本..."
    git -C $AppDir pull --ff-only
    if ($LASTEXITCODE -ne 0) { Write-Host "[注意] git pull 失败, 使用现有代码继续" -ForegroundColor Yellow }
} else {
    Write-Host "[克隆] $RepoUrl ..."
    git clone $RepoUrl $AppDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[错误] 克隆失败, 请检查网络或代理设置" -ForegroundColor Red
        Read-Host "按回车退出"
        exit 1
    }
}

# ---------- 4. 安装依赖 ----------
Write-Host "[安装] python 依赖 requests ..."
python -m pip install --user -q requests
if ($LASTEXITCODE -ne 0) {
    Write-Host "[错误] 依赖安装失败, 请手动执行: python -m pip install requests" -ForegroundColor Red
    Read-Host "按回车退出"
    exit 1
}

# ---------- 5. 初始化配置 ----------
Set-Location (Join-Path $AppDir "macd-monitor")
if (-not (Test-Path "config.json")) {
    Copy-Item "config.example.json" "config.json"
    Write-Host "[配置] 已生成 config.json, 请稍后编辑填入飞书 webhook"
} else {
    Write-Host "[配置] config.json 已存在, 保留现有配置"
}

# ---------- 6. 验证 ----------
Write-Host "[验证] 运行状态报告..."
python monitor.py --report
if ($LASTEXITCODE -ne 0) {
    Write-Host "[错误] 验证失败, 请检查上方报错信息" -ForegroundColor Red
    Read-Host "按回车退出"
    exit 1
}

Write-Host ""
Write-Host "=============================================================="
Write-Host "  安装完成!"
Write-Host ""
Write-Host "  启动监控:    cd $AppDir\macd-monitor ; python monitor.py"
Write-Host "  启动 Web UI: cd $AppDir\macd-monitor ; python webui.py"
Write-Host "               浏览器打开 http://localhost:8688"
Write-Host ""
Write-Host "  飞书通知:    编辑 $AppDir\macd-monitor\config.json 填入 webhook_url"
Write-Host "=============================================================="
