#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""训练底背离信号质量模型(元标签框架)

流程:
1. 回填 signal_track fwd数据(确认日收盘基准, 无前视)
2. 按(code,tf)拉K线, 计算特征(只用确认日及之前数据) + 三重障碍标签
3. Walk-forward 按确认日时间切分评估(无泄漏)
4. 全量训练 LogisticRegression, 特征重要性, 保存 model.json

用法: python3 train_model.py [--skip-backfill] [--use-cache]
                          [--dry-run] [--output PATH]
"""
import argparse
import json
import os
import sys
import time
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np

import db
from model import (FEATURES, EXPANDED, expand_features, build_features,
                   triple_barrier_label, atr_series, load_model)

BASE = os.path.dirname(os.path.abspath(__file__))
AUC_PATH = os.path.join(BASE, "last_train_auc.txt")

# --skip-backfill 时跳过阶段1; --use-cache 时复用 dataset.json
SKIP_BACKFILL = "--skip-backfill" in sys.argv
USE_CACHE = "--use-cache" in sys.argv
DATASET_PATH = "dataset.json"


def parse_args():
    ap = argparse.ArgumentParser(description="训练底背离信号质量模型")
    ap.add_argument("--skip-backfill", action="store_true",
                    help="跳过阶段1(signal_track回填)")
    ap.add_argument("--use-cache", action="store_true",
                    help="复用 dataset.json 缓存数据集")
    ap.add_argument("--dry-run", action="store_true",
                    help="训练但不写 model.json(仍写 last_train_auc.txt)")
    ap.add_argument("--output", default=None,
                    help="输出路径, 默认 model.json")
    return ap.parse_args()


def log(msg):
    print(msg, flush=True)


def phase1_backfill():
    """回填 signal_track(与 webui._track_backfill 同口径, 但不限今日17点)"""
    import webui
    pending = db.pending_track(limit=100000)
    if not pending:
        log("[阶段1] 无待回填信号")
        return
    groups = defaultdict(list)
    for p in pending:
        groups[(p["code"], p["tf"])].append(p)
    log(f"[阶段1] 待回填信号 {len(pending)} 条, {len(groups)} 个(标的,周期)组")
    done = fail = 0
    t0 = time.time()
    for gi, ((code, tf), sigs) in enumerate(groups.items(), 1):
        try:
            klines = webui.fetch_kline(code, tf, 800)
        except Exception:
            fail += len(sigs)
            continue
        if len(klines) < 30:
            fail += len(sigs)
            continue
        dates = [k[0] for k in klines]
        closes = [k[2] for k in klines]
        for s in sigs:
            if s["confirm"] not in dates:
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
            done += 1
        if gi % 200 == 0:
            log(f"  回填进度 {gi}/{len(groups)} 组, 用时 {time.time()-t0:.0f}s")
        time.sleep(0.05)
    log(f"[阶段1] 回填完成: {done} 条写入, {fail} 条数据不可用")


def _ctx_sources():
    """准备 ctx 数据源(一次性): 上证指数K线 / 情绪快照 / 行业动量与归属。
    指数K线走本地缓存(增量+容灾), 网络失败也能用旧数据完成特征对齐"""
    import webui
    idx_bars = None
    try:
        idx_bars = webui.get_kline_cached("sh000001", "day", 800)
    except Exception:
        idx_bars = None
    mood_map = db.zt_mood_map()
    # 行业动量: 确认日各行业涨停股平均涨幅; 行业归属取每只股票最近一次涨停的行业
    ind_mom_map, code_ind = {}, {}
    with db.conn() as c:
        pcols = {r[1] for r in c.execute("PRAGMA table_info(zt_pool)")}
        if {"date", "code", "hybk"} <= pcols:
            for r in c.execute(
                    """SELECT date, hybk, ROUND(AVG(pct), 2) m FROM zt_pool
                       GROUP BY date, hybk"""):
                ind_mom_map[(r["date"], r["hybk"])] = r["m"]
            for r in c.execute(
                    """SELECT p.code, p.hybk FROM zt_pool p
                       JOIN (SELECT code, MAX(date) md FROM zt_pool
                             GROUP BY code) t
                         ON p.code = t.code AND p.date = t.md"""):
                code_ind[r["code"]] = r["hybk"]
    return idx_bars, mood_map, ind_mom_map, code_ind


def phase2_dataset():
    """构建特征+标签数据集(结果缓存到 dataset.json)"""
    if USE_CACHE and os.path.exists(DATASET_PATH):
        with open(DATASET_PATH, encoding="utf-8") as f:
            rows = json.load(f)
        # 特征维度变更后旧缓存不可用(缺新特征键), 自动作废重建
        if rows and all(k in rows[0] for k in FEATURES):
            log(f"[阶段2] 从缓存加载数据集: {len(rows)} 样本")
            return rows
        log("[阶段2] 旧缓存缺新特征键, 作废重建")
    import webui
    with db.conn() as c:
        sigs = [dict(r) for r in c.execute(
            """SELECT s.id, s.code, s.tf, s.date1, s.date2, s.price1, s.price2,
                      s.dif1, s.dif2, s.score, s.tags, s.confirm, s.confirm_close
               FROM div_signal s""").fetchall()]
    groups = defaultdict(list)
    for s in sigs:
        groups[(s["code"], s["tf"])].append(s)
    log(f"[阶段2] 信号 {len(sigs)} 条, {len(groups)} 组, 开始拉K线构建数据集")
    idx_bars, mood_map, ind_mom_map, code_ind = _ctx_sources()
    if not idx_bars:
        log("[阶段2] 上证指数K线不可用, 无法构建指数特征, 终止(防静默填充)")
        sys.exit(1)
    rows = []
    t0 = time.time()
    for gi, ((code, tf), ss) in enumerate(groups.items(), 1):
        try:
            klines = webui.fetch_kline(code, tf, 800)
        except Exception:
            continue
        if len(klines) < 80:
            continue
        dates = [k[0] for k in klines]
        highs = [k[3] for k in klines]
        lows = [k[4] for k in klines]
        closes = [k[2] for k in klines]
        atr = atr_series(highs, lows, closes, 14)
        for s in ss:
            if s["confirm"] not in dates:
                continue
            cidx = dates.index(s["confirm"])
            ind = code_ind.get(s["code"])
            ctx = {"idx": idx_bars,
                   "mood": mood_map.get(s["confirm"]),
                   "ind_mom": (ind_mom_map.get((s["confirm"], ind), 0.0)
                               if ind else 0.0)}
            feats = build_features(klines, s, ctx)
            if feats is None or atr[cidx] is None:
                continue
            label = triple_barrier_label(klines, cidx, atr[cidx])
            if label is None:
                continue
            rows.append({"id": s["id"], "code": s["code"], "tf": s["tf"],
                         "confirm": s["confirm"], "label": label,
                         **feats})
        if gi % 200 == 0:
            log(f"  数据集进度 {gi}/{len(groups)} 组, 样本 {len(rows)}, "
                f"用时 {time.time()-t0:.0f}s")
        time.sleep(0.05)
    log(f"[阶段2] 数据集构建完成: {len(rows)} 样本")
    with open(DATASET_PATH, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
    log(f"[阶段2] 数据集已缓存到 {DATASET_PATH}")
    return rows


def _uniqueness_weights(rows, span_bars=10, bar_days=1.5):
    """同(code,tf)相邻信号的障碍窗口重叠度 -> 权重(独立样本=1.0, 全重叠≈0.4)

    元标签样本天然高度重叠(同一只股票在相邻确认日产生的信号, 标签窗口彼此
    覆盖), 用唯一性权重降低重叠样本在损失函数中的占比(López de Prado 2018)。
    """
    win = timedelta(days=int(span_bars * bar_days))
    w = [1.0] * len(rows)
    by = defaultdict(list)
    for i, r in enumerate(rows):
        by[(r["code"], r["tf"])].append(i)
    for idxs in by.values():
        idxs.sort(key=lambda i: rows[i]["confirm"])
        ts = [datetime.fromisoformat(rows[i]["confirm"]) for i in idxs]
        for j in range(len(idxs)):
            lo = bisect_left(ts, ts[j] - win)
            hi = bisect_right(ts, ts[j] + win)
            w[idxs[j]] = 1.0 / (1.0 + 0.5 * (hi - lo - 1))
    return np.array(w)


def phase3_walk_forward(rows):
    """Walk-forward评估: 按确认日日期分组切分(同日样本不跨折), 4个扩张式折,
    每折用过去训练预测未来; 训练集裁掉标签窗口伸入测试期的样本(purge);
    fit 使用唯一性权重降低重叠样本占比"""
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    rows = sorted(rows, key=lambda r: r["confirm"])
    X = np.array([expand_features(r) for r in rows], dtype=float)
    y = np.array([r["label"] for r in rows], dtype=int)
    confirms = np.array([r["confirm"] for r in rows])
    uniq = _uniqueness_weights(rows)
    n = len(rows)
    log(f"\n[阶段3] walk-forward: {n} 样本, 基线胜率 {y.mean()*100:.1f}%")
    log(f"唯一性权重均值 {uniq.mean():.3f}, "
        f"<0.5 占比 {(uniq < 0.5).mean()*100:.1f}%")
    # 按确认日日期切分: 同一交易日的所有样本落同一折(防同日信息跨折泄漏)
    dates = sorted(set(confirms))
    folds = 4
    if len(dates) <= folds:
        log("  日期数不足, 无法按日期分组切分")
        return {"auc": None, "n": 0, "base_win": None, "buckets": []}
    date_edges = [dates[int(len(dates) * (i + 1) / (folds + 1)) - 1]
                  for i in range(folds)]
    all_pred, all_true = [], []
    for fi, edge in enumerate(date_edges, 1):
        te_end = date_edges[fi] if fi < folds else dates[-1]
        te = (confirms > edge) & (confirms <= te_end)
        # purge: 训练集裁掉标签窗口(10根K线≈15自然日)伸入测试期的样本
        cutoff = (datetime.fromisoformat(edge)
                  - timedelta(days=15)).isoformat()
        tr = confirms <= cutoff
        Xtr, ytr = X[tr], y[tr]
        wtr = uniq[tr]
        Xte, yte = X[te], y[te]
        if len(yte) == 0 or len(np.unique(ytr)) < 2:
            continue
        # 缩尾边界只用训练折数据计算(无泄漏)
        lo = np.percentile(Xtr, 1, axis=0)
        hi = np.percentile(Xtr, 99, axis=0)
        Xtr, Xte = np.clip(Xtr, lo, hi), np.clip(Xte, lo, hi)
        mean, std = Xtr.mean(axis=0), Xtr.std(axis=0)
        std = np.where(std > 1e-12, std, 1.0)
        clf = LogisticRegression(max_iter=3000, C=0.5, class_weight="balanced")
        clf.fit((Xtr - mean) / std, ytr, sample_weight=wtr)
        p = clf.predict_proba((Xte - mean) / std)[:, 1]
        all_pred.extend(p)
        all_true.extend(yte)
    all_pred, all_true = np.array(all_pred), np.array(all_true)
    if len(all_true) == 0 or len(np.unique(all_true)) < 2:
        log("  有效折不足, 无法计算样本外AUC")
        return {"auc": None, "n": 0, "base_win": None, "buckets": []}
    auc = roc_auc_score(all_true, all_pred)
    log(f"  样本外 AUC: {auc:.3f}  (样本 {len(all_true)})")
    # 按模型分分桶看胜率提升
    log("  模型分分桶胜率(样本外):")
    base = all_true.mean()
    log(f"  基线: {base*100:.1f}%")
    wf = {"auc": round(float(auc), 3), "n": int(len(all_true)),
          "base_win": round(float(base) * 100, 1), "buckets": []}
    for lo, hi, name in ((0, 40, "0-40"), (40, 60, "40-60"),
                         (60, 75, "60-75"), (75, 101, ">=75")):
        m = (all_pred * 100 >= lo) & (all_pred * 100 < hi)
        if m.sum() >= 30:
            win = float(all_true[m].mean())
            log(f"    分数{name}: n={m.sum():4d}  胜率 {win*100:.1f}%"
                f"  提升 {(win-base)*100:+.1f}pct")
            wf["buckets"].append(
                {"key": name, "n": int(m.sum()), "win": round(win * 100, 1)})
    return wf


def phase4_train_save(rows, wf=None, out_path=None, dry_run=False):
    """全量训练 + 保存model.json(含缩尾边界与样本外指标, 线上纯Python打分可直接复现)"""
    import datetime
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    out_path = out_path or os.path.join(BASE, "model.json")
    X = np.array([expand_features(r) for r in rows], dtype=float)
    y = np.array([r["label"] for r in rows], dtype=int)
    lo = np.percentile(X, 1, axis=0)
    hi = np.percentile(X, 99, axis=0)
    Xc = np.clip(X, lo, hi)
    mean, std = Xc.mean(axis=0), Xc.std(axis=0)
    std = np.where(std > 1e-12, std, 1.0)
    Xs = (Xc - mean) / std
    clf = LogisticRegression(max_iter=3000, C=0.5, class_weight="balanced")
    clf.fit(Xs, y)
    p = clf.predict_proba(Xs)[:, 1]
    auc = roc_auc_score(y, p)
    model = {
        "mean": mean.tolist(), "std": std.tolist(),
        "lo": lo.tolist(), "hi": hi.tolist(),
        "coef": clf.coef_[0].tolist(), "intercept": float(clf.intercept_[0]),
        "threshold": 50.0, "auc": round(auc, 3),
        "trained_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "n_samples": len(rows), "base_win": round(float(y.mean()) * 100, 1),
    }
    if wf:
        model["wf"] = wf
    if dry_run:
        log(f"\n[阶段4] 全量AUC {auc:.3f}, --dry-run 不写模型文件")
    else:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(model, f, ensure_ascii=False, indent=2)
        log(f"\n[阶段4] 全量AUC {auc:.3f}, 已保存 {out_path}")
    # 无论是否 dry-run 都落盘, 供 retrain_daily 做上线门槛比对
    with open(AUC_PATH, "w", encoding="utf-8") as f:
        f.write(str(round(auc, 3)))
    log("特征重要性(标准化系数绝对值, 前15):")
    order = sorted(zip(EXPANDED, clf.coef_[0]), key=lambda kv: -abs(kv[1]))
    for name, w in order[:15]:
        log(f"    {name:22s} {w:+.3f}")


if __name__ == "__main__":
    args = parse_args()
    SKIP_BACKFILL = args.skip_backfill
    USE_CACHE = args.use_cache
    db.init()
    if not SKIP_BACKFILL:
        phase1_backfill()
    rows = phase2_dataset()
    if len(rows) < 300:
        log(f"样本不足({len(rows)}), 终止")
        sys.exit(1)
    wf = phase3_walk_forward(rows)
    phase4_train_save(rows, wf, out_path=args.output, dry_run=args.dry_run)
