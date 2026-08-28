# MACD金叉、死叉、背离自动监控&飞书通知

MACD 金叉/死叉 + 底背离/顶背离监控 + 飞书机器人提醒 + 自选管理 Web UI，数据源为腾讯行情接口，纯 Python 实现，适合部署在 VPS 上长期运行。

## 目录

* [功能](#功能)

  * [MACD 监控（monitor.py）](#macd-监控monitorpy)

  * [自选管理 Web UI（webui.py）](#自选管理-web-uiwebuipy)

* [一键安装](#一键安装)

* [一键卸载](#一键卸载)

* [手动运行（快速开始）](#手动运行快速开始)

* [VPS 服务器部署（systemd 开机自启）](#vps-服务器部署systemd-开机自启)

* [配置说明（config.json）](#配置说明configjson)

* [目录结构](#目录结构)

* [说明](#说明)

## 功能

### MACD 监控（monitor.py）

* 多周期监控：1分钟 / 5分钟 / 30分钟 / 60分钟 / 日线 / 周线

* 金叉 / 死叉信号自动识别，信号去重（同一周期同一根K线只提醒一次）

* 底背离 / 顶背离监控：基于已收盘K线识别 DIF 极值点，价格创新低（新高）而 DIF 低点抬高（高点降低）时提醒；极值确认后触发，同一对极值只提醒一次

* 通知策略：交易时段即时推送信号；非交易时段/非交易日信号仅记录不推送

* 飞书机器人推送：支持签名校验、卡片消息、错误日志告警、定时状态汇报

* 仅交易时段扫描（周一至周五 9:25-11:35 / 12:55-15:10），避开未完成K线可选

* 自选股改动实时写入 `config.json`，监控进程自动热加载，无需重启

### 自选管理 Web UI（webui.py）

* **左栏 · 宽基ETF列表**：自动筛选份额 ≥ 100亿份 的宽基ETF（沪深300、中证500、科创50、A500 等），按份额排序，实时刷新

* **左栏 · K线本地缓存**：主要指数与宽基ETF的日K/周K历史数据持久化到 VPS 本地（`kline_cache.json`），仅增量更新最新交易日的收盘数据（每周顺带全量校准一次前复权），非交易日不请求行情接口，加载大幅提速

* **中栏 · K线图**：日K / 周K 切换，MA5/10/20/60 均线，成交量副图，双端日期滑条 + 滚轮缩放，十字光标

* **中栏 · 价格/份额变动面板**：K线下方双轴图表

  * 左轴：收盘价折线

  * 右轴：每日/每周份额增减柱（红=净申购、绿=净赎回）

  * 份额数据由系统每日自动快照积累（`etf_share_hist.json`），随日K/周K联动切换

* **中栏 · 底背离标的面板**：K线图下方表格

  * **全部A股**（约5200只，不含北交所）日线 / 周线底背离全量扫描，**每个交易日收盘后（北京时间16:00）自动重扫**（约6分钟，含实时进度显示）；周末/节假日不扫描

  * 只保留**最近100个周期内**成立的底背离，更早周期自动忽略

  * **30个交易日滚动缓存**（`div_hist.json`）：扫描结果按日持久化，仅保留最近30个扫描日（非交易日不计入），第31天自动淘汰第1天；当日已扫过（含服务重启）直接读缓存不重扫，大幅降低资源消耗

  * 表格上方**筛选栏**：按扫描日期（默认最新交易日）/ 周期（默认日线）/ 是否自选股 / 名称代码关键字组合筛选，实时显示命中条数

  * 展示序号、股票基本信息（名称 / 代码）、底背离日期区间、价格与 DIF 变化、**DIF增加值**、**后3/5周期涨幅**、确认日期

  * **确认日期 / DIF增加值 / 3周期涨幅 / 5周期涨幅**四列表头可点击排序（升序/降序切换，K线不足显示"--"）

  * 默认按确认日期倒序，确认日期 = 第二个低点被确认为DIF极值的日期（其后4根K线收盘后信号才成立）

  * 点击行可在中栏K线图中打开该标的对应周期

  * 股票列表缓存于 `all_stocks.json`（新浪数据源，每日刷新，拉取失败自动回退上次缓存）

* **右栏 · 监控列表管理**：搜索添加 A股 / 指数 / ETF / LOF，分组管理，实时行情 + 主力净流入刷新

* 明暗主题切换（跟随系统 / 白天 / 夜间），响应式布局

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

* Windows：[git](https://git-scm.com/download/win) + [Python 3](https://www.python.org/downloads/)（安装时勾选 *Add python.exe to PATH*）

* Ubuntu：无（脚本会用 apt 自动安装缺失的 git / python3 / requests）

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

## 一键卸载

删除安装（自动终止运行中的监控进程、移除 systemd 服务、删除安装目录及全部数据，系统依赖如 Python / git 保留）：

### Windows（PowerShell）

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/huigezhi/stock_tracking/main/uninstall.ps1 | iex"
```

### Ubuntu / Debian（本地安装版）

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/huigezhi/stock_tracking/main/uninstall.sh)
```

### VPS 服务器版（含 systemd 服务）

```bash
sudo bash uninstall.sh    # 自动探测 /opt/macd-monitor; 也可指定目录: sudo bash uninstall.sh /opt/macd-monitor
```

注意：卸载会删除 `config.json`（飞书 webhook 配置）、监控状态与底背离扫描缓存等全部数据，如需保留请先备份。

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

# 重跑今日底背离全量扫描(清除当日缓存, 保留其余29天历史)
python3 webui.py --rescan
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

| 字段                           | 说明                                        |
| ---------------------------- | ----------------------------------------- |
| `webhook_url`                | 飞书机器人 Webhook 地址，留空则不推送                   |
| `webhook_secret`             | 飞书机器人签名密钥，可选                              |
| `poll_interval_sec`          | 扫描间隔（秒），默认 30                             |
| `signal_on_forming_bar`      | 是否对未完成K线发信号（默认只确认已完成K线）                   |
| `feishu_log_level`           | 推送到飞书的日志级别，默认 WARNING                     |
| `feishu_status_interval_min` | 定时状态汇报间隔（分钟）                              |
| `timeframes`                 | 监控周期列表，可删减                                |
| `stocks`                     | 自选股列表（code / name / group），Web UI 中增删自动同步 |

## 目录结构

```
├── install.sh            # Ubuntu/Debian 一键安装（本地运行）
├── install.ps1           # Windows 一键安装（PowerShell，支持一行命令）
├── install.bat           # Windows 一键安装（cmd / 双击运行）
├── uninstall.sh          # Linux 一键卸载（终止进程/移除服务/删除目录）
├── uninstall.ps1         # Windows 一键卸载（PowerShell）
├── deploy.sh             # VPS 服务器部署（systemd 开机自启）
└── macd-monitor/
    ├── monitor.py            # MACD 监控 + 飞书推送
    ├── webui.py              # 自选管理 Web UI 后端
    ├── config.json           # 运行配置（自选股、webhook）
    ├── config.example.json   # 配置示例
    ├── state.json            # 信号去重状态
    ├── etf_share_hist.json   # ETF 份额日度快照（自动积累）
    ├── kline_cache.json      # 指数/宽基ETF 日K周K本地缓存（增量更新）
    ├── monitor.log           # 运行日志
    └── static/               # 前端静态文件
        ├── index.html
        ├── app.js
        └── style.css
```

## 说明

* 份额数据无免费公开历史接口，采用每日快照积累方式：系统运行期间每个交易日自动记录一次当前份额，保留最近 500 天，历史曲线随运行时间逐步完整

* 行情数据来自腾讯公开接口（`qt.gtimg.cn` / `ifzq.gtimg.cn`），主力净流入来自新浪接口，仅供个人参考，不构成投资建议

