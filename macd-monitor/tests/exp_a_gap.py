#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验A: 验证 DIV_MAX_GAP=120 是否过松(按两低点间隔分桶看20日胜率)"""
import sys, os, statistics
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import db
from datetime import date

with db.conn() as c:
    cols = {r[1] for r in c.execute("PRAGMA table_info(div_signal)")}
    tcols = {r[1] for r in c.execute("PRAGMA table_info(signal_track)")}
    assert {"date1", "date2"} <= cols and {"fwd20"} <= tcols, \
        f"列名不符: {cols} {tcols}"
    rows = c.execute("""SELECT s.date1,s.date2,t.fwd20 FROM div_signal s
        JOIN signal_track t ON t.signal_id=s.id
        WHERE s.tf='day' AND t.fwd20 IS NOT NULL""").fetchall()

bk = {"<=30": [], "31-60": [], "61-90": [], ">90": []}
for d1, d2, f in rows:
    g = (date.fromisoformat(d2) - date.fromisoformat(d1)).days
    bk["<=30" if g <= 30 else "31-60" if g <= 60
       else "61-90" if g <= 90 else ">90"].append(f)

if not rows:
    print("实验A: 无数据(库为空), 无法判定, T1.5 保持跳过")
for k, v in bk.items():
    if v:
        print(f"{k}: n={len(v)} win20={sum(x > 0 for x in v)/len(v)*100:.1f}%"
              f" mean={statistics.mean(v):.2f}%")
