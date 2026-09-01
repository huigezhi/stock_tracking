#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""涨停板数据层: 东方财富涨停/炸板/跌停股池 + 短线动能评分 + 市场情绪温度
- 数据源: push2ex.eastmoney.com getTopicZTPool/ZBPool/DTPool (收盘后约15:10出全量)
- 过滤: 剔除科创板(688/689) / ST(含*ST) / 北交所(非60/00/30开头)
- 短线动能分(0-100): 封板时间/炸板次数/换手率/流通市值/封单强度/连板结构/
  板密度(几天几板)/板块效应 加权, 用于候选排序与无Key降级选股
- 情绪温度(0-100): 涨停家数/跌停家数/炸板率/连板高度/连板梯队 综合刻画情绪周期
"""
import threading
import time

import requests

import obs

ZT_API = "https://push2ex.eastmoney.com/getTopicZTPool"
ZB_API = "https://push2ex.eastmoney.com/getTopicZBPool"
DT_API = "https://push2ex.eastmoney.com/getTopicDTPool"

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0",
                 "Referer": "https://quote.eastmoney.com/"})

_FLOCK = threading.Lock()   # 熔断: 连续失败后冷却, 防止线程反复打挂接口
_fail_streak = 0
_cool_until = 0.0


def _get(url, date_str, timeout=15):
    """带简单熔断的池子请求, 返回 pool 列表(空=无数据)"""
    global _fail_streak, _cool_until
    with _FLOCK:
        if time.time() < _cool_until:
            return []
    try:
        r = S.get(url, params={
            "ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt",
            "Pageindex": 0, "pagesize": 500, "sort": "fbt:asc",
            "date": date_str}, timeout=timeout)
        pool = ((r.json().get("data") or {}).get("pool")) or []
        with _FLOCK:
            _fail_streak = 0
        return pool
    except Exception as e:
        with _FLOCK:
            _fail_streak += 1
            if _fail_streak >= 3:
                _cool_until = time.time() + 300   # 连续3次失败冷却5分钟
                obs.record("WARN", "zt", f"涨停池接口连续失败{_fail_streak}次: {e!r}")
        return []


def _norm(r):
    """原始行 → 标准行(sh/sz前缀 + 单位换算); fbt: 92500 → '09:25' + 925"""
    code = "sh" + r["c"] if r.get("m") == 1 else "sz" + r["c"]
    fbt = int(r.get("fbt") or 0)          # HHMMSS, 如 92500=09:25:00
    hhmm = fbt // 100                     # 925 / 1456
    ltsz = (r.get("ltsz") or 0) / 1e8     # 亿
    fund = (r.get("fund") or 0) / 1e8     # 亿
    zttj = r.get("zttj") or {}
    return {
        "code": code, "name": (r.get("n") or "").replace(" ", ""),
        "price": (r.get("p") or 0) / 1000, "pct": r.get("zdp"),
        "ltsz": round(ltsz, 1), "hs": round(r.get("hs") or 0, 2),
        "lbc": int(r.get("lbc") or 1), "fbt": hhmm,
        "fbt_s": f"{hhmm // 100:02d}:{hhmm % 100:02d}",
        "zbc": int(r.get("zbc") or 0), "hybk": r.get("hybk") or "",
        "days": int(zttj.get("days") or 0), "ct": int(zttj.get("ct") or 0),
        "fund": round(fund, 2),
        "seal": round(fund / ltsz * 100, 2) if ltsz > 0 else 0,  # 封单/流通市值%
    }


def _tradable(r):
    """过滤: 剔除科创板/ST/北交所, 仅保留沪深主板+创业板"""
    num = r["code"][2:]
    if not (num.startswith(("60", "00", "30"))):
        return False                        # 北交所(8/4/92)等剔除
    if num.startswith(("688", "689")):       # 科创板
        return False
    if "ST" in r["name"].upper():           # ST/*ST/S*ST
        return False
    return True


def fetch_day(date_str):
    """抓某交易日三池(涨停/炸板/跌停), 已过滤; 返回 dict 或 None(接口无数据)"""
    zt_raw = _get(ZT_API, date_str)
    if not zt_raw:                            # 涨停池为空=非交易日或数据未就绪
        return None
    zb_raw = _get(ZB_API, date_str)
    dt_raw = _get(DT_API, date_str)
    zt = [r for r in (_norm(x) for x in zt_raw) if _tradable(r)]
    zb = [r for r in (_norm(x) for x in zb_raw) if _tradable(r)]
    dt = [r for r in (_norm(x) for x in dt_raw) if _tradable(r)]
    return {"date": date_str, "zt": zt, "zb": zb, "dt": dt}


# ---------------- 短线动能评分 ----------------

def score_pool(day):
    """对涨停池逐只打短线动能分(0-100), 就地写入 score/tags 并按分降序; 返回day"""
    rows = day["zt"]
    sec_cnt = {}                              # 板块效应: 同行业涨停家数
    for r in rows:
        if r["hybk"]:
            sec_cnt[r["hybk"]] = sec_cnt.get(r["hybk"], 0) + 1
    for r in rows:
        s = 0.0
        # 1) 封板时间(25): 一字/开盘半小时内最强, 尾盘板最弱
        t = r["fbt"]
        if t <= 930:
            s += 25
        elif t <= 1000:
            s += 21
        elif t <= 1100:
            s += 16
        elif t <= 1400:
            s += 10
        else:
            s += 4
        # 2) 炸板次数(12): 回封越多越弱
        s += max(0, 12 - r["zbc"] * 6)
        # 3) 换手率(16): 首板5-15%筹码交换充分; 连板需要放量
        hs = r["hs"]
        if r["lbc"] == 1:
            s += 16 if 5 <= hs <= 15 else 11 if 3 <= hs <= 25 else 5
        else:
            s += 16 if 10 <= hs <= 25 else 11 if hs > 25 else 6
        # 4) 流通市值(16): 20-100亿游资最佳战场
        lt = r["ltsz"]
        s += 16 if 20 <= lt <= 100 else 13 if lt <= 200 else 9 if lt <= 500 else 3
        # 5) 封单强度(10): 封单资金/流通市值
        s += 10 if r["seal"] >= 3 else 7 if r["seal"] >= 1.5 else 4
        # 6) 连板结构(12): 2板确认动量, 3板龙头, 高位板风险大
        s += {1: 8, 2: 12, 3: 11}.get(r["lbc"], 7)
        # 7) 板密度(5): ct/days>=0.6 主升浪形态(如5天3板)
        if r["days"] and r["ct"]:
            s += 5 if r["ct"] / r["days"] >= 0.6 else 2
        # 8) 板块效应(8): 同板块>=3只涨停为当日主线
        c = sec_cnt.get(r["hybk"], 1) if r["hybk"] else 1
        s += 8 if c >= 3 else 4 if c == 2 else 0
        r["score"] = round(min(100, s))
        r["sec_n"] = c
        # 标签
        tags = []
        if r["lbc"] >= 2:
            tags.append(f"{r['lbc']}连板")
        else:
            tags.append("首板")
        if r["days"] and r["ct"] and r["ct"] < r["days"]:
            tags.append(f"{r['days']}天{r['ct']}板")
        if t <= 930:
            tags.append("一字")
        elif t <= 1000:
            tags.append("早封")
        elif t >= 1400:
            tags.append("尾盘板")
        if r["zbc"] >= 2:
            tags.append("烂板")
        if r["seal"] >= 3:
            tags.append("强封单")
        if c >= 3:
            tags.append("板块效应")
        r["tags"] = ",".join(tags)
    rows.sort(key=lambda r: -r["score"])
    return day


# ---------------- 市场情绪温度 ----------------

STAGES = [(20, "冰点", "down"), (35, "退潮", "down"), (50, "平稳", ""),
          (65, "发酵", "up"), (80, "强势", "up"), (101, "亢奋", "up")]


def mood_of(day):
    """由三池数据计算当日情绪温度(0-100)与阶段"""
    zt_n, zb_n, dt_n = len(day["zt"]), len(day["zb"]), len(day["dt"])
    max_lbc = max((r["lbc"] for r in day["zt"]), default=0)
    lbc2_n = sum(1 for r in day["zt"] if r["lbc"] >= 2)
    zb_rate = zb_n / (zt_n + zb_n) if zt_n + zb_n else 0
    temp = 50 + (zt_n - 50) * 0.35 - dt_n * 2.5 - zb_rate * 55 \
        + min(max_lbc, 8) * 2 + lbc2_n * 0.4
    temp = round(max(0, min(100, temp)))
    for lim, stage, cls in STAGES:
        if temp < lim:
            break
    return {"zt_n": zt_n, "dt_n": dt_n, "zb_n": zb_n, "zb_rate": round(zb_rate * 100, 1),
            "max_lbc": max_lbc, "lbc2_n": lbc2_n, "temp": temp,
            "stage": stage, "stage_cls": cls}
