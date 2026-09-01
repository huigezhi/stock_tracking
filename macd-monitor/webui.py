#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MACD 监控自选管理 Web UI
- 左栏: 宽基ETF列表(份额>=100亿份, 实时计算), 点击查看日K线
- 中栏: K线走势图(MA均线/成交量/日期范围滑条)
- 右栏: MACD 监控列表(搜索/增删/实时行情)
前端为 static/ 下的静态文件, 后端提供 JSON API
用法: python3 webui.py  然后浏览器打开 http://localhost:8688
"""
import collections
import itertools
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

import requests

# 复用监控进程的 MACD/背离检测逻辑, 保证面板与推送口径一致
from monitor import (bar_complete, combine_resonance, detect_divergences,
                     fetch_klines, is_trading_time, now_cst,
                     recent_tf_signals, send_feishu_text)
from monitor import macd as calc_macd
import db
import model as ml_model
import net
import obs
from net import robust_get, fetch_quotes_any

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
STATIC_DIR = os.path.join(BASE, "static")
PORT = 8688

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})

# 搜索结果类型标识 -> 中文
TYPE_NAME = {
    "GP-A": "A股", "GP-B": "B股", "GP-KCB": "科创板", "GP-CYB": "创业板",
    "ZS": "指数", "ETF": "ETF", "LOF": "LOF", "FJ": "分级基金",
    "HK": "港股", "US": "美股",
}

# 宽基ETF候选清单(按指数归类), 运行时按实际份额(>=100亿份)筛选
BROAD_ETF_CANDIDATES = {
    "sh510050": "上证50", "sh510180": "上证180", "sh510210": "上证指数",
    "sh510300": "沪深300", "sh510310": "沪深300", "sz159919": "沪深300", "sz159673": "沪深300",
    "sh510500": "中证500", "sz159922": "中证500",
    "sh512100": "中证1000", "sz159845": "中证1000", "sz159629": "中证1000",
    "sh563300": "中证2000", "sz159531": "中证2000",
    "sh563360": "中证A500", "sz159352": "中证A500", "sz159361": "中证A500",
    "sh512050": "中证A500", "sz159338": "中证A500",
    "sz159915": "创业板指", "sz159949": "创业板50",
    "sh588000": "科创50", "sh588080": "科创50",
    "sh588800": "科创100",
    "sz159780": "双创50", "sh588400": "双创50",
    "sz159901": "深证100", "sz159879": "北证50",
}
MIN_SHARES = 100  # 亿份

# 主要指数列表(左栏展示, 点击查看K线)
INDEX_LIST = {
    "sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指",
    "sh000688": "科创50", "bj899050": "北证50",
}
_INDEX_CACHE = {"ts": 0, "data": []}

CFG_LOCK = threading.Lock()
SHARE_HIST_PATH = os.path.join(BASE, "etf_share_hist.json")
SHARE_LOCK = threading.Lock()
SNAP_KEEP_DAYS = 500   # 每只ETF保留的日度份额快照数


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def load_cfg():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"stocks": []}


def save_cfg(cfg):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def search_suggest(q):
    """调用腾讯智能搜索框接口, 返回 [{code, name, type}, ...]"""
    q = q.strip()
    if not q:
        return []
    try:
        r = robust_get(f"https://smartbox.gtimg.cn/s3/?q={quote(q)}&t=all", timeout=8)
        import codecs
        m = re.search(r'v_hint="([^"]*)"', r.text)
        if not m:
            return []
        hint = m.group(1)
        # 接口返回的中文名是 \uXXXX 转义文本, 需解码为正常中文
        if "\\u" in hint:
            try:
                hint = codecs.decode(hint, "unicode_escape")
            except Exception:
                pass
        out, seen = [], set()
        for item in hint.split("^"):
            parts = item.split("~")
            if len(parts) < 5:
                continue
            mkt, code, name, _py, typ = parts[0], parts[1], parts[2], parts[3], parts[4]
            full = mkt + code
            if full in seen:
                continue
            seen.add(full)
            out.append({"code": full, "name": name, "type": TYPE_NAME.get(typ, typ)})
        return out[:20]
    except Exception:
        return []


# ---------------- 实时行情 ----------------

def is_index_code(code):
    """sh000xxx / sz399xxx 为指数"""
    return code.startswith("sh000") or code.startswith("sz399")


def fetch_quotes(codes):
    """批量实时行情(腾讯主源 + 新浪备源容灾), 返回 {code: {name, price, chg, chg_pct, volume, amount, mcap}}"""
    return fetch_quotes_any(codes)


_FLOW_CACHE = {}   # code -> (timestamp, 主力净流入元|None)
FLOW_TTL = 30     # 缓存秒数; 后台线程对SSE订阅代码持续刷新, 前端近实时


def fetch_flow(code):
    """新浪资金流向: 当日主力净流入(元), 缓存FLOW_TTL秒; 指数/无数据返回None"""
    now = time.time()
    c = _FLOW_CACHE.get(code)
    if c and now - c[0] < FLOW_TTL:
        return c[1]
    val = None
    try:
        url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               f"MoneyFlow.ssl_qsfx_lscjfb?page=1&num=1&sort=opendate&asc=0&daima={code}")
        r = robust_get(url, timeout=8, headers={"Referer": "https://finance.sina.com.cn"})
        data = json.loads(r.text)
        if data:
            val = float(data[0].get("netamount") or 0)
    except Exception:
        pass
    _FLOW_CACHE[code] = (now, val)
    return val


def _quote_item(code, q):
    """单条行情组装(build_quotes与/api/quotes?codes=共用)"""
    item = {"code": code, "ok": bool(q)}
    if q:
        item.update({"name": q["name"], "price": q["price"],
                     "chg": q["chg"], "chg_pct": q["chg_pct"],
                     "volume": q["volume"], "amount": q["amount"],
                     "is_index": is_index_code(code)})
    return item


def build_quotes(stocks):
    """组装监控列表的实时数据"""
    codes = [s.get("code", "") for s in stocks]
    quotes = fetch_quotes(codes)
    result = []
    for s in stocks:
        code = s.get("code", "")
        item = _quote_item(code, quotes.get(code))
        if item["ok"]:
            # 股票/ETF取主力净流入; 指数无资金流数据
            item["flow"] = None if item["is_index"] else fetch_flow(code)
        result.append(item)
    return result


# ---------------- SSE 行情推送(QuoteHub) ----------------
# 单线程每3秒拉一次订阅代码的行情(自选+左栏列表去重合并), diff后广播给所有SSE订阅者;
# 扫描线程每完成5%推送进度事件。替代前端各自轮询: N个标签页只有1份出网请求

HUB_QUOTE_SEC = 3        # 行情刷新间隔
HUB_HEARTBEAT_SEC = 15    # SSE 心跳间隔(防代理断连)

_hub_subs_lock = threading.Lock()
_hub_subs = {}   # client_id -> {"queue": deque, "codes": set, "alive": bool}
_hub_codes = set()   # 所有订阅者需要行情的代码并集
_hub_next_id = itertools.count(1)
_hub_last_quotes = {}   # code -> 上次广播的行情dict(做diff)


def hub_register(codes):
    """订阅: 返回 (client_id, queue); codes为该订阅者需要的行情代码集"""
    q = collections.deque(maxlen=50)
    cid = next(_hub_next_id)
    with _hub_subs_lock:
        _hub_subs[cid] = {"queue": q, "codes": set(codes), "alive": True}
        _recalc_hub_codes()
    return cid, q


def hub_unregister(cid):
    with _hub_subs_lock:
        _hub_subs.pop(cid, None)
        _recalc_hub_codes()


def hub_update_codes(cid, codes):
    """订阅者刷新其关注的代码集(如切换左栏列表/增删自选时)"""
    with _hub_subs_lock:
        sub = _hub_subs.get(cid)
        if sub is not None:
            sub["codes"] = set(codes)
            _recalc_hub_codes()


def _recalc_hub_codes():
    """重算并集(须持锁调用); 有新增代码时清空diff基准, 下轮立即全量推这些代码"""
    global _hub_codes
    new = set()
    for sub in _hub_subs.values():
        new |= sub["codes"]
    added = new - _hub_codes
    _hub_codes = new
    for code in added:
        _hub_last_quotes.pop(code, None)   # 新代码无diff基准 → 全量推


def _hub_push(event):
    """向所有订阅者队列投递事件(dict); 单订阅者队列满则丢最旧"""
    with _hub_subs_lock:
        for sub in _hub_subs.values():
            q = sub["queue"]
            q.append(event)
            while len(q) >= q.maxlen:
                q.popleft()


def _hub_quote_loop():
    """行情线程: 每3秒拉一次订阅代码的行情并广播diff(含主力净流入, 变化即推)"""
    while True:
        try:
            with _hub_subs_lock:
                codes = list(_hub_codes)
            if codes:
                quotes = fetch_quotes(codes)
                changed = {}
                for code, q in quotes.items():
                    if q and not is_index_code(code):
                        q = dict(q)
                        q["flow"] = _flow_cached(code)
                    if q != _hub_last_quotes.get(code):
                        changed[code] = q
                        _hub_last_quotes[code] = q
                if changed:
                    _hub_push({"type": "quotes", "data": changed})
            time.sleep(HUB_QUOTE_SEC)
        except Exception:
            time.sleep(5)


def _flow_cached(code):
    """读资金流缓存(不打网络); 无缓存返回None"""
    c = _FLOW_CACHE.get(code)
    return c[1] if c else None


def _hub_flow_loop():
    """资金流线程: 每10秒扫描订阅代码, 过期(>25s)的串行刷新, 避免阻塞行情线程"""
    while True:
        try:
            now = time.time()
            with _hub_subs_lock:
                codes = [c for c in _hub_codes if not is_index_code(c)]
            for code in codes:
                c = _FLOW_CACHE.get(code)
                if c and now - c[0] < FLOW_TTL - 5:
                    continue
                fetch_flow(code)
                time.sleep(0.15)
        except Exception:
            pass
        time.sleep(10)


def hub_scan_progress():
    """扫描线程调用: 每完成5%推送一次进度"""
    with DIV_LOCK:
        scanning, done, total = (DIV_SCAN["scanning"], DIV_SCAN["done"],
                                 DIV_SCAN["total"])
    if not scanning or not total:
        return
    pct = done * 100 // total
    if pct >= (getattr(hub_scan_progress, "_last", -1) + 5):
        hub_scan_progress._last = pct
        _hub_push({"type": "scan_progress", "done": done, "total": total, "pct": pct})
    if pct >= 100:
        hub_scan_progress._last = -1
        _hub_push({"type": "scan_done"})


# ---------------- 盘中实时背离预览(自选股60分钟线) ----------------
# 交易时段每5分钟对自选股的60分钟K线跑一轮底背离检测(基于已收盘60分钟线),
# 结果经SSE推给前端展示"盘中预览"徽章, 并以低优先级文本推送飞书(每信号每日一次);
# 非交易时段不扫描。预览信号带 provisional 标记, 收盘后由16:00全量扫描正式确认

INTRADAY_SEC = 300        # 交易时段扫描间隔(秒)
INTRADAY_RECENT = 60      # 只保留最近60根60分钟线内成立的预览信号
INTRADAY_TF = "60m"

INTRADAY_LOCK = threading.Lock()
INTRADAY_ROWS = []        # 最近一轮扫描到的预览信号(前端初始加载用)
INTRADAY_PUSHED = {}      # (code, date2) -> 上次推送日, 飞书去重


def _intraday_bars60(code, now):
    """拉取并修剪自选股的已收盘60分钟K线, 不足返回 None"""
    try:
        rows = fetch_klines(code, INTRADAY_TF, 320)
    except Exception:
        return None
    if len(rows) < 120:   # 60根窗口 + 极值确认窗口 + MACD预热下限
        return None
    last = len(rows) - 1
    if not bar_complete(INTRADAY_TF, rows[last][0], now):   # 剔除未收盘K线
        last -= 1
    if last < 120:
        return None
    return rows[:last + 1]


def _intraday_divs(code, name, bars):
    """基于已收盘60分钟线的底背离预览信号行"""
    last = len(bars) - 1
    closes = [c for _, c in bars]
    dif, _, _ = calc_macd(closes)
    out = []
    for d in detect_divergences(bars, dif, last, pairs=3):
        if d["div"] != "bull" or d["p2"] < last - INTRADAY_RECENT + 1:
            continue
        out.append({
            "code": code, "name": name, "tf": INTRADAY_TF,
            "date1": bars[d["p1"]][0], "date2": bars[d["p2"]][0],
            "price1": d["c1"], "price2": d["c2"],
            "dif1": round(d["d1"], 4), "dif2": round(d["d2"], 4),
            "confirm": bars[d["confirm"]][0],
            "provisional": True,
        })
    return out


def _intraday_scanner():
    """后台线程: 交易时段定期扫描自选股60分钟线底背离 + 多周期共振,
    SSE推送 + 飞书低优先级通知; 非交易时段低频维持共振快照"""
    while True:
        try:
            now = now_cst()
            if is_trading_time(now):
                _intraday_round(now)
                time.sleep(INTRADAY_SEC)
            else:
                # 非交易时段: 数据收盘后不变, 共振快照2小时校准一次
                if time.time() - RESONANCE_TS > RESONANCE_IDLE_SEC:
                    _resonance_round(now)
                time.sleep(60)
        except Exception:
            time.sleep(120)


def _intraday_round(now):
    """一轮盘中扫描: 60分钟背离预览(拉一次60m数据, 共振计算复用)"""
    today = now.strftime("%Y-%m-%d")
    with CFG_LOCK:
        stocks = load_cfg().get("stocks", [])
    fresh, alerts, res_rows = [], [], []
    for s in stocks:
        code, name = s.get("code", ""), s.get("name", "")
        bars60 = _intraday_bars60(code, now)
        if bars60 is not None:
            for r in _intraday_divs(code, name, bars60):
                fresh.append(r)
                key = (r["code"], r["date2"])
                if INTRADAY_PUSHED.get(key) != today:
                    INTRADAY_PUSHED[key] = today
                    alerts.append(r)
        res_rows.extend(_resonance_one(code, name, now, bars60))
    if len(INTRADAY_PUSHED) > 500:   # 防止长期运行无限膨胀
        for k in list(INTRADAY_PUSHED)[:250]:
            INTRADAY_PUSHED.pop(k, None)
    if alerts:
        lines = [
            f'[盘中预览] {r["name"]}({r["code"]}) 60分钟底背离 '
            f'价格{r["price1"]:.2f}→{r["price2"]:.2f} '
            f'DIF{r["dif1"]:.3f}→{r["dif2"]:.3f}(未收盘确认)'
            for r in alerts]
        try:
            with CFG_LOCK:
                cfg = load_cfg()
            send_feishu_text(cfg, "[MACD盘中预览·低优先级]\n" + "\n".join(lines))
        except Exception:
            pass
    with INTRADAY_LOCK:
        INTRADAY_ROWS[:] = fresh
    if fresh:
        _hub_push({"type": "intraday", "data": fresh})
    _set_resonance(res_rows)


# ---------------- 多周期共振(60分/日/周) ----------------
# 与 monitor.py 的全周期共振同一套算法(recent_tf_signals/combine_resonance),
# Web侧只覆盖 60分钟/日线/周线 三个慢周期(日/周走本地缓存不额外出网,
# 60m复用盘中线程已拉取的数据), 结果经SSE推送, 前端在自选行展示共振徽章。
# 飞书共振高优推送由 monitor.py 进程负责(覆盖全部已配置周期)

RESONANCE_LOCK = threading.Lock()
RESONANCE_ROWS = []        # 当前共振快照 [{code,name,dir,tfs,detail,close}]
RESONANCE_TS = 0.0         # 上次刷新时间戳
RESONANCE_IDLE_SEC = 7200  # 非交易时段刷新间隔(2小时)


def _resonance_one(code, name, now, bars60=None):
    """单只标的 60m/日/周 三周期共振检测, 返回共振行列表"""
    if not code:
        return []
    sigs = {}
    last_close = None
    # 60分钟线(优先复用盘中线程已拉取的已收盘数据)
    if bars60 is None:
        bars60 = _intraday_bars60(code, now)
    if bars60:
        closes = [c for _, c in bars60]
        dif, dea, _ = calc_macd(closes)
        sigs["60m"] = recent_tf_signals("60m", bars60, dif, dea, len(bars60) - 1)
        last_close = closes[-1]
    # 日线/周线(本地缓存, 不额外请求分钟线数据源)
    for tf in ("day", "week"):
        try:
            bars = get_kline_cached(code, tf, 400)
        except Exception:
            continue
        if not bars or len(bars) < 60:
            continue
        last = len(bars) - 1
        if not bar_complete(tf, bars[last][0], now):   # 剔除未收盘K线
            last -= 1
        if last < 60:
            continue
        bars2 = bars[:last + 1]
        closes = [c for _, c in bars2]
        dif, dea, _ = calc_macd(closes)
        sigs[tf] = recent_tf_signals(tf, bars2, dif, dea, last)
        if tf == "day":
            last_close = closes[-1]
    if last_close is None:
        return []
    order = {"60m": 0, "day": 1, "week": 2}
    rows = []
    for d in ("bull", "bear"):
        tfs = combine_resonance(sigs)[d]
        if len(tfs) < 2:
            continue
        ordered = sorted(tfs, key=lambda t: order.get(t, 9))
        rows.append({
            "code": code, "name": name, "dir": d, "tfs": ordered,
            "detail": [{"tf": t, "word": tfs[t]["word"], "ago": tfs[t]["ago"],
                        "label": tfs[t]["label"]} for t in ordered],
            "close": last_close, "updated": now.strftime("%Y-%m-%d %H:%M"),
        })
    return rows


def _resonance_round(now):
    """非交易时段的共振快照刷新(日线/周线/60m收盘后均不变, 低频校准)"""
    with CFG_LOCK:
        stocks = load_cfg().get("stocks", [])
    rows = []
    for s in stocks:
        rows.extend(_resonance_one(s.get("code", ""), s.get("name", ""), now))
    _set_resonance(rows)


def _set_resonance(rows):
    """更新共振快照并SSE广播(前端按 code+dir 去重提示新共振)"""
    global RESONANCE_TS
    with RESONANCE_LOCK:
        RESONANCE_ROWS[:] = rows
        RESONANCE_TS = time.time()
    if rows:
        _hub_push({"type": "resonance", "data": rows})


# ---------------- 宽基ETF列表 ----------------

_ETF_CACHE = {"ts": 0, "data": []}


def record_share_snapshots(etf_list):
    """将当日份额快照写入 etf_share_hist.json (每日一条, 盘中覆盖为最新值),
    随运行时间自然积累日度份额序列, 用于面板展示每日/每周份额变动"""
    today = time.strftime("%Y-%m-%d")
    with SHARE_LOCK:
        hist = load_json(SHARE_HIST_PATH, {})
        changed = False
        for e in etf_list:
            code, shares = e.get("code"), e.get("shares")
            if not code or not shares:
                continue
            days = hist.setdefault(code, {})
            if days.get(today) != shares:
                days[today] = shares
                changed = True
            if len(days) > SNAP_KEEP_DAYS:  # 只保留最近N天
                for k in sorted(days)[:-SNAP_KEEP_DAYS]:
                    days.pop(k, None)
                    changed = True
        if changed:
            tmp = SHARE_HIST_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(hist, f, ensure_ascii=False)
            os.replace(tmp, SHARE_HIST_PATH)


def index_list():
    """主要指数列表: 行情缓存10分钟"""
    now = time.time()
    if _INDEX_CACHE["data"] and now - _INDEX_CACHE["ts"] < 600:
        return _INDEX_CACHE["data"]
    quotes = fetch_quotes(list(INDEX_LIST.keys()))
    out = []
    for code, name in INDEX_LIST.items():
        q = quotes.get(code)
        if not q or not q["price"]:
            continue
        out.append({"code": code, "name": name,
                    "price": q["price"], "chg": q["chg"], "chg_pct": q["chg_pct"],
                    "amount": q["amount"], "volume": q["volume"]})
    if out:
        _INDEX_CACHE["ts"], _INDEX_CACHE["data"] = now, out
    return out


def broad_etf_list():
    """宽基ETF列表: 候选清单中份额>=100亿份的, 缓存10分钟"""
    now = time.time()
    if _ETF_CACHE["data"] and now - _ETF_CACHE["ts"] < 600:
        return _ETF_CACHE["data"]
    quotes = fetch_quotes(list(BROAD_ETF_CANDIDATES.keys()))
    out = []
    for code, idx_name in BROAD_ETF_CANDIDATES.items():
        q = quotes.get(code)
        if not q or not q["price"]:
            continue
        shares = q["mcap"] / q["price"]  # 份额(亿份) = 总市值 / 价格
        if shares < MIN_SHARES:
            continue
        out.append({"code": code, "name": q["name"], "index": idx_name,
                    "price": q["price"], "chg": q["chg"], "chg_pct": q["chg_pct"],
                    "amount": q["amount"], "shares": round(shares)})
    out.sort(key=lambda x: -x["shares"])
    if out:
        _ETF_CACHE["ts"], _ETF_CACHE["data"] = now, out
        record_share_snapshots(out)
    return out


# ---------------- K线数据 ----------------

def _sina_kline(code, tf, n):
    """新浪日K回退(腾讯对北证指数等仅返回当日1根时使用); tf=week时拉日线按ISO周聚合"""
    need = n * 5 if tf == "week" else n
    try:
        r = robust_get("https://money.finance.sina.com.cn/quotes_service/api/"
                  f"json_v2.php/CN_MarketData.getKLineData?symbol={code}"
                  f"&scale=240&ma=no&datalen={min(need, 1023)}",
                  timeout=10, headers={"Referer": "https://finance.sina.com.cn"})
        rows = json.loads(r.text)
        bars = [[x["day"], float(x["open"]), float(x["close"]),
                 float(x["high"]), float(x["low"]), float(x["volume"])] for x in rows]
        if tf == "week":
            out, cur_key = [], None
            for b in bars:
                key = datetime.strptime(b[0], "%Y-%m-%d").isocalendar()[:2]
                if key != cur_key:
                    out.append(b)
                    cur_key = key
                else:  # 同周合并开高低收
                    last = out[-1]
                    last[2] = b[2]                       # 收盘取最后
                    last[3] = max(last[3], b[3])          # 最高
                    last[4] = min(last[4], b[4])          # 最低
                    last[5] += b[5]                       # 成交量累加
            return out[-n:] if n else out
        return bars
    except Exception:
        return []


def fetch_minute(code):
    """当日分时走势(腾讯minute接口), 返回 {rows, prev_close, date}
    rows: [["0930", 价格, 该分钟成交量(手)], ...]"""
    empty = {"rows": [], "prev_close": None, "date": ""}
    try:
        r = robust_get(f"https://ifzq.gtimg.cn/appstock/app/minute/query?code={code}",
                       timeout=8)
        node = r.json()["data"][code]
        rows = []
        for line in (node.get("data", {}) or {}).get("data") or []:
            p = line.split()
            # 15:00后为盘后固定价格交易/填充(价格不变), 分时图只展示连续竞价时段
            if len(p) >= 3 and p[0] <= "1500":
                rows.append([p[0], float(p[1]), int(float(p[2]))])
        # 接口第3列是当日累计成交量(手), 差分为单分钟成交量
        if len(rows) > 1 and all(
                rows[i][2] >= rows[i - 1][2] for i in range(1, len(rows))):
            for i in range(len(rows) - 1, 0, -1):
                rows[i][2] = rows[i][2] - rows[i - 1][2]
        prev = None
        try:
            prev = float(node["qt"][code][4])
        except Exception:
            pass
        return {"rows": rows, "prev_close": prev,
                "date": (node.get("data", {}) or {}).get("date", "")}
    except Exception:
        return empty


def fetch_kline(code, tf="day", n=800):
    """腾讯前复权K线(失败回退非复权, 再回退kline接口, 最后新浪日K聚合), 返回 [[date, open, close, high, low, volume], ...]"""
    n = max(60, min(int(n or 800), 800))
    tf = tf if tf in ("day", "week") else "day"
    urls = [
        f"https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},{tf},,,{n},qfq",
        f"https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},{tf},,,{n},",
        f"https://ifzq.gtimg.cn/appstock/app/kline/kline?param={code},{tf},,,{n}",
    ]
    for url in urls:
        try:
            r = robust_get(url, timeout=10)
            data = r.json().get("data", {}).get(code, {})
            rows = data.get(f"qfq{tf}") or data.get(tf)
            if rows and len(rows) > 5:   # 数据量过少视为无效(如北证指数仅返回当日1根)
                return [[row[0], float(row[1]), float(row[2]),
                         float(row[3]), float(row[4]), float(row[5])] for row in rows]
        except Exception:
            continue
    return _sina_kline(code, tf, n)


# ---------------- 左栏指数/宽基ETF K线本地缓存 ----------------
# 历史K线持久化到 kline_cache.json(VPS本地), 增量只补最新交易日的数据, 加快加载;
# 只更新到最新交易日收盘价, 周末等缓存已含最近交易日时不发起任何网络请求

KLINE_CACHE_PATH = os.path.join(BASE, "kline_cache.json")
KLINE_LOCK = threading.Lock()
_KC = {"data": {}, "loaded": False}
KLINE_KEEP = 800                 # 每标的每周期缓存的K线根数上限
KLINE_FULL_SEC = 7 * 86400       # 每周顺带全量刷新一次, 修正前复权历史
KLINE_RECHECK_SEC = 3600         # 增量取到新数据后, 再次校验的间隔
KLINE_IDLE_RECHECK_SEC = 4 * 3600  # 校验后无新数据(节假日)的冷却时间
CACHED_KLINE_CODES = set(INDEX_LIST) | set(BROAD_ETF_CANDIDATES)


def _latest_expected_date(now):
    """按当前北京时间推断缓存应有的最新交易日:
    周末取最近周五; 交易日9:25开盘后取当日(含盘中未收盘K线), 之前取上一交易日"""
    d = now.date()
    if d.weekday() >= 5:
        d -= timedelta(days=d.weekday() - 4)
    elif now.hour < 9 or (now.hour == 9 and now.minute < 25):
        d -= timedelta(days=1 if d.weekday() else 3)
    return d.strftime("%Y-%m-%d")


def _forming_bar_stale(entry, bars, now, now_ts):
    """当日未收盘K线是否需要刷新: 盘中(bars含当日)每5分钟增量刷新一次,
    保证日K/周K图盘中也能看到最新变动; 收盘后当日K线定型不再刷新"""
    if not bars or bars[-1][0] != now.strftime("%Y-%m-%d"):
        return False
    if now.weekday() >= 5 or now.hour >= 15:
        return False
    return now_ts >= entry.get("next_check", 0)


KLINE_FORMING_SEC = 300     # 盘中当日未收盘K线的增量刷新间隔


def _iso_week(date_str):
    return datetime.strptime(date_str, "%Y-%m-%d").isocalendar()[:2]


def _merge_klines(old, fresh, tf):
    """按日期合并增量K线(同日期以新数据覆盖); 周线同一ISO周的旧K线被新K线替换"""
    if not fresh:
        return old
    if tf == "week":
        weeks = {_iso_week(b[0]) for b in fresh}
        old = [b for b in old if _iso_week(b[0]) not in weeks]
    bar_map = {b[0]: b for b in old}
    for b in fresh:
        bar_map[b[0]] = b
    return sorted(bar_map.values(), key=lambda b: b[0])[-KLINE_KEEP:]


def _kline_read(code, tf):
    """读缓存条目(进程内懒加载), 返回 (entry, bars)"""
    with KLINE_LOCK:
        if not _KC["loaded"]:
            _KC["data"] = load_json(KLINE_CACHE_PATH, {})
            _KC["loaded"] = True
        entry = _KC["data"].get(code, {}).get(tf) or {}
        return entry, list(entry.get("bars", []))


def _kline_stale(entry, bars, now_c, now_ts):
    """缓存是否需要网络刷新: 无数据/日期落后且过冷却期/盘中当日K线到刷新点;
    冷却期优先(刚刷新过/停牌/节假日不反复请求, 也避免前端stale重拉死循环)"""
    if not bars:
        return True
    if now_ts < entry.get("next_check", 0):
        return False
    if bars[-1][0] < _latest_expected_date(now_c):
        return True
    return _forming_bar_stale(entry, bars, now_c, now_ts)


_KLINE_DIRTY = {"flag": False}     # 脏标记: 落盘线程批量写, 避免每次请求全量写文件
_KLINE_REFRESHING = set()          # 刷新中的(code,tf), 防止重复发起


def _kline_mark_dirty():
    _KLINE_DIRTY["flag"] = True


def _kline_flush_loop():
    """K线缓存落盘线程: 有变更时每15秒批量写一次"""
    while True:
        time.sleep(15)
        if not _KLINE_DIRTY["flag"] or not _KC["loaded"]:
            continue
        with KLINE_LOCK:
            _KLINE_DIRTY["flag"] = False
            try:
                tmp = KLINE_CACHE_PATH + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(_KC["data"], f, ensure_ascii=False)
                os.replace(tmp, KLINE_CACHE_PATH)
            except Exception:
                _KLINE_DIRTY["flag"] = True   # 写失败, 下轮重试


def _refresh_kline(code, tf):
    """网络刷新一个(code,tf)的缓存: 无缓存→全量; 超一周→全量(修正前复权); 其余60根增量"""
    now_ts = time.time()
    with KLINE_LOCK:
        entry = _KC["data"].get(code, {}).get(tf) or {}
        full_ts = entry.get("full_ts", 0)
        has_bars = bool(entry.get("bars"))
    full = (not has_bars) or (now_ts - full_ts > KLINE_FULL_SEC)
    fresh = fetch_kline(code, tf, KLINE_KEEP if full else 60)
    with KLINE_LOCK:
        entry = _KC["data"].setdefault(code, {}).setdefault(tf, {})
        old_bars = entry.get("bars") or []
        prev_last = old_bars[-1][0] if old_bars else None
        if fresh:
            entry["bars"] = _merge_klines(old_bars, fresh, tf)
            if full:
                entry["full_ts"] = now_ts
            new_last = entry["bars"][-1][0] if entry["bars"] else None
            # 盘中持有当日未收盘K线 → 5分钟后增量刷新(价格实时变);
            # 盘中未取到新数据(停牌/未开盘) → 10分钟重试; 非交易日无新数据 → 冷却4小时
            intraday = is_trading_time(now_cst())
            if new_last == prev_last:
                entry["next_check"] = now_ts + (600 if intraday else KLINE_IDLE_RECHECK_SEC)
            else:
                today = now_cst().strftime("%Y-%m-%d")
                entry["next_check"] = now_ts + (KLINE_FORMING_SEC
                                                if new_last == today and intraday
                                                else KLINE_RECHECK_SEC)
        else:   # 拉取失败, 10分钟后重试
            entry["next_check"] = now_ts + 600
    _kline_mark_dirty()
    return list(entry.get("bars", []))


def _refresh_kline_async(code, tf):
    """后台线程刷新(去重: 同一code+tf刷新中不重复发起)"""
    key = (code, tf)
    with KLINE_LOCK:
        if key in _KLINE_REFRESHING:
            return
        _KLINE_REFRESHING.add(key)

    def _run():
        try:
            _refresh_kline(code, tf)
        except Exception:
            pass
        finally:
            with KLINE_LOCK:
                _KLINE_REFRESHING.discard(key)
    threading.Thread(target=_run, daemon=True).start()


def serve_kline(code, tf="day", n=800):
    """HTTP层K线(旧数据先出图): 缓存新鲜→直接返回; 过期但有旧数据→立即返回旧数据
    并后台刷新(返回stale=True, 前端1.5s后重拉); 无缓存→同步拉一次(冷启动首次)
    返回 (bars, stale)"""
    n = max(60, min(int(n or 800), KLINE_KEEP))
    tf = tf if tf in ("day", "week") else "day"
    entry, bars = _kline_read(code, tf)
    now_ts, now_c = time.time(), now_cst()
    if not _kline_stale(entry, bars, now_c, now_ts):
        return bars[-n:], False
    if bars:
        _refresh_kline_async(code, tf)
        return bars[-n:], True
    return _refresh_kline(code, tf)[-n:], False


def get_kline_cached(code, tf="day", n=800):
    """K线缓存(同步版, 供共振/背离扫描等后台逻辑): 过期即同步刷新"""
    n = max(60, min(int(n or 800), KLINE_KEEP))
    tf = tf if tf in ("day", "week") else "day"
    entry, bars = _kline_read(code, tf)
    now_ts, now_c = time.time(), now_cst()
    if not _kline_stale(entry, bars, now_c, now_ts):
        return bars[-n:]
    return _refresh_kline(code, tf)[-n:]


def _kline_prewarm():
    """启动预热: 后台把自选+指数+宽基ETF的日K/周K刷进本地缓存, 用户首次点击秒开"""
    time.sleep(3)   # 等服务先起来
    codes = list(CACHED_KLINE_CODES) + list(_watch_codes())
    for code in codes:
        for tf in ("day", "week"):
            try:
                entry, bars = _kline_read(code, tf)
                now_ts, now_c = time.time(), now_cst()
                if _kline_stale(entry, bars, now_c, now_ts):
                    _refresh_kline(code, tf)
            except Exception:
                pass
            time.sleep(0.1)
    _kline_mark_dirty()


def _watch_codes():
    """自选/监控列表的代码集合(这些标的的K线也走本地缓存)"""
    with CFG_LOCK:
        return {s.get("code", "") for s in load_cfg().get("stocks", [])}


def _drop_kline_cache(code):
    """从K线缓存中删除指定标的并落盘(删除自选时同步清理缓存)"""
    with KLINE_LOCK:
        if not _KC["loaded"]:
            _KC["data"] = load_json(KLINE_CACHE_PATH, {})
            _KC["loaded"] = True
        if code not in _KC["data"]:
            return
        del _KC["data"][code]
        tmp = KLINE_CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_KC["data"], f, ensure_ascii=False)
        os.replace(tmp, KLINE_CACHE_PATH)


def get_etf_share_history(code, tf="day"):
    """ETF份额历史(亿份): [[date, shares], ...]; tf=week 时按ISO周聚合, 取每周最后快照"""
    with SHARE_LOCK:
        hist = load_json(SHARE_HIST_PATH, {})
    days = hist.get(code, {})
    if not days:
        return []
    items = sorted(days.items())  # [(date, shares), ...]
    if tf != "week":
        return [[d, s] for d, s in items]
    out, cur_key = [], None
    for d, s in items:
        key = datetime.strptime(d, "%Y-%m-%d").isocalendar()[:2]
        if key != cur_key:
            out.append([d, s])
            cur_key = key
        else:
            out[-1] = [d, s]  # 同周取最后快照
    return out


# ---------------- 全市场底背离扫描(交易日收盘后16:00) ----------------

DIV_LOCK = threading.Lock()
DIV_SCAN = {"ts": 0, "rows": [], "scanning": False, "done": 0, "total": 0}
DIV_SCAN_HOUR = 16          # 每个交易日收盘后16:00才开始全量扫描
_SCAN_NOW = threading.Event()   # 前端"立即更新"手动触发, 唤醒扫描线程
DIV_RECENT = 100            # 只保留最近100周期内成立的背离
DIV_KLINE_N = 350           # 拉取K线根数: 100周期窗口 + 极值间隔 + MACD预热

ALL_STOCKS_PATH = os.path.join(BASE, "all_stocks.json")
ALL_LOCK = threading.Lock()
ALL_CACHE = {"ts": 0, "data": []}
SINA_LIST = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/"
             "json_v2.php/Market_Center.getHQNodeData?page={}&num=100"
             "&sort=symbol&asc=1&node=hs_a")


def fetch_all_stocks():
    """新浪接口分页拉取全部沪深A股(剔除北交所, 约5300只), 内存+落盘缓存1天"""
    now = time.time()
    with ALL_LOCK:
        if ALL_CACHE["data"] and now - ALL_CACHE["ts"] < 86400:
            return ALL_CACHE["data"]
    cached = load_json(ALL_STOCKS_PATH, {})
    if cached.get("stocks") and now - cached.get("ts", 0) < 86400:
        with ALL_LOCK:
            ALL_CACHE.update(ts=cached["ts"], data=cached["stocks"])
        return cached["stocks"]
    out, page = [], 1
    while True:
        try:
            r = robust_get(SINA_LIST.format(page), timeout=10).json()
        except Exception:
            break
        if not r:
            break
        for x in r:
            sym = x.get("symbol", "")
            if sym[:2] not in ("sh", "sz"):   # 剔除北交所
                continue
            try:
                price = float(x.get("trade") or 0) or None
            except (TypeError, ValueError):
                price = None
            out.append({"code": sym, "name": x.get("name", ""), "price": price})
        if len(r) < 100:
            break
        page += 1
    if len(out) < 100:   # 拉取异常, 回退旧缓存(哪怕已过期)
        with ALL_LOCK:
            if ALL_CACHE["data"]:
                return ALL_CACHE["data"]
        return cached.get("stocks", [])
    with ALL_LOCK:
        ALL_CACHE.update(ts=time.time(), data=out)
    tmp = ALL_STOCKS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"ts": ALL_CACHE["ts"], "stocks": out}, f, ensure_ascii=False)
    os.replace(tmp, ALL_STOCKS_PATH)
    return out


# ---------------- 共振评分(信号质量过滤) ----------------
# 全部基于已拉取的K线本地计算, 不产生额外网络请求
# 权重经统计面板(/api/stats 按共振分分层)可校准

TAG_LABELS = {
    "vol_shrink": "缩量",       # 第二低点量能 < 第一低点70%
    "ma_hold": "均线托底",       # 确认日站上20日线或20日线止跌
    "rsi_repair": "RSI修复",     # 低点RSI超卖后回升
    "kdj_gold": "KDJ金叉",      # 低点至确认日间K上穿D
    "week_align": "周线同向",    # (日线信号)周线DIF零轴下方上行
    "vol_engulf": "放量反包",    # 确认日阳线吞没前根实体且放量
}
TAG_WEIGHTS = {"vol_shrink": 2, "ma_hold": 2, "rsi_repair": 1,
              "kdj_gold": 1, "week_align": 2, "vol_engulf": 1}


def _sma(vals, n):
    out, s = [], 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= n:
            s -= vals[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def _rsi(closes, n=14):
    out = [None] * len(closes)
    gains = losses = 0.0
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        g, l = max(ch, 0), max(-ch, 0)
        if i <= n:   # 首个周期用简单平均
            gains += g
            losses += l
            if i == n:
                ag, al = gains / n, losses / n
                out[i] = 100 - 100 / (1 + (ag / al if al else 1e9))
        else:        # Wilder平滑
            ag = (ag * (n - 1) + g) / n
            al = (al * (n - 1) + l) / n
            out[i] = 100 - 100 / (1 + (ag / al if al else 1e9))
    return out


def _kdj(klines, n=9):
    """KDJ(9,3,3), 返回 (K[], D[]); 前部None"""
    k_l, d_l, pk, pd = [], [], None, None
    for i in range(len(klines)):
        lo = min(klines[j][4] for j in range(max(0, i - n + 1), i + 1))
        hi = max(klines[j][3] for j in range(max(0, i - n + 1), i + 1))
        rsv = 50.0 if hi == lo else (klines[i][2] - lo) / (hi - lo) * 100
        pk = 50.0 if pk is None else pk * 2 / 3 + rsv / 3
        pd = 50.0 if pd is None else pd * 2 / 3 + pk / 3
        k_l.append(None if i < n - 1 else pk)
        d_l.append(None if i < n - 1 else pd)
    return k_l, d_l


def _week_closes(klines):
    """日线按ISO周聚合出周收盘序列"""
    out, cur_key = [], None
    for k in klines:
        key = datetime.strptime(k[0], "%Y-%m-%d").isocalendar()[:2]
        if key != cur_key:
            out.append(k[2])
            cur_key = key
        else:
            out[-1] = k[2]
    return out


def _resonance(klines, closes, p1, p2, cidx, tf):
    """计算底背离信号的共振标签与加权分"""
    tags = []
    vols = [k[5] for k in klines]
    # 缩量: 低点附近3根均量比较
    v1 = sum(vols[max(0, p1 - 2):p1 + 1]) / (p1 + 1 - max(0, p1 - 2))
    v2 = sum(vols[max(0, p2 - 2):p2 + 1]) / (p2 + 1 - max(0, p2 - 2))
    if v1 > 0 and v2 < 0.7 * v1:
        tags.append("vol_shrink")
    # 均线托底
    ma20 = _sma(closes, 20)
    if ma20[cidx] is not None:
        prev = ma20[cidx - 5] if cidx >= 5 else None
        if closes[cidx] > ma20[cidx] or (prev is not None and ma20[cidx] >= prev):
            tags.append("ma_hold")
    # RSI修复
    rsi = _rsi(closes)
    if (rsi[p2] is not None and rsi[cidx] is not None
            and rsi[p2] < 35 and rsi[cidx] > rsi[p2]):
        tags.append("rsi_repair")
    # KDJ金叉
    k_l, d_l = _kdj(klines)
    for j in range(max(1, p2), cidx + 1):
        if (k_l[j] is not None and d_l[j] is not None
                and k_l[j - 1] is not None and d_l[j - 1] is not None
                and k_l[j - 1] <= d_l[j - 1] and k_l[j] > d_l[j]):
            tags.append("kdj_gold")
            break
    # 周线同向(仅日线信号)
    if tf == "day":
        wcloses = _week_closes(klines)
        if len(wcloses) >= 30:
            wdif, _, _ = calc_macd(wcloses)
            if wdif[-1] < 0 and wdif[-1] > wdif[-2]:
                tags.append("week_align")
    # 放量反包
    if cidx >= 1:
        o, c = klines[cidx][1], klines[cidx][2]
        po, pc = klines[cidx - 1][1], klines[cidx - 1][2]
        if c > o and o <= pc and c >= po and vols[cidx] > vols[cidx - 1]:
            tags.append("vol_engulf")
    score = sum(TAG_WEIGHTS.get(t, 0) for t in tags)
    return tags, score


def _scan_one_divs(stock, now):
    """扫描单只股票的日线底背离, 只保留最近 DIV_RECENT 周期内成立的信号"""
    code = stock["code"]
    rows = []
    for tf, tf_name in (("day", "日线"),):
        try:
            klines = fetch_kline(code, tf, DIV_KLINE_N)
        except Exception:
            continue
        if len(klines) < 160:   # 100周期窗口 + EMA预热下限
            continue
        last = len(klines) - 1
        if not bar_complete(tf, klines[last][0], now):   # 剔除未收盘K线
            last -= 1
            if last < 160:
                continue
        bars = [(k[0], k[2]) for k in klines[:last + 1]]  # (日期, 收盘价)
        dif, _, _ = calc_macd([c for _, c in bars])
        closes = [c for _, c in bars]
        for d in detect_divergences(bars, dif, last, pairs=999):
            if d["div"] != "bull" or d["p2"] < last - DIV_RECENT + 1:
                continue   # 只要最近100周期内成立的底背离
            p2 = d["p2"]
            cidx = min(d["confirm"], last)   # 确认日索引

            def _chg(n):
                """第二低点后n周期的涨幅%, K线不足返回None"""
                j = p2 + n
                return round((closes[j] / d["c2"] - 1) * 100, 2) if j <= last else None

            # 共振标签与加权分(纯本地计算)
            tags, score = _resonance(klines[:last + 1], closes, d["p1"], p2, cidx, tf)
            # 元标签模型质量分(只用确认日及之前数据, 无未来函数)
            ml_score = None
            feats = ml_model.build_features(klines[:last + 1], {
                "date1": bars[d["p1"]][0], "date2": bars[d["p2"]][0],
                "price1": d["c1"], "price2": d["c2"],
                "dif1": d["d1"], "dif2": d["d2"],
                "score": score, "tags": ",".join(tags),
                "confirm": bars[cidx][0], "confirm_close": closes[cidx],
            })
            if feats is not None:
                ml_score = ml_model.model_score(feats)
            rows.append({
                "code": code, "name": stock["name"], "price": stock["price"],
                "tf": tf, "tf_name": tf_name,
                "date1": bars[d["p1"]][0], "date2": bars[d["p2"]][0],
                "price1": d["c1"], "price2": d["c2"],
                "dif1": round(d["d1"], 3), "dif2": round(d["d2"], 3),
                "dif_inc": round(d["d2"] - d["d1"], 3),   # DIF增加值
                "chg3": _chg(3), "chg5": _chg(5),          # 后3/5周期涨幅%
                "confirm": bars[cidx][0],
                "confirm_close": closes[cidx],            # 跟踪基准: 确认日收盘
                "score": score, "tags": ",".join(tags),
                "ml_score": ml_score,
            })
        time.sleep(0.05)   # 轻微限速, 避免触发行情接口WAF
    return rows


DIV_KEEP_DAYS = 30   # 表格只展示最近30个扫描日内出现过的信号(库内保留2年供统计)


def _is_trading_day(day_str):
    """用上证指数日K(本地缓存增量更新)判断 day_str 是否交易日: 最后K线日期==该日(收盘后调用)。
    接口异常时按交易日处理, 由扫描自身的重试兜底"""
    try:
        k = get_kline_cached("sh000001", "day", 2)
        return (not k) or k[-1][0][:10] == day_str
    except Exception:
        return True


def _next_scan_wait(now):
    """距下一个16:00扫描时刻的秒数"""
    target = now.replace(hour=DIV_SCAN_HOUR, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return max(60.0, (target - now).total_seconds())


SCAN_WORKERS = max(1, int(os.environ.get("SCAN_WORKERS", "16")))   # 全市场扫描并发数


def _run_full_scan(scan_date):
    """全市场底背离扫描主体(16:00定时与前端"立即更新"手动触发共用), 返回是否成功"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    now = now_cst()
    with DIV_LOCK:
        DIV_SCAN.update(scanning=True, done=0, total=0)
    try:
        stocks = fetch_all_stocks()
        with DIV_LOCK:
            DIV_SCAN["total"] = len(stocks) * 2
        rows = []
        with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as ex:
            futs = [ex.submit(_scan_one_divs, s, now) for s in stocks]
            for fu in as_completed(futs):
                try:
                    rows.extend(fu.result())
                except Exception:
                    pass
                with DIV_LOCK:
                    DIV_SCAN["done"] += 2
                hub_scan_progress()   # 每完成5%推送SSE进度事件
        db.upsert_signals(rows, scan_date)
        with DIV_LOCK:
            DIV_SCAN["rows"] = db.div_rows(DIV_KEEP_DAYS)
            DIV_SCAN["ts"] = time.time()
        obs.record("INFO", "scan", f"全市场底背离扫描完成: {len(rows)}条信号入库")
        return True
    except Exception as e:
        obs.record("ERROR", "scan", f"全市场扫描中断: {e!r}")
        return False   # 扫描中断: 当日未标记, 稍后重试
    finally:
        with DIV_LOCK:
            DIV_SCAN["scanning"] = False


