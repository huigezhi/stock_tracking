@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

:: MACD 监控 Windows 一键安装脚本
:: 用法: 双击运行 或 在 cmd 中执行 install.bat
:: 包含: 安装依赖 -> 克隆代码 -> 初始化配置 -> 验证

set "REPO_URL=https://github.com/huigezhi/stock_tracking.git"
set "APP_DIR=%USERPROFILE%\macd-monitor"

echo ==============================================================
echo   MACD 监控一键安装 (Windows)
echo   安装目录: %APP_DIR%
echo ==============================================================

:: ---------- 1. 检查 git ----------
where git >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 git, 请先安装: https://git-scm.com/download/win
    pause
    exit /b 1
)

:: ---------- 2. 检查 python ----------
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 python, 请先安装: https://www.python.org/downloads/
    echo        安装时勾选 "Add python.exe to PATH"
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('python --version') do echo [OK] %%i

:: ---------- 3. 获取代码 ----------
if exist "%APP_DIR%\macd-monitor\monitor.py" (
    echo [更新] 代码已存在, 拉取最新版本...
    git -C "%APP_DIR%" pull --ff-only
) else (
    echo [克隆] %REPO_URL% ...
    git clone "%REPO_URL%" "%APP_DIR%"
    if errorlevel 1 (
        echo [错误] 克隆失败, 请检查网络或代理设置
        pause
        exit /b 1
    )
)

:: ---------- 4. 安装依赖 ----------
echo [安装] python 依赖 requests ...
python -m pip install --user -q requests
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)

:: ---------- 5. 初始化配置 ----------
cd /d "%APP_DIR%\macd-monitor"
if not exist config.json (
    copy config.example.json config.json >nul
    echo [配置] 已生成 config.json, 请稍后编辑填入飞书 webhook
) else (
    echo [配置] config.json 已存在, 保留现有配置
)

:: ---------- 6. 验证 ----------
echo [验证] 运行状态报告...
python monitor.py --report
if errorlevel 1 (
    echo.
    echo [错误] 验证失败, 请检查上方报错信息
    pause
    exit /b 1
)

echo.
echo ==============================================================
echo   安装完成!
echo.
echo   启动监控:     cd %APP_DIR%\macd-monitor ^&^& python monitor.py
echo   启动 Web UI:   cd %APP_DIR%\macd-monitor ^&^& python webui.py
echo                 浏览器打开 http://localhost:8688
echo.
echo   飞书通知:     编辑 %APP_DIR%\macd-monitor\config.json 填入 webhook_url
echo ==============================================================
pause
