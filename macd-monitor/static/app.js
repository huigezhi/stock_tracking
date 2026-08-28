/* MACD 监控 Web UI 前端逻辑 */
"use strict";

let groups = [];
let searchTimer = null;

/* ================= 通用 ================= */
function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 1800);
}
function esc(s) {
  return String(s).replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function pctClass(v) { return v > 0 ? 'up' : (v < 0 ? 'down' : 'flat'); }
function fmtPct(v) { return (v > 0 ? '+' : '') + v.toFixed(2) + '%'; }
function fmtYi(v) {
  const a = Math.abs(v);
  if (a >= 1e8) return (v / 1e8).toFixed(2) + '亿';
  if (a >= 1e4) return (v / 1e4).toFixed(1) + '万';
  return v.toFixed(0);
}
function fmtVol(v) {
  const a = Math.abs(v);
  if (a >= 1e8) return (v / 1e8).toFixed(2) + '亿手';
  if (a >= 1e4) return (v / 1e4).toFixed(1) + '万手';
  return v.toFixed(0) + '手';
}

/* ================= 主题 ================= */
function applyTheme(mode) {
  const dark = mode === 'dark' ||
    (mode === 'auto' && matchMedia('(prefers-color-scheme: dark)').matches);
  document.body.classList.toggle('dark', dark);
  if (window.__kchartReady) KChart.draw();  // KChart 定义前(初始化阶段)不绘制
  if (window.__kchartReady && window.ShareChart) ShareChart.draw();
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

/* ================= 指数列表 ================= */
let idxData = [];

async function loadIdxList() {
  try {
    const r = await fetch('/api/index/list');
    idxData = await r.json();
    renderIdxList();
  } catch (e) { /* 下轮重试 */ }
}

function renderIdxList() {
  const box = document.getElementById('idxList');
  if (!idxData.length) {
    box.innerHTML = '<div class="empty">暂无指数数据</div>';
    return;
  }
  box.innerHTML = idxData.map(i => `
    <div class="etf-item ${curEtf === i.code ? 'active' : ''}" onclick="selectIdx('${i.code}')">
      <span class="en">${esc(i.name)}</span>
      <span class="ep ${pctClass(i.chg_pct)}">${i.price.toFixed(2)}</span>
      <span class="epct ${pctClass(i.chg_pct)}">${fmtPct(i.chg_pct)}</span>
    </div>`).join('');
}

function selectIdx(code) {
  curEtf = code;
  renderIdxList();
  renderEtfList();
  const i = idxData.find(x => x.code === code);
  if (i) {
    document.getElementById('chName').textContent = i.name;
    document.getElementById('chCode').textContent = i.code;
    document.getElementById('chIndex').textContent = '指数';
    const p = document.getElementById('chPrice');
    p.textContent = i.price.toFixed(2);
    p.className = 'ch-price ' + pctClass(i.chg_pct);
    const c = document.getElementById('chChg');
    c.textContent = `${i.chg > 0 ? '+' : ''}${i.chg.toFixed(2)}  ${fmtPct(i.chg_pct)}`;
    c.className = 'ch-chg ' + pctClass(i.chg_pct);
    document.getElementById('chAmount').textContent = (i.amount / 1e4).toFixed(2) + '亿';
    document.getElementById('chShares').textContent = '--';
  }
  KChart.load(code, curTf);
  ShareChart.load(code, curTf);
}

/* ================= 宽基ETF列表 ================= */
let etfData = [];
let curEtf = null;

async function loadEtfList() {
  try {
    const r = await fetch('/api/etf/list');
    etfData = await r.json();
    document.getElementById('etfUpd').textContent =
      new Date().toTimeString().slice(0, 5) + ' 更新';
    renderEtfList();
    if (!curEtf && etfData.length) selectEtf(etfData[0].code);
  } catch (e) { /* 下轮重试 */ }
}

function renderEtfList() {
  const box = document.getElementById('etfList');
  if (!etfData.length) {
    box.innerHTML = '<div class="empty">暂无符合条件的宽基ETF</div>';
    return;
  }
  box.innerHTML = etfData.map(e => `
    <div class="etf-item ${curEtf === e.code ? 'active' : ''}" onclick="selectEtf('${e.code}')">
      <span class="en">${esc(e.name)}</span>
      <span class="ep ${pctClass(e.chg_pct)}">${e.price.toFixed(3)}</span>
      <span class="ec">${esc(e.index)} · ${e.shares}亿份</span>
      <span class="epct ${pctClass(e.chg_pct)}">${fmtPct(e.chg_pct)}</span>
    </div>`).join('');
}

function selectEtf(code) {
  curEtf = code;
  renderIdxList();
  renderEtfList();
  const e = etfData.find(x => x.code === code);
  if (e) {
    document.getElementById('chName').textContent = e.name;
    document.getElementById('chCode').textContent = e.code;
    document.getElementById('chIndex').textContent = e.index;
    const p = document.getElementById('chPrice');
    p.textContent = e.price.toFixed(3);
    p.className = 'ch-price ' + pctClass(e.chg_pct);
    const c = document.getElementById('chChg');
    c.textContent = `${e.chg > 0 ? '+' : ''}${e.chg.toFixed(3)}  ${fmtPct(e.chg_pct)}`;
    c.className = 'ch-chg ' + pctClass(e.chg_pct);
    document.getElementById('chAmount').textContent = (e.amount / 1e4).toFixed(2) + '亿';
    document.getElementById('chShares').textContent = e.shares + '亿份';
  }
  KChart.load(code, curTf);
  ShareChart.load(code, curTf);
}

/* ================= K线图 ================= */
let curTf = 'day';
function switchTf(tf) {
  curTf = tf;
  document.querySelectorAll('.tf').forEach(b =>
    b.classList.toggle('active', b.dataset.tf === tf));
  if (curEtf) {
    KChart.load(curEtf, tf);
    ShareChart.load(curEtf, tf);
  }
}

const KChart = {
  data: [],          // [[date, open, close, high, low, volume], ...]
  ma: {},            // {5: [...], 10: [...], 20: [...], 60: [...]}
  win: null,         // {start, end} 索引窗口
  hover: null,       // {x, y} 鼠标位置
  code: '', tf: 'day',
  MA_COLORS: {5: '#f5a623', 10: '#4a90e2', 20: '#b37feb', 60: '#26a69a'},
  PAD_R: 56, PAD_B: 20, VOL_H: 0.22,

  async load(code, tf) {
    this.code = code; this.tf = tf;
    this.data = []; this.win = null; this.draw();
    try {
      const r = await fetch(`/api/kline?code=${code}&tf=${tf}&n=800`);
      this.data = await r.json();
    } catch (e) { /* 保持空 */ }
    if (this.code !== code || this.tf !== tf) return;  // 已切换
    this.calcMa();
    // 默认显示最近 120 根
    const n = this.data.length;
    this.win = {start: Math.max(0, n - 120), end: n - 1};
    this.syncSlider();
    this.draw();
    ShareChart.draw();  // K线就绪后重绘(份额面板需引用收盘价)
  },

  calcMa() {
    this.ma = {};
    [5, 10, 20, 60].forEach(p => {
      const arr = [];
      for (let i = 0; i < this.data.length; i++) {
        if (i < p - 1) { arr.push(null); continue; }
        let s = 0;
        for (let j = i - p + 1; j <= i; j++) s += this.data[j][2];
        arr.push(s / p);
      }
      this.ma[p] = arr;
    });
  },

  /* ---------- 双端滑条 ---------- */
  initSlider() {
    const rs = document.getElementById('rStart'), re = document.getElementById('rEnd');
    const onInput = () => {
      const n = this.data.length;
      if (!n) return;
      let s = Math.round(rs.value / 100 * (n - 1));
      let e = Math.round(re.value / 100 * (n - 1));
      if (e - s < 29) {  // 最小窗口30根
        if (this._lastDrag === 's') s = Math.max(0, e - 29);
        else e = Math.min(n - 1, s + 29);
      }
      rs.value = s / (n - 1) * 100;
      re.value = e / (n - 1) * 100;
      this.win = {start: s, end: e};
      this.updateSliderUI();
      this.draw();
    };
    rs.addEventListener('input', () => { this._lastDrag = 's'; onInput(); });
    re.addEventListener('input', () => { this._lastDrag = 'e'; onInput(); });
  },

  syncSlider() {
    const n = this.data.length;
    if (!n) return;
    document.getElementById('rStart').value = this.win.start / (n - 1) * 100;
    document.getElementById('rEnd').value = this.win.end / (n - 1) * 100;
    this.updateSliderUI();
  },

  updateSliderUI() {
    const n = this.data.length;
    if (!n) return;
    const s = this.win.start, e = this.win.end;
    document.getElementById('rangeFill').style.left = (s / (n - 1) * 100) + '%';
    document.getElementById('rangeFill').style.width = ((e - s) / (n - 1) * 100) + '%';
    const sl = document.getElementById('rStartLabel');
    const el = document.getElementById('rEndLabel');
    const half = 40;  // 标签半宽px, 防溢出
    const w = document.getElementById('rangeWrap').clientWidth;
    sl.textContent = this.data[s][0];
    el.textContent = this.data[e][0];
    sl.style.left = Math.min(Math.max(s / (n - 1) * w, half), w - 2 * half) + 'px';
    el.style.right = Math.max(w - e / (n - 1) * w - half, 0) + 'px';
    document.getElementById('sliderCount').textContent = `${e - s + 1} 根`;
  },

  /* ---------- 绘制 ---------- */
  css(name) {
    return getComputedStyle(document.body).getPropertyValue(name).trim();
  },

  draw() {
    const cv = document.getElementById('kchart');
    const body = document.getElementById('chartBody');
    if (!cv || !body) return;
    const dpr = window.devicePixelRatio || 1;
    const W = body.clientWidth, H = body.clientHeight;
    if (cv.width !== W * dpr || cv.height !== H * dpr) {
      cv.width = W * dpr; cv.height = H * dpr;
    }
    const ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const dark = document.body.classList.contains('dark');
    const C = {
      up: this.css('--up') || '#d03a2f', down: this.css('--down') || '#0a8a4a',
      grid: this.css('--grid'), axis: this.css('--axis'), text: this.css('--text'),
      bg: this.css('--card'),
    };
    document.getElementById('chartTip').textContent = '';
    if (!this.data.length || !this.win) {
      ctx.fillStyle = C.axis; ctx.font = '13px sans-serif'; ctx.textAlign = 'center';
      ctx.fillText('加载K线数据…', W / 2, H / 2);
      return;
    }
    const rows = this.data.slice(this.win.start, this.win.end + 1);
    const off = this.win.start;
    const plotW = W - this.PAD_R;
    const volTop = H - this.PAD_B - (H - this.PAD_B) * this.VOL_H;
    const kH = volTop - 8 - 6;      // 主图高度(留顶部信息区)
    const kTop = 6;

    // ---- 价格范围 ----
    let pMin = Infinity, pMax = -Infinity, vMax = 0;
    rows.forEach(r => {
      pMin = Math.min(pMin, r[4]); pMax = Math.max(pMax, r[3]);
      vMax = Math.max(vMax, r[5]);
    });
    [5, 10, 20, 60].forEach(p => {
      for (let i = off; i <= this.win.end; i++) {
        const v = this.ma[p][i];
        if (v != null) { pMin = Math.min(pMin, v); pMax = Math.max(pMax, v); }
      }
    });
    const pPad = (pMax - pMin) * 0.06 || pMax * 0.01;
    pMin -= pPad; pMax += pPad;
    const yP = v => kTop + (1 - (v - pMin) / (pMax - pMin)) * kH;
    const yV = v => volTop + (H - this.PAD_B - 8 - volTop) * (1 - v / (vMax || 1)) + 8 - 8;
    const cw = plotW / rows.length;
    const xC = i => i * cw + cw / 2;

    // ---- 网格与坐标 ----
    ctx.font = '10px sans-serif';
    ctx.strokeStyle = C.grid; ctx.fillStyle = C.axis;
    ctx.lineWidth = 1; ctx.textAlign = 'left';
    const priceFmt = v => v >= 100 ? v.toFixed(1) : v.toFixed(3);
    for (let g = 0; g <= 4; g++) {
      const v = pMin + (pMax - pMin) * g / 4;
      const y = yP(v);
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(plotW, y); ctx.stroke();
      ctx.fillText(priceFmt(v), plotW + 4, y + 3);
    }
    // 日期刻度(约6个)
    ctx.textAlign = 'center';
    const step = Math.max(1, Math.floor(rows.length / 6));
    for (let i = 0; i < rows.length; i += step) {
      const x = xC(i);
      ctx.strokeStyle = C.grid;
      ctx.beginPath(); ctx.moveTo(x, kTop); ctx.lineTo(x, H - this.PAD_B); ctx.stroke();
      ctx.fillStyle = C.axis;
      ctx.fillText(rows[i][0].slice(2).replace(/-/g, '/'), x, H - 6);
    }
    // 成交量分隔线
    ctx.strokeStyle = C.grid;
    ctx.beginPath(); ctx.moveTo(0, volTop); ctx.lineTo(plotW, volTop); ctx.stroke();
    ctx.fillStyle = C.axis; ctx.textAlign = 'left';
    ctx.fillText(fmtVol(vMax), plotW + 4, volTop + 10);

    // ---- 蜡烛 ----
    const bw = Math.max(1, Math.min(cw * 0.7, 13));
    rows.forEach((r, i) => {
      const x = xC(i);
      const rising = r[2] >= r[1];
      const col = rising ? C.up : C.down;
      // 影线
      ctx.strokeStyle = col; ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, yP(r[3])); ctx.lineTo(x, yP(r[4]));
      ctx.stroke();
      // 实体
      const yO = yP(r[1]), yC = yP(r[2]);
      const top = Math.min(yO, yC), h = Math.max(Math.abs(yC - yO), 1);
      ctx.fillStyle = col;
      ctx.fillRect(x - bw / 2, top, bw, h);
      // 成交量柱
      const vh = (H - this.PAD_B - 8 - volTop - 4) * (r[5] / (vMax || 1));
      ctx.globalAlpha = 0.55;
      ctx.fillRect(x - bw / 2, H - this.PAD_B - 8 - vh + 8, bw, vh);
      ctx.globalAlpha = 1;
    });

    // ---- MA 均线 ----
    ctx.lineWidth = 1.3;
    [5, 10, 20, 60].forEach(p => {
      ctx.strokeStyle = this.MA_COLORS[p];
      ctx.beginPath();
      let started = false;
      for (let i = off; i <= this.win.end; i++) {
        const v = this.ma[p][i];
        if (v == null) { started = false; continue; }
        const x = xC(i - off), y = yP(v);
        if (!started) { ctx.moveTo(x, y); started = true; }
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
    });

    // ---- 十字光标 ----
    if (this.hover) {
      const {x, y} = this.hover;
      if (x < plotW) {
        const i = Math.min(rows.length - 1, Math.max(0, Math.floor(x / cw)));
        const cx = xC(i);
        ctx.strokeStyle = C.axis;
        ctx.setLineDash([4, 3]);
        ctx.beginPath(); ctx.moveTo(cx, kTop); ctx.lineTo(cx, H - this.PAD_B); ctx.stroke();
        if (y > kTop && y < H - this.PAD_B) {
          ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(plotW, y); ctx.stroke();
          // Y轴价格标签
          const pv = pMax - (y - kTop) / kH * (pMax - pMin);
          ctx.setLineDash([]);
          ctx.fillStyle = C.axis;
          ctx.fillRect(plotW + 1, y - 8, this.PAD_R - 2, 16);
          ctx.fillStyle = dark ? '#111' : '#fff';
          ctx.textAlign = 'left';
          ctx.fillText(priceFmt(pv), plotW + 4, y + 3);
        }
        ctx.setLineDash([]);
        // 信息面板(HTML)
        this.showTip(i + off);
      }
    } else {
      this.showTip(this.win.end);  // 默认显示最后一根
    }
  },

  showTip(i) {
    const r = this.data[i];
    if (!r) return;
    const prev = this.data[i - 1];
    const chgPct = prev ? (r[2] - prev[2]) / prev[2] * 100 : 0;
    const maTxt = [5, 10, 20, 60].map(p => {
      const v = this.ma[p][i];
      const col = this.MA_COLORS[p];
      return `<span style="color:${col}">MA${p}:${v == null ? '--' : v.toFixed(3)}</span>`;
    }).join('  ');
    const cls = pctClass(chgPct);
    document.getElementById('chartTip').innerHTML =
      `<span style="color:var(--muted)">${r[0]}</span>  ` +
      `开<b>${r[1].toFixed(3)}</b> 高<b class="up">${r[3].toFixed(3)}</b> ` +
      `低<b class="down">${r[4].toFixed(3)}</b> 收<b class="${cls}">${r[2].toFixed(3)}</b> ` +
      `<b class="${cls}">${fmtPct(chgPct)}</b> 量<b>${fmtVol(r[5])}</b><br>${maTxt}`;
  },

  init() {
    const cv = document.getElementById('kchart');
    cv.addEventListener('mousemove', e => {
      const rect = cv.getBoundingClientRect();
      this.hover = {x: e.clientX - rect.left, y: e.clientY - rect.top};
      this.draw();
    });
    cv.addEventListener('mouseleave', () => { this.hover = null; this.draw(); });
    // 滚轮缩放K线窗口
    cv.addEventListener('wheel', e => {
      e.preventDefault();
      if (!this.win || !this.data.length) return;
      const n = this.data.length;
      const cur = this.win.end - this.win.start + 1;
      const factor = e.deltaY > 0 ? 1.2 : 1 / 1.2;
      let cnt = Math.round(Math.min(n, Math.max(30, cur * factor)));
      let end = this.win.end + Math.round((cur - cnt) / 2);  // 以中心缩放
      end = Math.min(n - 1, Math.max(cnt - 1, end));
      this.win = {start: end - cnt + 1, end};
      this.syncSlider();
      this.draw();
    }, {passive: false});
    window.addEventListener('resize', () => this.draw());
    this.initSlider();
  }
};

/* ================= 价格/份额变动面板 ================= */
function weekKey(dateStr) {
  // 返回所在周的周一日期字符串, 用于周线数据对齐
  const d = new Date(dateStr + 'T00:00:00');
  d.setDate(d.getDate() - (d.getDay() + 6) % 7);
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') +
         '-' + String(d.getDate()).padStart(2, '0');
}

const ShareChart = {
  data: [],        // [[date, shares(亿份)], ...]
  code: '', tf: 'day',
  hover: null,     // 鼠标所在柱索引
  PRICE_COLOR: '#4a90e2',

  async load(code, tf) {
    this.code = code; this.tf = tf;
    this.data = []; this.hover = null; this.draw();
    try {
      const r = await fetch(`/api/etf/share?code=${code}&tf=${tf}`);
      this.data = await r.json();
    } catch (e) { /* 保持空 */ }
    if (this.code !== code || this.tf !== tf) return;  // 已切换
    this.draw();
  },

  /* 从K线数据查收盘价: 日线按日期匹配, 周线按所在周匹配 */
  priceAt(date) {
    const kd = KChart.data;
    if (!kd.length) return null;
    if (this.tf === 'week') {
      const key = weekKey(date);
      for (let i = kd.length - 1; i >= 0; i--) {
        if (weekKey(kd[i][0]) === key) return kd[i][2];
      }
      return null;
    }
    for (let i = kd.length - 1; i >= 0; i--) {
      if (kd[i][0] === date) return kd[i][2];
    }
    return null;
  },

  css(name) {
    return getComputedStyle(document.body).getPropertyValue(name).trim();
  },

  draw() {
    const cv = document.getElementById('shareChart');
    const body = document.getElementById('shareBody');
    if (!cv || !body) return;
    const dpr = window.devicePixelRatio || 1;
    const W = body.clientWidth, H = body.clientHeight;
    if (cv.width !== W * dpr || cv.height !== H * dpr) {
      cv.width = W * dpr; cv.height = H * dpr;
    }
    const ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const C = {
      up: this.css('--up') || '#d03a2f', down: this.css('--down') || '#0a8a4a',
      grid: this.css('--grid'), axis: this.css('--axis'),
    };
    const tip = document.getElementById('shareTip');
    const n = this.data.length;
    if (n < 2) {
      tip.textContent = '';
      ctx.fillStyle = C.axis; ctx.font = '12px sans-serif'; ctx.textAlign = 'center';
      ctx.fillText(n === 1 ? '份额数据积累中（每日自动记录，当前 1 天）'
                           : '暂无份额数据，将随系统运行每日自动记录', W / 2, H / 2);
      return;
    }
    // 变动序列: bar i 对应 data[i+1] 相对 data[i] 的份额增减
    const dates = [], chgs = [], prices = [];
    for (let i = 1; i < n; i++) {
      dates.push(this.data[i][0]);
      chgs.push(this.data[i][1] - this.data[i - 1][1]);
      prices.push(this.priceAt(this.data[i][0]));
    }
    const m = dates.length;
    const PAD_L = 48, PAD_R = 44, PAD_T = 8, PAD_B = 18;
    const plotW = W - PAD_L - PAD_R, plotH = H - PAD_T - PAD_B;
    // 右轴: 份额增减, 关于0对称
    let cMax = 0;
    chgs.forEach(v => cMax = Math.max(cMax, Math.abs(v)));
    cMax = (cMax || 1) * 1.15;
    // 左轴: 价格
    let pMin = Infinity, pMax = -Infinity;
    prices.forEach(v => {
      if (v != null) { pMin = Math.min(pMin, v); pMax = Math.max(pMax, v); }
    });
    if (pMin === Infinity) { pMin = 0; pMax = 1; }
    const pPad = (pMax - pMin) * 0.08 || pMax * 0.01 || 1;
    pMin -= pPad; pMax += pPad;
    const yC = v => PAD_T + (1 - (v + cMax) / (2 * cMax)) * plotH;
    const yP = v => PAD_T + (1 - (v - pMin) / (pMax - pMin)) * plotH;
    const cw = plotW / m;
    const xC = i => PAD_L + i * cw + cw / 2;

    // ---- 网格与坐标 ----
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'right';
    for (let g = 0; g <= 2; g++) {
      const v = pMax - (pMax - pMin) * g / 2;
      const y = yP(v);
      ctx.strokeStyle = C.grid; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(W - PAD_R, y); ctx.stroke();
      ctx.fillStyle = this.PRICE_COLOR;
      ctx.fillText(v >= 100 ? v.toFixed(1) : v.toFixed(3), PAD_L - 4, y + 3);
    }
    ctx.fillStyle = C.axis; ctx.textAlign = 'left';
    ctx.fillText('+' + cMax.toFixed(0), W - PAD_R + 4, yC(cMax) + 3);
    ctx.fillText('0', W - PAD_R + 4, yC(0) + 3);
    ctx.fillText('-' + cMax.toFixed(0), W - PAD_R + 4, yC(-cMax) + 3);
    // 日期刻度(约6个)
    ctx.textAlign = 'center';
    const step = Math.max(1, Math.floor(m / 6));
    for (let i = 0; i < m; i += step) {
      ctx.fillStyle = C.axis;
      ctx.fillText(dates[i].slice(2).replace(/-/g, '/'), xC(i), H - 5);
    }

    // ---- 份额增减柱 ----
    const bw = Math.max(1, Math.min(cw * 0.7, 13));
    const zero = yC(0);
    chgs.forEach((v, i) => {
      const y = yC(v);
      ctx.fillStyle = v >= 0 ? C.up : C.down;
      ctx.globalAlpha = 0.75;
      ctx.fillRect(xC(i) - bw / 2, Math.min(y, zero), bw, Math.max(Math.abs(y - zero), 1));
      ctx.globalAlpha = 1;
    });

    // ---- 价格折线(左轴) ----
    ctx.strokeStyle = this.PRICE_COLOR; ctx.lineWidth = 1.3;
    ctx.beginPath();
    let started = false;
    prices.forEach((v, i) => {
      if (v == null) { started = false; return; }
      const x = xC(i), y = yP(v);
      if (!started) { ctx.moveTo(x, y); started = true; }
      else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // ---- 十字光标 ----
    if (this.hover != null && this.hover >= 0 && this.hover < m) {
      const x = xC(this.hover);
      ctx.strokeStyle = C.axis; ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.moveTo(x, PAD_T); ctx.lineTo(x, H - PAD_B); ctx.stroke();
      ctx.setLineDash([]);
      this.showTip(this.hover);
    } else {
      this.showTip(m - 1);  // 默认显示最后一根
    }
  },

  showTip(i) {
    const cur = this.data[i + 1], prev = this.data[i];
    if (!cur || !prev) return;
    const chg = cur[1] - prev[1];
    const price = this.priceAt(cur[0]);
    const cls = chg > 0 ? 'up' : (chg < 0 ? 'down' : 'flat');
    document.getElementById('shareTip').innerHTML =
      `<span style="color:var(--muted)">${cur[0]}</span> ` +
      `价格<b style="color:${this.PRICE_COLOR}">${price == null ? '--' : price.toFixed(3)}</b> ` +
      `份额<b>${cur[1]}亿份</b> ` +
      `<b class="${cls}">${chg > 0 ? '+' : ''}${chg}亿份</b>`;
  },

  init() {
    const cv = document.getElementById('shareChart');
    cv.addEventListener('mousemove', e => {
      const rect = cv.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const body = document.getElementById('shareBody');
      const m = this.data.length - 1;
      if (!m || m < 1) return;
      const PAD_L = 48, PAD_R = 44;
      const plotW = body.clientWidth - PAD_L - PAD_R;
      if (x < PAD_L || x > body.clientWidth - PAD_R) return;
      const cw = plotW / m;
      this.hover = Math.min(m - 1, Math.max(0, Math.floor((x - PAD_L) / cw)));
      this.draw();
    });
    cv.addEventListener('mouseleave', () => { this.hover = null; this.draw(); });
    window.addEventListener('resize', () => this.draw());
  }
};

/* ================= MACD 监控列表 ================= */
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
    document.getElementById('upd').textContent =
      '更新 ' + new Date().toTimeString().slice(0, 5);
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
        flowEl.textContent = fmtVol(q.volume);
        flowEl.className = 'v';
        flowLbl.textContent = '成交额 ' + (q.amount / 1e4).toFixed(1) + '亿';
      } else if (q.flow !== null && q.flow !== undefined) {
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

/* ================= 搜索 ================= */
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
        <input id="newgrp-${esc(it.code)}" placeholder="新分组名" style="display:none;width:80px">
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
  if (res.ok) { loadWatch(); doSearch(); loadDivs(); }
}

async function delStock(code) {
  const r = await fetch('/api/stocks?code=' + encodeURIComponent(code), {method: 'DELETE'});
  const res = await r.json();
  toast(res.ok ? '已删除 ' + code : res.msg || '删除失败');
  if (res.ok) { loadWatch(); loadDivs(); }
}

/* ================= 启动 ================= */
document.getElementById('q').addEventListener('keydown', e => {
  if (e.key === 'Enter') doSearch();
});
document.getElementById('q').addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(doSearch, 400);
});

/* ================= 底背离标的面板 ================= */
let divAll = [];        // 全部行(最近30个扫描日)
let divFiltered = [];   // 筛选后的行
let divRetryTimer = null;

async function loadDivs() {
  try {
    const r = await fetch('/api/divergences');
    renderDivs(await r.json());
  } catch (e) { /* 下轮重试 */ }
}

function renderDivs(d) {
  divAll = d.rows || [];
  const upd = document.getElementById('divUpd');
  let txt = '';
  if (d.scanning) {
    const pct = d.total ? Math.round(d.done / d.total * 100) : 0;
    txt = `扫描中 ${d.done}/${d.total} (${pct}%)`;
    clearTimeout(divRetryTimer);
    divRetryTimer = setTimeout(loadDivs, 10000);   // 扫描期间高频轮询进度
  } else if (d.ts) {
    txt = new Date(d.ts * 1000).toLocaleString('zh-CN',
        { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) + ' 扫描';
  }
  upd.textContent = txt;
  updateDivDateOptions();
  applyDivFilters();
}

function updateDivDateOptions() {
  const sel = document.getElementById('divDate');
  const cur = sel.value;
  const dates = [...new Set(divAll.map(r => r.scan))].sort().reverse();
  sel.innerHTML = '<option value="">全部日期</option>' +
      dates.map(dt => `<option value="${dt}">${dt}</option>`).join('');
  // 默认选最新交易日; 用户已改选则保持
  sel.value = dates.includes(cur) ? cur : (dates[0] || '');
}

/* 列排序: 默认按确认日期倒序, 'asc'/'desc'=升降序; 空值排最后 */
let divSort = {key: 'confirm', dir: 'desc'};
const DIV_SORT_COLS = {confirm: 'sortConfirm', dif_inc: 'sortDifInc',
                       chg3: 'sortChg3', chg5: 'sortChg5'};

function toggleDivSort(key) {
  if (divSort.key === key) {
    divSort.dir = divSort.dir === 'desc' ? 'asc' : 'desc';
  } else {
    divSort = {key, dir: 'desc'};
  }
  for (const [k, id] of Object.entries(DIV_SORT_COLS)) {
    const el = document.getElementById(id);
    el.querySelector('.si').textContent =
        divSort.key === k ? (divSort.dir === 'desc' ? ' ▼' : ' ▲') : '';
  }
  renderDivTable();
}

function applyDivFilters() {
  const q = document.getElementById('divQ').value.trim().toLowerCase();
  const tf = document.getElementById('divTf').value;
  const date = document.getElementById('divDate').value;
  const watch = document.getElementById('divWatch').value;
  divFiltered = divAll.filter(r =>
      (!tf || r.tf === tf) &&
      (!date || r.scan === date) &&
      (watch === '' || !!r.watch === (watch === '1')) &&
      (!q || r.name.toLowerCase().includes(q) || r.code.toLowerCase().includes(q)));
  renderDivTable();
}

function renderDivTable() {
  const body = document.getElementById('divBody');
  document.getElementById('divCount').textContent =
      divAll.length ? `${divFiltered.length} / ${divAll.length} 条` : '';
  if (!divAll.length) {
    body.innerHTML = '<tr><td colspan="10" class="empty">暂无底背离标的</td></tr>';
    return;
  }
  if (!divFiltered.length) {
    body.innerHTML = '<tr><td colspan="10" class="empty">无符合条件的标的</td></tr>';
    return;
  }
  let rows = divFiltered;
  if (divSort.key) {
    const k = divSort.key, s = divSort.dir === 'desc' ? -1 : 1;
    rows = [...divFiltered].sort((a, b) => {
      const va = a[k], vb = b[k];
      if (va == null && vb == null) return 0;
      if (va == null) return 1;    // 空值(K线不足/旧缓存)恒排最后
      if (vb == null) return -1;
      return va < vb ? -s : va > vb ? s : 0;
    });
  }
  const fmtPct = v => v == null ? '--' :
      `<span class="${v > 0 ? 'up' : v < 0 ? 'down' : ''}">${v > 0 ? '+' : ''}${v.toFixed(2)}%</span>`;
  const idxOf = new Map(divFiltered.map((r, i) => [r, i]));   // 行对象→筛选结果下标
  body.innerHTML = rows.map((r, i) => `
    <tr class="div-row" onclick="viewDivIdx(${idxOf.get(r)})">
      <td class="di">${i + 1}</td>
      <td class="dstock"><span class="dn">${esc(r.name)}</span><span class="dc">${r.code}</span></td>
      <td class="dtf">${r.tf_name}</td>
      <td class="dd">${r.date1} → ${r.date2}</td>
      <td class="dv">${r.price1.toFixed(2)} → ${r.price2.toFixed(2)}</td>
      <td class="dv">${r.dif1} → ${r.dif2}</td>
      <td class="dv">${r.dif_inc != null ? r.dif_inc.toFixed(3) : '--'}</td>
      <td class="dv">${fmtPct(r.chg3)}</td>
      <td class="dv">${fmtPct(r.chg5)}</td>
      <td class="dd">${r.confirm}</td>
    </tr>`).join('');
}

function viewDivIdx(i) {
  const r = divFiltered[i];
  if (!r) return;
  curEtf = r.code;   // 让日K/周K切换按钮作用于该标的
  document.getElementById('chName').textContent = r.name;
  document.getElementById('chCode').textContent = r.code;
  document.getElementById('chIndex').textContent = r.tf_name + '底背离';
  ['chPrice', 'chChg'].forEach(id => {
    const el = document.getElementById(id);
    el.textContent = '--';
    el.className = el.id;
  });
  document.getElementById('chAmount').textContent = '--';
  document.getElementById('chShares').textContent = '--';
  switchTf(r.tf);
}

KChart.init();
ShareChart.init();
window.__kchartReady = true;
applyTheme(localStorage.getItem('theme') || 'auto');  // 补一次主题下的绘制
loadIdxList();
loadEtfList();
loadWatch();
loadDivs();
setInterval(refreshQuotes, 5000);
setInterval(loadIdxList, 60000);
setInterval(loadEtfList, 60000);
setInterval(loadDivs, 3600000);   // 每小时拉一次, 后端每日全量重扫