def _div_scanner():
    """后台线程: 每个交易日收盘后(北京时间16:00)对全部A股的日线/周线底背离全量重扫。
    结果UPSERT进SQLite(按code+tf+第二低点去重), 非交易日不扫描;
    当日已扫过(含重启)直接用库内数据不重扫; 前端"立即更新"可随时手动触发"""
    while True:
        try:
            with DIV_LOCK:
                DIV_SCAN["rows"] = db.div_rows(DIV_KEEP_DAYS)
                DIV_SCAN["ts"] = time.mktime(time.strptime(
                    db.last_scan_date() or "2000-01-01", "%Y-%m-%d"))
            now = now_cst()
            today = now.strftime("%Y-%m-%d")
            manual = _SCAN_NOW.is_set()
            _SCAN_NOW.clear()
            # 定时触发条件: 工作日 + 收盘后(16点) + 交易日 + 今日未扫过(手动触发不受限)
            due = manual or (now.weekday() < 5 and now.hour >= DIV_SCAN_HOUR
                             and _is_trading_day(today) and db.last_scan_date() != today)
            ok = True
            if due:
                last = db.last_scan_date()
                if manual and last != today:
                    # 手动刷新且今日定时扫描未完成: 结果记到最近一次扫描日,
                    # 不把 last_scan_date 推进到今天, 16:00 定时扫描照常执行;
                    # 盘中手动扫描只含已收盘K线, 与上次扫描数据口径一致
                    scan_date = last or (now - timedelta(days=1)).strftime("%Y-%m-%d")
                else:
                    scan_date = today   # 定时扫描 / 今日定时已完成后的手动刷新
                ok = _run_full_scan(scan_date)
            if due and not ok and not manual:
                _SCAN_NOW.wait(600)   # 定时扫描失败, 10分钟后重试当日(可被手动触发唤醒)
                continue
            _SCAN_NOW.wait(_next_scan_wait(now_cst()))   # 休眠到下一个16:00(可被手动触发唤醒)
        except Exception as e:
            obs.record("ERROR", "scan", f"扫描线程异常: {e!r}")
            time.sleep(300)


