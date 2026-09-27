#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验B: 共振分与大盘环境的混淆检验(强市/弱市分组60日胜率)"""
import sys, os
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import db
import webui   # fetch_kline 返回完整OHLCV, 与 build_features 口径一致

kl = webui.fetch_kline("sh000001", "day", 800)
d2c = {k[0]: k[2] for k in kl}
ds = [k[0] for k in kl]
cs = [k[2] for k in kl]
regime = {}  # date -> bool(收盘>MA60)
for i in range(59, len(ds)):
    regime[ds[i]] = cs[i] > sum(cs[i - 59:i + 1]) / 60

with db.conn() as c:
    rows = c.execute("""SELECT s.confirm,s.score,t.fwd60 FROM div_signal s
        JOIN signal_track t ON t.signal_id=s.id
        WHERE s.tf='day' AND t.fwd60 IS NOT NULL AND s.score>=5""").fetchall()

if not rows:
    print("实验B: 无数据(库为空), 无法判定, T1.5b 保持跳过")
for name, flt in (("强市", lambda d: regime.get(d) is True),
                  ("弱市", lambda d: regime.get(d) is False)):
    v = [f for cf, sc, f in rows if flt(cf)]
    if v:
        print(f"{name}: n={len(v)} win60={sum(x > 0 for x in v)/len(v)*100:.1f}%"
              f" mean={sum(v)/len(v):.2f}%")
    else:
        print(f"{name}: 无样本")
