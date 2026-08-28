# MACD金叉、死叉、背离自动监控&飞书通知

MACD 金叉/死叉 + 底背离/顶背离监控 + 飞书机器人提醒 + 自选管理 Web UI，数据源为腾讯行情接口，纯 Python 实现，适合部署在 VPS 上长期运行。

## 功能

### MACD 监控（monitor.py）

- 多周期监控：1分钟 / 5分钟 / 30分钟 / 60分钟 / 日线 / 周线
- 金叉 / 死叉信号自动识别，信号去重（同一周期同一根K线只提醒一次）
- 底背离 / 顶背离监控：基于已收盘K线识别 DIF 极值点，价格创新低（新高）而 DIF 低点抬高（高点降低）时提醒；极值确认后触发，同一对极值只提醒一次
- 通知策略：交易时段即时推送信号；非交易时段/非交易日信号仅记录不推送
- 飞书机器人推送：支持签名校验、卡片消息、错误日志告警、定时状态汇报
- 仅交易时段扫描（周一至周五 9:25-11:35 / 12:55-15:10），避开未完成K线可选
- 自选股改动实时写入 `config.json`，监控进程自动热加载，无需重启

### 自选管理 Web UI（webui.py）

- **左栏 · 宽基ETF列表**：自动筛选份额 ≥ 100亿份 的宽基ETF（沪深300、中证500、科创50、A500 等），按份额排序，实时刷新
- **中栏 · K线图**：日K / 周K 切换，MA5/10/20/60 均线，成交量副图，双端日期滑条 + 滚轮缩放，十字光标
- **中栏 · 价格/份额变动面板**：K线下方双轴图表
  - 左轴：收盘价折线
  - 右轴：每日/每周份额增减柱（红=净申购、绿=净赎回）
  - 份额数据由系统每日自动快照积累（`etf_share_hist.json`），随日K/周K联动切换
- **右栏 · 监控列表管理**：搜索添加 A股 / 指数 / ETF / LOF，分组管理，实时行情 + 主力净流入刷新
- 明暗主题切换（跟随系统 / 白天 / 夜间），响应式布局

## 一键安装

复制对应系统的一行命令到终端运行，从克隆代码到验证完成全自动，无需手动 clone：

### Windows（PowerShell，推荐）

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/huigezhi/stock_tracking/main/install.ps1 | iex"
```

命令直接从网络执行，不在本地留下安装脚本。也可以 [下载 install.ps1](install.ps1) 后执行，或使用 [install.bat](install.bat)（双击运行）。

### Windows（cmd）

```bat
curl -fsSL -o %TEMP%\install.bat https://raw.githubusercontent.com/huigezhi/stock_tracking/main/install.bat && %TEMP%\install.bat
```

### Ubuntu / Debian

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/huigezhi/stock_tracking/main/install.sh)
```

### 前置要求

- Windows：[git](https://git-scm.com/download/win) + [Python 3](https://www.python.org/downloads/)（安装时勾选 *Add python.exe to PATH*）
- Ubuntu：无（脚本会用 apt 自动安装缺失的 git / python3 / requests）

### 脚本自动完成的步骤

1. 克隆代码（Windows 到 `%USERPROFILE%\macd-monitor`，Linux 到 `~/macd-monitor`；已存在则 `git pull` 更新，config.json 保留不覆盖）
2. 安装 python 依赖 requests
3. 生成 config.json
4. 运行 `--report` 验证安装

安装完成后：

```bash
python3 monitor.py     # 启动监控 (代码目录下)
python3 webui.py       # 启动 Web UI, 浏览器打开 http://localhost:8688
```

## 手动运行（快速开始）

```bash
# 依赖: python3 + requests
pip3 install requests

# 1. 准备配置
cd macd-monitor
cp config.example.json config.json
# 编辑 config.json, 填入飞书机器人 webhook_url (可选)

# 2. 查看各周期 MACD 状态(不发通知)
python3 monitor.py --report

# 3. 测试一轮扫描+推送
python3 monitor.py --once

# 4. 启动持续监控
python3 monitor.py

# 5. 启动 Web UI (另一个终端)
python3 webui.py
# 浏览器打开 http://localhost:8688
```

## VPS 服务器部署（systemd 开机自启）

```bash
sudo bash deploy.sh
```

部署脚本会自动：安装依赖 → 克隆代码到 `/opt/macd-monitor` → 生成配置 → 注册 systemd 服务（`macd-monitor` / `macd-webui`）→ 自检。

Web UI 默认仅监听本机，通过 SSH 隧道访问：

```bash
ssh -L 8688:127.0.0.1:8688 root@你的VPS
# 然后本机打开 http://localhost:8688
```

常用运维命令：

```bash
systemctl status macd-monitor macd-webui   # 服务状态
journalctl -u macd-monitor -f              # 实时监控日志
systemctl restart macd-monitor              # 重启监控
```

## 配置说明（config.json）

| 字段 | 说明 |
|------|------|
| `webhook_url` | 飞书机器人 Webhook 地址，留空则不推送 |
| `webhook_secret` | 飞书机器人签名密钥，可选 |
| `poll_interval_sec` | 扫描间隔（秒），默认 30 |
| `signal_on_forming_bar` | 是否对未完成K线发信号（默认只确认已完成K线） |
| `feishu_log_level` | 推送到飞书的日志级别，默认 WARNING |
| `feishu_status_interval_min` | 定时状态汇报间隔（分钟） |
| `timeframes` | 监控周期列表，可删减 |
| `stocks` | 自选股列表（code / name / group），Web UI 中增删自动同步 |

## 目录结构

```
├── install.sh            # Ubuntu/Debian 一键安装（本地运行）
├── install.ps1           # Windows 一键安装（PowerShell，支持一行命令）
├── install.bat           # Windows 一键安装（cmd / 双击运行）
├── deploy.sh             # VPS 服务器部署（systemd 开机自启）
└── macd-monitor/
    ├── monitor.py            # MACD 监控 + 飞书推送
    ├── webui.py              # 自选管理 Web UI 后端
    ├── config.json           # 运行配置（自选股、webhook）
    ├── config.example.json   # 配置示例
    ├── state.json            # 信号去重状态
    ├── etf_share_hist.json   # ETF 份额日度快照（自动积累）
    ├── monitor.log           # 运行日志
    └── static/               # 前端静态文件
        ├── index.html
        ├── app.js
        └── style.css
```

## 说明

- 份额数据无免费公开历史接口，采用每日快照积累方式：系统运行期间每个交易日自动记录一次当前份额，保留最近 500 天，历史曲线随运行时间逐步完整
- 行情数据来自腾讯公开接口（`qt.gtimg.cn` / `ifzq.gtimg.cn`），主力净流入来自新浪接口，仅供个人参考，不构成投资建议
