#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLite 存储层: 底背离信号 + 信号跟踪(复盘统计)
- WAL 模式, 线程局部连接(ThreadingHTTPServer 多线程/扫描线程/回填线程各持一个)
- 首次启动自动从旧 div_hist.json 导入历史信号, 导入后改名 .bak 保留
- 信号按 (code, tf, date2) 去重, 每日扫描 UPSERT 刷新最新快照与共振评分
"""
import json
import os
import sqlite3
import threading

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "data.db")
LEGACY_DIV_HIST = os.path.join(BASE, "div_hist.json")
SIGNAL_KEEP_DAYS = 730   # 信号保留2年(供复盘统计), 到期清理

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS div_signal(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL, tf TEXT NOT NULL, date2 TEXT NOT NULL,
  name TEXT, price REAL,
  date1 TEXT, price1 REAL, price2 REAL,
  dif1 REAL, dif2 REAL, dif_inc REAL, confirm TEXT,
  chg3 REAL, chg5 REAL,                 -- 低点2后3/5周期涨幅(表格展示, 自price2)
  score REAL DEFAULT 0, tags TEXT DEFAULT '',
  confirm_close REAL,                  -- 确认日收盘价(跟踪基准)
  scan_first TEXT, scan_last TEXT,
  UNIQUE(code, tf, date2)
);
CREATE INDEX IF NOT EXISTS idx_div_confirm ON div_signal(confirm);
CREATE INDEX IF NOT EXISTS idx_div_scan_last ON div_signal(scan_last);

-- 信号跟踪: 确认日收盘后N个交易日收益%(自confirm_close), 供复盘统计
CREATE TABLE IF NOT EXISTS signal_track(
  signal_id INTEGER PRIMARY KEY REFERENCES div_signal(id) ON DELETE CASCADE,
  fwd3 REAL, fwd5 REAL, fwd10 REAL, fwd20 REAL, fwd60 REAL,
  upd TEXT
);
CREATE INDEX IF NOT EXISTS idx_track_upd ON signal_track(upd);
"""


def conn():
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(DB_PATH, timeout=20)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn = c
    return c


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def init():
    """建表 + 旧JSON一次性导入 + 过期清理(启动时调用一次)"""
    with conn() as c:
        c.executescript(SCHEMA)
        # 增量迁移: 模型质量分(元标签模型输出, 0-100, NULL=未打分)
        cols = {r[1] for r in c.execute("PRAGMA table_info(div_signal)")}
        if "ml_score" not in cols:
            c.execute("ALTER TABLE div_signal ADD COLUMN ml_score REAL")
    _migrate_legacy()
    prune()


def _migrate_legacy():
    if not os.path.exists(LEGACY_DIV_HIST):
        return
    days = _load_json(LEGACY_DIV_HIST, {}).get("days", {})
    if not isinstance(days, dict):
        days = {}
    # 同一信号(按第二低点去重)取首末扫描日; 后扫描日的chg3/chg5可能从None变为有值
    merged = {}
    for d in sorted(days):
        for r in days[d]:
            key = (r.get("code"), r.get("tf"), r.get("date2"))
            if not all(key):
                continue
            if key in merged:
                row = merged[key]
                row["scan_last"] = d
                if r.get("chg3") is not None:
                    row["chg3"] = r.get("chg3")
                if r.get("chg5") is not None:
                    row["chg5"] = r.get("chg5")
            else:
                merged[key] = {**r, "scan_first": d, "scan_last": d}
    with conn() as c:
        for r in merged.values():
            _upsert_signal(c, r)
    try:
        os.replace(LEGACY_DIV_HIST, LEGACY_DIV_HIST + ".bak")
    except OSError:
        pass


