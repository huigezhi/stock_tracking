#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""均值回归指标 MR-Score: 度量现价偏离"实际价值"的程度
纯函数模块, 输入日线bars(列序同 webui.fetch_kline: [date, open, close, high, low, volume],
时间升序且已剔除未收盘K线)。由16:00全市场扫描在底背离检测的同一循环内调用,
复用同一份K线, 零额外网络成本。

价值锚与偏离度量:
  REG_Z    现价相对120日线性回归趋势线拟合值的残差z-score(核心偏离度, σ为单位)
           趋势线代理"实际价值": 价值本身随时间增长/衰退, 比固定均线更真实
  BIAS20/60 传统乖离率%
  POS_250  年内位置: 0=年内最低, 1=年内最高(上市不足250日用全部数据)
  ATR%     ATR14/现价, 把乖离折算成"几个波动单位"后跨股可比
  SLOPE    趋势线斜率年化%, 价值锚的漂移速度(负=价值下沉, 判价值陷阱)

合成MR分(0-100, 越高越接近"深度低估+安全+止跌"):
  偏离50(REG_Z<=-2.5满分) + 位置20(POS<=0.2满分) + 波动乖离15(BIAS20/ATR%<=-3满分)
  + 安全10(斜率>=0满分, 破年线且年线下行=价值陷阱归零)
  + 确认5(3日低点抬高 或 收上5日线) + 共振10(近日存在底背离信号, 调用方传入)
  分级: S>=85且REG_Z<=-2 / A>=70 / B>=55, 其余C
"""
import math

MIN_BARS = 160      # 与底背离扫描同门槛(次新股不评分)
REG_WINDOW = 120    # 趋势线拟合窗口


def _linreg(vals):
    """最小二乘拟合 y = a + b*t, 返回 (末位拟合值, 残差σ, 斜率b)"""
    n = len(vals)
    mt = (n - 1) / 2.0
    mc = sum(vals) / n
    var = cov = 0.0
    for t, v in enumerate(vals):
        var += (t - mt) ** 2
        cov += (t - mt) * (v - mc)
    b = cov / var if var else 0.0
    a = mc - b * mt
    fit = a + b * (n - 1)
    ss = sum((v - (a + b * t)) ** 2 for t, v in enumerate(vals))
    return fit, math.sqrt(ss / n), b


def _atr(bars, n=14):
    """ATR: 最近n根K线真实波幅均值"""
    seg = bars[-(n + 1):]
    if len(seg) < 2:
        return 0.0
    trs = []
    for i in range(1, len(seg)):
        h, l, pc = seg[i][3], seg[i][4], seg[i - 1][2]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)


def mr_metrics(bars, div_boost=False):
    """计算单只股票的均值回归指标, 返回dict(合成分/中间量/分级)或None(数据不足)。
    div_boost: 该股近日存在底背离信号时传True, 加共振分10"""
    n = len(bars)
    if n < MIN_BARS:
        return None
    closes = [b[2] for b in bars]
    c = closes[-1]
    if c <= 0:
        return None

    def _ma(k):
        k = min(k, n)
        return sum(closes[-k:]) / k

    # 1) 核心偏离度: 现价 vs 趋势价值线(σ为单位)
    win = closes[-REG_WINDOW:]
    fit, sd, b = _linreg(win)
    if sd < 1e-9:
        return None   # 长期一字/数据异常, σ≈0无统计意义
    reg_z = (c - fit) / sd
    avg = sum(win) / len(win)
    slope_ann = b / avg * 250 * 100 if avg > 0 else 0.0   # 斜率年化%

    # 2) 乖离率 / 波动率 / 波动单位乖离
    ma20, ma60 = _ma(20), _ma(60)
    bias20 = (c - ma20) / ma20 * 100 if ma20 > 0 else 0.0
    bias60 = (c - ma60) / ma60 * 100 if ma60 > 0 else 0.0
    atr = _atr(bars)
    atr_pct = atr / c * 100 if c > 0 else 0.0
    vol_bias = bias20 / atr_pct if atr_pct > 1e-9 else None

    # 3) 年内位置(高/低点极值口径)
    k250 = min(250, n)
    hi = max(b[3] for b in bars[-k250:])
    lo = min(b[4] for b in bars[-k250:])
    pos250 = (c - lo) / (hi - lo) if hi > lo else None

    # 4) 趋势安全: 破年线且年线下行 = 价值陷阱(数据不足270根时只看斜率)
    ma250 = _ma(250)
    below_ma250 = c < ma250
    trap = False
    if n >= 270 and below_ma250:
        prev250 = sum(closes[-270:-20]) / 250
        trap = ma250 < prev250

    # 5) 止跌确认: 3日低点抬高 或 收上5日线
    l1, l2, l3 = bars[-3][4], bars[-2][4], bars[-1][4]
    conf = []
    if l3 > l2 > l1:
        conf.append("lpr")            # low pivot raise
    if c > _ma(5):
        conf.append("ma5")

    # 6) 合成MR分(各分项线性映射后求和, 越界截断)
    dev = 50.0 * max(0.0, min(1.0, -reg_z / 2.5))
    pos = 20.0 * max(0.0, min(1.0, (0.8 - (pos250 if pos250 is not None
                                           else 0.8)) / 0.6))
    vol = 15.0 * max(0.0, min(1.0, -(vol_bias or 0) / 3.0))
    safe = (10.0 if slope_ann >= 0
            else max(0.0, 10.0 * (1 + slope_ann / 60.0)))   # 年化-60%封底
    if trap:
        safe = 0.0
    confirm = 5.0 if conf else 0.0
    mr = int(round(min(100.0, dev + pos + vol + safe + confirm
                       + (10.0 if div_boost else 0.0))))
    grade = ("S" if mr >= 85 and reg_z <= -2 else
             "A" if mr >= 70 else "B" if mr >= 55 else "C")
    return {
        "close": round(c, 3),
        "reg_z": round(reg_z, 2),          # 偏离趋势价值线几个σ
        "bias20": round(bias20, 2),
        "bias60": round(bias60, 2),
        "pos250": round(pos250, 3) if pos250 is not None else None,
        "atr_pct": round(atr_pct, 2),
        "slope_ann": round(slope_ann, 1),  # 价值锚年化漂移%
        "below_ma250": 1 if below_ma250 else 0,
        "trap": 1 if trap else 0,          # 价值陷阱(破位+年线下行)
        "confirm": "+".join(conf),         # 止跌确认: lpr/ma5
        "div_flag": 1 if div_boost else 0,  # 底背离共振
        "chg5": round((c / closes[-6] - 1) * 100, 2),
        "chg20": round((c / closes[-21] - 1) * 100, 2),
        "mr": mr,
        "grade": grade,
    }
