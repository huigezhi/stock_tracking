/* MACD 监控 Web UI 前端逻辑 */
"use strict";

let groups = [];
let searchTimer = null;

/* ================= 通用 ================= */
/* 认证: 所有 /api/* 请求自动携带 Bearer token; 401/423 时弹登录框 */
let authToken = localStorage.getItem('auth_token') || '';

async function apiFetch(url, opts) {
  opts = opts || {};
  opts.headers = Object.assign({}, opts.headers);
  if (authToken) opts.headers['Authorization'] = 'Bearer ' + authToken;
  const r = await fetch(url, opts);
  if (r.status === 401 || r.status === 423) {
    let locked = 0;
    try { locked = (await r.clone().json()).locked || 0; } catch (e) { /* 忽略 */ }
    if (locked > 0) {
      toast(`尝试次数过多, 请${locked}秒后重试`);
    } else {
      showLogin(true);
    }
    throw new Error('unauthorized');
  }
  return r;
}

function showLogin(force) {
  const box = document.getElementById('loginBox');
  if (!box || (!force && box.style.display === 'flex')) return;
  box.style.display = 'flex';
  const input = document.getElementById('loginInput');
  input.value = authToken;
  input.focus();
  document.getElementById('loginErr').textContent = '';
}

async function submitLogin() {
  const input = document.getElementById('loginInput');
  const token = input.value.trim();
  const err = document.getElementById('loginErr');
  if (!token) { err.textContent = '请输入访问令牌'; return; }
  try {
    const r = await apiFetch('/api/auth/status', {
      headers: {'Authorization': 'Bearer ' + token}
    });
    const st = await r.json();
    if (!st.ok) { err.textContent = '令牌无效'; return; }
    authToken = token;
    localStorage.setItem('auth_token', token);
    document.getElementById('loginBox').style.display = 'none';
    // 重新拉取所有数据(loadWatch内部会重建SSE连接)
    loadIdxList(); loadEtfList(); loadWatch(); loadDivs(); loadIntraday(); loadResonance(); refreshQuotes();
  } catch (e) {
    err.textContent = '网络错误, 请重试';
  }
}

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
    const r = await apiFetch('/api/index/list');
    idxData = await r.json();
    renderIdxList();
    ensureSSE();
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
  loadMainChart(code);
  renderWatchActive();
}

/* ================= 宽基ETF列表 ================= */
let etfData = [];
let curEtf = null;

async function loadEtfList() {
  try {
    const r = await apiFetch('/api/etf/list');
    etfData = await r.json();
    document.getElementById('etfUpd').textContent =
      new Date().toTimeString().slice(0, 5) + ' 更新';
    renderEtfList();
    if (!curEtf && etfData.length) selectEtf(etfData[0].code);
    ensureSSE();
  } catch (e) { /* 下轮重试 */ }
}

/* ================= 列表分页(左右栏固定高度, 超出分页, 不无限增长) ================= */
let etfPage = { n: 1, size: 0 };
let watchPage = { n: 1, size: 0 };

const etfItemHtml = e => `
    <div class="etf-item ${curEtf === e.code ? 'active' : ''}" onclick="selectEtf('${e.code}')">
      <span class="en">${esc(e.name)}</span>
      <span class="ep ${pctClass(e.chg_pct)}">${e.price.toFixed(3)}</span>
      <span class="ec">${esc(e.index)} · ${e.shares}亿份</span>
      <span class="epct ${pctClass(e.chg_pct)}">${fmtPct(e.chg_pct)}</span>
    </div>`;

/* 以左栏宽基ETF面板底边线为基准, 对齐右栏监控列表高度 */
function alignColumns() {
  const etfPanel = document.querySelector('.etf-panel');
  const watch = document.getElementById('watch');
  if (!etfPanel || !watch) return;
  const bottom = etfPanel.getBoundingClientRect().bottom;
  const top = watch.getBoundingClientRect().top;
  const cs = getComputedStyle(watch.closest('.monitor-panel'));
  const pagerH = document.getElementById('watchPager').offsetHeight || 32;
  const h = bottom - top - (parseFloat(cs.paddingBottom) || 0) - pagerH - 1;
  if (h >= 120) watch.style.height = h + 'px';   // 窄屏堆叠布局下 h 为负, 走CSS默认高度
}

function renderPager(el, pg, pages) {
  if (pages <= 1) { el.innerHTML = ''; return; }
  el.innerHTML =
    `<button ${pg.n <= 1 ? 'disabled' : ''} onclick="turnPage('${el.id}',-1)">‹ 上一页</button>` +
    `<span class="pg-info">${pg.n} / ${pages} 页</span>` +
    `<button ${pg.n >= pages ? 'disabled' : ''} onclick="turnPage('${el.id}',1)">下一页 ›</button>`;
}

