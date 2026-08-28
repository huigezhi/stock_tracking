#!/usr/bin/env bash
# MACD 监控 Ubuntu/Debian 一键安装脚本 (本地运行版, 无 systemd)
# 用法: bash install.sh
# 包含: 安装依赖 -> 克隆代码 -> 初始化配置 -> 验证
# VPS 服务器部署(含 systemd 开机自启)请改用 deploy.sh

set -euo pipefail

REPO_URL="https://github.com/huigezhi/stock_tracking.git"
APP_DIR="$HOME/macd-monitor"

info()  { echo -e "\033[32m[安装]\033[0m $*"; }
warn()  { echo -e "\033[33m[注意]\033[0m $*"; }
die()   { echo -e "\033[31m[错误]\033[0m $*" >&2; exit 1; }

echo "=============================================================="
echo "  MACD 监控一键安装 (Ubuntu / Debian)"
echo "  安装目录: $APP_DIR"
echo "=============================================================="

# ---------- 1. 安装系统依赖 ----------
if command -v apt-get >/dev/null 2>&1; then
    MISSING=""
    command -v git    >/dev/null 2>&1 || MISSING="$MISSING git"
    command -v python3 >/dev/null 2>&1 || MISSING="$MISSING python3"
    if [[ -n "$MISSING" ]]; then
        info "安装系统依赖:$MISSING ..."
        sudo apt-get update -y
        sudo apt-get install -y $MISSING
    fi
else
    command -v git     >/dev/null 2>&1 || die "未检测到 git, 请手动安装"
    command -v python3 >/dev/null 2>&1 || die "未检测到 python3, 请手动安装"
fi

info "$(python3 --version), $(git --version)"

# ---------- 2. 安装 python 依赖 ----------
if ! python3 -c "import requests" >/dev/null 2>&1; then
    info "安装 python 依赖 requests ..."
    pip3 install --user -q requests 2>/dev/null \
        || python3 -m pip install --user -q requests \
        || sudo apt-get install -y python3-requests \
        || die "requests 安装失败, 请手动执行: pip3 install requests"
fi
info "依赖已就绪"

# ---------- 3. 获取代码 ----------
if [[ -f "$APP_DIR/macd-monitor/monitor.py" ]]; then
    info "代码已存在, 拉取最新版本..."
    git -C "$APP_DIR" pull --ff-only || warn "git pull 失败(可能有本地改动), 使用现有代码继续"
else
    info "克隆代码 $REPO_URL ..."
    git clone "$REPO_URL" "$APP_DIR" || die "克隆失败, 请检查网络"
fi

# ---------- 4. 初始化配置 ----------
cd "$APP_DIR/macd-monitor"
if [[ -f config.json ]]; then
    info "config.json 已存在, 保留现有配置"
else
    cp config.example.json config.json
    info "已生成 config.json, 请稍后编辑填入飞书 webhook"
fi

# ---------- 5. 验证 ----------
info "运行状态报告验证..."
if python3 monitor.py --report; then
    echo
    echo "=============================================================="
    echo "  安装完成!"
    echo
    echo "  启动监控:    cd $APP_DIR/macd-monitor && python3 monitor.py"
    echo "  启动 Web UI: cd $APP_DIR/macd-monitor && python3 webui.py"
    echo "               浏览器打开 http://localhost:8688"
    echo
    echo "  飞书通知:    编辑 $APP_DIR/macd-monitor/config.json 填入 webhook_url"
    echo "=============================================================="
else
    die "验证失败, 请检查上方报错信息"
fi