# ---------------- 信号跟踪回填(复盘统计) ----------------

TRACK_HOUR = 17   # 每日17:00后回填(16:00扫描完成后)


def _track_backfill():
    """后台线程: 每日收盘后对跟踪数据不完整的信号回填确认日后3/5/10/20/60个交易日收益。
    按标的去重拉一次K线; 已完整的信号不再处理"""
    while True:
        try:
            now = now_cst()
            target = now.replace(hour=TRACK_HOUR, minute=0, second=0, microsecond=0)
            if now < target:
                time.sleep(max(60.0, (target - now).total_seconds()))
                continue
            pending = db.pending_track()
            groups = {}
            for p in pending:
                groups.setdefault((p["code"], p["tf"]), []).append(p)
            for (code, tf), sigs in groups.items():
                try:
                    klines = fetch_kline(code, tf, 800)
                except Exception:
                    continue
                if len(klines) < 30:
                    continue
                dates = [k[0] for k in klines]
                closes = [k[2] for k in klines]
                for s in sigs:
                    if s["confirm"] not in dates:   # 确认日K线已滚出窗口
                        continue
                    base = s["confirm_close"] or closes[dates.index(s["confirm"])]
                    if not base:
                        continue
                    i0 = dates.index(s["confirm"])
                    fwd = {}
                    for n in (3, 5, 10, 20, 60):
                        j = i0 + n
                        fwd[f"fwd{n}"] = (round((closes[j] / base - 1) * 100, 2)
                                           if j < len(closes) else None)
                    db.update_track(s["id"], fwd)
                time.sleep(0.1)   # 轻微限速
            time.sleep(_next_scan_wait(now_cst()) + 3600)   # 明日17点后
        except Exception:
            time.sleep(600)


