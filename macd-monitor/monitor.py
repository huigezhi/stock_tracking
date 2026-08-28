#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MACD 金叉/死叉 + 底背离/顶背离 监控 + 飞书机器人提醒
数据源: 腾讯行情 (ifzq.gtimg.cn)  K线: 1m/5m/30m/60m/日线/周线
用法:
  python3 monitor.py --report   # 查看各周期 MACD 状态与近期信号(不发通知、不写状态)
  python3 monitor.py --once     # 扫描一轮, 输出新信号并退出(可用于测试推送)
  python3 monitor.py            # 启动持续监控
"""
import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests

CST = timezone(timedelta(hours=8))
BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
STATE_PATH = os.path.join(BASE, "state.json")
LOG_PATH = os.path.join(BASE, "monitor.log")

TF_MIN = {"1m": 1, "5m": 5, "30m": 30, "60m": 60}
TF_NAME = {"1m": "1分钟", "5m": "5分钟", "30m": "30分钟", "60m": "60分钟", "day": "日线", "week": "周线"}
MIN_BARS = 35          # MACD 至少需要的K线数
MIN_COUNT = 640        # 分钟线拉取根数
DAYW_COUNT = 300       # 日线/周线拉取根数
STATE_LIMIT = 3000     # 状态去重键上限

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("macd")


# ---------------- 基础工具 ----------------

def now_cst():
    return datetime.now(CST)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def norm_code(code):
    """支持 600519 / sh600519 两种写法"""
    code = str(code).strip().lower()
    if len(code) == 6 and code.isdigit():
        if code[0] in "69":
            return "sh" + code
        if code[0] in "03":
            return "sz" + code
    return code


def is_trading_time(now):
    """A股交易时段(含少量缓冲): 周一至周五 9:25-11:35 / 12:55-15:10"""
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return 925 <= hm <= 1135 or 1255 <= hm <= 1510


# ---------------- 行情数据 ----------------

_fail_streak = {}  # (code, tf) -> 连续失败轮数, 用于区分偶发超时与持续故障


def _klines_once(code, tf, count):
    """单次请求K线, 失败抛异常"""
    if tf in TF_MIN:
        url = ("https://ifzq.gtimg.cn/appstock/app/kline/mkline"
               f"?param={code},m{TF_MIN[tf]},,{count}")
        r = S.get(url, timeout=8)
        # 分钟线接口的key是 m1/m5/m30/m60 (修复: 之前误用 "1m" 查不到数据)
        rows = r.json()["data"][code].get(f"m{TF_MIN[tf]}") or []
        return [(row[0], float(row[2])) for row in rows if len(row) >= 3]
    url = ("https://ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={code},{tf},,,{count},qfq")
    r = S.get(url, timeout=8)
    try:
        data = r.json()["data"][code]
    except Exception:
        # fqkline 被WAF拦截(501页面)时回退到非复权接口
        url = ("https://ifzq.gtimg.cn/appstock/app/kline/kline"
               f"?param={code},{tf},,,{count}")
        r = S.get(url, timeout=8)
        data = r.json()["data"][code]
    rows = data.get(tf) or data.get("qfq" + tf) or []
    return [(row[0], float(row[2])) for row in rows if len(row) >= 3]


def fetch_klines(code, tf, count, retries=2):
    """返回 [(label, close), ...] 时间升序, 带重试。
    分钟线 label='202608241500'(K线结束时刻), 日/周线 label='2026-08-24'
    偶发超时只记INFO(不推飞书), 连续多轮失败才升级WARNING推送告警"""
    last_err = None
    for attempt in range(retries + 1):
        try:
            rows = _klines_once(code, tf, count)
            prev = _fail_streak.pop((code, tf), 0)
            if prev >= 4:
                log.info("K线接口已恢复: %s %s (此前连续失败%d轮)", code, tf, prev)
            return rows
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(1 + attempt)  # 退避1s/2s后重试
    streak = _fail_streak.get((code, tf), 0) + 1
    _fail_streak[(code, tf)] = streak
    if streak >= 4:
        log.warning("获取K线失败 %s %s (已连续%d轮, 请检查网络/接口): %s",
                    code, tf, streak, last_err)
    else:
        log.info("获取K线临时失败 %s %s (第%d轮, 下轮自动重试): %s",
                 code, tf, streak, last_err)
    return []


# ---------------- MACD ----------------

def macd(closes, fast=12, slow=26, sig=9):
    """标准MACD(12,26,9), 返回 (DIF, DEA, 柱)"""
    ef = es = dea = None
    dif_l, dea_l, hist_l = [], [], []
    kf, ks, kd = 2 / (fast + 1), 2 / (slow + 1), 2 / (sig + 1)
    for c in closes:
        ef = c if ef is None else ef + (c - ef) * kf
        es = c if es is None else es + (c - es) * ks
        d = ef - es
        dea = d if dea is None else dea + (d - dea) * kd
        dif_l.append(d)
        dea_l.append(dea)
        hist_l.append(2 * (d - dea))
    return dif_l, dea_l, hist_l


def detect_cross(dif, dea, i):
    """第 i 根K线上是否发生交叉: 'golden' / 'death' / None"""
    if i < 1:
        return None
    prev = dif[i - 1] - dea[i - 1]
    cur = dif[i] - dea[i]
    if prev <= 0 < cur:
        return "golden"
    if prev >= 0 > cur:
        return "death"
    return None


# ---------------- 背离检测 ----------------
PIVOT_WIN = 4        # DIF极值确认窗口: 前后各4根已收盘K线
DIV_MIN_GAP = 5      # 参与比较的两个极值间的最少K线数
DIV_MAX_GAP = 120    # 两个极值间最多K线数(超过视为不同波段, 不构成一对)


def find_dif_pivots(dif, upto, k=PIVOT_WIN):
    """已确认的DIF局部极值: bar i 前后各k根DIF都比它大(低点)或都小(高点),
    只扫描索引 <= upto 的已收盘K线。返回 (低点索引列表, 高点索引列表), 升序"""
    lows, highs = [], []
    for i in range(k, upto - k + 1):
        seg = dif[i - k:i + k + 1]
        v = dif[i]
        if seg.count(v) != 1:      # 并列极值(平台)不取, 避免歧义
            continue
        if v == min(seg):
            lows.append(i)
        elif v == max(seg):
            highs.append(i)
    return lows, highs


def detect_divergences(bars, dif, last_completed, pairs=1):
    """在最近 pairs 对相邻DIF极值中检测背离(只用已收盘K线):
      底背离 bull: 价格创新低 + DIF低点抬高(两个低点均在零轴下方)
      顶背离 bear: 价格创新高 + DIF高点降低(两个高点均在零轴上方)
    返回 [{div,p1,p2,c1,c2,d1,d2,confirm}, ...]; confirm=第二个极值确认bar索引,
    信号在 confirm 收盘后成立, 新鲜度判断与金叉/死叉一致(is_fresh_signal)"""
    if last_completed < 2 * PIVOT_WIN + DIV_MIN_GAP:
        return []
    closes = [c for _, c in bars]
    lows, highs = find_dif_pivots(dif, last_completed)
    out = []
    for pivots, kind in ((lows, "bull"), (highs, "bear")):
        # pairs 超过极值数时切片会被钳制到列表头, 导致自身配对, 故按实际数量封顶
        n = min(pairs, len(pivots) - 1) if len(pivots) > 1 else 0
        for p1, p2 in zip(pivots[-n - 1:-1], pivots[-n:]):
            if not (DIV_MIN_GAP <= p2 - p1 <= DIV_MAX_GAP):
                continue
            c1, c2, d1, d2 = closes[p1], closes[p2], dif[p1], dif[p2]
            if kind == "bull":
                ok = d1 < 0 and d2 < 0 and c2 < c1 and d2 > d1
            else:
                ok = d1 > 0 and d2 > 0 and c2 > c1 and d2 < d1
            if ok:
                out.append({"div": kind, "p1": p1, "p2": p2,
                            "c1": c1, "c2": c2, "d1": d1, "d2": d2,
                            "confirm": p2 + PIVOT_WIN})
    return out


# ---------------- K线完成判断 ----------------

def minute_dt(label):
    return datetime.strptime(label, "%Y%m%d%H%M").replace(tzinfo=CST)


def week_id(d):
    iy, iw, _ = d.isocalendar()
    return f"{iy}-W{iw:02d}"


def bar_complete(tf, label, now):
    """该K线是否已收盘完成(只在完成的K线上发信号, 避免盘中反复)"""
    if tf in TF_MIN:
        return now >= minute_dt(label)  # 腾讯分钟线label为结束时刻
    d = datetime.strptime(label, "%Y-%m-%d").date()
    if tf == "day":
        return now >= datetime(d.year, d.month, d.day, 15, 0, tzinfo=CST)
    # 周线: label为该周最新交易日, 属于上周则必然完成; 本周则等周五15:00后
    if week_id(d) != week_id(now.date()):
        return True
    friday = d + timedelta(days=(4 - d.weekday()))
    return now >= datetime(friday.year, friday.month, friday.day, 15, 0, tzinfo=CST)


def period_id(tf, label):
    """去重键: 周线label随交易日变化, 归一化为 年-周号"""
    if tf == "week":
        return week_id(datetime.strptime(label, "%Y-%m-%d").date())
    return label


def fmt_label(tf, label):
    if tf in TF_MIN and len(label) == 12:
        return f"{label[4:6]}-{label[6:8]} {label[8:10]}:{label[10:12]}"
    return label


# ---------------- 飞书推送 ----------------

def feishu_sign(secret, ts):
    s = f"{ts}\n{secret}"
    return base64.b64encode(hmac.new(s.encode("utf-8"), digestmod=hashlib.sha256).digest()).decode()


def sig_word(a):
    """信号中文名"""
    if a.get("kind") == "div":
        return "底背离" if a["div"] == "bull" else "顶背离"
    return "金叉" if a["cross"] == "golden" else "死叉"


def sig_dir(a):
    """信号方向: bull 看涨 / bear 看跌"""
    if a.get("kind") == "div":
        return a["div"]
    return "bull" if a["cross"] == "golden" else "bear"


def fmt_alert_line(a):
    if a.get("kind") == "div":
        bull = a["div"] == "bull"
        w = "底背离" if bull else "顶背离"
        if bull:
            shape = f'价格{a["c1"]:.2f}→{a["c2"]:.2f}创新低 DIF{a["d1"]:.3f}→{a["d2"]:.3f}抬高'
        else:
            shape = f'价格{a["c1"]:.2f}→{a["c2"]:.2f}创新高 DIF{a["d1"]:.3f}→{a["d2"]:.3f}降低'
        return (f'{a["name"]}({a["code"]})[{a["group"]}] {TF_NAME[a["tf"]]} {w} '
                f'{shape} @{fmt_label(a["tf"], a["label"])}')
    w = "金叉" if a["cross"] == "golden" else "死叉"
    flag = " (未收盘确认)" if a.get("forming") else ""
    return (f'{a["name"]}({a["code"]})[{a["group"]}] {TF_NAME[a["tf"]]} {w} '
            f'价格{a["close"]:.2f} DIF {a["dif"]:.3f}/DEA {a["dea"]:.3f} '
            f'@{fmt_label(a["tf"], a["label"])}{flag}')


def send_feishu(cfg, alerts):
    url = str(cfg.get("webhook_url", "")).strip()
    if not url:
        for a in alerts:
            log.info("[未配置webhook,仅控制台] %s", fmt_alert_line(a))
        return
    # 飞书卡片上限100KB, 分批发送(每批20条)防止超限
    for i in range(0, len(alerts), 20):
        send_feishu_card(cfg, alerts[i:i + 20])


def send_feishu_card(cfg, alerts):
    url = str(cfg.get("webhook_url", "")).strip()
    # 卡片颜色按方向: 全部看涨绿/全部看跌红/混合蓝; 标题取统一信号类型
    dirs = {sig_dir(a) for a in alerts}
    template = "green" if dirs == {"bull"} else ("red" if dirs == {"bear"} else "blue")
    words = {sig_word(a) for a in alerts}
    word = words.pop() if len(words) == 1 else "信号"
    elements = []
    for a in alerts:
        if a.get("kind") == "div":
            bull = a["div"] == "bull"
            emoji = "🟢" if bull else "🔴"
            w = "底背离" if bull else "顶背离"
            tagA, tagB = ("低点A", "低点B") if bull else ("高点A", "高点B")
            trend = "价格创新低，DIF低点抬高" if bull else "价格创新高，DIF高点降低"
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content":
                         f'{emoji} **{w}**　**{a["name"]}**（{a["code"]}）[{a["group"]}]\n'
                         f'周期：{TF_NAME[a["tf"]]}　现价：{a["close"]:.2f}\n'
                         f'{tagA} {fmt_label(a["tf"], a["p1"])}　价格 {a["c1"]:.2f}　DIF {a["d1"]:.3f}\n'
                         f'{tagB} {fmt_label(a["tf"], a["p2"])}　价格 {a["c2"]:.2f}　DIF {a["d2"]:.3f}\n'
                         f'{trend}　确认 @{fmt_label(a["tf"], a["label"])}'},
            })
            continue
        emoji = "🟢" if a["cross"] == "golden" else "🔴"
        w = "金叉" if a["cross"] == "golden" else "死叉"
        extra = "　⚠️未收盘确认" if a.get("forming") else ""
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content":
                     f'{emoji} **{w}**　**{a["name"]}**（{a["code"]}）[{a["group"]}]\n'
                     f'周期：{TF_NAME[a["tf"]]}　价格：{a["close"]:.2f}\n'
                     f'DIF {a["dif"]:.3f}　DEA {a["dea"]:.3f}　时间：{fmt_label(a["tf"], a["label"])}{extra}'},
        })
    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"template": template,
                       "title": {"tag": "plain_text",
                                 "content": f"MACD {word}提醒（{len(alerts)}条）"}},
            "elements": elements,
        },
    }
    secret = str(cfg.get("webhook_secret", "")).strip()
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = feishu_sign(secret, ts)
    try:
        r = S.post(url, json=payload, timeout=10)
        res = r.json()
        if res.get("code") == 0 or res.get("StatusCode") == 0:
            log.info("飞书推送成功: %d 条信号", len(alerts))
        else:
            log.error("飞书推送失败: %s", res)
    except Exception as e:
        log.error("飞书推送异常: %s", e)


def send_feishu_text(cfg, text):
    """发送纯文本消息到飞书(用于日志/状态同步)"""
    url = str(cfg.get("webhook_url", "")).strip()
    if not url:
        return
    payload = {"msg_type": "text", "content": {"text": text}}
    secret = str(cfg.get("webhook_secret", "")).strip()
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = feishu_sign(secret, ts)
    try:
        r = S.post(url, json=payload, timeout=10)
        res = r.json()
        if res.get("code") == 0 or res.get("StatusCode") == 0:
            log.info("飞书文本已推送: %s", text.splitlines()[0][:40])
        else:
            log.error("飞书文本推送失败: %s", res)
    except Exception as e:
        log.error("飞书文本推送异常: %s", e)


class FeishuLogHandler(logging.Handler):
    """将指定级别以上的日志实时转发到飞书(默认WARNING, 可通过 feishu_log_level 调整)"""
    _sending = False

    def emit(self, record):
        if self._sending:
            return
        cfg = load_json(CONFIG_PATH, {})
        if not str(cfg.get("webhook_url", "")).strip():
            return
        level = str(cfg.get("feishu_log_level", "WARNING")).upper()
        if record.levelno < getattr(logging, level, logging.WARNING):
            return
        self._sending = True
        try:
            send_feishu_text(cfg, f"[MACD监控] {record.levelname} {record.getMessage()}")
        finally:
            self._sending = False


# ---------------- 扫描逻辑 ----------------

def is_fresh_signal(tf, label, now):
    """信号是否值得即时推送: 只推刚完成不久的信号。
    分钟线: K线收盘距今不超过3个周期(1m→3分钟, 5m→15分钟, 30m→90分钟, 60m→3小时);
    日线: 当天; 周线: 本周。
    停机/重启期间错过的历史信号只入库去重、不推送(历史信号无即时意义)。"""
    if tf in TF_MIN:
        age = (now - minute_dt(label)).total_seconds()
        return -60 <= age <= TF_MIN[tf] * 180
    d = datetime.strptime(label, "%Y-%m-%d").date()
    if tf == "day":
        return d == now.date()
    return week_id(d) == week_id(now.date())


def scan(cfg, state, now):
    """扫描全部股票/周期, 返回值得推送的新信号列表。
    全部交叉信号入库去重; 但只推送"新鲜"信号(刚完成不久), 历史信号静默入库。"""
    signals = state["signals"]
    primed_stocks = state.setdefault("primed_stocks", [])
    alerts = []
    skipped = 0  # 入库但不推送的历史信号数
    for stock in cfg.get("stocks", []):
        code = norm_code(stock["code"])
        name = stock.get("name", code)
        group = stock.get("group", "自选")
        for tf in cfg.get("timeframes", []):
            bars = fetch_klines(code, tf, MIN_COUNT if tf in TF_MIN else DAYW_COUNT)
            if len(bars) < MIN_BARS:
                continue
            closes = [c for _, c in bars]
            dif, dea, _ = macd(closes)
            n = len(bars)
            forming = not bar_complete(tf, bars[-1][0], now)
            last_completed = n - 2 if forming else n - 1
            # 盘中可选: 对正在形成的K线也提示(标记"未收盘确认")
            check_upto = n - 1 if (forming and cfg.get("signal_on_forming_bar")) else last_completed
            for i in range(1, check_upto + 1):
                cross = detect_cross(dif, dea, i)
                if not cross:
                    continue
                key = f"{code}|{tf}|{period_id(tf, bars[i][0])}"
                if key in signals:
                    continue
                signals[key] = cross
                if not is_fresh_signal(tf, bars[i][0], now):
                    skipped += 1  # 历史信号: 只入库去重, 不推送
                    continue
                alerts.append({
                    "kind": "cross",
                    "code": code, "name": name, "group": group, "tf": tf,
                    "cross": cross, "label": bars[i][0], "close": closes[i],
                    "dif": dif[i], "dea": dea[i], "forming": i > last_completed,
                })
            # ---- 背离检测(只用已收盘K线; 每对DIF极值只报一次) ----
            for dv in detect_divergences(bars, dif, last_completed):
                key = (f"{code}|{tf}|div|{period_id(tf, bars[dv['p1']][0])}"
                       f"|{period_id(tf, bars[dv['p2']][0])}")
                if key in signals:
                    continue
                signals[key] = dv["div"]
                label = bars[dv["confirm"]][0]  # 第二个极值被确认的K线 = 信号成立时刻
                if not is_fresh_signal(tf, label, now):
                    skipped += 1  # 历史背离: 只入库去重, 不推送
                    continue
                alerts.append({
                    "kind": "div", "div": dv["div"],
                    "code": code, "name": name, "group": group, "tf": tf,
                    "label": label, "close": closes[dv["confirm"]],
                    "dif": dif[dv["confirm"]], "dea": dea[dv["confirm"]],
                    "forming": False,
                    "p1": bars[dv["p1"]][0], "p2": bars[dv["p2"]][0],
                    "c1": dv["c1"], "c2": dv["c2"], "d1": dv["d1"], "d2": dv["d2"],
                })
            time.sleep(0.1)
        if code not in primed_stocks:
            primed_stocks.append(code)
    if skipped:
        log.info("历史信号%d条已入库(不推送, 仅去重)", skipped)
    # 状态瘦身
    while len(signals) > STATE_LIMIT:
        signals.pop(next(iter(signals)))
    state["primed"] = True
    state["minute_primed"] = True
    return alerts


def report(cfg):
    """打印各周期MACD状态与最近60根完成K线内的信号, 不发通知"""
    now = now_cst()
    print(f"MACD 状态报告　{now:%Y-%m-%d %H:%M} (北京时间)")
    print("=" * 78)
    for stock in cfg.get("stocks", []):
        code = norm_code(stock["code"])
        name = stock.get("name", code)
        group = stock.get("group", "自选")
        print(f"\n◆ {name} {code} [{group}]")
        for tf in cfg.get("timeframes", []):
            bars = fetch_klines(code, tf, MIN_COUNT if tf in TF_MIN else DAYW_COUNT)
            if len(bars) < MIN_BARS:
                print(f"  {TF_NAME[tf]:>4}　数据不足")
                continue
            closes = [c for _, c in bars]
            dif, dea, hist = macd(closes)
            i = len(bars) - 1
            forming = not bar_complete(tf, bars[i][0], now)
            last_completed = i - 1 if forming else i
            recent = None
            for j in range(last_completed, 0, -1):
                c = detect_cross(dif, dea, j)
                if c:
                    recent = (c, bars[j][0], last_completed - j)
                    break
            gap = dif[i] - dea[i]
            trend = "多头" if gap > 0 else "空头"
            status = "形成中" if forming else "已收盘"
            print(f"  {TF_NAME[tf]:>4}　DIF {dif[i]:>9.3f}　DEA {dea[i]:>9.3f}　"
                  f"柱 {hist[i]:>9.3f}　{trend}　[{status}]")
            if recent:
                c, lb, ago = recent
                w = "🟢金叉" if c == "golden" else "🔴死叉"
                print(f"  　　　└ 最近信号: {w} @ {fmt_label(tf, lb)} ({ago}根K线前)")
            for dv in detect_divergences(bars, dif, last_completed, pairs=2):
                w = "🟢底背离" if dv["div"] == "bull" else "🔴顶背离"
                print(f"  　　　└ 最近背离: {w} {fmt_label(tf, bars[dv['p1']][0])} → "
                      f"{fmt_label(tf, bars[dv['p2']][0])}　"
                      f"价格 {dv['c1']:.2f}→{dv['c2']:.2f}　DIF {dv['d1']:.3f}→{dv['d2']:.3f}")
            time.sleep(0.1)
    print("\n(柱=DIF-DEA的2倍; 距离0越近越接近交叉)")


# ---------------- 主流程 ----------------

def main():
    ap = argparse.ArgumentParser(description="MACD金叉/死叉/背离监控 + 飞书提醒")
    ap.add_argument("--report", action="store_true", help="查看MACD状态报告, 不发通知")
    ap.add_argument("--once", action="store_true", help="扫描一轮后退出(测试用)")
    args = ap.parse_args()

    cfg = load_json(CONFIG_PATH, {})
    if not cfg.get("stocks"):
        log.error("config.json 中没有股票, 请先配置")
        return

    if args.report:
        report(cfg)
        return

    state = load_json(STATE_PATH, {"signals": {}, "primed": False})
    if "signals" not in state:
        state["signals"] = {}

    if args.once:
        alerts = scan(cfg, state, now_cst())
        save_json(STATE_PATH, state)
        if alerts:
            for a in alerts:
                print(fmt_alert_line(a))
            send_feishu(cfg, alerts)
        else:
            print("无新信号")
        return

    log.info("启动MACD监控: %d只股票 × %d个周期 %s",
             len(cfg["stocks"]), len(cfg.get("timeframes", [])),
             cfg.get("timeframes"))
    # 日志实时同步到飞书(WARNING及以上), 心跳状态定时推送
    log.addHandler(FeishuLogHandler())
    send_feishu_text(cfg, f"✅ MACD监控已启动\n"
                          f"标的: {len(cfg['stocks'])}只 × {len(cfg.get('timeframes', []))}周期\n"
                          f"信号: 金叉/死叉 + 底背离/顶背离\n"
                          f"周期: {'/'.join(cfg.get('timeframes', []))}\n"
                          f"通知策略: 交易时段即时推送信号; 非交易时段/非交易日静默")
    start_ts = time.time()
    last_hb = time.time()
    last_round = "尚未扫描"
    while True:
        now = now_cst()
        trading = is_trading_time(now)
        try:
            # 每轮热加载配置, Web UI 增删股票后无需重启即可生效
            cfg = load_json(CONFIG_PATH, cfg)
            if cfg.get("stocks"):
                t0 = time.time()
                alerts = scan(cfg, state, now)
                if alerts:
                    for a in alerts:
                        log.info("信号: %s", fmt_alert_line(a))
                    if trading:
                        # 交易时段: 即时推送
                        send_feishu(cfg, alerts)
                    else:
                        # 非交易时段/非交易日: 信号仅入库记录, 不推送
                        log.info("非交易时段, %d条信号仅记录不推送", len(alerts))
                save_json(STATE_PATH, state)
                last_round = (f"{now_cst():%H:%M:%S} 新信号{len(alerts)}条 "
                              f"耗时{time.time() - t0:.1f}s")
                log.info("本轮扫描完成: %s", last_round)
            # 心跳状态摘要(默认每30分钟, feishu_status_interval_min 可调, 0=关闭)
            # 仅交易时段推送心跳; 非交易时段/非交易日完全静默
            hb_min = cfg.get("feishu_status_interval_min", 30)
            if hb_min and trading and time.time() - last_hb >= float(hb_min) * 60:
                last_hb = time.time()
                mins = int((time.time() - start_ts) // 60)
                send_feishu_text(cfg,
                                 f"📊 MACD监控状态\n"
                                 f"时间: {now_cst():%Y-%m-%d %H:%M} (北京时间)\n"
                                 f"状态: 运行中 (已运行 {mins // 60}小时{mins % 60}分)\n"
                                 f"标的: {len(cfg.get('stocks', []))}只\n"
                                 f"最后一轮: {last_round}\n"
                                 f"当前: 交易时段({cfg.get('poll_interval_sec', 30)}秒轮询)")
        except Exception:
            log.exception("扫描异常")
        interval = cfg.get("poll_interval_sec", 30) if is_trading_time(now_cst()) else 300
        time.sleep(interval)


if __name__ == "__main__":
    main()