function turnPage(id, d) {
  if (id === 'etfPager') { etfPage.n += d; renderEtfList(); }
  else { watchPage.n += d; renderWatchList(); }
}

/* 窗口尺寸变化: 重算左右栏每页条数并重新对齐底边线 */
window.addEventListener('resize', () => {
  etfPage.size = 0;
  watchPage.size = 0;
  renderEtfList();
  renderWatchList();
});

function renderEtfList() {
  const box = document.getElementById('etfList');
  const pgEl = document.getElementById('etfPager');
  if (!etfData.length) {
    box.innerHTML = '<div class="empty">暂无符合条件的宽基ETF</div>';
    pgEl.innerHTML = '';
    return;
  }
  box.innerHTML = etfData.map(etfItemHtml).join('');   // 全量渲染一次以测量行高
  if (!etfPage.size) {
    const item = box.querySelector('.etf-item');
    if (item) etfPage.size = Math.max(1, Math.floor(box.clientHeight / item.offsetHeight));
  }
  const pages = Math.max(1, Math.ceil(etfData.length / etfPage.size));
  etfPage.n = Math.min(Math.max(1, etfPage.n), pages);
  const start = (etfPage.n - 1) * etfPage.size;
  if (etfPage.size < etfData.length)
    box.innerHTML = etfData.slice(start, start + etfPage.size).map(etfItemHtml).join('');
  renderPager(pgEl, etfPage, pages);
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
  loadMainChart(code);
  renderWatchActive();
}

/* ================= K线图 ================= */
/* KChart/ShareChart 图表实现拆分至 chart.js(先于本文件加载) */
let curTf = 'day';

/* 按当前周期加载中栏主图(分时模式加载分时数据, 份额面板按日线口径) */
function loadMainChart(code) {
  if (curTf === 'min') {
    KChart.tf = 'min';              // 触发KChart.draw转发到分时图
    MinuteChart.load(code);
    ShareChart.load(code, 'day');
  } else {
    KChart.load(code, curTf);
    ShareChart.load(code, curTf);
  }
}

function switchTf(tf) {
  curTf = tf;
  document.querySelectorAll('.tf').forEach(b =>
    b.classList.toggle('active', b.dataset.tf === tf));
  // 分时模式隐藏K线窗口滑条(分时图固定全天视图)
  const slider = document.querySelector('.chart-slider');
  if (slider) slider.style.display = tf === 'min' ? 'none' : '';
  if (curEtf) loadMainChart(curEtf);
  else if (tf === 'min') {
    KChart.tf = 'min';
    MinuteChart.draw();
  }
}

/* ================= 选中标的交易时段实时刷新 =================
   分时模式: 8秒轮询当日分钟数据(仅当前选中标的一个请求);
   日K模式: 用最新行情tick更新最后一根K线(优先SSE缓存, 未订阅时单码拉取)。
   未选中的标的不发起任何请求。 */
function isTradingNow() {
  const d = new Date();
  const day = d.getDay();
  if (day === 0 || day === 6) return false;
  const m = d.getHours() * 60 + d.getMinutes();
  return (m >= 570 && m <= 690) || (m >= 780 && m <= 900);   // 9:30-11:30 / 13:00-15:00
}

async function liveTick() {
  if (!curEtf || !isTradingNow() || document.hidden) return;
  if (curTf === 'min') { MinuteChart.load(curEtf); return; }
  if (curTf !== 'day') return;               // 周K实时tick意义不大, 不刷新
  let q = quoteMap[curEtf];
  if (!q || !q.price) {
    // 选中标的不在SSE订阅集(如从底背离表格点开的股票), 单码拉取
    try {
      const r = await apiFetch('/api/quotes?codes=' + encodeURIComponent(curEtf));
      const arr = await r.json();
      if (Array.isArray(arr) && arr[0] && arr[0].ok) q = arr[0];
    } catch (e) { return; }
  }
  if (q && q.price) KChart.liveTick(q);
}
setInterval(liveTick, 8000);

/* ================= MACD 监控列表 ================= */
let watchData = [];   // 自选列表原始数据(名称/分组)
let quoteMap = {};    // code -> 最新行情(点击自选时填充中栏头部)

function renderWatchActive() {
  document.querySelectorAll('.watch-item').forEach(el =>
    el.classList.toggle('active', el.dataset.code === curEtf));
}