# ---------------- AI选股(元标签模型 + deepseek精选) ----------------
# 每个交易日收盘后3小时(18:00): 从近期确认的日线底背离信号(剔除科创板/ST)中,
# 按元标签模型分取前30候选 → deepseek精选10只胜率最高; 无key/AI失败降级为模型分Top10

AI_PICK_HOUR = 18          # 收盘15:00 + 3小时
AI_POOL_DAYS = 7            # 候选池: 近7个自然日内确认的信号(当日不足自动向近期扩展)
AI_CAND_N = 30              # 送入AI的候选数量
AI_TOP_N = 10               # 最终选股数量
AI_STATE = {"running": False, "msg": "", "ts": 0}
AI_RUN_NOW = threading.Event()

DS_API = "https://api.deepseek.com/chat/completions"
DS_DEFAULT_MODEL = "deepseek-v4-flash"   # 默认模型; 不存在时_ds_call自动回退deepseek-chat


def _ai_cfg():
    """读AI配置(config.json的ai节): {api_key, model}"""
    with CFG_LOCK:
        return dict(load_cfg().get("ai") or {})


def _save_ai_cfg(api_key=None, model=None, clear_key=False):
    """写AI配置; api_key为空表示保持不变, clear_key=True清除"""
    with CFG_LOCK:
        cfg = load_cfg()
        ai = cfg.setdefault("ai", {})
        if clear_key:
            ai.pop("api_key", None)
        elif api_key:
            ai["api_key"] = api_key.strip()
        if model is not None and model.strip():
            ai["model"] = model.strip()
        save_cfg(cfg)


