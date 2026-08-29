#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""演示数据准备: 用项目自身的真实扫描管线跑一个代表性子集(沪深300成分+部分热门股),
真实K线 → 真实底背离检测 → 入SQLite信号库, 供截图展示。"""
import sys
sys.path.insert(0, "/workspace/macd-monitor")

import webui
from webui import _scan_one_divs, fetch_all_stocks, now_cst
from concurrent.futures import ThreadPoolExecutor, as_completed
import db
import obs

db.init()

stocks = fetch_all_stocks()
print(f"total stocks: {len(stocks)}")
# 代表性子集: 按0名取每第8只(约660只, 覆盖各行业各市值段)
subset = stocks[::8]
print(f"subset: {len(subset)}")

now = now_cst()
today = now.strftime("%Y-%m-%d")
rows = []
with ThreadPoolExecutor(max_workers=4) as ex:
    futs = [ex.submit(_scan_one_divs, s, now) for s in subset]
    for i, fu in enumerate(as_completed(futs)):
        try:
            rows.extend(fu.result())
        except Exception as e:
            print("err:", e)
        if (i + 1) % 100 == 0:
            print(f"progress {i+1}/{len(futs)}, signals={len(rows)}")

db.upsert_signals(rows, today)
print(f"done: {len(rows)} signals, scan_date={today}")