function selectWatch(code) {
  curEtf = code;
  renderIdxList();
  renderEtfList();
  renderWatchActive();
  const s = watchData.find(x => x.code === code);
  if (s) {
    document.getElementById('chName').textContent = s.name;
    document.getElementById('chCode').textContent = s.code;
    document.getElementById('chIndex').textContent = s.group || '自选';
  }
  const q = quoteMap[code];
  const p = document.getElementById('chPrice');
  const c = document.getElementById('chChg');
  if (q && q.ok) {
    p.textContent = q.price.toFixed(2);
    p.className = 'ch-price ' + pctClass(q.chg_pct);
    c.textContent = `${q.chg > 0 ? '+' : ''}${q.chg.toFixed(2)}  ${fmtPct(q.chg_pct)}`;
    c.className = 'ch-chg ' + pctClass(q.chg_pct);
    document.getElementById('chAmount').textContent = (q.amount / 1e4).toFixed(2) + '亿';
  } else {
    p.textContent = '--';
    p.className = 'ch-price';
    c.textContent = '';
    c.className = 'ch-chg';
    document.getElementById('chAmount').textContent = '--';
  }
  document.getElementById('chShares').textContent = '--';
  loadMainChart(code);
}

const watchItemHtml = (s, i) => `
    <div class="watch-item ${curEtf === s.code ? 'active' : ''}" data-code="${esc(s.code)}" onclick="selectWatch('${esc(s.code)}')">
      <span class="seq">${i + 1}</span>
      <span class="wname"><span class="n">${esc(s.name)}</span><span class="c">${esc(s.code)}</span></span>
      <span class="grp">${esc(s.group || '自选')}</span>
      <span class="spacer"></span>
      <span class="qcell qprice"><span class="p">--</span><span class="u">--</span></span>
      <span class="qcell qpct">--</span>
      <span class="qcell qflow"><span class="v">--</span><span class="l">--</span></span>
      <button class="del" onclick="event.stopPropagation();delStock('${esc(s.code)}')">删除</button>
    </div>`;

function renderWatchList() {
  const box = document.getElementById('watch');
  const pgEl = document.getElementById('watchPager');
  alignColumns();   // 先对齐左栏ETF面板底边线, 再按高度分页
  if (!watchData.length) {
    box.innerHTML = '<div class="empty">暂无监控标的，请在上方搜索添加</div>';
    pgEl.innerHTML = '';
    return;
  }
  box.innerHTML = watchData.map(watchItemHtml).join('');   // 全量渲染一次以测量行高
  if (!watchPage.size) {
    const item = box.querySelector('.watch-item');
    if (item) watchPage.size =
      Math.max(1, Math.floor((box.clientHeight + 6) / (item.offsetHeight + 6)));
  }
  const pages = Math.max(1, Math.ceil(watchData.length / watchPage.size));
  watchPage.n = Math.min(Math.max(1, watchPage.n), pages);
  const start = (watchPage.n - 1) * watchPage.size;
  const view = watchPage.size < watchData.length
    ? watchData.slice(start, start + watchPage.size) : watchData;
  box.innerHTML = view.map((s, i) => watchItemHtml(s, start + i)).join('');
  renderPager(pgEl, watchPage, pages);
  view.forEach(s => updateWatchRow(s.code));   // 行情来自quoteMap缓存, 不发请求
  updateIntradayBadges();
  updateResBadges();
}

async function loadWatch() {
  const r = await apiFetch('/api/stocks');
  const stocks = await r.json();
  watchData = stocks;
  groups = [...new Set(stocks.map(s => s.group || '自选'))];
  document.getElementById('count').textContent = stocks.length;
  renderWatchList();
  refreshQuotes();
  ensureSSE();   // 自选变化后重连SSE(订阅代码集已变)
}

async function refreshQuotes() {
  try {
    const r = await apiFetch('/api/quotes');
    const data = await r.json();
    document.getElementById('upd').textContent =
      '更新 ' + new Date().toTimeString().slice(0, 5);
    for (const q of data) {
      quoteMap[q.code] = q;
      updateWatchRow(q.code);
    }
  } catch (e) { /* 下轮重试 */ }
}