def _ai_candidates():
    """候选池: 近AI_POOL_DAYS确认的日线底背离(不足AI_CAND_N只时自动放宽窗口,
    7→14→30天), 剔除科创板(688/689)与ST; 同一标的取模型分最高的一条, 按模型分降序"""
    with db.conn() as c:
        rows = []
        for days in (AI_POOL_DAYS, AI_POOL_DAYS * 2, 30):   # 逐级放宽时间窗
            rows = c.execute(
                """SELECT code, name, price, confirm, score, tags, ml_score,
                          dif_inc, price1, price2, date2
                   FROM div_signal
                   WHERE tf='day' AND ml_score IS NOT NULL
                     AND confirm >= date('now', ?)
                   ORDER BY ml_score DESC""",
                (f"-{days} day",)).fetchall()
            if len(rows) >= AI_CAND_N:
                break
    best, seen = [], {}
    for r in rows:
        code, name = r["code"] or "", (r["name"] or "")
        num = code[2:]
        if num.startswith(("688", "689")):      # 科创板
            continue
        if "ST" in name.upper():                # ST/*ST/S*ST
            continue
        if code in seen:                        # 同标的保留模型分最高
            continue
        seen[code] = 1
        best.append(dict(r))
        if len(best) >= AI_CAND_N:
            break
    return best