def _upsert_signal(c, r):
    """单条信号UPSERT: 保留scan_first/confirm_close(首次), 刷新其余快照字段;
    ml_score由扫描时的模型打分提供, 未提供时保留旧值"""
    c.execute(
        """INSERT INTO div_signal(code, tf, date2, name, price, date1, price1, price2,
               dif1, dif2, dif_inc, confirm, chg3, chg5, score, tags,
               confirm_close, scan_first, scan_last, ml_score)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(code, tf, date2) DO UPDATE SET
               name=excluded.name, price=excluded.price, date1=excluded.date1,
               price1=excluded.price1, price2=excluded.price2,
               dif1=excluded.dif1, dif2=excluded.dif2, dif_inc=excluded.dif_inc,
               confirm=excluded.confirm, chg3=excluded.chg3, chg5=excluded.chg5,
               score=excluded.score, tags=excluded.tags,
               scan_last=excluded.scan_last,
               ml_score=COALESCE(excluded.ml_score, div_signal.ml_score)""",
        (r["code"], r["tf"], r["date2"], r.get("name"), r.get("price"),
         r.get("date1"), r.get("price1"), r.get("price2"),
         r.get("dif1"), r.get("dif2"), r.get("dif_inc"),
         r.get("confirm"), r.get("chg3"), r.get("chg5"),
         r.get("score", 0) or 0, r.get("tags", "") or "",
         r.get("confirm_close"), r.get("scan_first"), r.get("scan_last"),
         r.get("ml_score")))
    sig_id = c.execute(
        "SELECT id FROM div_signal WHERE code=? AND tf=? AND date2=?",
        (r["code"], r["tf"], r["date2"])).fetchone()
    if sig_id and r.get("confirm_close") is not None:
        c.execute("INSERT OR IGNORE INTO signal_track(signal_id) VALUES(?)",
                  (sig_id[0],))


def upsert_signals(rows, scan_date):
    """批量写入一个扫描日的信号(每日扫描完成后调用)"""
    with conn() as c:
        for r in rows:
            r = {**r, "scan_first": scan_date, "scan_last": scan_date}
            _upsert_signal(c, r)


def prune():
    """清理超过 SIGNAL_KEEP_DAYS 未再扫到的信号(及其跟踪行)"""
    with conn() as c:
        c.execute("DELETE FROM div_signal WHERE scan_last < date('now', ?)",
                  (f"-{SIGNAL_KEEP_DAYS} day",))


def last_scan_date():
    with conn() as c:
        row = c.execute("SELECT MAX(scan_last) FROM div_signal").fetchone()
        return row[0] if row else None


def div_rows(keep_days=30):
    """表格展示: 最近 keep_days 个扫描日内出现过的信号(按确认日期倒序)"""
    with conn() as c:
        rows = c.execute(
            """SELECT code, name, price, tf,
                      CASE tf WHEN 'day' THEN '日线' ELSE '周线' END AS tf_name,
                      date1, date2, price1, price2,
                      dif1, dif2, dif_inc, chg3, chg5, confirm, score, tags,
                      ml_score, scan_last AS scan
               FROM div_signal
               WHERE scan_last >= date('now', ?)
               ORDER BY confirm DESC, code DESC""",
            (f"-{keep_days} day",)).fetchall()
    return [dict(r) for r in rows]


def pending_track(min_confirm=None, limit=2000):
    """待回填信号: 跟踪行缺失或任一fwd为空; 只取最近400天内的(保证800根K线能覆盖)"""
    q = """SELECT s.id, s.code, s.tf, s.confirm, s.confirm_close
           FROM div_signal s
           LEFT JOIN signal_track t ON t.signal_id = s.id
           WHERE (t.signal_id IS NULL OR t.fwd3 IS NULL OR t.fwd5 IS NULL
                  OR t.fwd10 IS NULL OR t.fwd20 IS NULL OR t.fwd60 IS NULL)
             AND s.confirm >= date('now', '-400 day')
             AND (:mc IS NULL OR s.confirm >= :mc)
           ORDER BY s.confirm DESC LIMIT :lim"""
    with conn() as c:
        rows = c.execute(q, {"mc": min_confirm, "lim": limit}).fetchall()
    return [dict(r) for r in rows]


