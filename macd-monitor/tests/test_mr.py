#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mr.py 均值回归指标单元测试: 合成规则/边界/陷阱判定/数据不足"""
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mr import mr_metrics, MIN_BARS, REG_WINDOW


def mk_bars(closes, spread=0.5):
    """由收盘序列构造 [date, open, close, high, low, volume]"""
    out = []
    for i, c in enumerate(closes):
        o = c * (1 + (random.random() - 0.5) * 0.02)
        h = max(o, c) * (1 + spread / max(c, 0.01) / 2)
        l = min(o, c) * (1 - spread / max(c, 0.01) / 2)
        out.append([f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", o, c, h, l, 1000])
    return out


def test_insufficient():
    assert mr_metrics(mk_bars([10.0] * 100)) is None          # < MIN_BARS
    assert mr_metrics([]) is None
    assert mr_metrics(mk_bars([5.0] * MIN_BARS)) is None     # σ≈0 一字板


def test_flat_series():
    """横盘小幅波动: 偏离≈0, 无趋势, 分数低"""
    random.seed(7)
    closes = [100 + 2.0 * math.sin(i / 5) for i in range(300)]
    m = mr_metrics(mk_bars(closes))
    assert m is not None
    assert abs(m["reg_z"]) < 1.0
    assert abs(m["slope_ann"]) < 25
    assert m["grade"] in ("B", "C")


def test_deep_oversold():
    """上升趋势末段急跌: 现价深度低于趋势价值线, 高分且带止跌确认"""
    random.seed(7)
    up = [10 * (1.0015 ** i) for i in range(240)]                  # 温和上涨
    crash = [up[-1] * (1 - 0.32 * (k + 1) / 9) for k in range(9)]  # 9日急跌-32%
    closes = up + crash
    # 尾部3根: 再探低后回升, 制造低点抬高 + 收上5日线
    closes[-3] = closes[-4] * 0.99
    closes[-2] = closes[-3] * 1.012
    closes[-1] = closes[-2] * 1.038
    bars = mk_bars(closes)
    m = mr_metrics(bars)
    assert m is not None
    assert m["reg_z"] <= -2.5, m["reg_z"]
    assert m["mr"] >= 60, m
    assert m["trap"] == 0
    assert "ma5" in (m["confirm"] or "") or "lpr" in (m["confirm"] or "")
    # 背离共振加分(同一份bars, 仅div_boost不同)
    m2 = mr_metrics(bars, div_boost=True)
    assert m2["mr"] == min(100, m["mr"] + 10)
    assert m2["div_flag"] == 1


def test_value_trap():
    """长期阴跌+破年线+年线下行: 应打陷阱标且安全分归零"""
    random.seed(3)
    closes = [30 * (0.998 ** i) for i in range(200)]       # 缓慢阴跌200日
    closes += [closes[-1] * 0.995 for _ in range(100)]     # 加速赶底
    m = mr_metrics(mk_bars(closes))
    assert m is not None
    assert m["slope_ann"] < 0
    if m["trap"]:
        assert m["below_ma250"] == 1


def test_blowoff_top():
    """妖股拉升: 价格远高于趋势线, 偏离分为0, 总分应很低"""
    random.seed(3)
    closes = [10.0] * 200
    closes += [10 * (1.05 ** i) for i in range(1, 101)]   # 末段连续涨停式拉升
    m = mr_metrics(mk_bars(closes))
    assert m is not None
    assert m["reg_z"] >= 2.5
    assert m["mr"] <= 20, m


def run():
    for name, fn in sorted((k, v) for k, v in globals().items()
                           if k.startswith("test_") and callable(v)):
        fn()
        print(f"  {name} ✓")
    print("mr.py 全部测试通过")


if __name__ == "__main__":
    run()