def _ds_headers(key):
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _ds_call(key, model, messages, timeout=90, max_tokens=2000):
    """调用deepseek chat completions, 返回文本; 模型不存在时自动回退deepseek-chat"""
    body = {"model": model, "messages": messages,
            "temperature": 0.2, "max_tokens": max_tokens}
    r = requests.post(DS_API, headers=_ds_headers(key), json=body, timeout=timeout)
    if r.status_code != 200 and "not exist" in r.text.lower():
        body["model"] = "deepseek-chat"
        r = requests.post(DS_API, headers=_ds_headers(key), json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _ai_pick_prompt(cands):
    """构造候选清单与选股指令"""
    lines = []
    for i, c in enumerate(cands, 1):
        ml = f"{c['ml_score']:.0f}" if c.get("ml_score") is not None else "--"
        sc = c.get("score")
        sc = f"{sc:.0f}" if sc is not None else "--"
        drop = ((c["price2"] / c["price1"] - 1) * 100
                if c.get("price1") and c.get("price2") else 0)
        lines.append(
            f"{i}. {c['code']} {c['name']} 现价{c['price'] or '--'} "
            f"模型分{ml} 共振分{sc} 背离低点{c['date2']} 前低跌幅{drop:.1f}% "
            f"确认日{c['confirm']} DIF增量{c.get('dif_inc') or 0:.3f}")
    return f"""你是专业的A股量化分析师。以下是全市场MACD日线底背离信号(已剔除科创板与ST股票)。
其中「模型分」是元标签机器学习模型(三重障碍标注+walk-forward验证, 样本外AUC 0.66)对信号历史胜率的估计(0-100, 越高越好), 「共振分」是多指标共振强度(KDJ金叉/周线同向/放量反包等加权)。

候选信号列表:
{chr(10).join(lines)}

请综合模型分、共振分、背离结构(前低跌幅/DIF增量)与你的A股市场经验, 从中选出{AI_TOP_N}只未来5-10个交易日胜率(上涨概率)最高的标的。
选择原则: 模型分与共振分是核心依据, 避免集中在单一行业, 剔除你判断有明显利空风险(如连续一字跌停打开、退市风险)的标的。
严格只输出一个JSON数组, 不要输出任何其他文字, 格式:
[{{"code": "sh600000", "reason": "一句话核心理由, 30字以内"}}, ...]
必须恰好{AI_TOP_N}项, code必须完全来自上面候选列表。"""


def _parse_ai_picks(text, cands):
    """解析AI返回的JSON数组, 映射回候选明细; 无效code丢弃"""
    i, j = text.find("["), text.rfind("]")
    if i < 0 or j <= i:
        return []
    try:
        arr = json.loads(text[i:j + 1])
    except Exception:
        return []
    by_code = {c["code"]: c for c in cands}
    out, seen = [], set()
    for it in arr:
        if not isinstance(it, dict):
            continue
        code = str(it.get("code", "")).strip()
        c = by_code.get(code)
        if not c or code in seen:
            continue
        seen.add(code)
        pick = dict(c)
        pick["reason"] = str(it.get("reason", ""))[:80]
        out.append(pick)
        if len(out) >= AI_TOP_N:
            break
    return out


def run_ai_pick():
    """执行一次AI选股(定时/手动共用): 模型分候选 → deepseek精选; 降级为模型分Top10。
    返回 (ok, msg)"""
    if AI_STATE["running"]:
        return False, "选股进行中, 请稍候"
    AI_STATE["running"] = True
    try:
        today = now_cst().strftime("%Y-%m-%d")
        cands = _ai_candidates()
        if not cands:
            AI_STATE.update(msg="近期无符合条件的底背离信号", ts=time.time())
            return False, "近期无符合条件的底背离信号(已剔除科创板/ST)"
        ai = _ai_cfg()
        key = ai.get("api_key", "")
        model = ai.get("model") or DS_DEFAULT_MODEL
        picks, source, msg = None, "model", ""
        if key:
            try:
                text = _ds_call(key, model,
                                [{"role": "user",
                                  "content": _ai_pick_prompt(cands)}])
                picks = _parse_ai_picks(text, cands)
                if len(picks) < AI_TOP_N:
                    obs.record("WARN", "ai",
                               f"deepseek返回{len(picks)}只有效标的(<{AI_TOP_N}), 降级补齐")
                    source = "ai+model"
                else:
                    source = "ai"
            except Exception as e:
                obs.record("WARN", "ai", f"deepseek调用失败: {e!r}")
                msg = f"AI调用失败({e!r}), 已降级为模型分Top10; "
        else:
            msg = "未配置API Key, 使用模型分Top10; "
        # AI结果不足10只时用候选池(模型分序)补齐
        picks = picks or []
        if len(picks) < AI_TOP_N:
            chosen = {p["code"] for p in picks}
            for c in cands:
                if len(picks) >= AI_TOP_N:
                    break
                if c["code"] not in chosen:
                    p = dict(c)
                    p.setdefault("reason", "模型分Top候选")
                    picks.append(p)
                    chosen.add(c["code"])
        for p in picks:
            p["source"] = source
        db.save_ai_picks(today, picks)
        AI_STATE.update(msg=f"{today} 选股完成: {len(picks)}只 ({source})",
                        ts=time.time())
        obs.record("INFO", "ai", f"AI选股完成: {today} {len(picks)}只, 来源={source}")
        return True, f"{today} 选股完成: {len(picks)}只 ({source})"
    except Exception as e:
        obs.record("ERROR", "ai", f"AI选股失败: {e!r}")
        AI_STATE.update(msg=f"选股失败: {e!r}", ts=time.time())
        return False, f"选股失败: {e!r}"
    finally:
        AI_STATE["running"] = False


def _ai_pick_loop():
    """后台线程: 每个交易日18:00(收盘+3小时)自动选股; 当日已选过不重复;
    前端"立即选股"可随时手动触发(非交易日也可)"""
    while True:
        try:
            now = now_cst()
            target = now.replace(hour=AI_PICK_HOUR, minute=0, second=0,
                                 microsecond=0)
            if now < target:
                # 未到18:00: 休眠至定点, 可被手动触发提前唤醒
                if AI_RUN_NOW.wait(max(60.0, (target - now).total_seconds())):
                    AI_RUN_NOW.clear()
                    run_ai_pick()
                continue
            # 已过18:00: 手动触发立即执行; 否则交易日定时(当日未选过)执行
            if AI_RUN_NOW.wait(60):
                AI_RUN_NOW.clear()
                run_ai_pick()
                continue
            today = now.strftime("%Y-%m-%d")
            if now.weekday() < 5 and _is_trading_day(today) \
                    and db.last_ai_pick_date() != today:
                run_ai_pick()
                continue
            # 今日已完成或非交易日: 休眠到明天18:00(手动可唤醒)
            nxt = now_cst().replace(hour=AI_PICK_HOUR, minute=0, second=0,
                                    microsecond=0)
            if now_cst() >= nxt:
                nxt += timedelta(days=1)
            if AI_RUN_NOW.wait(max(60.0, (nxt - now_cst()).total_seconds())):
                AI_RUN_NOW.clear()
                run_ai_pick()
        except Exception as e:
            obs.record("ERROR", "ai", f"AI选股线程异常: {e!r}")
            time.sleep(300)


MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml",
        ".ico": "image/x-icon", ".webmanifest": "application/manifest+json; charset=utf-8"}