/* 更新单只自选行的行情DOM(轮询与SSE共用) */
function updateWatchRow(code) {
  const q = quoteMap[code];
  const row = document.querySelector(`.watch-item[data-code="${CSS.escape(code)}"]`);
  if (!q || !row) return;
  const priceEl = row.querySelector('.qprice .p');
  const unitEl = row.querySelector('.qprice .u');
  const pctEl = row.querySelector('.qpct');
  const flowEl = row.querySelector('.qflow .v');
  const flowLbl = row.querySelector('.qflow .l');
  if (!q.ok) {
    priceEl.textContent = '--'; unitEl.textContent = '';
    pctEl.textContent = '--'; pctEl.className = 'qcell qpct';
    flowEl.textContent = '--'; flowLbl.textContent = '';
    return;
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

/* ================= SSE 实时推送 ================= */
/* 后端 /api/stream: 行情diff(3s) + 扫描进度 + 盘中背离预览事件。
   替代前端各自轮询: N个标签页只有1份出网请求; SSE断开时自动回退轮询 */
let es = null;
let sseOk = false;
let sseLastCodes = '';
let intradayMap = {};   // code -> 最近一轮盘中预览信号

function sseCodes() {
  const codes = [];
  watchData.forEach(s => codes.push(s.code));
  idxData.forEach(i => codes.push(i.code));
  etfData.forEach(e => codes.push(e.code));
  return [...new Set(codes)].slice(0, 60).join(',');
}

function setSseUI(ok) {
  const dot = document.getElementById('sseDot');
  if (!dot) return;
  dot.className = 'sse-dot ' + (ok ? 'on' : 'off');
  dot.title = ok ? 'SSE 实时推送已连接' : 'SSE 未连接，行情走轮询';
}

/* 关注代码集变化(增删自选/左栏刷新)时重连SSE */
function ensureSSE() {
  const c = sseCodes();
  if (!c || !window.EventSource) return;
  if (es && c === sseLastCodes) return;
  sseLastCodes = c;
  try { es && es.close(); } catch (e) { /* 忽略 */ }
  let url = '/api/stream?codes=' + encodeURIComponent(c);
  if (authToken) url += '&token=' + encodeURIComponent(authToken);
  es = new EventSource(url);
  es.onopen = () => { sseOk = true; setSseUI(true); };
  es.onerror = () => { sseOk = false; setSseUI(false); };  // EventSource自动重连
  es.onmessage = ev => {
    try { handleSSE(JSON.parse(ev.data)); } catch (e) { /* 忽略坏帧 */ }
  };
}

function handleSSE(ev) {
  if (ev.type === 'quotes') {
    applyQuoteDiff(ev.data || {});
  } else if (ev.type === 'scan_progress') {
    document.getElementById('divUpd').textContent =
      `扫描中 ${ev.done}/${ev.total} (${ev.pct}%)`;
  } else if (ev.type === 'scan_done') {
    loadDivs();
    toast('底背离全市场扫描完成');
  } else if (ev.type === 'intraday') {
    setIntraday(ev.data || []);
  } else if (ev.type === 'resonance') {
    setResonance(ev.data || []);
  }
}

/* 行情diff: 更新自选行/左栏列表/中栏头部 */
function applyQuoteDiff(diff) {
  let listDirty = false;
  for (const [code, q] of Object.entries(diff)) {
    if (!q || !q.price) continue;
    const i = idxData.find(x => x.code === code);
    if (i) {
      i.price = q.price; i.chg = q.chg; i.chg_pct = q.chg_pct; i.amount = q.amount;
      listDirty = true;
    }
    const e = etfData.find(x => x.code === code);
    if (e) {
      e.price = q.price; e.chg = q.chg; e.chg_pct = q.chg_pct; e.amount = q.amount;
      listDirty = true;
    }
    const prev = quoteMap[code];
    quoteMap[code] = Object.assign({}, prev, q, {
      ok: true,
      is_index: (prev && prev.is_index) || code.startsWith('sh000') || code.startsWith('sz399'),
    });
    updateWatchRow(code);
    if (code === curEtf) updateChartHeader(q);
  }
  if (listDirty) { renderIdxList(); renderEtfList(); }
}

function updateChartHeader(q) {
  const p = document.getElementById('chPrice');
  p.textContent = q.price.toFixed(2);
  p.className = 'ch-price ' + pctClass(q.chg_pct);
  const c = document.getElementById('chChg');
  c.textContent = `${q.chg > 0 ? '+' : ''}${q.chg.toFixed(2)}  ${fmtPct(q.chg_pct)}`;
  c.className = 'ch-chg ' + pctClass(q.chg_pct);
  document.getElementById('chAmount').textContent = (q.amount / 1e4).toFixed(2) + '亿';
}

/* ================= 盘中背离预览徽章 ================= */
function setIntraday(rows) {
  const prevKeys = Object.keys(intradayMap);
  intradayMap = {};
  rows.forEach(r => { intradayMap[r.code] = r; });
  updateIntradayBadges();
  const fresh = Object.keys(intradayMap).filter(k => !prevKeys.includes(k));
  if (fresh.length) {
    const names = fresh.slice(0, 3).map(k => intradayMap[k].name).join('、');
    toast(`盘中底背离: ${names}${fresh.length > 3 ? ' 等' : ''}(60分钟, 未收盘确认)`);
  }
}

async function loadIntraday() {
  try {
    const r = await apiFetch('/api/intraday');
    const d = await r.json();
    (d.rows || []).forEach(x => { intradayMap[x.code] = x; });
    updateIntradayBadges();
  } catch (e) { /* 下轮重试 */ }
}

function updateIntradayBadges() {
  document.querySelectorAll('.watch-item').forEach(el => {
    const r = intradayMap[el.dataset.code];
    let b = el.querySelector('.intraday-badge');
    if (r) {
      if (!b) {
        b = document.createElement('span');
        b.className = 'intraday-badge';
        b.textContent = '60分背离';
        el.querySelector('.wname').appendChild(b);
      }
      b.title = `60分钟底背离预览: 价格${r.price1.toFixed(2)}→${r.price2.toFixed(2)} ` +
        `DIF ${r.dif1}→${r.dif2}(未收盘确认)`;
    } else if (b) {
      b.remove();
    }
  });
}

/* ================= 多周期共振徽章(60分/日/周) ================= */
const TF_CN = {'60m': '60分钟', 'day': '日线', 'week': '周线'};
let resMap = {};   // code -> 共振行

function setResonance(rows) {
  const prevKeys = Object.keys(resMap);
  resMap = {};
  rows.forEach(r => { resMap[r.code] = r; });
  updateResBadges();
  const fresh = Object.keys(resMap).filter(k => !prevKeys.includes(k));
  if (fresh.length) {
    const names = fresh.slice(0, 3).map(k => `${resMap[k].name}(${resMap[k].tfs.map(t => TF_CN[t]).join('+')})`).join('、');
    toast(`多周期共振: ${names}${fresh.length > 3 ? ' 等' : ''}`);
  }
}

async function loadResonance() {
  try {
    const r = await apiFetch('/api/resonance');
    const d = await r.json();
    setResonance(d.rows || []);
  } catch (e) { /* 下轮重试 */ }
}

function updateResBadges() {
  document.querySelectorAll('.watch-item').forEach(el => {
    const r = resMap[el.dataset.code];
    let b = el.querySelector('.res-badge');
    if (r) {
      if (!b) {
        b = document.createElement('span');
        b.className = 'res-badge';
        el.querySelector('.wname').appendChild(b);
      }
      b.textContent = `${r.dir === 'bull' ? '↑' : '↓'}${r.tfs.length}周期共振`;
      b.classList.toggle('up', r.dir === 'bull');
      b.classList.toggle('down', r.dir === 'bear');
      b.title = r.detail.map(x =>
        `${TF_CN[x.tf] || x.tf} ${x.word}(${x.ago}根K线前, ${x.label})`).join('\n');
    } else if (b) {
      b.remove();
    }
  });
}

/* ================= 搜索 ================= */
async function doSearch() {
  const q = document.getElementById('q').value.trim();
  if (!q) return;
  const box = document.getElementById('results');
  box.innerHTML = '<div class="empty">搜索中…</div>';
  const r = await apiFetch('/api/search?q=' + encodeURIComponent(q));
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
  const r = await apiFetch('/api/stocks', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({code, name, group})
  });
  const res = await r.json();
  toast(res.ok ? `已添加 ${name} → ${group}` : res.msg || '添加失败');
  if (res.ok) { loadWatch(); doSearch(); loadDivs(); }
}

async function delStock(code) {
  const r = await apiFetch('/api/stocks?code=' + encodeURIComponent(code), {method: 'DELETE'});
  const res = await r.json();
  toast(res.ok ? '已删除 ' + code : res.msg || '删除失败');
  if (res.ok) { loadWatch(); loadDivs(); }
}

/* ================= 系统面板(健康+日志) ================= */
let sysTimer = null;
let logLvl = '';

async function openSys() {
  document.getElementById('sysBox').style.display = 'flex';
  loadSysData();
  sysTimer = setInterval(loadSysData, 15000);   // 打开期间15秒刷新
}

function closeSys() {
  document.getElementById('sysBox').style.display = 'none';
  clearInterval(sysTimer);
  sysTimer = null;
}

async function loadSysData() {
  loadSysHealth();
  loadSysLogs();
}

async function loadSysHealth() {
  try {
    const r = await apiFetch('/api/health');
    renderSysHealth(await r.json());
  } catch (e) {
    document.getElementById('sysHealth').innerHTML =
      '<div class="empty">健康数据获取失败(可能离线)</div>';
  }
}

function fmtUptime(sec) {
  const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600),
        m = Math.floor(sec % 3600 / 60);
  return (d ? d + '天' : '') + (h || d ? h + '时' : '') + m + '分';
}

