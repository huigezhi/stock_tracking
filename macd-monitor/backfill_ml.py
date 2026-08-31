#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为库内存量信号回填 ml_score(模型质量分)
扫描器已对新信号自动打分; 本脚本用于模型(重)训练后刷新历史信号。
用法: python3 backfill_ml.py
"""
import time
from collections import defaultdict

import db
import model as ml_model
from model import build_features

if not ml_model.load_model():
    raise SystemExit("model.json 不存在, 请先运行 train_model.py")

m = ml_model._MODEL
print(f"模型已加载: AUC={m.get('auc')} 训练于{m.get('trained_at')}", flush=True)

with db.conn() as c:
    sigs = [dict(r) for r in c.execute(
        """SELECT id, code, tf, date1, date2, price1, price2, dif1, dif2,
                  score, tags, confirm, confirm_close FROM div_signal""").fetchall()]

groups = defaultdict(list)
for s in sigs:
    groups[(s["code"], s["tf"])].append(s)

import webui

done = skip = 0
t0 = time.time()
for gi, ((code, tf), ss) in enumerate(groups.items(), 1):
    try:
        klines = webui.fetch_kline(code, tf, 800)
    except Exception:
        skip += len(ss)
        continue
    if len(klines) < 80:
        skip += len(ss)
        continue
    updates = []
    for s in ss:
        feats = build_features(klines, s)
        if feats is None:
            continue
        sc = ml_model.model_score(feats)
        if sc is not None:
            updates.append((sc, s["id"]))
    if updates:
        with db.conn() as c:
            c.executemany("UPDATE div_signal SET ml_score=? WHERE id=?", updates)
        done += len(updates)
    if gi % 200 == 0:
        print(f"  进度 {gi}/{len(groups)} 组, 已打分 {done}, 用时 {time.time()-t0:.0f}s",
              flush=True)
    time.sleep(0.05)
print(f"完成: 打分 {done} 条, 跳过 {skip} 条(数据不足)")