# ---------------- Web UI 访问认证 ----------------
# config.json: webui.auth_token 非空时启用, 所有 /api/* 需携带
# Authorization: Bearer <token> 或 ?token=<token>; 静态文件放行(登录页需要)
# 同 IP 连续失败5次锁定60秒

AUTH_FAIL_LIMIT = 5
AUTH_LOCK_SEC = 60
_AUTH_FAILS = {}   # ip -> (失败次数, 锁定截止时间戳)
_AUTH_LOCK = threading.Lock()


def _auth_token():
    with CFG_LOCK:
        return (load_cfg().get("webui") or {}).get("auth_token", "") or ""


def _auth_check(handler, count_fail=True):
    """返回 None 表示通过; 否则返回剩余锁定秒数(>0)或 -1(仅校验失败)
    count_fail=False 用于状态探测, 不累计失败次数"""
    if not _auth_token():
        return None   # 未配置token, 不启用认证
    token = ""
    auth = handler.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
    if not token:
        token = parse_qs(urlparse(handler.path).query).get("token", [""])[0]
    ip = handler.client_address[0]
    now_ts = time.time()
    with _AUTH_LOCK:
        fails, lock_until = _AUTH_FAILS.get(ip, (0, 0))
        if now_ts < lock_until:
            return int(lock_until - now_ts) + 1
    if token and token == _auth_token():
        with _AUTH_LOCK:
            _AUTH_FAILS.pop(ip, None)
        return None
    if not count_fail:
        return -1
    with _AUTH_LOCK:
        fails, lock_until = _AUTH_FAILS.get(ip, (0, 0))
        if now_ts >= lock_until:
            fails = 0
        fails += 1
        _AUTH_FAILS[ip] = (fails, now_ts + AUTH_LOCK_SEC if fails >= AUTH_FAIL_LIMIT else lock_until)
    if fails >= AUTH_FAIL_LIMIT:
        obs.record("ERROR", "auth", f"IP {ip} 连续{fails}次认证失败, 锁定{AUTH_LOCK_SEC}s")
    else:
        obs.record("WARN", "auth", f"IP {ip} 认证失败({fails}/{AUTH_FAIL_LIMIT})")
    return -1


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _auth_guard(self):
        """API 访问守卫: 通过返回 True; 已拒绝(已发送401/423响应)返回 False"""
        r = _auth_check(self)
        if r is None:
            return True
        if r > 0:
            self._json({"ok": False, "msg": f"尝试次数过多, 请{r}秒后重试", "auth": "locked"}, 423)
        else:
            self._json({"ok": False, "msg": "未授权访问", "auth": "unauthorized"}, 401)
        return False

    def _json(self, obj, status=200, headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _file(self, name):
        path = os.path.normpath(os.path.join(STATIC_DIR, name))
        if not path.startswith(STATIC_DIR) or not os.path.isfile(path):
            self.send_error(404)
            return
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            self._file("index.html")
        elif u.path.startswith("/static/"):
            self._file(u.path[len("/static/"):])
        elif u.path == "/api/auth/status":
            # 前端探测认证状态: 是否启用/当前是否已通过(不计失败次数)
            r = _auth_check(self, count_fail=False)
            self._json({"enabled": bool(_auth_token()),
                        "ok": r is None,
                        "locked": r if (r is not None and r > 0) else 0})
        elif not self._auth_guard():
            return
        elif u.path == "/api/stocks":
            with CFG_LOCK:
                stocks = load_cfg().get("stocks", [])
            self._json(stocks)
        elif u.path == "/api/quotes":
            # 支持 ?codes= 指定代码集(前端实时刷新当前选中标的用, 不拉主力资金流)
            want = [c for c in parse_qs(u.query).get("codes", [""])[0].split(",") if c]
            if want:
                quotes = fetch_quotes(want)
                self._json([_quote_item(c, quotes.get(c)) for c in want])
            else:
                with CFG_LOCK:
                    stocks = load_cfg().get("stocks", [])
                self._json(build_quotes(stocks))
        elif u.path == "/api/search":
            q = parse_qs(u.query).get("q", [""])[0]
            self._json(search_suggest(q))
        elif u.path == "/api/index/list":
            self._json(index_list())
        elif u.path == "/api/etf/list":
            self._json(broad_etf_list())
        elif u.path == "/api/etf/share":
            q = parse_qs(u.query)
            code = q.get("code", [""])[0]
            tf = q.get("tf", ["day"])[0]
            self._json(get_etf_share_history(code, tf))
        elif u.path == "/api/divergences":
            with CFG_LOCK:
                watch_codes = {s.get("code") for s in load_cfg().get("stocks", [])}
            with DIV_LOCK:
                rows = [{**r, "watch": r["code"] in watch_codes} for r in DIV_SCAN["rows"]]
                payload = {"ts": DIV_SCAN["ts"], "scanning": DIV_SCAN["scanning"],
                           "done": DIV_SCAN["done"], "total": DIV_SCAN["total"],
                           "rows": rows}
            self._json(payload)
        elif u.path == "/api/kline/minute":
            # 当日分时走势(交易时段前端8s轮询, 仅当前选中标的)
            code = parse_qs(u.query).get("code", [""])[0]
            self._json(fetch_minute(code))
        elif u.path == "/api/kline":
            q = parse_qs(u.query)
            code = q.get("code", [""])[0]
            tf = q.get("tf", ["day"])[0]
            n = q.get("n", ["800"])[0]
            # 左栏指数/宽基ETF及自选股走本地缓存(旧数据先出图+后台刷新), 其余标的直接实时拉取
            if code in CACHED_KLINE_CODES or code in _watch_codes():
                bars, stale = serve_kline(code, tf, n)
                self._json(bars, headers={"X-Kline-Stale": "1" if stale else "0"})
            else:
                self._json(fetch_kline(code, tf, n))
        elif u.path == "/api/stats":
            # 复盘统计: 总览 + 周期/共振分/确认月份分层
            st = db.stats()
            # 模型元信息(含样本外walk-forward指标, 历史分层为样本内会偏乐观)
            if ml_model.load_model():
                m = ml_model._MODEL
                st["ml_meta"] = {k: m.get(k) for k in
                                 ("auc", "base_win", "n_samples", "trained_at", "wf")}
            self._json(st)
        elif u.path == "/api/tags":
            # 共振标签定义(前端展示徽章用)
            self._json({"labels": TAG_LABELS, "weights": TAG_WEIGHTS})
        elif u.path == "/api/ai/config":
            # AI选股配置(密钥脱敏返回)
            ai = _ai_cfg()
            key = ai.get("api_key", "")
            self._json({
                "has_key": bool(key),
                "key_mask": (key[:5] + "***" + key[-4:]) if len(key) > 12 else ("***" if key else ""),
                "model": ai.get("model") or DS_DEFAULT_MODEL,
                "hour": AI_PICK_HOUR, "top_n": AI_TOP_N,
                "default_model": DS_DEFAULT_MODEL,
            })
        elif u.path == "/api/ai/picks":
            # 最近一次选股结果 + 运行状态
            d, rows = db.ai_picks_latest()
            self._json({"running": AI_STATE["running"], "date": d,
                        "rows": rows, "msg": AI_STATE["msg"],
                        "ts": AI_STATE["ts"], "top_n": AI_TOP_N,
                        "hour": AI_PICK_HOUR})
        elif u.path == "/api/ai/run":
            # 手动选股: 由选股线程异步执行(可能含deepseek调用, 秒级到分钟级)
            if AI_STATE["running"]:
                self._json({"ok": False, "msg": "选股进行中, 请稍候"})
            else:
                AI_RUN_NOW.set()
                self._json({"ok": True, "msg": "已触发AI选股, 结果稍后自动刷新"})
        elif u.path == "/api/intraday":
            # 盘中60分钟底背离预览信号(自选股, 前端初始加载; 之后走SSE)
            with INTRADAY_LOCK:
                self._json({"rows": list(INTRADAY_ROWS)})
        elif u.path == "/api/resonance":
            # 多周期共振快照(60分/日/周, 前端初始加载; 之后走SSE)
            with RESONANCE_LOCK:
                self._json({"rows": list(RESONANCE_ROWS), "ts": RESONANCE_TS})
        elif u.path == "/api/logs":
            # 结构化日志: obs 进程内事件缓冲(新的在前), 支持级别过滤
            q = parse_qs(u.query)
            lvl = q.get("lvl", [""])[0]
            try:
                limit = min(300, max(1, int(q.get("limit", ["100"])[0] or 100)))
            except ValueError:
                limit = 100
            self._json({"rows": obs.recent(lvl=lvl or None, limit=limit)})
        elif u.path == "/api/health":
            # 健康检查: 数据源状态/扫描状态/订阅数/进程可观测性
            with DIV_LOCK:
                scan = {k: DIV_SCAN[k] for k in ("scanning", "done", "total", "ts")}
            with _hub_subs_lock:
                nsubs = len(_hub_subs)
            kc = 0
            try:
                with KLINE_LOCK:
                    kc = sum(len(v.get("bars", [])) for v in _KC["data"].values())
            except Exception:
                pass
            with INTRADAY_LOCK:
                n_intraday = len(INTRADAY_ROWS)
            with RESONANCE_LOCK:
                n_res = len(RESONANCE_ROWS)
            self._json({"ok": True, "sources": net.health(), "scan": scan,
                        "sse_subs": nsubs, "kline_bars": kc, "intraday": n_intraday,
                        "resonance": n_res, "obs": obs.health(),
                        "db_signals": len(db.div_rows(3650))})
        elif u.path == "/api/stream":
            self._handle_stream(parse_qs(u.query).get("codes", [""])[0])
        else:
            self.send_error(404)

    def _handle_stream(self, codes_arg):
        """SSE: 行情diff + 扫描进度 + 盘中预览事件推送, 替代前端轮询"""
        if not self._auth_guard():
            return
        codes = [c for c in codes_arg.split(",") if c][:60]
        cid, q = hub_register(codes)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"retry: 5000\n\n")   # 断线5秒后重连
            self.wfile.flush()
            last_heartbeat = time.time()
            while True:
                try:
                    sent = False
                    while q:   # 排空队列
                        ev = q.popleft()
                        data = json.dumps(ev, ensure_ascii=False)
                        self.wfile.write(f"data: {data}\n\n".encode())
                        sent = True
                    now_ts = time.time()
                    if not sent and now_ts - last_heartbeat >= HUB_HEARTBEAT_SEC:
                        self.wfile.write(b":ping\n\n")   # 心跳防代理断连
                        sent = True
                        last_heartbeat = now_ts
                    if sent:
                        self.wfile.flush()
                    else:
                        time.sleep(0.5)
                except (BrokenPipeError, ConnectionResetError):
                    break
                except Exception:
                    break
        except Exception:
            pass   # 客户端断开
        finally:
            hub_unregister(cid)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path in ("/api/ai/config", "/api/ai/test"):
            # AI选股配置保存 / deepseek连接测试
            if not self._auth_guard():
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length))
            except Exception:
                self._json({"ok": False, "msg": "请求格式错误"}, 400)
                return
            if u.path == "/api/ai/config":
                _save_ai_cfg(api_key=data.get("api_key"),
                             model=data.get("model"),
                             clear_key=bool(data.get("clear_key")))
                ai = _ai_cfg()
                self._json({"ok": True, "model": ai.get("model") or DS_DEFAULT_MODEL,
                            "has_key": bool(ai.get("api_key"))})
                return
            # /api/ai/test: 用当前保存的key发一条最小消息验证连通
            ai = _ai_cfg()
            key = ai.get("api_key", "")
            model = data.get("model") or ai.get("model") or DS_DEFAULT_MODEL
            if not key:
                key = (data.get("api_key") or "").strip()
            if not key:
                self._json({"ok": False, "msg": "未填写API Key"})
                return
            try:
                text = _ds_call(key, model,
                                [{"role": "user", "content": "回复OK"}],
                                timeout=20, max_tokens=8)
                self._json({"ok": True, "msg": f"连接成功, 模型回复: {text[:20]}"})
            except Exception as e:
                detail = ""
                try:
                    detail = e.response.text[:120] if getattr(e, "response", None) else ""
                except Exception:
                    pass
                self._json({"ok": False,
                            "msg": f"连接失败: {e}{'; ' + detail if detail else ''}"})
            return
        if u.path == "/api/div/rescan":
            # 前端"立即更新"按钮: 唤醒扫描线程手动全量重扫(不影响16:00定时扫描)
            if not self._auth_guard():
                return
            with DIV_LOCK:
                scanning = DIV_SCAN["scanning"]
            if scanning:
                self._json({"ok": False, "msg": "全市场扫描进行中, 请等待完成"})
            else:
                _SCAN_NOW.set()
                self._json({"ok": True, "msg": "已触发全市场底背离扫描"})
            return
        if u.path != "/api/stocks":
            self.send_error(404)
            return
        if not self._auth_guard():
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"ok": False, "msg": "请求格式错误"}, 400)
            return
        code, name, group = data.get("code", ""), data.get("name", ""), data.get("group", "自选")
        if not code or not name:
            self._json({"ok": False, "msg": "缺少代码或名称"}, 400)
            return
        with CFG_LOCK:
            cfg = load_cfg()
            stocks = cfg.setdefault("stocks", [])
            if any(s.get("code") == code for s in stocks):
                self._json({"ok": False, "msg": f"{name} 已在监控列表中"})
                return
            stocks.append({"code": code, "name": name, "group": group})
            save_cfg(cfg)
        self._json({"ok": True})

    def do_DELETE(self):
        u = urlparse(self.path)
        if u.path != "/api/stocks":
            self.send_error(404)
            return
        if not self._auth_guard():
            return
        code = parse_qs(u.query).get("code", [""])[0]
        with CFG_LOCK:
            cfg = load_cfg()
            stocks = cfg.get("stocks", [])
            cfg["stocks"] = [s for s in stocks if s.get("code") != code]
            save_cfg(cfg)
        # 删除自选时同步清理其K线缓存(指数/宽基ETF等固定标的除外)
        if code not in CACHED_KLINE_CODES:
            _drop_kline_cache(code)
        self._json({"ok": True})