function renderSysHealth(h) {
  const o = h.obs || {};
  const scan = h.scan || {};
  const scanTxt = scan.scanning ?
    `扫描中 ${scan.done}/${scan.total}` :
    (scan.ts ? new Date(scan.ts * 1000).toLocaleString('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false}) : '未扫描');
  const card = (label, val, warn) =>
    `<div class="sh-card${warn ? ' warn' : ''}"><div class="sh-v">${val}</div><div class="sh-l">${label}</div></div>`;
  const domains = Object.entries((h.sources || {}).domains || {})
    .map(([d, s]) => `${d}${s.open_sec > 0 ? `(熔断中${s.open_sec}s)` : `(连续失败${s.fails})`}`)
    .join('、') || '正常';
  document.getElementById('sysHealth').innerHTML =
    `<div class="sh-grid">` +
    card('运行时长', fmtUptime(o.uptime_sec || 0)) +
    card('24h错误', o.errors_24h || 0, (o.errors_24h || 0) > 0) +
    card('缓冲事件', o.events || 0) +
    card('SSE订阅', h.sse_subs || 0) +
    card('K线缓存', (h.kline_bars || 0).toLocaleString()) +
    card('信号库', h.db_signals || 0) +
    `</div>` +
    `<div class="sh-row"><span>底背离扫描</span><b>${scanTxt}</b></div>` +
    `<div class="sh-row"><span>盘中背离</span><b>${h.intraday || 0} 条</b></div>` +
    `<div class="sh-row"><span>多周期共振</span><b>${h.resonance || 0} 条</b></div>` +
    `<div class="sh-row"><span>数据源域名</span><b>${esc(domains)}</b></div>` +
    `<div class="sh-row"><span>全局限流</span><b>${(h.sources || {}).rate_limiter ? ((h.sources.rate_limiter.rate || 0) + ' req/s, 余' + h.sources.rate_limiter.tokens + '令牌') : '--'}</b></div>`;
}

async function loadSysLogs() {
  try {
    const r = await apiFetch('/api/logs?limit=150' + (logLvl ? '&lvl=' + logLvl : ''));
    const d = await r.json();
    renderSysLogs(d.rows || []);
  } catch (e) {
    document.getElementById('sysLogs').innerHTML =
      '<div class="empty">日志获取失败(可能离线)</div>';
  }
}

function renderSysLogs(rows) {
  const box = document.getElementById('sysLogs');
  if (!rows.length) {
    box.innerHTML = '<div class="empty">暂无事件</div>';
    return;
  }
  box.innerHTML = rows.map(ev => {
    const t = new Date(ev.ts * 1000).toLocaleString('zh-CN',
      {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
       second: '2-digit', hour12: false});
    return `<div class="sl-row lvl-${ev.lvl.toLowerCase()}">` +
      `<span class="sl-t">${t}</span><span class="sl-l">${ev.lvl}</span>` +
      `<span class="sl-m">[${esc(ev.mod)}]</span><span class="sl-x">${esc(ev.msg)}</span></div>`;
  }).join('');
}

function setLogLvl(btn) {
  logLvl = btn.dataset.lvl || '';
  document.querySelectorAll('#sysLvls button').forEach(b =>
    b.classList.toggle('active', b === btn));
  loadSysLogs();
}

/* ================= PWA / 网络状态 ================= */
/* Service Worker 注册: 静态外壳缓存 + 离线快照 */
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/static/sw.js').catch(() => {});
  });
}

