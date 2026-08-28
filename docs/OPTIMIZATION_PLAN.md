# MACD Monitor 优化路线图（详细方案）

> 版本：v1（2026-08）
> 范围：macd-monitor 全项目（monitor.py 监控引擎 / webui.py 服务端 / static 原生前端）
> 说明：所有功能按优先级分四个阶段，每项含【背景 → 详细设计 → 涉及文件 → 验收标准 → 风险】。工作量标注为 ★（低）\~ ★★★★★（高）。

***

## 0. 现状盘点

### 0.1 架构与数据流

```
腾讯行情接口(主) ─┐
新浪K线接口(备) ─┼─> monitor.py ──> 飞书webhook通知
                 │     │
                 │     └──> state.json(信号去重)
                 │
                 └─> webui.py(ThreadingHTTPServer)
                       ├── /api/quotes    实时行情(5s轮询)
                       ├── /api/kline    K线(指数/ETF/自选走kline_cache.json本地缓存)
                       ├── /api/etf/*    宽基ETF列表/份额历史
                       ├── /api/divergences 底背离扫描结果(div_hist.json, 29天)
                       ├── /api/stocks   自选增删(config.json)
                       └── static/ 原生JS+Canvas三栏SPA(无构建/无框架)
```

### 0.2 核心模块

| 模块      | 位置                                      | 职责                                |
| ------- | --------------------------------------- | --------------------------------- |
| 行情/K线拉取 | webui.py `fetch_kline` / `fetch_quotes` | 腾讯主源三级回退 + 新浪兜底                   |
| K线本地缓存  | webui.py `get_kline_cached`             | 指数/ETF/自选日K周K增量缓存                 |
| MACD 计算 | monitor.py L143-171                     | 标准 MACD(12,26,9)，金叉死叉检测           |
| 底背离检测   | monitor.py L174-222                     | DIF 局部极值 + 价格/DIF 反向关系            |
| 全市场扫描   | webui.py `_div_scanner`                 | 每交易日16:00后全A扫描，结果存 div\_hist.json |
| 飞书通知    | monitor.py L263-412                     | HMAC 签名卡片消息                       |

### 0.3 已知局限（本方案要解决的）

1. **信号只发现、不评估**：有"后3/5周期涨幅"列但无胜率统计、无参数寻优
2. **存储层薄弱**：4个 JSON 文件全量读写，无事务、无历史切片查询能力
3. **K线图与主题脱节**：叫 MACD monitor 但 K 线图没有 MACD 副图，背离不可视
4. **无认证**：webui.py 默认 `0.0.0.0` 裸奔，自选可被任意增删
5. **轮询架构**：多标签页各自 5s 轮询，浪费且易触发数据源限流
6. **无盘中信号**：背离扫描只在收盘后跑一次
7. **单机单用户**：无多用户、无配置界面、无移动端 App 体验

***

## 阶段 P0：安全与核心体验（建议最先做）

### 1. Web UI 认证与访问控制 ★★

**背景**：README 建议绑定 127.0.0.1 + SSH 隧道，但默认 `0.0.0.0`。公网部署时任何人可增删自选、查看持仓级信息。

**详细设计**：

* config.json 新增：

  ```json
  {
    "webui": {
      "auth_token": "<32位随机串，为空则不启用>",
      "bind": "127.0.0.1"
    }
  }
  ```

* 认证方式（二选一或都做）：

  * **方案A · Bearer Token**：首次访问弹登录框输入 token，存 localStorage；所有 `/api/*` 请求带 `Authorization: Bearer <token>`，静态资源放行

  * **方案B · HttpOnly Cookie**：`POST /api/login` 校验后下发签名 Cookie（防 XSS 比 localStorage 好）

* 服务端：`Handler` 基类加统一 `self._authed()` 检查；失败返回 401

* 附加：连续失败 5 次锁定 IP 60 秒（内存计数即可）

* `deploy.sh` / `install.sh` 安装时提示生成 token 并写入 config

**涉及文件**：webui.py（Handler.do\_GET/do\_POST/do\_DELETE 入口处）、static/app.js（fetch 封装统一注入头 + 401 跳登录）、config.example.json

