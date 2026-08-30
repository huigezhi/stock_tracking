@echo off
chcp 65001 >nul
title MACD监控 Web UI SSH隧道

echo ============================================
echo   MACD监控 Web UI - SSH 隧道访问
echo ============================================
echo.
echo 正在建立 SSH 隧道 (8688 端口)...
echo 连接后请保持本窗口开启, 然后用浏览器打开:
echo.
echo     http://localhost:8688
echo.
echo 按 Ctrl+C 或直接关闭本窗口即可断开隧道。
echo.
ssh -L 8688:127.0.0.1:8688 root@192.227.167.52

echo.
echo 隧道已断开。
pause