/* 离线横幅: 断网提示(SW缓存的壳仍可打开, 行情暂停更新) */
function updateOnlineUI() {
  const b = document.getElementById('offlineBanner');
  if (b) b.style.display = navigator.onLine ? 'none' : 'flex';
}
window.addEventListener('online', updateOnlineUI);
window.addEventListener('offline', updateOnlineUI);
updateOnlineUI();

/* iOS 主屏安装引导: Safari 分享 → 添加到主屏幕(仅提示一次, 已安装/已关闭不再弹) */
function showIosGuide() {
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const standalone = navigator.standalone === true ||
    matchMedia('(display-mode: standalone)').matches;
  if (isIOS && !standalone && !localStorage.getItem('ios_guide_done')) {
    const g = document.getElementById('iosGuide');
    if (g) g.style.display = 'flex';
  }
}
function dismissIosGuide() {
  localStorage.setItem('ios_guide_done', '1');
  document.getElementById('iosGuide').style.display = 'none';
}
showIosGuide();

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
    const r = await apiFetch('/api/divergences');
    renderDivs(await r.json());
  } catch (e) { /* 下轮重试 */ }
}

/* 手动触发全市场底背离重扫(不影响16:00定时扫描) */
async function triggerDivRescan() {
  const btn = document.getElementById('divRescanBtn');
  btn.disabled = true;
  try {
    const r = await apiFetch('/api/div/rescan', {method: 'POST'});
    const d = await r.json();
    toast(d.ok ? '已触发全市场底背离扫描, 约6分钟' : (d.msg || '触发失败'));
    if (d.ok) setTimeout(loadDivs, 1000);   // 立即进入扫描进度轮询
  } catch (e) {
    toast('触发失败, 请稍后重试');
  } finally {
    setTimeout(() => { btn.disabled = false; }, 5000);   // 防连点
  }
}

