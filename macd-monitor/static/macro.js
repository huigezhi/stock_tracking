"use strict";
/* ============================================================
   宏观通胀视图: 指标卡片 + 通胀趋势折线 + 美债收益率曲线 + 10Y-2Y利差 + 明细表
   依赖 app.js 运行时工具(apiFetch/esc/pctClass/toast), 由 index.html 在
   app.js 之前引入(仅运行时调用, 加载顺序无要求)
   ============================================================ */

const MacroView = {
  loaded: false,
  loading: false,
  overview: null,     // /api/macro/overview
  yields: null,       // /api/macro/yields
  seriesData: [],     // /api/macro/series
  sel: null,          // 选中的指标key集合
  range: '5y',
  yieldMode: 'yield', // yield=实际收益率 | spread=相对10Y利差
  hoverM: null,       // 趋势图悬停的x索引
  hoverY: null,       // 收益率曲线悬停的期限索引
  hoverS: null,       // 利差图悬停索引
  sortKey: 'name',
  sortDir: 1,

  /* 指标配色(与chips/折线一致) */
  COLORS: {
    cn_cpi: '#d03a2f', cn_core_cpi: '#f5a623', cn_ppi: '#9b59b6',
    us_cpi: '#4a90e2', us_core_cpi: '#26a69a', us_ppi: '#7f8c8d',
  },
  METRIC_DEFS: [
    {key: 'cn_cpi', label: '中国 CPI', country: '中国'},
    {key: 'cn_core_cpi', label: '中国 核心 CPI', country: '中国'},
    {key: 'cn_ppi', label: '中国 PPI', country: '中国'},
    {key: 'us_cpi', label: '美国 CPI', country: '美国'},
    {key: 'us_core_cpi', label: '美国 核心 CPI', country: '美国'},
    {key: 'us_ppi', label: '美国 PPI', country: '美国'},
  ],
  PAD_R: 52, PAD_B: 22,

  /* ================= 入口(showView调度) ================= */
  enter() {
    if (!this.sel) {
      try {
        const saved = JSON.parse(localStorage.getItem('macro_sel') || 'null');
        this.sel = new Set(Array.isArray(saved) && saved.length ? saved : ['cn_cpi', 'us_cpi']);
        this.range = localStorage.getItem('macro_range') || '5y';
      } catch (e) { this.sel = new Set(['cn_cpi', 'us_cpi']); }
      document.querySelectorAll('#macroRange .tf').forEach(b =>
        b.classList.toggle('active', b.dataset.r === this.range));
    }
    if (!this.loaded && !this.loading) this.loadAll();
    else this.redrawAll();   /* 已有数据: 视图由隐藏变可见, 重新测量并重绘 */
  },

  async loadAll() {
    this.loading = true;
    const jobs = [
      apiFetch('/api/macro/overview').then(r => r.json()),
      apiFetch('/api/macro/yields').then(r => r.json()),
      this.loadSeries(),
    ];
    try {
      const [ov, yw] = await Promise.all(jobs);
      this.overview = ov;
      this.yields = yw;
      this.loaded = true;
      this.renderMeta();
      this.renderCards();
      this.renderChips();
      this.renderTable();
      this.drawYields();
    } catch (e) {
      document.getElementById('macroCards').innerHTML =
        '<div class="empty" style="grid-column:1/-1">宏观数据加载失败(可能离线)</div>';
      document.getElementById('macroBody').innerHTML =
        '<tr><td colspan="7" class="empty">加载失败</td></tr>';
    }
    this.loading = false;
  },

  renderMeta() {
    const el = document.getElementById('macroMeta');
    if (!el || !this.overview) return;
    const upd = this.overview.updated || {};
    const ts = upd.inflation || upd.yields || 0;
    el.textContent = ts ? '更新于 ' + new Date(ts * 1000).toLocaleString('zh-CN',
      {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false}) : '';
  },

  /* ================= 顶部指标卡片 ================= */
  renderCards() {
    const box = document.getElementById('macroCards');
    if (!box) return;
    const cards = (this.overview || {}).cards || [];
    box.innerHTML = cards.map(c => {
      const has = c.value != null && !c.missing;
      const chgCls = c.chg > 0 ? 'up' : (c.chg < 0 ? 'down' : 'flat');
      const arrow = c.chg == null ? '' :
        `<span class="mc-chg ${chgCls}">${c.chg > 0 ? '▲' : c.chg < 0 ? '▼' : '—'} ` +
        `${c.chg > 0 ? '+' : ''}${c.chg.toFixed(2)}</span>`;
      return `
      <div class="mc-card${has ? '' : ' miss'}" data-country="${c.country}" onclick="MacroView.cardClick('${c.key}')" title="${esc(c.note || c.label)}">
        <div class="mc-top"><span class="mc-country">${c.country}</span><span class="mc-name">${esc(c.name)}</span></div>
        <div class="mc-val mono">${has ? c.value.toFixed(1) + '<i>%</i>' : '暂缺'}</div>
        <div class="mc-foot">
          ${has ? arrow : `<span class="mc-chg flat">${esc((c.note || '数据源暂缺').slice(0, 12))}</span>`}
          <span class="mc-date">${has ? c.date : ''}</span>
        </div>
        ${has && c.publish ? `<div class="mc-pub">发布 ${c.publish}</div>` : ''}
      </div>`;
    }).join('');
  },

  /* 点击卡片 → 趋势图只看该指标并滚动过去 */
  cardClick(key) {
    const def = this.METRIC_DEFS.find(m => m.key === key);
    if (!def) return;
    if (key === 'cn_core_cpi') { toast('中国核心 CPI 数据源暂缺'); return; }
    this.sel = new Set([key]);
    this.saveSel();
    this.renderChips();
    this.loadSeries().then(() => {
      document.querySelector('.macro-sec').scrollIntoView({behavior: 'smooth', block: 'start'});
    });
  },

  /* ================= 指标chips + 范围 ================= */
  renderChips() {
    const box = document.getElementById('macroChips');
    if (!box) return;
    box.innerHTML = this.METRIC_DEFS.map(m => {
      const on = this.sel.has(m.key);
      const missing = m.key === 'cn_core_cpi';
      return `<button class="m-chip${on ? ' on' : ''}${missing ? ' miss' : ''}"
        style="--c:${this.COLORS[m.key]}" onclick="MacroView.toggleMetric('${m.key}')"
        title="${missing ? '数据源暂缺' : ''}">${m.label}</button>`;
    }).join('');
  },

  toggleMetric(key) {
    if (key === 'cn_core_cpi') { toast('中国核心 CPI 数据源暂缺'); return; }
    if (this.sel.has(key)) {
      if (this.sel.size <= 1) { toast('至少保留一个指标'); return; }
      this.sel.delete(key);
    } else {
      this.sel.add(key);
    }
    this.saveSel();
    this.renderChips();
    this.loadSeries();
  },

  setRange(r) {
    this.range = r;
    try { localStorage.setItem('macro_range', r); } catch (e) { /* 忽略 */ }
    document.querySelectorAll('#macroRange .tf').forEach(b =>
      b.classList.toggle('active', b.dataset.r === r));
    this.loadSeries();
  },

  saveSel() {
    try { localStorage.setItem('macro_sel', JSON.stringify([...this.sel])); } catch (e) { /* 忽略 */ }
  },

  async loadSeries() {
    try {
      const r = await apiFetch(`/api/macro/series?metrics=${[...this.sel].join(',')}&range=${this.range}`);
      const d = await r.json();
      this.seriesData = d.series || [];
    } catch (e) { this.seriesData = []; }
    this.hoverM = null;
    this.drawTrend();
  },

  css(name) {
    return getComputedStyle(document.body).getPropertyValue(name).trim();
  },

  /* ================= 通胀趋势折线图 ================= */
  drawTrend() {
    const cv = document.getElementById('macroChart');
    const body = document.getElementById('macroChartBody');
    if (!cv || !body) return;
    const dpr = window.devicePixelRatio || 1;
    const W = body.clientWidth, H = body.clientHeight;
    if (cv.width !== W * dpr || cv.height !== H * dpr) {
      cv.width = W * dpr; cv.height = H * dpr;
    }
    const ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const C = {grid: this.css('--grid'), axis: this.css('--axis'),
               line: this.css('--line')};
    const tip = document.getElementById('macroTip');
    const series = this.seriesData.filter(s => !s.missing && s.points.length);
    if (!series.length) {
      ctx.fillStyle = C.axis; ctx.font = '13px sans-serif'; ctx.textAlign = 'center';
      ctx.fillText('加载趋势数据…', W / 2, H / 2);
      if (tip) tip.textContent = '';
      return;
    }
    /* 统一x轴: 所有序列日期并集 */
    const dates = [...new Set(series.flatMap(s => s.points.map(p => p[0])))].sort();
    const di = new Map(dates.map((d, i) => [d, i]));
    const maps = series.map(s => new Map(s.points));
    let vMin = Infinity, vMax = -Infinity;
    series.forEach(s => s.points.forEach(p => {
      vMin = Math.min(vMin, p[1]); vMax = Math.max(vMax, p[1]);
    }));
    const pad = (vMax - vMin) * 0.1 || 1;
    vMin -= pad; vMax += pad;
    const plotW = W - this.PAD_R, plotH = H - this.PAD_B - 8;
    const yV = v => 8 + (1 - (v - vMin) / (vMax - vMin)) * plotH;
    const xI = i => dates.length > 1 ? i / (dates.length - 1) * plotW : plotW / 2;

    /* 网格 + 右轴刻度 */
    ctx.font = '10px sans-serif'; ctx.textAlign = 'left';
    for (let g = 0; g <= 4; g++) {
      const v = vMin + (vMax - vMin) * g / 4;
      const y = yV(v);
      ctx.strokeStyle = C.grid; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(plotW, y); ctx.stroke();
      ctx.fillStyle = C.axis;
      ctx.fillText(v.toFixed(1) + '%', plotW + 4, y + 3);
    }
    /* 0轴虚线 */
    if (vMin < 0 && vMax > 0) {
      ctx.strokeStyle = C.axis; ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.moveTo(0, yV(0)); ctx.lineTo(plotW, yV(0)); ctx.stroke();
      ctx.setLineDash([]);
    }
    /* x轴日期标签(取~6个) */
    ctx.textAlign = 'center'; ctx.fillStyle = C.axis;
    const step = Math.max(1, Math.ceil(dates.length / 6));
    for (let i = 0; i < dates.length; i += step) {
      ctx.fillText(dates[i], Math.min(Math.max(xI(i), 24), plotW - 24), H - 6);
    }
    /* 各序列折线 */
    series.forEach(s => {
      ctx.strokeStyle = this.COLORS[s.key]; ctx.lineWidth = 1.6;
      ctx.beginPath();
      let started = false;
      s.points.forEach(p => {
        const x = xI(di.get(p[0])), y = yV(p[1]);
        if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
      });
      ctx.stroke();
    });
    /* 十字光标 */
    let hi = this.hoverM == null ? dates.length - 1 : this.hoverM;
    if (hi >= 0 && hi < dates.length) {
      const x = xI(hi);
      ctx.strokeStyle = C.axis; ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.moveTo(x, 8); ctx.lineTo(x, 8 + plotH); ctx.stroke();
      ctx.setLineDash([]);
      if (tip) {
        tip.innerHTML = `<span style="color:var(--muted)">${dates[hi]}</span>  ` +
          series.map((s, idx) => {
            const v = maps[idx].get(dates[hi]);
            return v == null ? '' :
              `<b style="color:${this.COLORS[s.key]}">${s.label} ${v.toFixed(2)}%</b>`;
          }).filter(Boolean).join('  ');
      }
    }
  },

  idxAtM(x, n) {
    const plotW = document.getElementById('macroChartBody').clientWidth - this.PAD_R;
    return n > 1 ? Math.min(n - 1, Math.max(0, Math.round(x / plotW * (n - 1)))) : 0;
  },

  /* ================= 美债收益率曲线 ================= */
  setYieldMode(mode) {
    this.yieldMode = mode;
    document.getElementById('ymYield').classList.toggle('active', mode === 'yield');
    document.getElementById('ymSpread').classList.toggle('active', mode === 'spread');
    this.drawYields();
  },

  /* 模式转换: spread=各期限相对本曲线10Y的利差 */
  curveVals(curve) {
    if (!curve) return null;
    const m = new Map(curve.points);
    const base = this.yieldMode === 'spread' ? m.get('10Y') : 0;
    return curve.points.map(([t, v]) => [t, this.yieldMode === 'spread' ? v - base : v]);
  },

  drawYields() {
    const cv = document.getElementById('yieldChart');
    const body = document.getElementById('yieldChartBody');
    if (!cv || !body) return;
    const dpr = window.devicePixelRatio || 1;
    const W = body.clientWidth, H = body.clientHeight;
    if (cv.width !== W * dpr || cv.height !== H * dpr) {
      cv.width = W * dpr; cv.height = H * dpr;
    }
    const ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const C = {grid: this.css('--grid'), axis: this.css('--axis')};
    const tip = document.getElementById('yieldTip');
    const y = this.yields || {};
    const cur = this.curveVals(y.current);
    if (!cur || !cur.length) {
      ctx.fillStyle = C.axis; ctx.font = '13px sans-serif'; ctx.textAlign = 'center';
      ctx.fillText('加载美债收益率数据…', W / 2, H / 2);
      if (tip) tip.textContent = '';
      this.drawSpread();
      return;
    }
    const dateEl = document.getElementById('yieldDate');
    if (dateEl) dateEl.textContent = `当前 ${y.current.date}` +
      ((y.history || {})['1y'] ? ` · 1年前 ${y.history['1y'].date}` : '');

    const h1 = document.getElementById('hist1y') && document.getElementById('hist1y').checked ?
      this.curveVals(y.history['1y']) : null;
    const h3 = document.getElementById('hist3y') && document.getElementById('hist3y').checked ?
      this.curveVals(y.history['3y']) : null;

    const n = cur.length;
    let vMin = Infinity, vMax = -Infinity;
    [cur, h1, h3].forEach(arr => (arr || []).forEach(([, v]) => {
      vMin = Math.min(vMin, v); vMax = Math.max(vMax, v);
    }));
    const pad = (vMax - vMin) * 0.12 || 0.5;
    vMin -= pad; vMax += pad;
    const plotW = W - this.PAD_R, plotH = H - this.PAD_B - 8;
    const yV = v => 8 + (1 - (v - vMin) / (vMax - vMin)) * plotH;
    const xI = i => n > 1 ? i / (n - 1) * plotW : plotW / 2;

    /* 网格 + 右轴 */
    ctx.font = '10px sans-serif'; ctx.textAlign = 'left';
    for (let g = 0; g <= 4; g++) {
      const v = vMin + (vMax - vMin) * g / 4;
      const yy = yV(v);
      ctx.strokeStyle = C.grid; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(0, yy); ctx.lineTo(plotW, yy); ctx.stroke();
      ctx.fillStyle = C.axis;
      ctx.fillText((v > 0 ? '+' : '') + v.toFixed(2), plotW + 4, yy + 3);
    }
    /* 0轴 */
    if (vMin < 0 && vMax > 0) {
      ctx.strokeStyle = C.axis; ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.moveTo(0, yV(0)); ctx.lineTo(plotW, yV(0)); ctx.stroke();
      ctx.setLineDash([]);
    }
    /* x轴期限标签 */
    ctx.textAlign = 'center'; ctx.fillStyle = C.axis;
    cur.forEach(([t], i) =>
      ctx.fillText(t, Math.min(Math.max(xI(i), 14), plotW - 10), H - 6));

    const drawLine = (arr, color, dash, width) => {
      ctx.strokeStyle = color; ctx.lineWidth = width;
      ctx.setLineDash(dash);
      ctx.beginPath();
      arr.forEach(([, v], i) => {
        const x = xI(i), yy = yV(v);
        i === 0 ? ctx.moveTo(x, yy) : ctx.lineTo(x, yy);
      });
      ctx.stroke();
      ctx.setLineDash([]);
    };
    if (h3) drawLine(h3, 'rgba(155, 89, 182, .75)', [6, 4], 1.4);
    if (h1) drawLine(h1, 'rgba(74, 144, 226, .75)', [6, 4], 1.4);
    drawLine(cur, this.css('--accent') || '#c0392b', [], 2);

    /* 当前曲线: 点 + 数值标注 */
    ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
    cur.forEach(([t, v], i) => {
      const x = xI(i), yy = yV(v);
      ctx.fillStyle = this.css('--accent') || '#c0392b';
      ctx.beginPath(); ctx.arc(x, yy, 2.6, 0, Math.PI * 2); ctx.fill();
      const label = this.yieldMode === 'spread' ? (v > 0 ? '+' : '') + v.toFixed(2) : v.toFixed(2);
      ctx.fillStyle = C.axis;
      ctx.fillText(label, x, Math.max(12, yy - 8));
    });
    /* 十字光标 */
    let hi = this.hoverY == null ? -1 : this.hoverY;
    if (hi >= 0 && hi < n) {
      const x = xI(hi);
      ctx.strokeStyle = C.axis; ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.moveTo(x, 8); ctx.lineTo(x, 8 + plotH); ctx.stroke();
      ctx.setLineDash([]);
      if (tip) {
        const t = cur[hi][0];
        const fmt = ([, v]) => (v > 0 ? '+' : '') + v.toFixed(2) + '%';
        tip.innerHTML = `<span style="color:var(--muted)">${t}</span>  ` +
          `<b style="color:${this.css('--accent')}">当前 ${fmt(cur[hi])}</b>` +
          (h1 ? `  <b style="color:#4a90e2">1年前 ${fmt(h1[hi])}</b>` : '') +
          (h3 ? `  <b style="color:#9b59b6">3年前 ${fmt(h3[hi])}</b>` : '');
      }
    } else if (tip) {
      tip.textContent = this.yieldMode === 'spread' ?
        '相对 10Y 的利差: 数值 = 各期限收益率 − 同曲线 10Y' : '';
    }
    this.renderSpreadHead();
    this.drawSpread();
  },

  renderSpreadHead() {
    const sp = (this.yields || {}).spread || {};
    const v = document.getElementById('spreadVal');
    const inv = document.getElementById('spreadInv');
    if (v) v.innerHTML = sp.value == null ? '--' :
      `<span class="${sp.value < 0 ? 'down' : 'up'}">${(sp.value > 0 ? '+' : '') + sp.value.toFixed(3)}%</span>`;
    if (inv) {
      inv.textContent = sp.inverted ? `已倒挂 ${sp.days} 个交易日` : '未倒挂';
      inv.className = 'macro-inv ' + (sp.inverted ? 'inv' : '');
    }
  },

  /* ================= 10Y-2Y 利差小图 ================= */
  drawSpread() {
    const cv = document.getElementById('spreadChart');
    const body = document.getElementById('spreadBody');
    if (!cv || !body) return;
    const dpr = window.devicePixelRatio || 1;
    const W = body.clientWidth, H = body.clientHeight;
    if (cv.width !== W * dpr || cv.height !== H * dpr) {
      cv.width = W * dpr; cv.height = H * dpr;
    }
    const ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const C = {grid: this.css('--grid'), axis: this.css('--axis')};
    const sp = ((this.yields || {}).spread || {}).series || [];
    if (!sp.length) return;
    let vMin = Math.min(0, ...sp.map(p => p[1]));
    let vMax = Math.max(0.2, ...sp.map(p => p[1]));
    const pad = (vMax - vMin) * 0.12 || 0.1;
    vMin -= pad; vMax += pad;
    const plotW = W - this.PAD_R, plotH = H - 4;
    const yV = v => 2 + (1 - (v - vMin) / (vMax - vMin)) * plotH;
    const xI = i => sp.length > 1 ? i / (sp.length - 1) * plotW : plotW / 2;
    /* 网格 */
    ctx.font = '9px sans-serif'; ctx.textAlign = 'left';
    for (let g = 0; g <= 2; g++) {
      const v = vMin + (vMax - vMin) * g / 2;
      ctx.strokeStyle = C.grid;
      ctx.beginPath(); ctx.moveTo(0, yV(v)); ctx.lineTo(plotW, yV(v)); ctx.stroke();
      ctx.fillStyle = C.axis;
      ctx.fillText(v.toFixed(1), plotW + 4, yV(v) + 3);
    }
    /* 倒挂区间色带(0轴以下填充) */
    const y0 = yV(0);
    ctx.fillStyle = 'rgba(208, 58, 47, .16)';
    sp.forEach((p, i) => {
      if (p[1] < 0) ctx.fillRect(xI(i) - 0.7, y0, 1.4, yV(p[1]) - y0);
    });
    /* 0轴 */
    ctx.strokeStyle = C.axis; ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.moveTo(0, y0); ctx.lineTo(plotW, y0); ctx.stroke();
    ctx.setLineDash([]);
    /* 利差线 */
    ctx.strokeStyle = this.css('--accent') || '#c0392b'; ctx.lineWidth = 1.2;
    ctx.beginPath();
    sp.forEach((p, i) => i === 0 ? ctx.moveTo(xI(i), yV(p[1])) : ctx.lineTo(xI(i), yV(p[1])));
    ctx.stroke();
    /* 悬停光标 */
    if (this.hoverS != null && this.hoverS >= 0 && this.hoverS < sp.length) {
      const x = xI(this.hoverS);
      ctx.strokeStyle = C.axis; ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(x, 2); ctx.lineTo(x, 2 + plotH); ctx.stroke();
      ctx.setLineDash([]);
      const p = sp[this.hoverS];
      const t = document.getElementById('yieldTip');
      if (t) t.innerHTML = `<span style="color:var(--muted)">${p[0]} 利差</span>  ` +
        `<b class="${p[1] < 0 ? 'down' : 'up'}">${(p[1] > 0 ? '+' : '') + p[1].toFixed(3)}%</b>`;
    }
  },

  idxAtS(x, n) {
    const plotW = document.getElementById('spreadBody').clientWidth - this.PAD_R;
    return n > 1 ? Math.min(n - 1, Math.max(0, Math.round(x / plotW * (n - 1)))) : 0;
  },

  /* ================= 数据明细表 ================= */
  tableRows() {
    const rows = [];
    ((this.overview || {}).cards || []).forEach(c => {
      rows.push({country: c.country, group: '通胀指标', name: c.label,
                 value: c.missing ? null : c.value, chg: c.missing ? null : c.chg,
                 date: c.date || '', publish: c.publish || '', missing: !!c.missing});
    });
    const y = this.yields || {};
    if (y.current && y.prev) {
      const cm = new Map(y.current.points), pm = new Map(y.prev.points);
      y.current.points.forEach(([t, v]) => {
        const pv = pm.get(t);
        rows.push({country: '美国', group: '美债收益率', name: '美债 ' + t,
                   value: v, chg: pv == null ? null : +(v - pv).toFixed(3),
                   date: y.current.date, publish: y.current.date, missing: false});
      });
    }
    return rows;
  },

  renderTable() {
    const body = document.getElementById('macroBody');
    if (!body) return;
    const fc = (document.getElementById('macroFCountry') || {}).value || '';
    const fg = (document.getElementById('macroFGroup') || {}).value || '';
    const q = ((document.getElementById('macroQ') || {}).value || '').trim().toLowerCase();
    let rows = this.tableRows().filter(r =>
      (!fc || r.country === fc) && (!fg || r.group === fg) &&
      (!q || r.name.toLowerCase().includes(q) || r.date.includes(q)));
    const k = this.sortKey, dir = this.sortDir;
    rows.sort((a, b) => {
      const va = a[k], vb = b[k];
      if (va == null) return 1;
      if (vb == null) return -1;
      if (typeof va === 'number') return (va - vb) * dir;
      return String(va).localeCompare(String(vb)) * dir;
    });
    /* 排序指示 */
    ['Name', 'Value', 'Chg', 'Date'].forEach(sfx => {
      const th = document.getElementById('mSort' + sfx);
      if (th) th.querySelector('.si').textContent =
        sfx.toLowerCase() === k ? (dir > 0 ? ' ▲' : ' ▼') : '';
    });
    const cnt = document.getElementById('macroCount');
    if (cnt) cnt.textContent = `共 ${rows.length} 条`;
    body.innerHTML = rows.length ? rows.map(r => {
      const chgCls = r.chg > 0 ? 'up' : (r.chg < 0 ? 'down' : 'flat');
      return `<tr>
        <td>${r.country}</td><td>${r.group}</td>
        <td>${esc(r.name)}</td>
        <td class="mono">${r.missing ? '<span class="flat">暂缺</span>' : r.value.toFixed(2) + '%'}</td>
        <td class="mono">${r.chg == null ? '--' :
          `<span class="${chgCls}">${r.chg > 0 ? '▲' : r.chg < 0 ? '▼' : '—'} ` +
          `${r.chg > 0 ? '+' : ''}${r.chg.toFixed(2)}</span>`}</td>
        <td class="mono">${r.date || '--'}</td>
        <td class="mono">${r.publish || '--'}</td>
      </tr>`;
    }).join('') : '<tr><td colspan="7" class="empty">无匹配数据</td></tr>';
  },

  toggleSort(key) {
    if (this.sortKey === key) this.sortDir *= -1;
    else { this.sortKey = key; this.sortDir = 1; }
    this.renderTable();
  },

  /* ================= 重绘(主题切换/尺寸变化) ================= */
  redrawAll() {
    this.drawTrend();
    this.drawYields();
  },

  /* ================= 事件绑定(一次性) ================= */
  init() {
    const mc = document.getElementById('macroChart');
    mc.addEventListener('mousemove', e => {
      const n = new Set(this.seriesData.flatMap(s => s.points.map(p => p[0]))).size;
      if (!n) return;
      const rect = mc.getBoundingClientRect();
      this.hoverM = this.idxAtM(e.clientX - rect.left, n);
      this.drawTrend();
    });
    mc.addEventListener('mouseleave', () => { this.hoverM = null; this.drawTrend(); });
    mc.addEventListener('touchstart', e => {
      const n = new Set(this.seriesData.flatMap(s => s.points.map(p => p[0]))).size;
      if (!n) return;
      const rect = mc.getBoundingClientRect();
      this.hoverM = this.idxAtM(e.touches[0].clientX - rect.left, n);
      this.drawTrend();
    }, {passive: true});

    const yc = document.getElementById('yieldChart');
    const tenorIdx = x => {
      const plotW = document.getElementById('yieldChartBody').clientWidth - this.PAD_R;
      const cur = (this.yields || {}).current;
      const n = cur ? cur.points.length : 0;
      return n > 1 ? Math.min(n - 1, Math.max(0, Math.round(x / plotW * (n - 1)))) : -1;
    };
    yc.addEventListener('mousemove', e => {
      const rect = yc.getBoundingClientRect();
      this.hoverY = tenorIdx(e.clientX - rect.left);
      this.drawYields();
    });
    yc.addEventListener('mouseleave', () => { this.hoverY = null; this.drawYields(); });
    yc.addEventListener('touchstart', e => {
      const rect = yc.getBoundingClientRect();
      this.hoverY = tenorIdx(e.touches[0].clientX - rect.left);
      this.drawYields();
    }, {passive: true});

    const sc = document.getElementById('spreadChart');
    sc.addEventListener('mousemove', e => {
      const sp = ((this.yields || {}).spread || {}).series || [];
      if (!sp.length) return;
      const rect = sc.getBoundingClientRect();
      this.hoverS = this.idxAtS(e.clientX - rect.left, sp.length);
      this.drawSpread();
    });
    sc.addEventListener('mouseleave', () => { this.hoverS = null; this.drawSpread(); });

    window.addEventListener('resize', () => {
      if (document.getElementById('view-macro').classList.contains('active')) this.redrawAll();
    });
  },
};

/* const 声明不上 window: app.js 通过 window.MacroView 探测(与内联 onclick 兼容), 需显式挂载 */
window.MacroView = MacroView;
MacroView.init();