**验收**：无 token 访问 API 返回 401；带 token 正常；静态页面可加载但接口拒绝。

**风险**：低。注意 WebSocket/SSE 升级（P2）时也要带凭证。

***

### 2. K线图 MACD 副图 + 背离可视化 ★★★（性价比最高）

**背景**：项目核心是 MACD 背离，但中栏 K 线图只有均线+成交量，背离信号在图上不可见，用户要靠脑补。

**详细设计**：

**(a) MACD 副图**（放 K 线主图与成交量之间，或替换成交量区下方）：

* 前端已有 DIF/DEA 计算能力吗？——没有，需新增 `calcMacd(closes)` 返回 `{dif, dea, hist}`（EMA12-EMA26, DEA=EMA9(DIF), HIST=2\*(DIF-DEA)）

* 布局改造：`KChart.draw()` 由两段（K线+成交量）改为三段，比例 `0.62 / 0.16 / 0.22`（K线 / MACD / 成交量），MACD 副图与主图共享 X 轴窗口 `this.win`

* MACD 副图绘制：DIF 白线、DEA 黄线、柱状图红绿（与蜡烛配色一致），零轴虚线

* 十字光标联动显示当日 DIF/DEA/HIST 值（右上角小标签）

**(b) 背离可视化**：

* 后端 `/api/divergences` 的 rows 已含背离两个低点的日期区间（`d1/d2` 及对应 DIF 值），需扩展：新增字段 `kline_marks`，或者前端直接请求 `/api/kline` 后用已有背离数据按日期匹配

* 前端绘制：

  * 价格低点之间画一条**趋势线**（点击背离行时高亮/闪烁）

  * MACD 副图 DIF 低点之间画对应连线（两条线方向相反即为背离的可视化证明）

  * 低点画圆圈标记，背离成立区间背景加淡色遮罩

* 交互：底背离表格点击行 → `KChart.locate(divRange)`：自动把窗口滚动到背离区间、高亮连线，2 秒后恢复

**(c) 副图指标切换**：

* 副图 tab：`MACD | 成交量 | KDJ | RSI`（复用同一副图渲染区）

* KDJ/RSI 各写一个纯计算函数（\~30 行/个）

**涉及文件**：static/app.js（KChart 大改：calcMacd/draw 布局/locate）、webui.py（背离数据补 DIF 极值点坐标字段）、static/style.css（副图 tab 样式）

**验收**：中栏显示 MACD 副图且与主图窗口联动缩放；点击底背离表格行，K 线图定位到背离区间并画出两条背离连线。

**风险**：KChart.draw 已 280+ 行，注意拆函数（drawMain/drawMacd/drawVol/drawCrosshair）避免失控；纯前端改动可独立分支开发。

***

### 3. 信号统计面板（胜率复盘） ★★★★

**背景**：底背离表格展示"后3/5周期涨幅"，但用户无法回答"这套参数的信号历史上到底赚不赚钱"。这是工具从"发现信号"到"评估信号"的关键跃迁。

**详细设计**：

**(a) 数据准备——信号跟踪表**：

* 现有 `div_hist.json` 只存 29 天扫描结果且信号里的"后N周期涨幅"依赖后续 K 线（信号发出时不存在）。需要**事后回填**机制：

  * 新表 `signal_track.json`（或 SQLite，见 P2-6）：每个信号记录 `{code, tf, confirm_date, d1_price, d2_price, fwd_3/5/10/20/60_ret, max_drawdown_20, hit_tp, hit_sl}`

  * 回填线程：每日收盘扫描后，对所有 `confirm_date` 已满 N 日的未回填信号拉 K 线补全（自选走缓存，非自选按需拉）

  * 保留期从 29 天延长到 2 年（只存信号不存 K 线，体量可控：全A日线背离每天约 20-60 条 × 500 交易日 ≈ 3 万条，JSON 可承受，SQLite 更稳）

**(b) 统计维度**（新页面或右栏 tab"复盘"）：

* 总览卡片：样本数、5/10/20/60 日**胜率**（收益>0 占比）、**平均/中位收益**、**盈亏比**、最大回撤分布

