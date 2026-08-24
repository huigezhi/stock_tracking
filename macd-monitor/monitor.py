#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MACD 金叉/死叉监控 + 飞书机器人提醒
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

def fetch_klines(code, tf, count):
    """返回 [(label, close), ...] 时间升序。
    分钟线 label='202608241500'(K线结束时刻), 日/周线 label='2026-08-24'"""
    try:
        if tf in TF_MIN:
            url = ("https://ifzq.gtimg.cn/appstock/app/kline/mkline"
                   f"?param={code},m{TF_MIN[tf]},,{count}")
            key = f"m{TF_MIN[tf]}"
            r = S.get(url, timeout=10)
            data = r.json()["data"][code]
        else:
            url = ("https://ifzq.gtimg.cn/appstock/app/fqkline/get"
                   f"?param={code},{tf},,,{count},qfq")
            r = S.get(url, timeout=10)
            try:
                data = r.json()["data"][code]
            except Exception:
                # fqkline 被WAF拦截(501页面)时回退到非复权接口
                url = ("https://ifzq.gtimg.cn/appstock/app/kline/kline"
                       f"?param={code},{tf},,,{count}")
                r = S.get(url, timeout=10)
                data = r.json()["data"][code]
        rows = data.get(tf) or data.get("qfq" + tf) or []
        return [(row[0], float(row[2])) for row in rows if len(row) >= 3]
    except Exception as e:
        log.warning("获取K线失败 %s %s: %s", code, tf, e)
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


def fmt_alert_line(a):
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
    kinds = {a["cross"] for a in alerts}
    if kinds == {"golden"}:
        template, word = "green", "金叉"
    elif kinds == {"death"}:
        template, word = "red", "死叉"
    else:
        template, word = "blue", "交叉"
    elements = []
    for a in alerts:
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


# ---------------- 扫描逻辑 ----------------

def scan(cfg, state, now):
    """扫描全部股票/周期, 返回新信号列表。
    首次运行(未prime)只记录历史信号、仅提醒最新一根完成K线上的交叉;
    之后运行可完整补漏停机期间错过的信号(去重键保证不重复提醒)。"""
    first_run = not state.get("primed")
    alerts = []
    signals = state["signals"]
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
                if first_run and i != last_completed:
                    continue  # 首次运行: 历史信号只入库不提醒
                alerts.append({
                    "code": code, "name": name, "group": group, "tf": tf,
                    "cross": cross, "label": bars[i][0], "close": closes[i],
                    "dif": dif[i], "dea": dea[i], "forming": i > last_completed,
                })
            time.sleep(0.1)
    # 状态瘦身
    while len(signals) > STATE_LIMIT:
        signals.pop(next(iter(signals)))
    state["primed"] = True
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
            time.sleep(0.1)
    print("\n(柱=DIF-DEA的2倍; 距离0越近越接近交叉)")


# ---------------- 主流程 ----------------

def main():
    ap = argparse.ArgumentParser(description="MACD金叉/死叉监控 + 飞书提醒")
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
    while True:
        now = now_cst()
        try:
            # 每轮热加载配置, Web UI 增删股票后无需重启即可生效
            cfg = load_json(CONFIG_PATH, cfg)
            if cfg.get("stocks"):
                alerts = scan(cfg, state, now)
                if alerts:
                    for a in alerts:
                        log.info("信号: %s", fmt_alert_line(a))
                    send_feishu(cfg, alerts)
                save_json(STATE_PATH, state)
        except Exception:
            log.exception("扫描异常")
        interval = cfg.get("poll_interval_sec", 30) if is_trading_time(now_cst()) else 300
        time.sleep(interval)


if __name__ == "__main__":
    main()
