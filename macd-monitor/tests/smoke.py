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
    # ctx: 与个股K线同构的指数序列(build_features 需要指数严格对齐到确认日)
    idx = [r[:] for r in base]
    ctx = {"idx": idx, "mood": {"temp": 50, "zb_rate": 30}, "ind_mom": 0.0}
    f1 = model.build_features(base, sig, ctx)
    tampered = [r[:] for r in base]
    for i in range(71, 100):
        tampered[i][2] = 999.0
    f2 = model.build_features(tampered, sig, ctx)
    assert f1 is not None and f2 is not None, "特征构建失败"
    for k in f1:
        assert abs(f1[k] - f2[k]) < 1e-9, f"特征{k}存在未来函数泄漏"
    # 指数K线确认日之后被篡改, 指数特征同样必须不变
    idx2 = [r[:] for r in idx]
    for i in range(71, 100):
        idx2[i][2] = 777.0
    f3 = model.build_features(base, sig, {**ctx, "idx": idx2})
    for k in ("idx_ma60_pos", "idx_ret20"):
        assert abs(f1[k] - f3[k]) < 1e-9, f"ctx特征{k}存在未来函数泄漏"
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


def test_calibrated_score():
    """概率校准: 有wf分桶时按样本外胜率插值, 无wf数据原样返回"""
    import model
    # 1) 无 wf 数据(当前线上模型为残缺stub): 原样返回, 不抛异常
    model.load_model()
    assert model.calibrated_score(66.6) == 66.6, "无wf数据应原样返回"
    assert model.calibrated_score(None) is None
    # 2) 注入 wf 分桶验证插值映射
    saved = dict(model._MODEL)
    try:
        model._MODEL["wf"] = {"buckets": [
            {"key": "0-40", "n": 100, "win": 38.0},
            {"key": "40-60", "n": 80, "win": 45.0},
            {"key": "60-75", "n": 50, "win": 58.0},
            {"key": ">=75", "n": 40, "win": 71.0},
        ]}
        # 控制点: (0,38)(40,38)(60,45)(75,58)(100,71)
        assert model.calibrated_score(20) == 38.0     # 段内平坦
        assert model.calibrated_score(50) == 41.5      # 40-60线性中点
        assert model.calibrated_score(72.5) == 55.8   # 60-75段内插值
        assert model.calibrated_score(75) == 58.0      # 桶边界取分桶胜率
        assert model.calibrated_score(90) == 65.8     # 顶桶内插值(75-100段)
        assert model.calibrated_score(0) == 38.0 and model.calibrated_score(100) == 71.0
        out = model.calibrated_score(66.6)
        assert out != 66.6, "有wf数据时cal与raw应不同(映射生效)"
    finally:
        model._MODEL.update(saved)
    print("ok calibrated_score")


if __name__ == "__main__":
    test_db()
    test_features_no_lookahead()
    test_model_score_range()
    test_calibrated_score()
    print("SMOKE ALL PASS")
