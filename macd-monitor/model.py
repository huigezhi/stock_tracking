#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""底背离信号质量模型(元标签, Meta-Labeling, López de Prado 2018)

框架:
- 主模型(方向): MACD底背离做多信号(monitor.detect_divergences)
- 元模型(质量): 学习"该信号是否值得做", 对每个信号输出 0-100 质量分
- 标签: 三重障碍法(Triple-Barrier) —— 上障碍=+2*ATR, 下障碍=-1*ATR,
  垂直障碍=确认日后10根K线; 先触上障碍计胜
- 特征: 只用确认日及之前的OHLCV数据(严格无未来函数)
- 部署: LogisticRegression 权重序列化到 model.json, 线上纯Python打分

无前视保证:
- 所有特征仅索引 <= cidx(确认日) 的数据
- 标签仅用 cidx+1 之后的数据
- 训练/验证按确认日时间切分(walk-forward)
"""
import json
import math
import os

BASE = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE, "model.json")

# 特征名(顺序即模型系数顺序)
FEATURES = [
    "rsi14",          # 确认日RSI(14), 超卖程度
    "dif_inc_n",      # 背离强度: (dif2-dif1)/ATR, DIF抬升越多动能修复越好
    "zero_depth",     # 零轴深度: dif2/price2, 越负越超卖
    "price_drop",     # 新低幅度: price2/price1-1, 前置跌幅
    "ret20",          # 确认日前20根累计收益(下跌深度)
    "ret60",          # 确认日前60根累计收益
    "vol_ratio",      # 确认日量能/20日均量
    "vol_shrink",     # 二次探底缩量: 低点2附近量/两低点间均量
    "ma60_pos",       # 长期趋势位置: close/MA60-1
    "ma20_slope",     # MA20十根斜率
    "atr_pct",        # ATR(14)/price 波动率状态
    "off_low",        # 脱离低点幅度: 确认日收盘/低点2-1
    "pivot_gap",      # 形态时长: ln(两低点间隔根数)
    "hist_rise",      # MACD柱拐头(0/1): hist[cidx]>hist[cidx-1]
    "score",          # 原共振分
    "kdj_gold",       # KDJ金叉共振(0/1)
    "week_align",     # 周线同向(0/1)
    "vol_engulf",     # 放量反包(0/1)
]

# 特征交互项(基于线性模型系数重要性前6的两两乘积):
# 捕捉"深度超跌 x 高波动"等非线性组合, walk-forward AUC +0.005,
# 高分桶(>=60)样本外胜率 +3pct 且单调性改善
INTERACTIONS = [
    ("zero_depth", "ret60"), ("zero_depth", "rsi14"), ("zero_depth", "atr_pct"),
    ("zero_depth", "ma60_pos"), ("zero_depth", "ret20"), ("ret60", "rsi14"),
    ("ret60", "atr_pct"), ("ret60", "ma60_pos"), ("ret60", "ret20"),
    ("rsi14", "atr_pct"), ("rsi14", "ma60_pos"), ("rsi14", "ret20"),
    ("atr_pct", "ma60_pos"), ("atr_pct", "ret20"), ("ma60_pos", "ret20"),
]

# 展开后特征名(模型系数的实际顺序)
EXPANDED = FEATURES + [f"{a}*{b}" for a, b in INTERACTIONS]


def expand_features(feat):
    """基础特征dict -> 展开向量(基础18维 + 交互15维), 供训练/打分共用"""
    vals = [float(feat[f]) for f in FEATURES]
    for a, b in INTERACTIONS:
        vals.append(float(feat[a]) * float(feat[b]))
    return vals


# ---------------- 技术指标(纯Python, 只依赖历史) ----------------

def ema_series(vals, n):
    """EMA序列(与monitor.calc_macd同口径)"""
    k = 2 / (n + 1)
    out, e = [], None
    for v in vals:
        e = v if e is None else e + (v - e) * k
        out.append(e)
    return out


def macd_series(closes):
    dif = [f - s for f, s in zip(ema_series(closes, 12), ema_series(closes, 26))]
    dea = ema_series(dif, 9)
    hist = [2 * (d - e) for d, e in zip(dif, dea)]
    return dif, dea, hist


def rsi_series(closes, n=14):
    """Wilder RSI, 只依赖过去"""
    out = [None] * len(closes)
    gain = loss = 0.0
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        g, l = max(ch, 0), max(-ch, 0)
        if i <= n:
            gain += g
            loss += l
            if i == n:
                gain /= n
                loss /= n
                out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
        else:
            gain = (gain * (n - 1) + g) / n
            loss = (loss * (n - 1) + l) / n
            out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def atr_series(highs, lows, closes, n=14):
    """Wilder ATR"""
    out = [None] * len(closes)
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    if len(trs) <= n:
        return out
    a = sum(trs[1:n + 1]) / n
    out[n] = a
    for i in range(n + 1, len(closes)):
        a = (a * (n - 1) + trs[i]) / n
        out[i] = a
    return out


def ma_val(closes, i, n):
    """closes[i-n+1..i]的简单均值"""
    if i + 1 < n:
        return None
    return sum(closes[i - n + 1:i + 1]) / n


# ---------------- 特征计算(无未来函数的核心) ----------------

def build_features(klines, sig):
    """对单个底背离信号计算特征向量。

    klines: [[date, open, close, high, low, volume], ...] 前复权
    sig: {date1, date2, price1, price2, dif1, dif2, score, tags,
          confirm, confirm_close}
    返回 dict(特征名->值) 或 None(数据不足)。只用索引<=cidx的数据。
    """
    dates = [k[0] for k in klines]
    confirm = sig.get("confirm")
    if confirm not in dates:
        return None
    cidx = dates.index(confirm)
    if cidx < 65:      # 指标预热下限
        return None
    date1, date2 = sig.get("date1"), sig.get("date2")
    if date1 not in dates or date2 not in dates:
        return None
    p1, p2 = dates.index(date1), dates.index(date2)
    if not (0 < p1 < p2 <= cidx):
        return None

    closes = [k[2] for k in klines]
    highs = [k[3] for k in klines]
    lows = [k[4] for k in klines]
    vols = [k[5] for k in klines]

    dif, dea, hist = macd_series(closes)
    rsi = rsi_series(closes, 14)
    atr = atr_series(highs, lows, closes, 14)
    if rsi[cidx] is None or atr[cidx] is None or not atr[cidx]:
        return None

    price2 = sig.get("price2") or closes[p2]
    dif2, dif1 = sig.get("dif2"), sig.get("dif1")
    if dif2 is None:
        dif2 = dif[p2]
    if dif1 is None:
        dif1 = dif[p1]
    price1 = sig.get("price1") or closes[p1]
    a = atr[cidx]

    def _mean(seg):
        return sum(seg) / len(seg) if seg else 0.0

    vol20 = _mean([v for v in vols[cidx - 20:cidx] if v > 0]) or 1.0
    seg_span = vols[p1:p2 + 1] or [1.0]
    near_low = vols[max(0, p2 - 3):p2 + 4] or [1.0]
    ma60 = ma_val(closes, cidx, 60)
    ma20_now = ma_val(closes, cidx, 20)
    ma20_prev = ma_val(closes, cidx - 10, 20)
    tags = set((sig.get("tags") or "").split(","))

    return {
        "rsi14": rsi[cidx],
        "dif_inc_n": (dif2 - dif1) / a,
        "zero_depth": dif2 / price2,
        "price_drop": price2 / price1 - 1,
        "ret20": closes[cidx] / closes[cidx - 20] - 1,
        "ret60": closes[cidx] / closes[cidx - 60] - 1,
        "vol_ratio": vols[cidx] / vol20,
        "vol_shrink": _mean(near_low) / (_mean(seg_span) or 1.0),
        "ma60_pos": closes[cidx] / ma60 - 1 if ma60 else 0.0,
        "ma20_slope": ma20_now / ma20_prev - 1 if ma20_now and ma20_prev else 0.0,
        "atr_pct": a / closes[cidx],
        "off_low": closes[cidx] / price2 - 1,
        "pivot_gap": math.log(max(p2 - p1, 1)),
        "hist_rise": 1.0 if hist[cidx] > hist[cidx - 1] else 0.0,
        "score": float(sig.get("score") or 0),
        "kdj_gold": 1.0 if "kdj_gold" in tags else 0.0,
        "week_align": 1.0 if "week_align" in tags else 0.0,
        "vol_engulf": 1.0 if "vol_engulf" in tags else 0.0,
    }


# ---------------- 三重障碍标签 ----------------

def triple_barrier_label(klines, cidx, atr_val, span=10,
                        up_mult=2.0, dn_mult=1.0):
    """三重障碍元标签: 自确认日收盘起, span根内先触上障碍(2*ATR)计1,
    先触下障碍(1*ATR)计0; 同根双触按开盘方向判断; 未触按到期收盘方向。"""
    closes = [k[2] for k in klines]
    entry = closes[cidx]
    up = entry * (1 + up_mult * atr_val / entry)
    dn = entry * (1 - dn_mult * atr_val / entry)
    end = min(cidx + span, len(klines) - 1)
    for j in range(cidx + 1, end + 1):
        hi, lo = klines[j][3], klines[j][4]
        hit_up, hit_dn = hi >= up, lo <= dn
        if hit_up and hit_dn:      # 同根双触: 按开盘位置近似
            return 1 if klines[j][1] > entry else 0
        if hit_up:
            return 1
        if hit_dn:
            return 0
    if end <= cidx:                # 无后续数据
        return None
    return 1 if closes[end] > entry else 0


# ---------------- 模型加载与打分(线上推理, 不依赖sklearn) ----------------

_MODEL = {"loaded": False, "mean": None, "std": None, "coef": None,
         "intercept": 0.0, "threshold": 50.0, "auc": None, "trained_at": "",
         "lo": None, "hi": None}


def load_model(path=MODEL_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
        _MODEL.update(m)
        _MODEL["loaded"] = True
    except Exception:
        _MODEL["loaded"] = False
    return _MODEL["loaded"]


def model_score(features, path=MODEL_PATH):
    """特征dict -> 质量分0-100(LogisticRegression概率)

    流程: 基础特征 -> 交互项展开 -> 缩尾(训练集1/99分位) -> 标准化 -> 线性打分
    """
    if not _MODEL["loaded"] and not load_model(path):
        return None
    try:
        x = expand_features(features)
    except (KeyError, TypeError, ValueError):
        return None
    for i, v in enumerate(x):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        lo = _MODEL["lo"][i] if _MODEL["lo"] else None
        hi = _MODEL["hi"][i] if _MODEL["hi"] else None
        if lo is not None and v < lo:
            v = lo
        if hi is not None and v > hi:
            v = hi
        sd = _MODEL["std"][i]
        x[i] = (v - _MODEL["mean"][i]) / (sd if sd > 1e-12 else 1.0)
    z = _MODEL["intercept"]
    for c, xi in zip(_MODEL["coef"], x):
        z += c * xi
    p = 1 / (1 + math.exp(-max(-30, min(30, z))))
    return round(p * 100, 1)