* 分层统计：按周期（日/周）、按 DIF 增加值分位、按背离持续天数、按是否自选、按当时均线趋势（20日线上/下）

* 时间序列图：按月分组的胜率热力图（判断策略是否失效）

* Top/Bottom 个案列表：最赚/最亏的 10 个信号（点击看 K 线复盘——与 P0-2 的 locate 联动）

**(c) 参数寻优（二期）**：

* 将 monitor.py 的背离参数（极值间隔、确认根数、价格幅度阈值）参数化提取到 config

* 回测脚本 `backtest.py`：用缓存 K 线网格搜索参数组合，输出各组合胜率/收益表格，写入报告

* 注意标注过拟合风险：用 2023-2024 训练、2025-2026 验证的 walk-forward 方式

**涉及文件**：webui.py（signal\_track 读写 + 回填线程 + `/api/stats` 聚合接口）、static/app.js + index.html（复盘面板）、新增 backtest.py（二期）

**验收**：复盘面板展示近一年日/周线底背离分周期胜率、平均收益；数字与抽样人工核对一致。

**风险**：前视偏差——回填涨幅必须用确认日之后的 K 线；除权问题——用前复权 K 线（缓存已是 qfq）。

***

## 阶段 P1：信号质量与数据层

### 4. 多指标共振过滤与信号评分 ★★★

**背景**：底背离是必要非充分条件，大量信号是下跌中继。参考问财"指标共振"，叠加过滤可显著减少噪音。

**详细设计**：

* 扫描时对每个背离信号附加**共振标签数组**，全部可配置开关：

  | 标签       | 条件                    | 权重示例 |
  | -------- | --------------------- | ---- |
  | 缩量       | 背离第二低点成交量 < 第一低点的 70% | +2   |
  | 均线托底     | 收盘价站上 20 日线或 20 日线走平  | +2   |
  | RSI 超卖修复 | RSI14 从 <30 回升至 30-50 | +1   |
  | KDJ 金叉   | 确认日附近 5 根内 KDJ 金叉     | +1   |
  | 周线同向     | 周线 DIF 也在低位上行         | +2   |
  | 放量反包     | 确认日阳线放量吞没前阴           | +1   |

* 每个信号算 `score = Σ权重`，前端表格新增"共振分"列（可排序），并用小徽章展示命中的标签（hover 看每个标签含义）

* config 可调每项权重与开关；统计面板（P0-3）按 score 分桶看胜率，反过来校准权重——**形成"信号→评分→统计→调参"闭环**

* 飞书通知卡片同步带上共振标签

**涉及文件**：monitor.py（共振计算，扫描时顺带算，不额外拉数据）、webui.py（`_div_scanner` 扩展）、前端表格、config

**验收**：新扫描结果带 score 与标签；统计面板能按 score 分桶对比胜率。

***

### 5. SQLite 存储层迁移 ★★★

**背景**：`div_hist.json`/`kline_cache.json`/`etf_share_hist.json`/`state.json` 全量读写，无事务；kline\_cache 单文件已 44KB，全A自选扩展后膨胀；JSON 无法做时间范围查询，制约 P0-3。

**详细设计**：

* 单文件 `data.db`（SQLite, WAL 模式），表结构：

  ```sql
  CREATE TABLE kline(code TEXT, tf TEXT, date TEXT, o REAL, c REAL,
                     h REAL, l REAL, v REAL, PRIMARY KEY(code, tf, date));
  CREATE TABLE div_signal(id INTEGER PRIMARY KEY, code TEXT, tf TEXT,
                     confirm_date TEXT, d1 TEXT, d2 TEXT, score REAL,
                     tags TEXT, created_at TEXT);          -- 背离信号
  CREATE TABLE signal_track(signal_id INTEGER, fwd_n INT, ret REAL,
                     mdd REAL, FOREIGN KEY(signal_id) REFERENCES div_signal(id));
  CREATE TABLE etf_share(code TEXT, date TEXT, shares REAL, PRIMARY KEY(code, date));
  ```

* 迁移策略：启动时检测旧 JSON 存在则一次性导入并改名 `.bak`；写路径逐表替换（kline 最后迁，读多写少风险低）

* 保留 `load_json/save_json` 兼容 config.json / state.json（小文件不值得迁）

