#!/usr/bin/env bash
# MACD 监控一键部署脚本 (适用于 Ubuntu/Debian/CentOS 等 Linux VPS)
# 用法: 在 VPS 上执行
#   curl -fsSL https://raw.githubusercontent.com/huigezhi/stock_tracking/main/deploy.sh | bash
# 或克隆仓库后: bash deploy.sh
set -euo pipefail

APP_DIR="/opt/macd-monitor"
REPO_URL="https://github.com/huigezhi/stock_tracking.git"
SVC_MONITOR="macd-monitor"
SVC_WEBUI="macd-webui"

info()  { echo -e "\\033[32m[部署]\\033[0m $*"; }
warn()  { echo -e "\\033[33m[注意]\\033[0m $*"; }
die()   { echo -e "\\033[31m[错误]\\033[0m $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请用 root 运行 (sudo bash deploy.sh)"

# ---------- 1. 安装依赖 ----------
install_deps() {
    if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -y
        apt-get install -y python3 python3-requests git curl
    elif command -v yum >/dev/null 2>&1; then
        yum install -y python3 python3-pip git curl
        python3 -c "import requests" 2>/dev/null || pip3 install requests
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y python3 python3-pip git curl
        python3 -c "import requests" 2>/dev/null || pip3 install requests
    else
        die "未识别的包管理器, 请手动安装 python3 / requests / git"
    fi
}

# ---------- 2. 获取代码 ----------
fetch_code() {
    if [[ -d "$APP_DIR/.git" ]]; then
        info "更新代码..."
        git -C "$APP_DIR" pull --ff-only || warn "git pull 失败, 使用现有代码继续"
    else
        info "克隆仓库到 $APP_DIR ..."
        rm -rf "$APP_DIR"
        git clone "$REPO_URL" "$APP_DIR"
    fi
}

# ---------- 3. 配置 ----------
setup_config() {
    local cfg="$APP_DIR/macd-monitor/config.json"
    if [[ -f "$cfg" ]]; then
        info "config.json 已存在, 保留现有配置"
    else
        cp "$APP_DIR/macd-monitor/config.example.json" "$cfg"
        # 交互式填写飞书 webhook (非交互环境跳过, 之后手动编辑)
        if [[ -t 0 ]]; then
            read -r -p "飞书机器人 Webhook URL (留空稍后配置): " url
            read -r -p "飞书机器人签名密钥 (留空跳过): " secret
            [[ -n "$url" ]] && sed -i "s|\"webhook_url\": \"\"|\"webhook_url\": \"$url\"|" "$cfg"
            [[ -n "$secret" ]] && sed -i "s|\"webhook_secret\": \"\"|\"webhook_secret\": \"$secret\"|" "$cfg"
        fi
        info "配置文件: $cfg"
    fi
}

# ---------- 4. systemd 服务 ----------
install_services() {
    cat > "/etc/systemd/system/${SVC_MONITOR}.service" <<EOF
[Unit]
Description=MACD golden/death cross monitor with Feishu alerts
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}/macd-monitor
ExecStart=/usr/bin/python3 ${APP_DIR}/macd-monitor/monitor.py
Restart=always
RestartSec=15
Environment=TZ=Asia/Shanghai

[Install]
WantedBy=multi-user.target
EOF

    cat > "/etc/systemd/system/${SVC_WEBUI}.service" <<EOF
[Unit]
Description=MACD watchlist management Web UI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}/macd-monitor
ExecStart=/usr/bin/python3 ${APP_DIR}/macd-monitor/webui.py
Restart=always
RestartSec=15
Environment=TZ=Asia/Shanghai
# 安全: Web UI 仅监听本机, 通过 SSH 隧道访问
# ssh -L 8688:127.0.0.1:8688 user@你的VPS  然后本机打开 http://localhost:8688
# 如需公网直接访问, 改为 WEBUI_HOST=0.0.0.0 (自行承担风险)
Environment=WEBUI_HOST=127.0.0.1

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable --now "${SVC_MONITOR}.service" "${SVC_WEBUI}.service" 2>/dev/null \
        || systemctl enable "${SVC_MONITOR}" "${SVC_WEBUI}"
    systemctl restart "${SVC_MONITOR}" "${SVC_WEBUI}"
}

# ---------- 5. 自检 ----------
health_check() {
    sleep 3
    info "服务状态:"
    systemctl --no-pager -l status "${SVC_MONITOR}" | head -n 5 || true
    systemctl --no-pager -l status "${SVC_WEBUI}"    | head -n 5 || true
    if curl -sf http://127.0.0.1:8688/api/stocks >/dev/null; then
        info "Web UI 自检通过: http://127.0.0.1:8688"
    else
        warn "Web UI 自检失败, 请查看日志: journalctl -u ${SVC_WEBUI} -n 50"
    fi
    # 检查能否访问腾讯行情数据源 (美国VPS一般可直连)
    if curl -sf -m 10 -H "User-Agent: Mozilla/5.0" \
        "https://smartbox.gtimg.cn/s3/?q=600519&t=all" | grep -q "600519"; then
        info "行情数据源连通性正常"
    else
        warn "行情数据源(smartbox.gtimg.cn)访问失败, 若持续失败需配置代理"
    fi
}

main() {
    info "开始部署 MACD 监控..."
    install_deps
    fetch_code
    setup_config
    install_services
    health_check
    echo
    info "部署完成! 常用命令:"
    echo "  systemctl status ${SVC_MONITOR}     # 查看监控状态"
    echo "  journalctl -u ${SVC_MONITOR} -f     # 实时查看监控日志"
    echo "  systemctl restart ${SVC_MONITOR}   # 重启监控"
    echo "  vim ${APP_DIR}/macd-monitor/config.json  # 修改配置(改后重启生效)"
    echo
    info "访问 Web UI(本机电脑执行): ssh -L 8688:127.0.0.1:8688 root@你的VPS"
    info "然后浏览器打开 http://localhost:8688"
}

main "$@"