/* 导出当前筛选结果为CSV(带BOM, Excel打开中文不乱码) */
function exportDivs() {
  if (!divFiltered.length) { toast('当前筛选无数据可导出'); return; }
  const head = ['代码', '名称', '周期', '现价', '低点1日期', '低点1价格', '低点2日期',
    '低点2价格', 'DIF1', 'DIF2', 'DIF增量', '后3日涨幅%', '后5日涨幅%',
    '模型分', '确认日', '共振标签', '共振分', '扫描日'];
  const esc = v => {
    if (v === null || v === undefined) return '';
    v = String(v);
    return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
  };
  const lines = [head.join(',')];
  for (const r of divFiltered) {
    lines.push([r.code, r.name, r.tf_name, r.price, r.date1, r.price1, r.date2,
      r.price2, r.dif1, r.dif2, r.dif_inc, r.chg3, r.chg5, r.ml_score,
      r.confirm, (r.tags || '').split(',').filter(Boolean)
        .map(t => TAG_LABELS[t] || t).join('/'),
      r.score, r.scan].map(esc).join(','));
  }
  const blob = new Blob(['\uFEFF' + lines.join('\r\n')],
      {type: 'text/csv;charset=utf-8'});
  const a = document.createElement('a');
  const ts = new Date();
  const pad = n => String(n).padStart(2, '0');
  a.download = `底背离信号_${ts.getFullYear()}${pad(ts.getMonth() + 1)}${pad(ts.getDate())}_${pad(ts.getHours())}${pad(ts.getMinutes())}.csv`;
  a.href = URL.createObjectURL(blob);
  a.click();
  URL.revokeObjectURL(a.href);
  toast(`已导出 ${divFiltered.length} 条信号`);
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
const DIV_SORT_COLS = {confirm: 'sortConfirm', dif_inc: 'sortDifInc', chg3: 'sortChg3',
                       chg5: 'sortChg5', ml_score: 'sortMl', score: 'sortScore', name: 'sortName', tf: 'sortTf',
                       date2: 'sortDate2', price2: 'sortPrice2', dif2: 'sortDif2'};
const TAG_LABELS = {vol_shrink: '缩量', ma_hold: '均线托底', rsi_repair: 'RSI修复',
                    kdj_gold: 'KDJ金叉', week_align: '周线同向', vol_engulf: '放量反包'};

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
    body.innerHTML = '<tr><td colspan="12" class="empty">暂无底背离标的</td></tr>';
    return;
  }
  if (!divFiltered.length) {
    body.innerHTML = '<tr><td colspan="12" class="empty">无符合条件的标的</td></tr>';
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
  const scoreCell = r => {
    if (r.score == null) return '<td class="dv">--</td>';
    const tags = (r.tags || '').split(',').filter(Boolean);
    const badges = tags.map(t =>
        `<span class="tagb" title="${TAG_LABELS[t] || t}">${TAG_LABELS[t] || t}</span>`).join('');
    return `<td class="dv dsc"><b class="${r.score >= 5 ? 'sc-hi' : r.score >= 3 ? 'sc-mid' : 'sc-lo'}">${r.score}</b>${badges}</td>`;
  };
  const mlCell = r => {
    if (r.ml_score == null) return '<td class="dv">--</td>';
    const v = r.ml_score;
    const cls = v >= 60 ? 'sc-hi' : v >= 40 ? 'sc-mid' : 'sc-lo';
    return `<td class="dv"><b class="${cls}">${v.toFixed(0)}</b></td>`;
  };
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
      ${mlCell(r)}
      ${scoreCell(r)}
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

/* ================= 复盘统计弹窗 ================= */
async function openStats() {
  document.getElementById('statsBox').style.display = 'flex';
  const body = document.getElementById('statsBody');
  body.innerHTML = '加载中…';
  try {
    const r = await apiFetch('/api/stats');
    renderStats(await r.json());
  } catch (e) {
    body.innerHTML = '<div class="empty">加载失败, 请重试</div>';
  }
}

function closeStats() {
  document.getElementById('statsBox').style.display = 'none';
}

/* 模型样本外(walk-forward)指标说明: 上方历史分层为样本内打分, 会偏乐观;
   此处以模型训练时的样本外评估为准 */
function mlWfNote(meta) {
  if (!meta || !meta.wf || !meta.wf.buckets || !meta.wf.buckets.length) return '';
  const wf = meta.wf;
  const buckets = wf.buckets.map(b =>
    `模型分${b.key}: ${b.win}%`).join(' · ');
  return `<div class="wf-note">样本外评估(walk-forward, ${wf.n}样本): AUC ${wf.auc}, 基线胜率 ${wf.base_win}% —— ${buckets}
    <br><small>历史信号由全量模型回填打分(样本内), 上表分层偏乐观; 线上对新信号的打分应参考本行样本外口径。模型训练于 ${meta.trained_at}。</small></div>`;
}

function renderStats(s) {
  const body = document.getElementById('statsBody');
  if (!s || !s.total) {
    body.innerHTML = '<div class="empty">暂无已跟踪信号(扫描运行后按日积累)</div>';
    return;
  }
  const pc = v => v == null ? '--' :
      `<span class="${v > 0 ? 'up' : v < 0 ? 'down' : ''}">${v > 0 ? '+' : ''}${v}%</span>`;
  const wr = v => v == null ? '--' : `${v}%`;
  // 总览卡片: 胜率/平均收益矩阵(3/5/10/20/60周期)
  const o = s.overall;
  const cell = (n) => `
    <div class="sc-cell">
      <div class="sc-n">${n}周期</div>
      <div class="sc-win">${wr(o['win' + n])}</div>
      <div class="sc-avg">${pc(o['avg' + n])}</div>
      <div class="sc-cnt">${o['n' + n]}样本</div>
    </div>`;
  // 分层表: 周期 / 共振分
  const rowsHtml = arr => arr.map(x => `
    <tr><td>${x.name}</td><td>${x.n3 || 0}</td><td>${wr(x.win3)}</td><td>${pc(x.avg3)}</td>
    <td>${wr(x.win5)}</td><td>${pc(x.avg5)}</td><td>${wr(x.win10)}</td><td>${pc(x.avg10)}</td>
    <td>${wr(x.win20)}</td><td>${pc(x.avg20)}</td><td>${wr(x.win60)}</td><td>${pc(x.avg60)}</td></tr>`).join('');
  const months = s.by_month.map(m => `
    <tr><td>${m.key}</td><td>${m.n}</td><td>${wr(m.win5)}</td><td>${pc(m.avg5)}</td><td>${pc(m.avg20)}</td></tr>`).join('');
  body.innerHTML = `
    <div class="sc-note">信号总数 ${s.total} · 确认日期 ${s.from || '--'} ~ ${s.to || '--'} ·
      胜率/收益均自<b>确认日收盘</b>起算(可实际入场点); 共振分越高代表多指标共振越强;
      模型分为元标签质量模型打分(0-100, 只用确认日前数据)</div>
    <div class="sc-cells">${[3, 5, 10, 20, 60].map(cell).join('')}</div>
    <h4>按周期 / 共振分分层</h4>
    <table class="stats-table">
      <thead><tr><th>分层</th><th>样本</th><th>3日胜率</th><th>3日均收</th>
      <th>5日胜率</th><th>5日均收</th><th>10日胜率</th><th>10日均收</th>
      <th>20日胜率</th><th>20日均收</th><th>60日胜率</th><th>60日均收</th></tr></thead>
      <tbody>${rowsHtml(s.by_tf)}${rowsHtml(s.by_score)}</tbody>
    </table>
    ${s.by_ml && s.by_ml.some(x => x.n3 > 0) ? `
    <h4>按模型分分层(元标签信号质量模型)</h4>
    <table class="stats-table">
      <thead><tr><th>模型分</th><th>样本</th><th>3日胜率</th><th>3日均收</th>
      <th>5日胜率</th><th>5日均收</th><th>10日胜率</th><th>10日均收</th>
      <th>20日胜率</th><th>20日均收</th><th>60日胜率</th><th>60日均收</th></tr></thead>
      <tbody>${rowsHtml(s.by_ml)}</tbody>
    </table>
    ${mlWfNote(s.ml_meta)}` : ''}
    <h4>按确认月份(近12个月)</h4>
    <table class="stats-table slim">
      <thead><tr><th>月份</th><th>信号数</th><th>5日胜率</th><th>5日均收</th><th>20日均收</th></tr></thead>
      <tbody>${months || '<tr><td colspan="5" class="empty">暂无数据</td></tr>'}</tbody>
    </table>`;
}

/* chart.js 已完成 KChart/ShareChart 初始化 */
applyTheme(localStorage.getItem('theme') || 'auto');  // 补一次主题下的绘制

/* 启动认证探测: 已启用认证且当前token无效则弹登录框, 否则正常加载 */
(async function probeAuth() {
  try {
    const r = await fetch('/api/auth/status', {
      headers: authToken ? {'Authorization': 'Bearer ' + authToken} : {}
    });
    const st = await r.json();
    if (st.enabled && !st.ok) showLogin(true);
    else { loadIdxList(); loadEtfList(); loadWatch(); loadDivs(); loadIntraday(); loadResonance(); }
  } catch (e) {
    loadIdxList(); loadEtfList(); loadWatch(); loadDivs(); loadIntraday(); loadResonance();
  }
})();
/* SSE连接时行情实时推送, 断开时回退5秒轮询; 低频轮询兜底主力资金流等SSE未覆盖字段 */
setInterval(() => { if (!sseOk) refreshQuotes(); }, 5000);
setInterval(refreshQuotes, 120000);
setInterval(loadIdxList, 60000);
setInterval(loadEtfList, 60000);
setInterval(loadResonance, 300000);   // 共振快照5分钟兜底(SSE断开时也能更新)
setInterval(loadDivs, 3600000);   // 每小时拉一次, 后端每日全量重扫
