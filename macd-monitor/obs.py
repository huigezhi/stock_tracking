#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可观测性模块: 进程内结构化事件环形缓冲
- record(): 任意线程记录 {ts, lvl, mod, msg}(INFO/WARN/ERROR)
- recent(): 供 /api/logs 查询(支持 lvl/mod/limit 过滤)
- health(): 供 /api/health(运行时长/近24h错误数/事件总数)
net.py(数据源切换/熔断) 与 webui.py(扫描/SSE/认证) 均写入此缓冲"""
import collections
import threading
import time

START_TS = time.time()

_LOCK = threading.Lock()
_BUF = collections.deque(maxlen=600)   # 环形缓冲, 只保留最近600条
_ERR_TS = collections.deque(maxlen=500)  # 近期错误时间戳(算24h错误数用)


def record(lvl, mod, msg):
    """记录一条结构化事件; lvl: INFO/WARN/ERROR"""
    ts = time.time()
    with _LOCK:
        _BUF.append({"ts": round(ts, 3), "lvl": lvl, "mod": mod, "msg": msg})
        if lvl == "ERROR":
            _ERR_TS.append(ts)
            while _ERR_TS and ts - _ERR_TS[0] > 86400:
                _ERR_TS.popleft()


def recent(lvl=None, mod=None, limit=100):
    """倒序返回最近事件(新的在前), 可按级别/模块过滤"""
    out = []
    with _LOCK:
        for ev in reversed(_BUF):
            if lvl and ev["lvl"] != lvl:
                continue
            if mod and ev["mod"] != mod:
                continue
            out.append(ev)
            if len(out) >= limit:
                break
    return out


def health():
    uptime = int(time.time() - START_TS)
    with _LOCK:
        n = len(_BUF)
        errs = len(_ERR_TS)
    return {"uptime_sec": uptime, "events": n, "errors_24h": errs}