* Python 标准库 sqlite3 即可，无新依赖；连接放线程局部变量（ThreadingHTTPServer 多线程）

**涉及文件**：webui.py（新增 db.py 模块更清晰：`db.py` 封装连接池+迁移+DAO）

**验收**：功能全部不变；`sqlite3 data.db "SELECT COUNT(*) FROM kline"` 与原 JSON 条数一致；写入过程 kill 进程不损坏（WAL）。

**风险**：迁移代码路径多，建议逐表分 PR；kline\_cache 迁移后 `.gitignore` 改为 `data.db`。

***

### 6. 数据源容灾与请求治理 ★★

**背景**：`fetch_quotes` 单点失败整列 `--`；全市场扫描对腾讯接口瞬时并发大；被封 IP 后全线瘫痪。

**详细设计**：

* 行情批量接口加新浪备用源（`hq.sinajs.cn`，注意 Referer 头）+ 指数备用东财

* 全局 `RateLimiter`（令牌桶，如 8 req/s），所有出网请求走它

* 请求级熔断：某域名连续 10 次失败 → 冷却 5 分钟切备用源，恢复后探测回切

* 数据源健康状态暴露到 `/api/health`（见 P3-11）

* 扫描线程已有的失败重试保留，增加指数退避

**涉及文件**：webui.py（S 会话封装处）、monitor.py（共享限流器，可提取 `net.py` 公共模块）

**验收**：手动断网/改错域名模拟故障，行情列 30 秒内从备用源恢复；日志有熔断/回切记录。

***

### 7. 盘中实时背离预览 ★★★

**背景**：背离扫描只在收盘后跑，信号滞后一天。盘中"背离雏形"对盯盘用户价值大。

**详细设计**：

* **只对自选股做盘中监控**（全A盘中扫描会被限流）：webui 启动自选盘中扫描线程，交易日 09:35-15:00 每 5 分钟拉自选 60 分钟 K 线，跑同一套背离检测（参数可另配，盘中确认根数收紧防假信号）

* 命中"预览信号"：页面右栏自选行加脉冲角标，飞书发低优先级卡片（标题注明"盘中预览，待收盘确认"）

* 收盘扫描时用日线正式结果覆盖盘中预览，去重逻辑复用 state 机制但加 `provisional` 标记

* 前端：底背离表格加"来源"徽章 `盘中/收盘`

**涉及文件**：monitor.py（检测函数复用）、webui.py（新线程 + 配置）、飞书卡片、前端徽章

**验收**：盘中自选触发 60 分钟背离后 5 分钟内收到飞书预览推送；收盘正式扫描后预览标记被替换。

**风险**：盘中信号噪音大——必须低优先级推送 + 统计面板单独分桶评估其胜率（接 P0-3）。

***

## 阶段 P2：架构与体验升级

### 8. 轮询改 SSE 推送 + 统一行情调度 ★★★

**背景**：`setInterval(refreshQuotes, 5000)` × N 个标签页 = N 倍请求；扫描进度无实时反馈。

**详细设计**：

* 服务端：

  * 新增 `QuoteHub`：单线程每 3 秒拉一次所有需要的行情（自选 + 当前左栏列表去重合并），diff 后向所有 SSE 订阅者广播

  * `GET /api/stream`（`text/event-stream`）：ThreadingHTTPServer 下每个连接占一个线程，注意设 `timeout` + 心跳（每 15s 发 `:ping\n\n`）防僵尸连接；浏览器并发上限 6 个 SSE 连接（HTTP/1.1），单页只开 1 条

  * 扫描进度事件：`_div_scanner` 每完成 5% push `{"type":"scan_progress","done":843,"total":5321}`，前端显示进度条

* 前端：`EventSource('/api/stream')` 替换两个 setInterval；断线自动重连（EventSource 原生支持）+ 重连后全量刷新一次

* 兼容：保留现有轮询 API 作为降级路径（老书签/第三方脚本）

**涉及文件**：webui.py（Hub + /api/stream + Handler 改造）、app.js（EventSource 消费、进度条 UI）

**验收**：开 3 个标签页，外部抓包确认行情请求只有 1 份；扫描时前端实时进度条。

