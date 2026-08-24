#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MACD 监控自选管理 Web UI
搜索股票/指数/ETF(腾讯智能搜索框接口), 管理监控列表并写入 config.json
用法: python3 webui.py  然后浏览器打开 http://localhost:8688
"""
import json
import os
import re
import threading
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
        out, seen = [], set()
        for item in m.group(1).split("^"):
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
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         background: var(--bg); color: var(--text); min-height: 100vh; }
  .wrap { max-width: 780px; margin: 0 auto; padding: 32px 20px 60px; }
  header { display: flex; align-items: baseline; gap: 12px; margin-bottom: 24px; }
  h1 { font-size: 22px; }
  header .sub { color: var(--muted); font-size: 13px; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
          padding: 20px; margin-bottom: 20px; }
  .search-row { display: flex; gap: 10px; }
  #q { flex: 1; border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px;
       font-size: 15px; outline: none; }
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
  .item .spacer { flex: 1; }
  .group-pick { display: flex; gap: 8px; align-items: center; }
  .group-pick select, .group-pick input { border: 1px solid var(--line); border-radius: 6px;
       padding: 6px 8px; font-size: 13px; }
  .item .add-btn { background: var(--ok); color: #fff; padding: 6px 16px; }
  h2 { font-size: 15px; margin-bottom: 14px; color: var(--muted); font-weight: 600; }
  #watch { display: grid; grid-template-columns: 1fr; gap: 8px; }
  .watch-item { display: flex; align-items: center; gap: 10px; padding: 12px 14px;
          border: 1px solid var(--line); border-radius: 8px; background: #fafafa; }
  .watch-item .grp { font-size: 12px; color: var(--muted); border-left: 2px solid var(--accent);
          padding-left: 8px; }
  .del { background: #fee; color: var(--accent); padding: 6px 14px; }
  .del:hover { background: #fdd; }
  .toast { position: fixed; bottom: 28px; left: 50%; transform: translateX(-50%);
           background: #2c3e50; color: #fff; padding: 10px 22px; border-radius: 20px;
           font-size: 14px; opacity: 0; transition: opacity .3s; pointer-events: none; }
  .toast.show { opacity: .95; }
  .empty { color: var(--muted); font-size: 14px; text-align: center; padding: 20px; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>MACD 监控 · 自选管理</h1>
    <span class="sub">改动实时写入 config.json，监控进程自动热加载</span>
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
    <h2>当前监控列表（<span id="count">0</span> 只）</h2>
    <div id="watch"></div>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
let groups = [];
let timer = null;

function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 1800);
}

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
  box.innerHTML = stocks.map(s => `
    <div class="watch-item">
      <span class="name">${esc(s.name)}</span>
      <span class="code">${esc(s.code)}</span>
      <span class="grp">${esc(s.group || '自选')}</span>
      <span class="spacer"></span>
      <button class="del" onclick="delStock('${esc(s.code)}')">删除</button>
    </div>`).join('');
}

function esc(s) {
  return String(s).replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

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
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"自选管理 Web UI 已启动: http://localhost:{PORT}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