def _rescan_today():
    """--rescan: 删除今日扫描的信号, 启动后扫描线程会立即全量重扫"""
    today = time.strftime("%Y-%m-%d")
    with db.conn() as c:
        cur = c.execute("DELETE FROM div_signal WHERE scan_last=? AND scan_first=?",
                        (today, today))
        removed = cur.rowcount
    print(f"已清除 {today} 扫描的 {removed} 条信号, 启动后将立即重新全量扫描"
          if removed else f"{today} 尚无当日新扫信号, 启动后将直接全量扫描")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="MACD 监控自选管理 Web UI")
    ap.add_argument("--rescan", action="store_true",
                    help="清除今日底背离扫描结果并全量重扫")
    args = ap.parse_args()
    db.init()   # 建表 + 旧div_hist.json一次性导入 + 过期清理
    if ml_model.load_model():   # 元标签信号质量模型(model.json, 由train_model.py生成)
        m = ml_model._MODEL
        print(f"信号质量模型已加载: AUC={m.get('auc')} 基线胜率={m.get('base_win')}%"
              f" 样本={m.get('n_samples')} 训练于{m.get('trained_at')}")
    if args.rescan:
        _rescan_today()
    # 公网部署时建议 WEBUI_HOST=127.0.0.1 仅本机监听, 通过SSH隧道访问
    host = os.environ.get("WEBUI_HOST", "0.0.0.0")
    port = int(os.environ.get("WEBUI_PORT", str(PORT)))
    srv = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=_hub_quote_loop, daemon=True).start()   # SSE行情聚合推送
    threading.Thread(target=_hub_flow_loop, daemon=True).start()   # 主力净流入30s刷新(SSE随行情diff推送)
    threading.Thread(target=_kline_flush_loop, daemon=True).start()  # K线缓存批量落盘(15s)
    threading.Thread(target=_kline_prewarm, daemon=True).start()  # 启动预热自选/指数/ETF的K线缓存
    threading.Thread(target=_div_scanner, daemon=True).start()  # 每日底背离全量扫描
    threading.Thread(target=_track_backfill, daemon=True).start()  # 每日信号跟踪回填
    threading.Thread(target=_ai_pick_loop, daemon=True).start()  # 每日18:00 AI选股(收盘+3小时)
    threading.Thread(target=_intraday_scanner, daemon=True).start()  # 盘中60分钟背离预览
    print(f"自选管理 Web UI 已启动: http://{'localhost' if host in ('127.0.0.1', 'localhost') else host}:{port} (绑定 {host})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