**风险**：ThreadingHTTPServer 每连接一线程，并发连接 >50 需换 asyncio 或接受现状（自用工具可接受）；SSE 需过认证（P0-1 的 Cookie 方案更顺）。

***

### 9. 前端工程化重构（渐进式） ★★★

**背景**：app.js 已 900+ 行单文件，原生 JS 无组件化，新功能（复盘面板/画线工具）继续堆会失控。但引入 React+Vite 全家桶对单用户工具过重。

**详细设计**（渐进三步，每步可独立停止）：

1. **第一步（零依赖）**：app.js 按模块拆分 `static/js/{chart,watchlist,divergence,stats,net}.js`，ES Module 原生 `<script type="module">`；Canvas 图表类拆文件。浏览器直下无需构建
2. **第二步（轻构建）**：引入 esbuild 单命令打包（0 配置），获得压缩与浏览器兼容；加 `prettier` 统一格式
3. **第三步（按需）**：若 UI 复杂度持续增长再评估 Preact（3KB）而非 React；图表库评估 ECharts/KLineCharts 替换自绘 K 线（自绘的优势是包体零依赖+完全可控，换库前先用 P0-2 验证自绘是否能满足）

**原则**：不为重构而重构，每个新功能开发时顺手拆所在模块。

***

### 10. 移动端 PWA ★★

**背景**：有响应式断点但真机体验一般；手机盯盘是最常用场景。

**详细设计**：

* `manifest.json`（name/icons/theme\_color/standalone）

* Service Worker：静态资源 cache-first；API 不缓存；离线时展示"上次快照"（localStorage 存最近一次行情/自选/背离表）

* 移动端专项优化：三栏改单栏 tab 切换（行情/K线/自选）；K 线图触摸手势——单指拖动平移、双指缩放、长按出十字光标（替代滚轮+hover）；底部安全区适配

* iOS 添加到主屏幕引导页（首次访问检测 `navigator.standalone`）

**涉及文件**：static/（manifest/sw\.js/app.js 触摸事件/KChart 手势）、webui.py（sw/manifest MIME）

**验收**：手机添加到主屏后全屏无地址栏；断网打开能看到上次数据并提示离线。

***

### 11. 可观测性与运维 ★★

**背景**：VPS 上跑着扫描线程+通知，出问题只能翻 monitor.log。

**详细设计**：

* `GET /api/health`：`{uptime, data_sources:{tencent:{ok,latency},sina:{...}}, cache:{kline_codes,kb}, scan:{today_done,total,last_ts}, feishu:{last_send_ts,last_err}, errors_24h}`——uptime 监控（UptimeRobot 等）直接可用

* 日志结构化：`logging` 换 JSON lines（`{"ts","lvl","mod","msg","code"...}`），`/api/logs?mod=scan&lvl=WARN&limit=100` 页面内查日志

* 前端"系统"小页：缓存命中率、今日扫描信号数、飞书最近发送时间、当前配置只读视图

* deploy.sh 增加 `--update` 幂等更新命令；systemd 增加 `Restart=on-failure`

***

## 阶段 P3：大胆想象（差异化能力）

### 12. AI 集成：自然语言选股 + 每日智能复盘 ★★★★

**背景**：问财证明了"一句话选股"的需求；本项目已有全市场数据底座（all\_stocks.json + K线缓存 + 背离扫描），接 LLM 可低成本复刻并超越（因为指标口径自控）。

**详细设计**：

* **两段式架构**（不靠 LLM 算指标，只靠它"翻译"）：

  1. LLM 把自然语言解析成**结构化筛选 DSL**：

     ```json
     {"period":"day","conditions":[{"ind":"macd_divergence","side":"bottom"},
      {"ind":"vol_ratio","op":"<","val":0.7},{"ind":"above_ma","n":20},
      {"ind":"mcap","op":"<","val":500}],"sort_by":"score"}
     ```
  2. 本地引擎执行 DSL（复用扫描器与缓存），LLM 完全不接触行情数据——**杜绝幻觉数字**

* 查询示例："日线底背离且缩量、市值500亿以下、站上20日线，按共振分排序"

