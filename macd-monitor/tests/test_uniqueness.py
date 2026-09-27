#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""唯一性权重(_uniqueness_weights)单测: T4.3
- 单样本权重=1.0
- 同(code,tf)相邻信号重叠 -> 权重衰减(<1.0), 远离障碍窗口的样本恢复1.0
- 不同code相同时段互不影响(各自独立=1.0)
注: 默认span_bars=10, bar_days=1.5 -> 障碍窗口±15天; 第4个样本须距前一个
>15天才能验证"独立样本恢复1.0"(方案的01-20距01-08仅12天仍在窗口内,
故用02-01替代以匹配实际实现语义)。"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from train_model import _uniqueness_weights


def _row(code, confirm, tf="day"):
    return {"code": code, "tf": tf, "confirm": confirm}


def test_single_is_one():
    w = _uniqueness_weights([_row("sh600000", "2025-01-10T00:00:00")])
    assert abs(w[0] - 1.0) < 1e-9, f"单样本应为1.0, 实际{w[0]}"


def test_overlap_decays():
    rows = [_row("sh600000", f"2025-01-{d:02d}T00:00:00") for d in (2, 5, 8)]
    rows.append(_row("sh600000", "2025-02-01T00:00:00"))   # 距01-08为24天 > 15天窗口
    w = _uniqueness_weights(rows)
    # 前3个互相落在彼此±15天窗口内 -> 权重衰减(各与另2个重叠, 均=1/(1+0.5*2)=0.5)
    assert w[0] < 1.0 and w[1] < 1.0 and w[2] < 1.0, f"重叠样本应衰减: {w}"
    # 第4个独立样本恢复1.0
    assert abs(w[3] - 1.0) < 1e-9, f"独立样本应恢复1.0, 实际{w[3]}"


def test_cross_code_independent():
    rows = [_row("sh600000", "2025-01-02T00:00:00"),
            _row("sz000001", "2025-01-02T00:00:00")]
    w = _uniqueness_weights(rows)
    assert abs(w[0] - 1.0) < 1e-9 and abs(w[1] - 1.0) < 1e-9, \
        f"不同code同时段应互不影响: {w}"


if __name__ == "__main__":
    test_single_is_one()
    test_overlap_decays()
    test_cross_code_independent()
    print("UNIQUENESS TESTS PASS")
