#!/usr/bin/env bash
# MACD 监控一键卸载脚本 (适用于 Ubuntu/Debian/CentOS 等 Linux)
# 用法:
#   curl -fsSL https://raw.githubusercontent.com/huigezhi/stock_tracking/main/uninstall.sh | bash
# 或克隆仓库后: bash uninstall.sh
# 可选参数:
#   bash uninstall.sh /opt/macd-monitor   # 指定安装目录(默认自动探测)

set -euo pipefail

SVC_MONITOR="macd-monitor"
SVC_WEBUI="macd-webui"

info()  { echo -e "\\033[32m[卸载]\\033[0m $*"; }
warn()  { echo -e "\\033[33m[注意]\\033[0m $*"; }

# ---------- 1. 定位安装目录 ----------
# 优先用参数指定; 否则依次探测 /opt 和 ~/macd-monitor
find_app_dir() {
    if [[ -n "${1:-}" ]]; then
        echo "$1"
        return
    fi
    for d in "/opt/macd-monitor" "$HOME/macd-monitor"; do
        [[ -f "$d/macd-monitor/monitor.py" ]] && { echo "$d"; return; }
    done
    echo ""
}

APP_DIR="$(find_app_dir "${1:-}")"

# ---------- 2. 停止并移除 systemd 服务(仅 root 可操作, 本地安装无服务则跳过) ----------
remove_services() {
    if command -v systemctl >/dev/null 2>&1; then
        for svc in "$SVC_MONITOR" "$SVC_WEBUI"; do
            if systemctl list-unit-files 2>/dev/null | grep -q "^${svc}\\.service"; then
                if [[ $EUID -eq 0 ]]; then
                    info "停止并移除 systemd 服务: $svc"
                    systemctl stop "${svc}.service" 2>/dev/null || true
                    systemctl disable "${svc}.service" 2>/dev/null || true
                    rm -f "/etc/systemd/system/${svc}.service"
                else
                    warn "检测到服务 $svc 但当前非 root, 跳过服务移除(可用 sudo 重跑本脚本)"
                fi
            fi
        done
        [[ $EUID -eq 0 ]] && systemctl daemon-reload 2>/dev/null || true
    fi
}

# ---------- 3. 杀掉残留进程 ----------
kill_processes() {
    [[ -z "$APP_DIR" ]] && return 0
    local n=0 pid cwd cmdline
    for pid in $(pgrep -f "monitor\.py|webui\.py" 2>/dev/null || true); do
        # 排除自身及父进程(调用卸载脚本的shell)
        [[ "$pid" == "$$" || "$pid" == "$PPID" ]] && continue
        cmdline=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null) || continue
        # 以进程工作目录判断归属: 支持"cd目录后相对路径启动"(README推荐方式)和绝对路径启动
        cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null) || cwd=""
        if [[ "$cwd" == "$APP_DIR/macd-monitor" || "$cmdline" == *"$APP_DIR/macd-monitor/"* ]]; then
            info "终止进程 $pid ($(echo "$cmdline" | cut -c1-60))"
            kill "$pid" 2>/dev/null || true
            n=$((n + 1))
        fi
    done
    [[ $n -eq 0 ]] || info "已终止 $n 个进程"
}

# ---------- 4. 删除安装目录 ----------
remove_app_dir() {
    if [[ -z "$APP_DIR" ]]; then
        warn "未找到安装目录(默认探测 /opt/macd-monitor 和 ~/macd-monitor)"
        warn "如安装在其他位置, 用法: bash uninstall.sh <安装目录>"
        exit 0
    fi
    if [[ ! -f "$APP_DIR/macd-monitor/monitor.py" ]]; then
        warn "$APP_DIR 下未找到程序文件, 跳过删除"
        exit 0
    fi
    info "删除安装目录: $APP_DIR"
    if [[ -w "$(dirname "$APP_DIR")" ]]; then
        rm -rf "$APP_DIR"
    else
        sudo rm -rf "$APP_DIR"
    fi
}

echo "=============================================================="
echo "  MACD 监控一键卸载 (Linux)"
[[ -n "$APP_DIR" ]] && echo "  安装目录: $APP_DIR"
echo "  将删除: systemd服务 / 进程 / 安装目录(含config.json等全部数据)"
echo "=============================================================="

remove_services
kill_processes
remove_app_dir

echo
echo "=============================================================="
echo "  卸载完成!"
echo "  (python3 / git 等系统依赖保留, 不受影响)"
echo "=============================================================="