* **每日智能复盘**：收盘后把当日市场概况（指数涨跌、板块热点、自选异动、新信号列表）+ 昨日预判对照喂给 LLM，生成 300 字复盘卡片推送飞书

* 模型接入：OpenAI 兼容接口（DeepSeek/通义/Kimi 均可），API key 进 config，无 key 时功能隐藏不影响主流程

**风险**：外部 API 依赖与费用（日复盘约 2 万 token/天，成本几厘钱）；DSL 解析错误需给出澄清反问。

### 13. 组合与仓位跟踪 ★★★

* 持仓表：`{code, name, cost, shares, opened_at, note}`，前端右栏新 tab"持仓"

* 与信号打通：持仓股在底背离表格高亮"持有中"；买入时可选关联当时的信号 ID

* 盈亏视图：当日/累计盈亏、按持仓集中度的小饼图

* **信号→建仓→平仓全链路复盘**：平仓时自动回看"入场信号 score → 持有天数 → 实际收益"，反哺统计面板（P0-3 的实盘验证维度）

### 14. 市场情绪面板 ★★

* 涨跌家数/涨停跌停梯队/连板高度（东财接口）、两市成交额趋势、北向资金（若仍可得）、主要指数 20 日波动率

* 前端顶部一条情绪带：绿红比例条 + 涨停数 + 简单情绪分（0-100）

* 与信号联动：统计面板可按当日情绪分分桶看背离信号胜率（"冰点期背离更有效？"可验证）

### 15. ETF 专题增强 ★★

* **溢价率监控**：ETF 场内价 vs IOPV 溢价超过阈值告警（套利/避坑）；LOF 同理

* **轮动策略回测**：基于已缓存的宽基 ETF 日线，回测经典动量轮动（如 20 日动量 top1 持有），输出净值曲线与年度收益——和 P0-3 共用回测骨架

* 份额异动提醒：现有 etf\_share\_hist 数据上做环比突变检测，大额申赎推送飞书（机构动向代理指标）

### 16. 多通道通知 ★

* 通知抽象层 `notifier.py`：飞书/钉钉/企微/Telegram/邮件(Bark 亦可) 插件式注册

* 分级路由：正式信号→全通道；盘中预览→仅页面角标；系统告警→管理员通道

* 免打扰时段配置（如 22:00-08:00 仅页面）

### 17. 数据分享与导出 ★

* 底背离表格导出 CSV（前端 Blob 即可）

* 信号分享卡片：Canvas 生成 K 线+背离连线的 PNG（复用 P0-2 绘制能力），一键发飞书/保存

* 分享链接：`?code=xx&tf=day&div=d1~d2` 打开即定位（纯前端解析，无服务端状态）

***

## 里程碑建议

| 里程碑       | 内容                     | 交付判据                      |
| --------- | ---------------------- | ------------------------- |
| M1（P0）    | 认证 + MACD副图/背离可视化      | 公网部署安全；背离在图上可见可定位         |
| M2（P0+P1） | 信号统计面板 + 共振评分 + SQLite | 能回答"这信号胜率多少"；按score调参闭环   |
| M3（P1+P2） | 盘中预览 + SSE + 数据容灾      | 秒级行情单份拉取；盘中信号实时到手机        |
| M4（P2）    | PWA + 工程化拆分 + 可观测性     | 手机主屏App体验；/api/health 接监控 |
| M5（P3）    | AI选股/复盘 + 组合跟踪         | 一句话选股落地；信号-持仓全链路复盘        |

## 全局技术决策记录（ADR 摘要）

1. **不引入重前端框架**：ES Module 渐进拆分优先，图表保持自绘（可控+零依赖），Preact 仅在失控时备选
2. **存储**：SQLite(WAL) 承接信号/K线/份额；config/state 留 JSON（人可读可手改）
3. **LLM 不算数**：AI 只做自然语言→DSL 翻译与文本生成，一切数字来自本地引擎
4. **兼容承诺**：现有轮询 API 全程保留为降级路径，SSE/SQLite 均平滑迁移（.bak 机制）
5. **每个信号功能必须进统计面板**：新信号类型（盘中/三次背离/隐性背离）上线时同步加统计分桶，用数据淘汰无效功能

