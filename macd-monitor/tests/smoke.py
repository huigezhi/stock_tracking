#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每个任务 commit 前必跑: python3 tests/smoke.py"""
import sys, os
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)


def test_db():
    import db
    db.init()
    rows = db.div_rows(30)
    assert isinstance(rows, list)
    print("ok db")


def test_features_no_lookahead():
    """篡改确认日之后的K线, 特征值必须不变"""
    import model
    base = [[f"2025-{1+i//28:02d}-{1+i%28+1:02d}", 100, 100+i*0.1,
             100+i*0.1+1, 100+i*0.1-1, 1000+i] for i in range(100)]
    sig = {"date1": base[10][0], "date2": base[50][0],
           "price1": base[10][2], "price2": base[50][2],
           "dif1": 0.1, "dif2": 0.05, "score": 5, "tags": "kdj_gold",
           "confirm": base[70][0], "confirm_close": base[70][2]}
    f1 = model.build_features(base, sig)
    tampered = [r[:] for r in base]
    for i in range(71, 100):
        tampered[i][2] = 999.0
    f2 = model.build_features(tampered, sig)
    assert f1 is not None and f2 is not None, "特征构建失败"
    for k in f1:
        assert abs(f1[k] - f2[k]) < 1e-9, f"特征{k}存在未来函数泄漏"
    print("ok no-lookahead")


def test_model_score_range():
    import model
    if not model.load_model():
        print("skip model_score (no model.json)")
        return
    # 用任意合法 feats 验证输出在 0-100
    import random
    feats = {k: random.uniform(0, 1) for k in model.FEATURES}
    s = model.model_score(feats)
    assert s is None or 0 <= s <= 100, f"model_score 越界: {s}"
    print("ok model_score")


if __name__ == "__main__":
    test_db()
    test_features_no_lookahead()
    test_model_score_range()
    print("SMOKE ALL PASS")
