#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网络层公共模块: 全局限流 + 域名熔断 + 行情备用源容灾
- RateLimiter 令牌桶: 进程内所有出网请求统一限速, 防止瞬时并发触发数据源WAF
- CircuitBreaker 域名熔断: 同域名连续失败达阈值 → 冷却期内请求直接快速失败,
  冷却结束后放行探测请求, 成功即回切
- fetch_quotes_any: 腾讯批量行情 + 新浪逐个行情双源容灾
"""
import json
import re
import threading
import time

import requests

import obs

S = requests.Session()

# ---------------- 令牌桶限流 ----------------


class RateLimiter:
    """简单令牌桶: rate=每秒令牌数, burst=桶容量"""

    def __init__(self, rate=8, burst=16):
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.ts = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self):
        """阻塞直到拿到一个令牌"""
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.burst, self.tokens + (now - self.ts) * self.rate)
                self.ts = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.rate
            time.sleep(wait)


LIMITER = RateLimiter(rate=8, burst=16)   # 全局 8 req/s


def get(url, timeout=8, headers=None, **kw):
    """限流版 GET: 所有行情数据源请求统一入口"""
    LIMITER.acquire()
    return S.get(url, timeout=timeout, headers=headers, **kw)


# ---------------- 域名熔断 ----------------


class CircuitBreaker:
    """同域名连续 fail_threshold 次失败 → 熔断 cooldown_sec; 冷却后放行半开探测"""

    def __init__(self, fail_threshold=10, cooldown_sec=300):
        self.fail_threshold = fail_threshold
        self.cooldown_sec = cooldown_sec
        self.fails = {}      # domain -> 连续失败次数
        self.open_until = {}  # domain -> 熔断截止时间戳
        self.lock = threading.Lock()

    def check(self, url):
        """请求前检查: 熔断中返回 False(跳过), 正常/半开返回 True"""
        domain = url.split("/")[2]
        with self.lock:
            until = self.open_until.get(domain, 0)
            if time.time() >= until:
                return True
            return False    # 冷却期内: 直接放弃该域名

    def report(self, url, ok):
        domain = url.split("/")[2]
        with self.lock:
            if ok:
                was_open = domain in self.open_until
                self.fails[domain] = 0
                self.open_until.pop(domain, None)
            else:
                n = self.fails.get(domain, 0) + 1
                self.fails[domain] = n
                if n >= self.fail_threshold:
                    self.open_until[domain] = time.time() + self.cooldown_sec
                    self.fails[domain] = 0
        if not ok and domain in self.open_until:
            obs.record("ERROR", "net", f"域名熔断开启: {domain} 冷却{self.cooldown_sec}s")
        elif ok and was_open:
            obs.record("INFO", "net", f"域名恢复回切: {domain}")

    def status(self):
        with self.lock:
            now = time.time()
            return {d: {"fails": f, "open_sec": max(0, int(self.open_until.get(d, 0) - now))}
                    for d, f in self.fails.items() if f or d in self.open_until}


BREAKER = CircuitBreaker(fail_threshold=10, cooldown_sec=300)


def robust_get(url, timeout=8, headers=None, **kw):
    """限流 + 熔断版 GET: 熔断中直接抛 RuntimeError(调用方走备用源)"""
    if not BREAKER.check(url):
        raise RuntimeError(f"circuit open: {url.split('/')[2]}")
    try:
        r = get(url, timeout=timeout, headers=headers, **kw)
        BREAKER.report(url, True)
        return r
    except Exception:
        BREAKER.report(url, False)
        raise


# ---------------- 行情多源容灾 ----------------

_SINA_Q_HEADERS = {"Referer": "https://finance.sina.com.cn"}


def _sina_quote(code):
    """新浪单标的实时行情(腾讯批量失败时的逐个回退源)
    返回 {name, price, chg, chg_pct, volume, amount, mcap} 或 None"""
    try:
        r = robust_get(f"https://hq.sinajs.cn/list={code}", timeout=6,
                       headers=_SINA_Q_HEADERS)
        text = r.content.decode("gbk", errors="replace")
        m = re.search(r'="([^"]*)"', text)
        if not m:
            return None
        f = m.group(1).split(",")
        if len(f) < 32 or not f[3]:
            return None
        price = float(f[3])
        prev = float(f[2])
        # 新浪字段: 0名称 1今开 2昨收 3现价 ... 8成交量(股) 9成交额(元)
        return {
            "name": f[0], "price": price,
            "chg": round(price - prev, 3),
            "chg_pct": round((price - prev) / prev * 100, 2) if prev else 0,
            "volume": float(f[8] or 0) / 100,        # 股→手
            "amount": float(f[9] or 0) / 10000,      # 元→万元
            "mcap": None,                            # 新浪实时接口无总市值
        }
    except Exception:
        return None


def fetch_quotes_any(codes):
    """批量实时行情容灾: 腾讯批量 → 新浪逐个(仅补腾讯缺失的代码)
    返回 {code: {...}}; 全部失败返回 {}"""
    out = {}
    codes = [c for c in codes if c]
    if not codes:
        return out
    # 主源: 腾讯批量
    try:
        r = robust_get("https://qt.gtimg.cn/q=" + ",".join(codes), timeout=8)
        text = r.content.decode("gbk", errors="replace")
        for m in re.finditer(r'v_(\w+)="([^"]*)"', text):
            code, f = m.group(1), m.group(2).split("~")
            if len(f) < 46 or not f[3]:
                continue
            out[code] = {
                "name": f[1], "price": float(f[3]),
                "chg": float(f[31] or 0), "chg_pct": float(f[32] or 0),
                "volume": float(f[36] or 0),   # 手
                "amount": float(f[37] or 0),   # 万元
                "mcap": float(f[45] or 0),     # 总市值(亿)
            }
    except Exception:
        pass
    # 备源: 新浪逐个补缺(每次最多补40个, 防止极端场景限流)
    missing = [c for c in codes if c not in out][:40]
    for c in missing:
        q = _sina_quote(c)
        if q:
            out[c] = q
    if missing:
        got = sum(1 for c in missing if c in out)
        obs.record("WARN" if got else "ERROR", "net",
                   f"行情主源缺{len(missing)}个, 新浪备源补回{got}个")
    return out


def health():
    """数据源健康快照(供 /api/health)"""
    return {
        "rate_limiter": {"rate": LIMITER.rate, "burst": LIMITER.burst,
                         "tokens": round(LIMITER.tokens, 1)},
        "domains": BREAKER.status(),
    }
