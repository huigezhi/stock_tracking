#!/usr/bin/env bash
# 在部署 VPS 上安装"交易日早上6点模型自动迭代重训"的 systemd timer + service。
# 用法: sudo bash install_retrain.sh [APP_DIR]   (默认 APP_DIR=/opt/macd-monitor)
set -euo pipefail

APP_DIR="${1:-/opt/macd-monitor}"
T="$APP_DIR/macd-monitor"

[[ $EUID -eq 0 ]] || { echo "错误: 请用 root 运行 (sudo bash install_retrain.sh)"; exit 1; }
[[ -f "$T/retrain_daily.py" ]] || { echo "错误: 未找到 $T/retrain_daily.py, 请先 git pull 更新到最新代码"; exit 1; }

echo "[安装] 校验/安装训练依赖 scikit-learn + numpy ..."
python3 -c "import sklearn, numpy" 2>/dev/null \
  || pip3 install --no-cache-dir scikit-learn numpy

echo "[安装] 写入 systemd 单元文件 ..."
cat > /etc/systemd/system/macd-retrain.service <<EOF
[Unit]
Description=MACD divergence model daily retrain (trading days 06:00)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$T
ExecStart=/usr/bin/python3 $T/retrain_daily.py
Environment=TZ=Asia/Shanghai
EOF

cat > /etc/systemd/system/macd-retrain.timer <<EOF
[Unit]
Description=Daily model retrain timer (trading mornings 06:00)

[Timer]
OnCalendar=Mon,Tue,Wed,Thu,Fri 06:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

echo "[安装] 重载并启用 timer ..."
systemctl daemon-reload
systemctl enable --now macd-retrain.timer
systemctl start macd-retrain.timer

echo "[完成] 已启用。常用命令:"
echo "  systemctl list-timers macd-retrain        # 查看下次触发时间"
echo "  systemctl status macd-retrain.service     # 最近一次训练结果"
echo "  tail -f $T/retrain.log                    # 查看训练日志"
echo "  systemctl start macd-retrain.service      # 手动立即跑一次(非交易日会跳过)"