def update_track(signal_id, fwd):
    """写入/覆盖信号跟踪收益; fwd为{fwd3..fwd60}, None字段也写入(显式未知)"""
    cols = ["fwd3", "fwd5", "fwd10", "fwd20", "fwd60"]
    vals = tuple(fwd.get(n) for n in cols)
    with conn() as c:
        exists = c.execute("SELECT 1 FROM signal_track WHERE signal_id=?",
                           (signal_id,)).fetchone()
        if exists:
            sets = ",".join(f"{n}=?" for n in cols)
            c.execute(f"UPDATE signal_track SET {sets},upd=date('now')"
                      " WHERE signal_id=?", vals + (signal_id,))
        else:
            c.execute(
                f"INSERT INTO signal_track(signal_id,{','.join(cols)},upd)"
                " VALUES(?,?,?,?,?,?,date('now'))",
                (signal_id, *vals))


def _agg(c, where, params):
    """聚合: 各周期样本数/胜率/平均收益(收益>0计胜; NULL未成熟不计入胜率分母)"""
    sel = []
    for n in (3, 5, 10, 20, 60):
        sel.append(f"SUM(fwd{n} IS NOT NULL)")
        sel.append(f"ROUND(AVG(CASE WHEN fwd{n} IS NULL THEN NULL"
                   f" WHEN fwd{n}>0 THEN 1.0 ELSE 0.0 END)*100, 1)")
        sel.append(f"ROUND(AVG(fwd{n}), 2)")
    row = c.execute(
        f"SELECT {','.join(sel)} FROM div_signal s JOIN signal_track t"
        f" ON t.signal_id=s.id WHERE {where}", params).fetchone()
    out = {}
    for i, n in enumerate((3, 5, 10, 20, 60)):
        cnt, win, avg = row[i * 3], row[i * 3 + 1], row[i * 3 + 2]
        out[f"n{n}"] = cnt or 0
        out[f"win{n}"] = win
        out[f"avg{n}"] = avg
    return out


def stats():
    """复盘统计: 总览 + 按周期/共振分/月份分层"""
    with conn() as c:
        total = c.execute(
            "SELECT COUNT(*) FROM div_signal s JOIN signal_track t"
            " ON t.signal_id=s.id").fetchone()[0]
        span = c.execute("SELECT MIN(confirm), MAX(confirm) FROM div_signal").fetchone()
        out = {"total": total, "from": span[0], "to": span[1],
               "overall": _agg(c, "1=1", ())}
        # 分层: 周期(只列有样本的周期; 周线已停扫, 存量数据仍可展示)
        out["by_tf"] = []
        for tf, name in (("day", "日线"), ("week", "周线")):
            a = _agg(c, "s.tf=?", (tf,))
            if (a.get("n5") or 0) > 0:
                out["by_tf"].append({"key": tf, "name": name, **a})
        # 分层: 共振分 0-2 / 3-4 / 5-9
        out["by_score"] = []
        for lo, hi in ((0, 2), (3, 4), (5, 9)):
            a = _agg(c, "s.score BETWEEN ? AND ?", (lo, hi))
            out["by_score"].append(
                {"key": f"{lo}-{hi}", "name": f"共振分{lo}-{hi}", **a})
        # 分层: 模型质量分(元标签模型, NULL=未打分单独一档)
        out["by_ml"] = []
        for lo, hi in ((0, 40), (40, 60), (60, 101)):
            a = _agg(c, "s.ml_score >= ? AND s.ml_score < ?", (lo, hi))
            out["by_ml"].append(
                {"key": f"{lo}-{hi}", "name": f"模型分{lo}-{hi}", **a})
        a = _agg(c, "s.ml_score IS NULL", ())
        out["by_ml"].append({"key": "na", "name": "未打分", **a})
        # 分层: 确认月份(近12个月)
        out["by_month"] = []
        rows = c.execute(
            """SELECT substr(confirm,1,7) ym, COUNT(*) n,
                      ROUND(AVG(CASE WHEN t.fwd5 IS NULL THEN NULL
                                    WHEN t.fwd5>0 THEN 1.0 ELSE 0.0 END)*100,1) win5,
                      ROUND(AVG(t.fwd5),2) avg5,
                      ROUND(AVG(t.fwd20),2) avg20
               FROM div_signal s JOIN signal_track t ON t.signal_id=s.id
               GROUP BY ym ORDER BY ym DESC LIMIT 12""").fetchall()
        for r in rows:
            out["by_month"].append({"key": r[0], "n": r[1],
                                    "win5": r[2], "avg5": r[3], "avg20": r[4]})
        return out
