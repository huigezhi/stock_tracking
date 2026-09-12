#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宏观数据层: 中美通胀指标(月度同比%) + 美债收益率曲线(日度%)

数据源(免费公开接口, 直接调用底层源, 不引入 akshare 依赖):
- 中国 CPI/PPI 同比: 东方财富 datacenter RPT_ECONOMY_CPI / RPT_ECONOMY_PPI
- 美国 CPI/核心CPI 同比: 东方财富 RPT_ECONOMICVALUE_USA
  (美国普通 PPI 同比东财未提供 → 以核心 PPI 同比 EMG00177799 替代并标注;
   中国核心 CPI 同比暂无稳定公开接口 → 界面标注"暂缺", 待补充国家统计局源)
- 美债收益率(1M..30Y 全期限): FRED fredgraph.csv
  (美国财政部官网 CSV 对数据中心 IP 有 WAF 拦截, FRED 数据同源且稳定,
   注意: FRED 拦截 Mozilla UA 但放行 python-requests 默认 UA)

缓存: macro_cache.json(与 kline_cache.json 同模式):
- 首次启动全量拉取; 之后通胀每个交易日收盘后增量刷新(月度数据, 无变化不重复写),
  美债收益率每日刷新一次(cosd 起查7天, 兼容修正)
网络: 复用 net.robust_get(全局限流 + 域名熔断)
"""
import csv
import io
import json
import os
import threading
import time
from datetime import datetime, timedelta

from net import robust_get

try:
    from monitor import now_cst
except Exception:  # 独立运行时退化为 UTC+8
    def now_cst():
        return datetime.utcnow() + timedelta(hours=8)

try:
    import obs
except Exception:
    class _Obs:
        @staticmethod
        def record(*a, **k):
            pass
    obs = _Obs()

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(BASE, "macro_cache.json")

# 期限(展示顺序)与 FRED 序列一一对应
TENORS = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]
FRED_IDS = ["DGS1MO", "DGS3MO", "DGS6MO", "DGS1", "DGS2", "DGS3",
            "DGS5", "DGS7", "DGS10", "DGS20", "DGS30"]

EM_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# 通胀指标定义(key 顺序即卡片顺序)
METRICS = {
    "cn_cpi":      {"label": "中国 CPI 同比",      "country": "中国", "name": "CPI"},
    "cn_core_cpi": {"label": "中国 核心 CPI 同比", "country": "中国", "name": "核心 CPI",
                    "missing": True, "note": "剔除食品和能源, 数据源暂缺"},
    "cn_ppi":      {"label": "中国 PPI 同比",      "country": "中国", "name": "PPI"},
    "us_cpi":      {"label": "美国 CPI 同比",      "country": "美国", "name": "CPI"},
    "us_core_cpi": {"label": "美国 核心 CPI 同比", "country": "美国", "name": "核心 CPI"},
    "us_ppi":      {"label": "美国 核心 PPI 同比", "country": "美国", "name": "PPI",
                    "note": "东财未提供美国 PPI 同比, 以核心 PPI 同比替代"},
}

# 东方财富美国经济指标 ID(月度同比)
_US_EMG = {"us_cpi": "EMG00000733", "us_core_cpi": "EMG00000746",
           "us_ppi": "EMG00177799"}

# 中国 CPI/PPI: (指标key, 东财reportName, 同比字段)
_CN_ECON = (("cn_cpi", "RPT_ECONOMY_CPI", "NATIONAL_SAME"),
            ("cn_ppi", "RPT_ECONOMY_PPI", "BASE_SAME"))

RANGES = {"1y": 1, "3y": 3, "5y": 5, "10y": 10, "all": None}

MACRO_LOCK = threading.Lock()
_CACHE = {"data": None}
_REFRESHING = {"inflation": False, "yields": False}


# ---------------- 缓存读写 ----------------

def _load():
    """进程内懒加载缓存(返回引用, 读多写少)"""
    with MACRO_LOCK:
        if _CACHE["data"] is None:
            try:
                with open(CACHE_PATH, encoding="utf-8") as f:
                    _CACHE["data"] = json.load(f)
            except Exception:
                _CACHE["data"] = {}
        return _CACHE["data"]


def _save(d):
    with MACRO_LOCK:
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, CACHE_PATH)


# ---------------- 数据拉取 ----------------

def _fetch_cn_econ(report, field):
    """中国 CPI/PPI 同比: 东财接口不支持排序参数, 取全量后本地排序
    返回 [["YYYY-MM", 同比%], ...] 升序"""
    params = {"reportName": report, "columns": "ALL",
              "pageSize": "600", "pageNumber": "1"}
    r = robust_get(EM_URL, timeout=15, params=params)
    data = (r.json().get("result") or {}).get("data") or []
    out = []
    for row in data:
        v = row.get(field)
        dt = (row.get("REPORT_DATE") or "")[:7]
        if v is None or not dt:
            continue
        out.append([dt, round(float(v), 2)])
    out.sort(key=lambda x: x[0])
    return out


def _fetch_us_econ(emg_id):
    """美国指标同比: 按日期倒序取全量(未发布的 VALUE 为 null 的行剔除)
    返回 (points升序, 最新发布日期)"""
    params = {
        "reportName": "RPT_ECONOMICVALUE_USA", "columns": "ALL",
        "filter": f'(INDICATOR_ID="{emg_id}")',
        "sortColumns": "REPORT_DATE", "sortTypes": "-1",
        "pageSize": "600", "source": "WEB", "client": "WEB",
    }
    r = robust_get(EM_URL, timeout=15, params=params)
    data = (r.json().get("result") or {}).get("data") or []
    out, publish = [], ""
    for row in data:
        v = row.get("VALUE")
        dt = (row.get("REPORT_DATE") or "")[:7]
        if v is None or not dt:
            continue
        if not publish:   # 倒序 → 第一条有效数据即最新, 取其发布日期
            publish = (row.get("PUBLISH_DATE") or "")[:10]
        out.append([dt, round(float(v), 2)])
    out.sort(key=lambda x: x[0])
    return out, publish


def _fetch_yields(since=None):
    """美债收益率: FRED CSV 全期限批量拉取
    since="YYYY-MM-DD" 时只取该日期起(增量); 返回 {date: [11个期限值或None]}"""
    params = {"id": ",".join(FRED_IDS)}
    if since:
        params["cosd"] = since
    r = robust_get(FRED_URL, timeout=40, params=params)
    if r.status_code != 200 or not r.text.startswith("observation_date"):
        raise RuntimeError(f"FRED 返回异常: HTTP {r.status_code}")
    out = {}
    for row in csv.DictReader(io.StringIO(r.text)):
        d = row.get("observation_date")
        if not d:
            continue
        vals = []
        for fid in FRED_IDS:
            v = (row.get(fid) or "").strip()
            try:
                vals.append(round(float(v), 3) if v and v != "." else None)
            except ValueError:
                vals.append(None)
        out[d] = vals
    return out


# ---------------- 刷新(增量 + 写缓存) ----------------

def refresh_inflation():
    """刷新通胀指标(月度数据量小, 直接全量取后合并); 无变化跳过写盘"""
    with MACRO_LOCK:
        if _REFRESHING["inflation"]:
            return False
        _REFRESHING["inflation"] = True
    try:
        infl, pub = {}, {}
        for key, report, field in _CN_ECON:
            rows = _fetch_cn_econ(report, field)
            if rows:
                infl[key] = rows
        for key, emg in _US_EMG.items():
            rows, publish = _fetch_us_econ(emg)
            if rows:
                infl[key] = rows
                if publish:
                    pub[key] = publish
        if not infl:
            raise RuntimeError("通胀数据全部为空")
        d = _load()
        old_infl = d.get("inflation") or {}
        if old_infl == infl:   # 无变化: 不写盘
            return False
        d["inflation"] = infl
        d["publish"] = {**(d.get("publish") or {}), **pub}
        upd = d.setdefault("updated", {})
        upd["inflation"] = time.time()
        upd["inflation_day"] = now_cst().strftime("%Y-%m-%d")
        _save(d)
        obs.record("INFO", "macro", "通胀数据已更新: " +
                   ",".join(f"{k}@{v[-1][0]}" for k, v in sorted(infl.items())))
        return True
    except Exception as e:
        obs.record("WARN", "macro", f"通胀数据拉取失败: {e}")
        return False
    finally:
        with MACRO_LOCK:
            _REFRESHING["inflation"] = False


def refresh_yields():
    """刷新美债收益率: 增量(cosd=缓存最新日期-7天, 兼容FRED近期修正)"""
    with MACRO_LOCK:
        if _REFRESHING["yields"]:
            return False
        _REFRESHING["yields"] = True
    try:
        d = _load()
        ydata = (d.get("yields") or {}).get("data") or {}
        dates = sorted(ydata)
        since = None
        if dates:
            since = (datetime.strptime(dates[-1], "%Y-%m-%d") -
                     timedelta(days=7)).strftime("%Y-%m-%d")
        fresh = _fetch_yields(since)
        if not fresh:
            raise RuntimeError("FRED 无数据")
        for dt, vals in fresh.items():
            if ydata.get(dt) != vals:
                ydata[dt] = vals
        if not since:   # 首次全量: 只保留1990年以来, 控制缓存体积
            ydata = {k: v for k, v in ydata.items() if k >= "1990-01-01"}
        d["yields"] = {"cols": TENORS, "data": ydata}
        upd = d.setdefault("updated", {})
        upd["yields"] = time.time()
        upd["yields_day"] = now_cst().strftime("%Y-%m-%d")
        _save(d)
        obs.record("INFO", "macro",
                   f"美债收益率已更新: 最新 {sorted(ydata)[-1]}, 共{len(ydata)}个交易日")
        return True
    except Exception as e:
        obs.record("WARN", "macro", f"美债收益率拉取失败: {e}")
        return False
    finally:
        with MACRO_LOCK:
            _REFRESHING["yields"] = False


# ---------------- 更新策略 ----------------

def _stale(d):
    """按更新策略判断哪些数据需要刷新:
    通胀: 交易日(周一~五)收盘后(>=17:00)且当日未刷过; 美债: 每日(>=7:00, 覆盖美东前日收盘)一次"""
    now = now_cst()
    today = now.strftime("%Y-%m-%d")
    upd = d.get("updated") or {}
    need_infl = (not (d.get("inflation") or {})) or (
        now.weekday() < 6 and now.hour >= 17 and upd.get("inflation_day") != today)
    need_yld = (not (d.get("yields") or {}).get("data")) or (
        now.hour >= 7 and upd.get("yields_day") != today)
    return need_infl, need_yld


def daily_update():
    """后台线程周期调用: 按需增量刷新(当日已刷过/非交易日自动跳过)"""
    d = _load()
    need_infl, need_yld = _stale(d)
    if need_infl:
        refresh_inflation()
    if need_yld:
        refresh_yields()


def _bg_refresh():
    """旧数据先出图: 缓存过期时后台线程刷新(不阻塞API)"""
    d = _load()
    need_infl, need_yld = _stale(d)
    if need_infl:
        threading.Thread(target=refresh_inflation, daemon=True).start()
    if need_yld:
        threading.Thread(target=refresh_yields, daemon=True).start()


def _ensure_loaded():
    """API 层入口: 有缓存(可能过期→后台增量刷)直接返回; 冷启动同步拉一次"""
    d = _load()
    if (d.get("inflation") or {}) and (d.get("yields") or {}).get("data"):
        _bg_refresh()
        return d
    refresh_inflation()
    refresh_yields()
    return _load()


# ---------------- API 数据组装 ----------------

def _spread_info(d, with_series=True):
    """10Y-2Y 利差: 当前值/是否倒挂/持续倒挂交易日数/近3年走势"""
    ydata = (d.get("yields") or {}).get("data") or {}
    i10, i2 = TENORS.index("10Y"), TENORS.index("2Y")
    sp = [[dt, round(v[i10] - v[i2], 3)]
          for dt, v in sorted(ydata.items())
          if v[i10] is not None and v[i2] is not None]
    if not sp:
        return {"value": None, "inverted": False, "days": 0, "date": ""}
    latest = sp[-1]
    days = 0
    for _, sv in reversed(sp):
        if sv < 0:
            days += 1
        else:
            break
    out = {"value": latest[1], "inverted": latest[1] < 0, "days": days,
           "date": latest[0]}
    if with_series:
        cutoff = (now_cst() - timedelta(days=365 * 3)).strftime("%Y-%m-%d")
        out["series"] = [r for r in sp if r[0] >= cutoff]
    return out


def serve_overview():
    """顶部指标卡片(6张) + 10Y-2Y利差摘要"""
    d = _ensure_loaded()
    infl = d.get("inflation") or {}
    pub = d.get("publish") or {}
    cards = []
    for key, m in METRICS.items():
        card = {"key": key, "country": m["country"], "name": m["name"],
                "label": m["label"], "missing": bool(m.get("missing"))}
        if m.get("note"):
            card["note"] = m["note"]
        rows = infl.get(key) or []
        if rows:
            latest, prev = rows[-1], (rows[-2] if len(rows) > 1 else None)
            card.update({"date": latest[0], "value": latest[1],
                         "prev": prev[1] if prev else None,
                         "chg": round(latest[1] - prev[1], 2) if prev else None,
                         "publish": pub.get(key, "")})
        cards.append(card)
    return {"ok": True, "cards": cards, "spread": _spread_info(d, with_series=False),
            "updated": d.get("updated") or {}}


def serve_series(metrics_arg, rng="5y"):
    """通胀趋势: ?metrics=cn_cpi,us_cpi&range=1y|3y|5y|10y|all"""
    d = _ensure_loaded()
    keys = [k for k in metrics_arg.split(",") if k in METRICS][:6]
    if not keys:
        keys = ["cn_cpi", "us_cpi"]
    years = RANGES.get(rng, 5)
    cutoff = (None if years is None else
              (now_cst() - timedelta(days=365 * years)).strftime("%Y-%m"))
    infl = d.get("inflation") or {}
    series = []
    for k in keys:
        m = METRICS[k]
        pts = infl.get(k) or []
        if cutoff:
            pts = [p for p in pts if p[0] >= cutoff]
        series.append({"key": k, "label": m["label"], "country": m["country"],
                       "missing": bool(m.get("missing")), "points": pts})
    return {"ok": True, "range": rng, "series": series}


def serve_yields():
    """美债收益率: 当前曲线 + 1年/3年前曲线 + 利差摘要与近3年走势"""
    d = _ensure_loaded()
    ydata = (d.get("yields") or {}).get("data") or {}
    dates = sorted(ydata)

    def curve_at(target):
        """target 日期往回找最近一个有数据的交易日"""
        for dt in reversed(dates):
            if dt <= target:
                return {"date": dt, "points": [[t, v] for t, v in
                        zip(TENORS, ydata[dt]) if v is not None]}
        return None

    now = now_cst()
    cur = curve_at(now.strftime("%Y-%m-%d")) if dates else None
    history = {}
    for label, days in (("1y", 365), ("3y", 365 * 3)):
        c = curve_at((now - timedelta(days=days)).strftime("%Y-%m-%d"))
        if c:
            history[label] = c
    # 前一交易日(环比用)
    prev = curve_at((dates[-2] if len(dates) > 1 else dates[-1])
                    if dates else "") if dates else None
    return {"ok": True, "tenors": TENORS, "current": cur,
            "prev": prev, "history": history,
            "spread": _spread_info(d, with_series=True),
            "updated": d.get("updated") or {}}



