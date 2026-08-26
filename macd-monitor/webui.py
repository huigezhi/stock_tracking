#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MACD 监控自选管理 Web UI
- 左栏: 宽基ETF列表(份额>=100亿份, 实时计算), 点击查看日K线
- 中栏: K线走势图(MA均线/成交量/日期范围滑条)
- 右栏: MACD 监控列表(搜索/增删/实时行情)
前端为 static/ 下的静态文件, 后端提供 JSON API
用法: python3 webui.py  然后浏览器打开 http://localhost:8688
"""
import json
import os
import re
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
STATIC_DIR = os.path.join(BASE, "static")
PORT = 8688

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})

# 搜索结果类型标识 -> 中文
TYPE_NAME = {
    "GP-A": "A股", "GP-B": "B股", "GP-KCB": "科创板", "GP-CYB": "创业板",
    "ZS": "指数", "ETF": "ETF", "LOF": "LOF", "FJ": "分级基金",
    "HK": "港股", "US": "美股",
}

# 宽基ETF候选清单(按指数归类), 运行时按实际份额(>=100亿份)筛选
BROAD_ETF_CANDIDATES = {
    "sh510050": "上证50", "sh510180": "上证180", "sh510210": "上证指数",
    "sh510300": "沪深300", "sh510310": "沪深300", "sz159919": "沪深300", "sz159673": "沪深300",
    "sh510500": "中证500", "sz159922": "中证500",
    "sh512100": "中证1000", "sz159845": "中证1000", "sz159629": "中证1000",
    "sh563300": "中证2000", "sz159531": "中证2000",
    "sh563360": "中证A500", "sz159352": "中证A500", "sz159361": "中证A500",
    "sh512050": "中证A500", "sz159338": "中证A500",
    "sz159915": "创业板指", "sz159949": "创业板50",
    "sh588000": "科创50", "sh588080": "科创50",
    "sh588800": "科创100",
    "sz159780": "双创50", "sh588400": "双创50",
    "sz159901": "深证100", "sz159879": "北证50",
}
MIN_SHARES = 100  # 亿份

CFG_LOCK = threading.Lock()
SHARE_HIST_PATH = os.path.join(BASE, "etf_share_hist.json")
SHARE_LOCK = threading.Lock()
SNAP_KEEP_DAYS = 500   # 每只ETF保留的日度份额快照数


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def load_cfg():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"stocks": []}


def save_cfg(cfg):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def search_suggest(q):
    """调用腾讯智能搜索框接口, 返回 [{code, name, type}, ...]"""
    q = q.strip()
    if not q:
        return []
    try:
        r = S.get(f"https://smartbox.gtimg.cn/s3/?q={quote(q)}&t=all", timeout=8)
        import codecs
        m = re.search(r'v_hint="([^"]*)"', r.text)
        if not m:
            return []
        hint = m.group(1)
        # 接口返回的中文名是 \uXXXX 转义文本, 需解码为正常中文
        if "\\u" in hint:
            try:
                hint = codecs.decode(hint, "unicode_escape")
            except Exception:
                pass
        out, seen = [], set()
        for item in hint.split("^"):
            parts = item.split("~")
            if len(parts) < 5:
                continue
            mkt, code, name, _py, typ = parts[0], parts[1], parts[2], parts[3], parts[4]
            full = mkt + code
            if full in seen:
                continue
            seen.add(full)
            out.append({"code": full, "name": name, "type": TYPE_NAME.get(typ, typ)})
        return out[:20]
    except Exception:
        return []


# ---------------- 实时行情 ----------------

def is_index_code(code):
    """sh000xxx / sz399xxx 为指数"""
    return code.startswith("sh000") or code.startswith("sz399")


def fetch_quotes(codes):
    """腾讯批量实时行情, 返回 {code: {name, price, chg, chg_pct, volume, amount, mcap}}"""
    out = {}
    codes = [c for c in codes if c]
    if not codes:
        return out
    try:
        r = S.get("https://qt.gtimg.cn/q=" + ",".join(codes), timeout=8)
        text = r.content.decode("gbk", errors="replace")
        for m in re.finditer(r'v_(\w+)="([^"]*)"', text):
            code, f = m.group(1), m.group(2).split("~")
            if len(f) < 46 or not f[3]:
                continue
            out[code] = {
                "name": f[1], "price": float(f[3]),
                "chg": float(f[31] or 0), "chg_pct": float(f[32] or 0),
                "volume": float(f[36] or 0),   # 手
                "amount": float(f[37] or 0),   # 万元
                "mcap": float(f[45] or 0),     # 总市值(亿)
            }
        return out
    except Exception:
        return out


_FLOW_CACHE = {}  # code -> (timestamp, 主力净流入元|None)


def fetch_flow(code):
    """新浪资金流向: 当日主力净流入(元), 缓存60秒; 指数/无数据返回None"""
    now = time.time()
    c = _FLOW_CACHE.get(code)
    if c and now - c[0] < 60:
        return c[1]
    val = None
    try:
        url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               f"MoneyFlow.ssl_qsfx_lscjfb?page=1&num=1&sort=opendate&asc=0&daima={code}")
        r = S.get(url, timeout=8, headers={"Referer": "https://finance.sina.com.cn"})
        data = json.loads(r.text)
        if data:
            val = float(data[0].get("netamount") or 0)
    except Exception:
        pass
    _FLOW_CACHE[code] = (now, val)
    return val


def build_quotes(stocks):
    """组装监控列表的实时数据"""
    codes = [s.get("code", "") for s in stocks]
    quotes = fetch_quotes(codes)
    result = []
    for s in stocks:
        code = s.get("code", "")
        q = quotes.get(code)
        item = {"code": code, "ok": bool(q)}
        if q:
            item.update({"name": q["name"], "price": q["price"],
                         "chg": q["chg"], "chg_pct": q["chg_pct"],
                         "volume": q["volume"], "amount": q["amount"],
                         "is_index": is_index_code(code)})
            # 股票/ETF取主力净流入; 指数无资金流数据
            item["flow"] = None if item["is_index"] else fetch_flow(code)
        result.append(item)
    return result


# ---------------- 宽基ETF列表 ----------------

_ETF_CACHE = {"ts": 0, "data": []}


def record_share_snapshots(etf_list):
    """将当日份额快照写入 etf_share_hist.json (每日一条, 盘中覆盖为最新值),
    随运行时间自然积累日度份额序列, 用于面板展示每日/每周份额变动"""
    today = time.strftime("%Y-%m-%d")
    with SHARE_LOCK:
        hist = load_json(SHARE_HIST_PATH, {})
        changed = False
        for e in etf_list:
            code, shares = e.get("code"), e.get("shares")
            if not code or not shares:
                continue
            days = hist.setdefault(code, {})
            if days.get(today) != shares:
                days[today] = shares
                changed = True
            if len(days) > SNAP_KEEP_DAYS:  # 只保留最近N天
                for k in sorted(days)[:-SNAP_KEEP_DAYS]:
                    days.pop(k, None)
                    changed = True
        if changed:
            tmp = SHARE_HIST_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(hist, f, ensure_ascii=False)
            os.replace(tmp, SHARE_HIST_PATH)


def broad_etf_list():
    """宽基ETF列表: 候选清单中份额>=100亿份的, 缓存10分钟"""
    now = time.time()
    if _ETF_CACHE["data"] and now - _ETF_CACHE["ts"] < 600:
        return _ETF_CACHE["data"]
    quotes = fetch_quotes(list(BROAD_ETF_CANDIDATES.keys()))
    out = []
    for code, idx_name in BROAD_ETF_CANDIDATES.items():
        q = quotes.get(code)
        if not q or not q["price"]:
            continue
        shares = q["mcap"] / q["price"]  # 份额(亿份) = 总市值 / 价格
        if shares < MIN_SHARES:
            continue
        out.append({"code": code, "name": q["name"], "index": idx_name,
                    "price": q["price"], "chg": q["chg"], "chg_pct": q["chg_pct"],
                    "amount": q["amount"], "shares": round(shares)})
    out.sort(key=lambda x: -x["shares"])
    if out:
        _ETF_CACHE["ts"], _ETF_CACHE["data"] = now, out
        record_share_snapshots(out)
    return out


# ---------------- K线数据 ----------------

def fetch_kline(code, tf="day", n=800):
    """腾讯前复权K线(失败回退非复权), 返回 [[date, open, close, high, low, volume], ...]"""
    n = max(60, min(int(n or 800), 800))
    tf = tf if tf in ("day", "week") else "day"
    for param in (f"{code},{tf},,,{n},qfq", f"{code},{tf},,,{n},"):
        try:
            r = S.get(f"https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={param}", timeout=10)
            data = r.json().get("data", {}).get(code, {})
            rows = data.get(f"qfq{tf}") or data.get(tf)
            if rows:
                return [[row[0], float(row[1]), float(row[2]),
                         float(row[3]), float(row[4]), float(row[5])] for row in rows]
        except Exception:
            continue
    return []


def get_etf_share_history(code, tf="day"):
    """ETF份额历史(亿份): [[date, shares], ...]; tf=week 时按ISO周聚合, 取每周最后快照"""
    with SHARE_LOCK:
        hist = load_json(SHARE_HIST_PATH, {})
    days = hist.get(code, {})
    if not days:
        return []
    items = sorted(days.items())  # [(date, shares), ...]
    if tf != "week":
        return [[d, s] for d, s in items]
    out, cur_key = [], None
    for d, s in items:
        key = datetime.strptime(d, "%Y-%m-%d").isocalendar()[:2]
        if key != cur_key:
            out.append([d, s])
            cur_key = key
        else:
            out[-1] = [d, s]  # 同周取最后快照
    return out


MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml",
        ".ico": "image/x-icon"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, name):
        path = os.path.normpath(os.path.join(STATIC_DIR, name))
        if not path.startswith(STATIC_DIR) or not os.path.isfile(path):
            self.send_error(404)
            return
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            self._file("index.html")
        elif u.path.startswith("/static/"):
            self._file(u.path[len("/static/"):])
        elif u.path == "/api/stocks":
            with CFG_LOCK:
                stocks = load_cfg().get("stocks", [])
            self._json(stocks)
        elif u.path == "/api/quotes":
            with CFG_LOCK:
                stocks = load_cfg().get("stocks", [])
            self._json(build_quotes(stocks))
        elif u.path == "/api/search":
            q = parse_qs(u.query).get("q", [""])[0]
            self._json(search_suggest(q))
        elif u.path == "/api/etf/list":
            self._json(broad_etf_list())
        elif u.path == "/api/etf/share":
            q = parse_qs(u.query)
            code = q.get("code", [""])[0]
            tf = q.get("tf", ["day"])[0]
            self._json(get_etf_share_history(code, tf))
        elif u.path == "/api/kline":
            q = parse_qs(u.query)
            code = q.get("code", [""])[0]
            tf = q.get("tf", ["day"])[0]
            n = q.get("n", ["800"])[0]
            self._json(fetch_kline(code, tf, n))
        else:
            self.send_error(404)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/api/stocks":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"ok": False, "msg": "请求格式错误"}, 400)
            return
        code, name, group = data.get("code", ""), data.get("name", ""), data.get("group", "自选")
        if not code or not name:
            self._json({"ok": False, "msg": "缺少代码或名称"}, 400)
            return
        with CFG_LOCK:
            cfg = load_cfg()
            stocks = cfg.setdefault("stocks", [])
            if any(s.get("code") == code for s in stocks):
                self._json({"ok": False, "msg": f"{name} 已在监控列表中"})
                return
            stocks.append({"code": code, "name": name, "group": group})
            save_cfg(cfg)
        self._json({"ok": True})

    def do_DELETE(self):
        u = urlparse(self.path)
        if u.path != "/api/stocks":
            self.send_error(404)
            return
        code = parse_qs(u.query).get("code", [""])[0]
        with CFG_LOCK:
            cfg = load_cfg()
            stocks = cfg.get("stocks", [])
            cfg["stocks"] = [s for s in stocks if s.get("code") != code]
            save_cfg(cfg)
        self._json({"ok": True})


def main():
    # 公网部署时建议 WEBUI_HOST=127.0.0.1 仅本机监听, 通过SSH隧道访问
    host = os.environ.get("WEBUI_HOST", "0.0.0.0")
    port = int(os.environ.get("WEBUI_PORT", str(PORT)))
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"自选管理 Web UI 已启动: http://{'localhost' if host in ('127.0.0.1', 'localhost') else host}:{port} (绑定 {host})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
