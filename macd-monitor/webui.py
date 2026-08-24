#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MACD 监控自选管理 Web UI
搜索股票/指数/ETF(腾讯智能搜索框接口), 管理监控列表并写入 config.json
实时行情: 腾讯报价(价格/涨跌幅/成交量) + 新浪资金流向(主力净流入)
用法: python3 webui.py  然后浏览器打开 http://localhost:8688
"""
import codecs
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
PORT = 8688

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})

# 搜索结果类型标识 -> 中文
TYPE_NAME = {
    "GP-A": "A股", "GP-B": "B股", "GP-KCB": "科创板", "GP-CYB": "创业板",
    "ZS": "指数", "ETF": "ETF", "LOF": "LOF", "FJ": "分级基金",
    "HK": "港股", "US": "美股",
}

CFG_LOCK = threading.Lock()


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
    num = code[2:]
    return code.startswith("sh000") or code.startswith("sz399")


def fetch_quotes(codes):
    """腾讯批量实时行情, 返回 {code: {name, price, chg, chg_pct, volume, amount}}"""
    out = {}
    codes = [c for c in codes if c]
    if not codes:
        return out
    try:
        r = S.get("https://qt.gtimg.cn/q=" + ",".join(codes), timeout=8)
        text = r.content.decode("gbk", errors="replace")
        for m in re.finditer(r'v_(\w+)="([^"]*)"', text):
            code, f = m.group(1), m.group(2).split("~")
            if len(f) < 38 or not f[3]:
                continue
            out[code] = {
                "name": f[1], "price": float(f[3]),
                "chg": float(f[31] or 0), "chg_pct": float(f[32] or 0),
                "volume": float(f[36] or 0),   # 手
                "amount": float(f[37] or 0),   # 万元
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


HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MACD 监控 · 自选管理</title>
<style>
  :root {
    --bg: #f5f6f8; --card: #fff; --line: #e5e7eb; --text: #1f2328;
    --muted: #6b7280; --accent: #c0392b; --accent2: #b03a2e; --ok: #1e8e4e;
    --up: #d03a2f; --down: #0a8a4a; --hover: #fafafa;
  }
  body.dark {
    --bg: #111418; --card: #1b1f24; --line: #2c323a; --text: #e6e8ea;
    --muted: #9aa3ad; --accent: #e05a4e; --accent2: #c94a3f; --ok: #2eaa5f;
    --up: #ef5350; --down: #26a65b; --hover: #22262c;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         background: var(--bg); color: var(--text); min-height: 100vh; }
  .wrap { max-width: 860px; margin: 0 auto; padding: 32px 20px 60px; }
  header { display: flex; align-items: center; gap: 12px; margin-bottom: 24px; }
  h1 { font-size: 22px; }
  header .sub { color: var(--muted); font-size: 13px; flex: 1; }
  #themeSel { border: 1px solid var(--line); border-radius: 8px; padding: 6px 10px;
              font-size: 13px; background: var(--card); color: var(--text); }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
          padding: 20px; margin-bottom: 20px; }
  .search-row { display: flex; gap: 10px; }
  #q { flex: 1; border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px;
       font-size: 15px; outline: none; background: var(--card); color: var(--text); }
  #q:focus { border-color: var(--accent); }
  button { border: none; border-radius: 8px; padding: 10px 18px; font-size: 14px;
           cursor: pointer; background: var(--line); color: var(--text); }
  button.primary { background: var(--accent); color: #fff; }
  button.primary:hover { background: var(--accent2); }
  .hint { color: var(--muted); font-size: 12px; margin-top: 10px; }
  #results { margin-top: 14px; }
  .item { display: flex; align-items: center; gap: 10px; padding: 10px 12px;
          border: 1px solid var(--line); border-radius: 8px; margin-bottom: 8px; }
  .item .name { font-weight: 600; }
  .item .code { color: var(--muted); font-size: 13px; }
  .tag { font-size: 12px; padding: 2px 8px; border-radius: 10px; background: #fdecea;
         color: var(--accent); }
  .tag.etf, .tag.lof { background: #e8f0fe; color: #1a56db; }
  .tag.zs { background: #fef3c7; color: #b45309; }
  body.dark .tag { background: #3a2422; }
  body.dark .tag.etf, body.dark .tag.lof { background: #1d2c4d; }
  body.dark .tag.zs { background: #3d331a; }
  .item .spacer { flex: 1; }
  .group-pick { display: flex; gap: 8px; align-items: center; }
  .group-pick select, .group-pick input { border: 1px solid var(--line); border-radius: 6px;
       padding: 6px 8px; font-size: 13px; background: var(--card); color: var(--text); }
  .item .add-btn { background: var(--ok); color: #fff; padding: 6px 16px; }
  h2 { font-size: 15px; margin-bottom: 14px; color: var(--muted); font-weight: 600; }
  h2 .upd { float: right; font-weight: 400; font-size: 12px; }
  #watch { display: grid; grid-template-columns: 1fr; gap: 8px; }
  .watch-item { display: flex; align-items: center; gap: 12px; padding: 12px 14px;
          border: 1px solid var(--line); border-radius: 8px; background: var(--hover); }
  .seq { width: 22px; text-align: center; color: var(--muted); font-size: 13px;
         font-variant-numeric: tabular-nums; }
  .wname { min-width: 130px; }
  .wname .n { font-weight: 600; }
  .wname .c { color: var(--muted); font-size: 12px; display: block; }
  .grp { font-size: 12px; color: var(--muted); border-left: 2px solid var(--accent);
         padding-left: 8px; }
  .qcell { text-align: right; font-variant-numeric: tabular-nums; }
  .qprice { min-width: 90px; }
  .qprice .p { font-size: 17px; font-weight: 700; }
  .qprice .u { font-size: 11px; color: var(--muted); display: block; }
  .qpct { min-width: 80px; font-size: 15px; font-weight: 700; }
  .qflow { min-width: 130px; font-size: 13px; }
  .qflow .l { font-size: 11px; color: var(--muted); display: block; }
  .spacer { flex: 1; }
  .del { background: transparent; color: var(--accent); padding: 6px 14px;
         border: 1px solid var(--line); }
  .del:hover { border-color: var(--accent); }
  .up { color: var(--up); }
  .down { color: var(--down); }
  .flat { color: var(--muted); }
  .toast { position: fixed; bottom: 28px; left: 50%; transform: translateX(-50%);
           background: #2c3e50; color: #fff; padding: 10px 22px; border-radius: 20px;
           font-size: 14px; opacity: 0; transition: opacity .3s; pointer-events: none; }
  .toast.show { opacity: .95; }
  .empty { color: var(--muted); font-size: 14px; text-align: center; padding: 20px; }
  @media (max-width: 640px) {
    .watch-item { flex-wrap: wrap; }
    .qprice, .qpct, .qflow { min-width: 0; }
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>MACD 监控 · 自选管理</h1>
    <span class="sub">改动实时写入 config.json，监控进程自动热加载</span>
    <select id="themeSel" onchange="setTheme(this.value)">
      <option value="auto">跟随系统</option>
      <option value="light">白天</option>
      <option value="dark">夜间</option>
    </select>
  </header>

  <div class="card">
    <div class="search-row">
      <input id="q" placeholder="输入名称、拼音或代码，如：贵州茅台 / gzmt / 510300 / 沪深300" autofocus>
      <button class="primary" onclick="doSearch()">搜索</button>
    </div>
    <div class="hint">支持 A 股、指数、ETF / LOF（数据源：腾讯行情）</div>
    <div id="results"></div>
  </div>

  <div class="card">
    <h2>当前监控列表（<span id="count">0</span> 只）<span class="upd" id="upd"></span></h2>
    <div id="watch"></div>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
let groups = [];
let timer = null;
let quoteTimer = null;

/* ---------- 主题 ---------- */
function applyTheme(mode) {
  const dark = mode === 'dark' ||
    (mode === 'auto' && matchMedia('(prefers-color-scheme: dark)').matches);
  document.body.classList.toggle('dark', dark);
}
function setTheme(mode) {
  localStorage.setItem('theme', mode);
  document.getElementById('themeSel').value = mode;
  applyTheme(mode);
}
(function initTheme() {
  const mode = localStorage.getItem('theme') || 'auto';
  document.getElementById('themeSel').value = mode;
  applyTheme(mode);
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    applyTheme(localStorage.getItem('theme') || 'auto');
  });
})();

function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 1800);
}

function esc(s) {
  return String(s).replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

/* ---------- 格式化 ---------- */
function pctClass(v) { return v > 0 ? 'up' : (v < 0 ? 'down' : 'flat'); }
function fmtPct(v) { return (v > 0 ? '+' : '') + v.toFixed(2) + '%'; }
function fmtYi(v) {  // 元 -> 亿/万
  const a = Math.abs(v);
  if (a >= 1e8) return (v / 1e8).toFixed(2) + '亿';
  if (a >= 1e4) return (v / 1e4).toFixed(1) + '万';
  return v.toFixed(0);
}
function fmtVol(v) {  // 手
  const a = Math.abs(v);
  if (a >= 1e8) return (v / 1e8).toFixed(2) + '亿手';
  if (a >= 1e4) return (v / 1e4).toFixed(1) + '万手';
  return v.toFixed(0) + '手';
}

/* ---------- 监控列表 ---------- */
async function loadWatch() {
  const r = await fetch('/api/stocks');
  const stocks = await r.json();
  groups = [...new Set(stocks.map(s => s.group || '自选'))];
  document.getElementById('count').textContent = stocks.length;
  const box = document.getElementById('watch');
  if (!stocks.length) {
    box.innerHTML = '<div class="empty">暂无监控标的，请在上方搜索添加</div>';
    return;
  }
  box.innerHTML = stocks.map((s, i) => `
    <div class="watch-item" data-code="${esc(s.code)}">
      <span class="seq">${i + 1}</span>
      <span class="wname"><span class="n">${esc(s.name)}</span><span class="c">${esc(s.code)}</span></span>
      <span class="grp">${esc(s.group || '自选')}</span>
      <span class="spacer"></span>
      <span class="qcell qprice"><span class="p">--</span><span class="u">--</span></span>
      <span class="qcell qpct">--</span>
      <span class="qcell qflow"><span class="v">--</span><span class="l">--</span></span>
      <button class="del" onclick="delStock('${esc(s.code)}')">删除</button>
    </div>`).join('');
  refreshQuotes();
}

async function refreshQuotes() {
  try {
    const r = await fetch('/api/quotes');
    const data = await r.json();
    const now = new Date();
    document.getElementById('upd').textContent =
      '行情更新 ' + now.toTimeString().slice(0, 8);
    for (const q of data) {
      const row = document.querySelector(`.watch-item[data-code="${CSS.escape(q.code)}"]`);
      if (!row) continue;
      const priceEl = row.querySelector('.qprice .p');
      const unitEl = row.querySelector('.qprice .u');
      const pctEl = row.querySelector('.qpct');
      const flowEl = row.querySelector('.qflow .v');
      const flowLbl = row.querySelector('.qflow .l');
      if (!q.ok) {
        priceEl.textContent = '--'; unitEl.textContent = '';
        pctEl.textContent = '--'; pctEl.className = 'qcell qpct';
        flowEl.textContent = '--'; flowLbl.textContent = '';
        continue;
      }
      priceEl.textContent = q.price.toFixed(2);
      priceEl.className = 'p ' + pctClass(q.chg_pct);
      unitEl.textContent = (q.chg > 0 ? '+' : '') + q.chg.toFixed(2);
      pctEl.textContent = fmtPct(q.chg_pct);
      pctEl.className = 'qcell qpct ' + pctClass(q.chg_pct);
      if (q.is_index) {
        // 指数: 成交量 + 成交额
        flowEl.textContent = fmtVol(q.volume);
        flowEl.className = 'v';
        flowLbl.textContent = '成交额 ' + (q.amount / 1e4).toFixed(1) + '亿';
      } else if (q.flow !== null && q.flow !== undefined) {
        // 股票/ETF: 主力净流入
        flowEl.textContent = (q.flow > 0 ? '+' : '') + fmtYi(q.flow);
        flowEl.className = 'v ' + pctClass(q.flow);
        flowLbl.textContent = '主力净流入';
      } else {
        flowEl.textContent = (q.amount / 1e4).toFixed(1) + '亿';
        flowEl.className = 'v';
        flowLbl.textContent = '成交额';
      }
    }
  } catch (e) { /* 下轮重试 */ }
}

/* ---------- 搜索 ---------- */
async function doSearch() {
  const q = document.getElementById('q').value.trim();
  if (!q) return;
  const box = document.getElementById('results');
  box.innerHTML = '<div class="empty">搜索中…</div>';
  const r = await fetch('/api/search?q=' + encodeURIComponent(q));
  const items = await r.json();
  if (!items.length) {
    box.innerHTML = '<div class="empty">未找到相关标的</div>';
    return;
  }
  box.innerHTML = items.map(it => `
    <div class="item">
      <span class="name">${esc(it.name)}</span>
      <span class="code">${esc(it.code)}</span>
      <span class="tag ${it.type.toLowerCase()}">${esc(it.type)}</span>
      <span class="spacer"></span>
      <span class="group-pick">
        <select id="grp-${esc(it.code)}">
          ${groups.map(g => `<option value="${esc(g)}">${esc(g)}</option>`).join('')}
          <option value="新分组">＋新分组</option>
        </select>
        <input id="newgrp-${esc(it.code)}" placeholder="新分组名" style="display:none;width:90px">
      </span>
      <button class="add-btn" onclick="addStock('${esc(it.code)}','${esc(it.name)}')">添加</button>
    </div>`).join('');
  items.forEach(it => {
    const sel = document.getElementById('grp-' + it.code);
    if (sel) sel.onchange = () => {
      document.getElementById('newgrp-' + it.code).style.display =
        sel.value === '新分组' ? '' : 'none';
    };
  });
}

async function addStock(code, name) {
  let group = document.getElementById('grp-' + code).value;
  if (group === '新分组') {
    group = document.getElementById('newgrp-' + code).value.trim() || '自选';
  }
  const r = await fetch('/api/stocks', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({code, name, group})
  });
  const res = await r.json();
  toast(res.ok ? `已添加 ${name} → ${group}` : res.msg || '添加失败');
  if (res.ok) { loadWatch(); doSearch(); }
}

async function delStock(code) {
  const r = await fetch('/api/stocks?code=' + encodeURIComponent(code), {method: 'DELETE'});
  const res = await r.json();
  toast(res.ok ? '已删除 ' + code : res.msg || '删除失败');
  if (res.ok) loadWatch();
}

document.getElementById('q').addEventListener('keydown', e => {
  if (e.key === 'Enter') doSearch();
});
document.getElementById('q').addEventListener('input', () => {
  clearTimeout(timer);
  timer = setTimeout(doSearch, 400);
});

loadWatch();
quoteTimer = setInterval(refreshQuotes, 5000);
</script>
</body>
</html>
"""


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

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
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
