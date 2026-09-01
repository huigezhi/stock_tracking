/* 图表模块: K线主图(MA/MACD副图/成交量) + 价格/份额变动图
   从 app.js 拆分出的经典脚本, 依赖 app.js 后定义的 apiFetch/fmtPct 等工具函数
   (仅在运行时调用, 加载顺序无要求)。含移动端触摸手势:
   - 单指水平拖动平移K线窗口
   - 双指捏合缩放窗口宽度
   - 长按0.35s弹出十字光标(移动手指跟随) */
"use strict";

/* ================= K线图 ================= */
const KChart = {
  data: [],          // [[date, open, close, high, low, volume], ...]
  ma: {},            // {5: [...], 10: [...], 20: [...], 60: [...]}
  macd: null,        // {dif: [...], dea: [...], hist: [...]}(与data等长, 前部为null)
  win: null,         // {start, end} 索引窗口
  hover: null,       // {x, y} 鼠标位置
  code: '', tf: 'day',
  MA_COLORS: {5: '#f5a623', 10: '#4a90e2', 20: '#b37feb', 60: '#26a69a'},
  DIF_COLOR: '#e6c217', DEA_COLOR: '#e0559c',
  PAD_R: 56, PAD_B: 20,
  MACD_H: 0.18, VOL_H: 0.15,   // MACD副图/成交量占图高比例

  async load(code, tf) {
    this.code = code; this.tf = tf;
    if (tf !== 'min') this.data = [];
    this.win = null; this.draw();
    let stale = false;
    try {
      const r = await apiFetch(`/api/kline?code=${code}&tf=${tf}&n=800`);
      this.data = await r.json();
      stale = r.headers.get('X-Kline-Stale') === '1';
    } catch (e) { /* 保持空 */ }
    if (this.code !== code || this.tf !== tf) return;  // 已切换
    this.calcMa();
    this.calcMacd();
    // 默认显示最近 120 根
    const n = this.data.length;
    this.win = {start: Math.max(0, n - 120), end: n - 1};
    this.syncSlider();
    this.draw();
    ShareChart.draw();  // K线就绪后重绘(份额面板需引用收盘价)
    // 旧数据先出图: 服务端后台刷新完成后静默重拉一次(期间用户已能看图操作)
    if (stale) {
      setTimeout(() => {
        if (this.code === code && this.tf === tf) this.load(code, tf);
      }, 1500);
    }
  },

  /* 实时tick: 交易时段用最新行情更新最后一根日K(不重拉全量) */
  liveTick(q) {
    if (this.tf !== 'day' || !this.data.length || !q || !q.price) return;
    const d = new Date();
    const today = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') +
                  '-' + String(d.getDate()).padStart(2, '0');
    const last = this.data[this.data.length - 1];
    const atEnd = this.win && this.win.end >= this.data.length - 1;
    if (last[0] === today) {
      last[2] = q.price;
      if (q.price > last[3]) last[3] = q.price;
      if (q.price < last[4]) last[4] = q.price;
      // 成交量单位不一致时(如指数)不更新, 只动价格
      if (q.volume && last[5] && q.volume >= last[5] * 0.2 && q.volume <= last[5] * 5)
        last[5] = q.volume;
    } else {
      this.data.push([today, q.price, q.price, q.price, q.price, q.volume || 0]);
      if (atEnd) this.win = {start: Math.min(this.win.start + 1, this.data.length - 30),
                             end: this.data.length - 1};
    }
    this.calcMa();
    this.calcMacd();
    if (atEnd) this.syncSlider();
    this.draw();
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

  /* 标准 MACD(12,26,9): DIF=EMA12-EMA26, DEA=EMA9(DIF), HIST=2*(DIF-DEA) */
  calcMacd() {
    const closes = this.data.map(r => r[2]);
    const n = closes.length;
    const dif = new Array(n).fill(null);
    const dea = new Array(n).fill(null);
    const hist = new Array(n).fill(null);
    if (n < 26) { this.macd = {dif, dea, hist}; return; }
    const ema = (p) => {
      const out = new Array(n).fill(null);
      const k = 2 / (p + 1);
      let prev = null;
      for (let i = 0; i < n; i++) {
        prev = prev == null ? closes[i] : closes[i] * k + prev * (1 - k);
        out[i] = prev;
      }
      return out;
    };
    const ema12 = ema(12), ema26 = ema(26);
    const k9 = 2 / (9 + 1);
    let deaPrev = null;
    for (let i = 0; i < n; i++) {
      dif[i] = ema12[i] - ema26[i];
      deaPrev = deaPrev == null ? dif[i] : dif[i] * k9 + deaPrev * (1 - k9);
      dea[i] = deaPrev;
      hist[i] = 2 * (dif[i] - dea[i]);
    }
    this.macd = {dif, dea, hist};
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
    if (this.tf === 'min') { MinuteChart.draw(); return; }  // 分时模式转发
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
    // 三段布局: 主图(K线) / MACD副图 / 成交量, 底部留PAD_B
    const plotH = H - this.PAD_B - 8 - 6;           // 总绘图高度(顶部信息区6px)
    const volH = plotH * this.VOL_H;
    const macdH = plotH * this.MACD_H;
    const kH = plotH - volH - macdH - 2 * 8;        // 两处8px分隔带
    const kTop = 6;
    const macdTop = kTop + kH + 8;
    const volTop = macdTop + macdH + 8;

    // ---- 价格范围 ----
    let pMin = Infinity, pMax = -Infinity, vMax = 0;
    let mAbsMax = 0;
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
    for (let i = off; i <= this.win.end; i++) {
      const h = this.macd.hist[i];
      if (h != null) mAbsMax = Math.max(mAbsMax, Math.abs(h),
                                        Math.abs(this.macd.dif[i]), Math.abs(this.macd.dea[i]));
    }
    const pPad = (pMax - pMin) * 0.06 || pMax * 0.01;
    pMin -= pPad; pMax += pPad;
    const yP = v => kTop + (1 - (v - pMin) / (pMax - pMin)) * kH;
    const yM = v => macdTop + (1 - (v + mAbsMax) / (2 * mAbsMax || 1)) * macdH;
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
    // MACD副图分隔线 + 零轴
    ctx.strokeStyle = C.grid;
    ctx.beginPath(); ctx.moveTo(0, macdTop); ctx.lineTo(plotW, macdTop); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(0, macdTop + macdH); ctx.lineTo(plotW, macdTop + macdH); ctx.stroke();
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(0, yM(0)); ctx.lineTo(plotW, yM(0)); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = C.axis; ctx.textAlign = 'left';
    ctx.fillText((mAbsMax || 0).toFixed(2), plotW + 4, macdTop + 8);
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
      const vh = (H - this.PAD_B - volTop - 4) * (r[5] / (vMax || 1));
      ctx.globalAlpha = 0.55;
      ctx.fillRect(x - bw / 2, H - this.PAD_B - vh, bw, vh);
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

    // ---- MACD 副图: 柱状图 + DIF/DEA ----
    if (this.macd) {
      const zero = yM(0);
      for (let i = off; i <= this.win.end; i++) {
        const h = this.macd.hist[i];
        if (h == null) continue;
        const x = xC(i - off);
        const y = yM(h);
        ctx.fillStyle = h >= 0 ? C.up : C.down;
        ctx.globalAlpha = 0.75;
        ctx.fillRect(x - bw / 2, Math.min(y, zero), Math.max(bw, 1), Math.max(Math.abs(y - zero), 1));
        ctx.globalAlpha = 1;
      }
      const drawLine = (arr, color) => {
        ctx.strokeStyle = color; ctx.lineWidth = 1.2;
        ctx.beginPath();
        let started = false;
        for (let i = off; i <= this.win.end; i++) {
          const v = arr[i];
          if (v == null) { started = false; continue; }
          const x = xC(i - off), y = yM(v);
          if (!started) { ctx.moveTo(x, y); started = true; }
          else ctx.lineTo(x, y);
        }
        ctx.stroke();
      };
      drawLine(this.macd.dif, this.DIF_COLOR);
      drawLine(this.macd.dea, this.DEA_COLOR);
    }

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
    let macdTxt = '';
    if (this.macd && this.macd.dif[i] != null) {
      const d = this.macd.dif[i], a = this.macd.dea[i], h = this.macd.hist[i];
      macdTxt = `<br>` +
        `<span style="color:${this.DIF_COLOR}">DIF:${d.toFixed(3)}</span>  ` +
        `<span style="color:${this.DEA_COLOR}">DEA:${a.toFixed(3)}</span>  ` +
        `MACD:<b class="${h >= 0 ? 'up' : 'down'}">${h.toFixed(3)}</b>`;
    }
    const cls = pctClass(chgPct);
    document.getElementById('chartTip').innerHTML =
      `<span style="color:var(--muted)">${r[0]}</span>  ` +
      `开<b>${r[1].toFixed(3)}</b> 高<b class="up">${r[3].toFixed(3)}</b> ` +
      `低<b class="down">${r[4].toFixed(3)}</b> 收<b class="${cls}">${r[2].toFixed(3)}</b> ` +
      `<b class="${cls}">${fmtPct(chgPct)}</b> 量<b>${fmtVol(r[5])}</b><br>${maTxt}${macdTxt}`;
  },

  init() {
    const cv = document.getElementById('kchart');
    cv.addEventListener('mousemove', e => {
      const rect = cv.getBoundingClientRect();
      this.hover = {x: e.clientX - rect.left, y: e.clientY - rect.top};
      this.draw();
    });
    cv.addEventListener('mouseleave', () => { this.hover = null; this.draw(); });
    // 滚轮缩放K线窗口(分时模式无缩放)
    cv.addEventListener('wheel', e => {
      e.preventDefault();
      if (this.tf === 'min' || !this.win || !this.data.length) return;
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

    // ---- 触摸手势: 单指平移 / 双指缩放 / 长按十字光标 ----
    let tc = null;   // 当前触摸上下文
    const pinchDist = ts => Math.hypot(
      ts[0].clientX - ts[1].clientX, ts[0].clientY - ts[1].clientY);
    cv.addEventListener('touchstart', e => {
      if (this.tf === 'min' || !this.data.length || !this.win) return;
      if (tc && tc.timer) clearTimeout(tc.timer);
      if (e.touches.length === 1) {
        const rect = cv.getBoundingClientRect();
        const t = e.touches[0];
        this.hover = null;   // 新触摸先清掉旧十字
        tc = {mode: 'pan', x0: t.clientX, y0: t.clientY,
              x: t.clientX - rect.left, y: t.clientY - rect.top,
              win: {start: this.win.start, end: this.win.end}, moved: false};
        // 长按350ms且未明显移动 → 进入十字光标模式
        tc.timer = setTimeout(() => {
          if (tc && tc.mode === 'pan' && !tc.moved) {
            tc.mode = 'cross';
            this.hover = {x: tc.x, y: tc.y};
            this.draw();
            if (navigator.vibrate) navigator.vibrate(15);
          }
        }, 350);
      } else if (e.touches.length >= 2) {
        tc = {mode: 'pinch', d0: pinchDist(e.touches),
              cnt: this.win.end - this.win.start + 1};
      }
      e.preventDefault();
    }, {passive: false});

    cv.addEventListener('touchmove', e => {
      if (!tc || !this.win || !this.data.length) return;
      const n = this.data.length;
      if (tc.mode === 'pan' && e.touches.length === 1) {
        const dx = e.touches[0].clientX - tc.x0;
        if (Math.abs(dx) > 10) tc.moved = true;
        const cnt = tc.win.end - tc.win.start + 1;
        const cw = (cv.clientWidth - this.PAD_R) / cnt;   // 单根K线像素宽
        const shift = Math.round(dx / cw);                // 右滑 → 回看历史
        let s = Math.min(n - cnt, Math.max(0, tc.win.start - shift));
        this.win = {start: s, end: s + cnt - 1};
        this.syncSlider();
        this.draw();
      } else if (tc.mode === 'cross' && e.touches.length === 1) {
        const rect = cv.getBoundingClientRect();
        tc.x = e.touches[0].clientX - rect.left;
        tc.y = e.touches[0].clientY - rect.top;
        this.hover = {x: tc.x, y: tc.y};
        this.draw();
      } else if (tc.mode === 'pinch' && e.touches.length >= 2) {
        const c = Math.min(n, Math.max(30,
          Math.round(tc.cnt * tc.d0 / Math.max(20, pinchDist(e.touches)))));
        const mid = this.win.start + (this.win.end - this.win.start) / 2;
        let s = Math.min(n - c, Math.max(0, Math.round(mid - c / 2)));
        this.win = {start: s, end: s + c - 1};
        this.syncSlider();
        this.draw();
      }
      e.preventDefault();
    }, {passive: false});

    cv.addEventListener('touchend', e => {
      if (tc && tc.timer) clearTimeout(tc.timer);
      if (e.touches.length === 0) tc = null;   // 十字光标保留展示, 下次触摸刷新
    });

    window.addEventListener('resize', () => this.draw());
    this.initSlider();
  }
};

/* ================= 分时图(当日分钟走势, 交易时段实时刷新) ================= */
const MinuteChart = {
  data: [],        // [["0930", 价格, 该分钟成交量(手)], ...]
  code: '', prevClose: null, date: '',
  hover: null,      // 悬停的数据点索引
  LINE_COLOR: '#4a90e2', AVG_COLOR: '#f5a623',
  PAD_R: 56, PAD_B: 20, VOL_H: 0.18,
  SLOTS: 242,      // 9:30-11:30(121) + 13:00-15:00(121)

  /* "0930"/"1305" -> x槽位(0..241), 中午休市段直接跳过 */
  slotOf(t) {
    const h = Math.floor(t / 100), m = t % 100;
    const a = h * 60 + m;
    const noonS = 11 * 60 + 30, noonE = 13 * 60, open = 9 * 60 + 30;
    return a <= noonS ? a - open : 120 + (a - noonE);
  },

  async load(code) {
    this.code = code; this.hover = null;
    try {
      const r = await apiFetch(`/api/kline/minute?code=${code}`);
      const d = await r.json();
      if (this.code !== code) return;          // 已切换标的
      this.data = d.rows || [];
      this.prevClose = d.prev_close;
      this.date = d.date || '';
    } catch (e) { return; }
    if (KChart.tf !== 'min' || this.code !== code) return;
    this.draw();
    this.updateHead();
  },

  /* 用最新分时数据刷新中栏头部价格/涨跌幅 */
  updateHead() {
    const n = this.data.length;
    if (!n) return;
    const price = this.data[n - 1][1];
    const chgEl = document.getElementById('chPrice');
    if (chgEl) chgEl.textContent = price >= 100 ? price.toFixed(2) : price.toFixed(3);
    if (this.prevClose) {
      const pct = (price - this.prevClose) / this.prevClose * 100;
      const c = document.getElementById('chChg');
      if (c) {
        c.textContent = (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%';
        c.className = 'ch-chg ' + pctClass(pct);
      }
    }
  },

  css(name) {
    return getComputedStyle(document.body).getPropertyValue(name).trim();
  },

  draw() {
    if (KChart.tf !== 'min') return;           // 仅分时模式绘制
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
    const C = {
      up: this.css('--up') || '#d03a2f', down: this.css('--down') || '#0a8a4a',
      grid: this.css('--grid'), axis: this.css('--axis'),
    };
    document.getElementById('chartTip').textContent = '';
    const n = this.data.length;
    if (!n) {
      ctx.fillStyle = C.axis; ctx.font = '13px sans-serif'; ctx.textAlign = 'center';
      ctx.fillText('加载分时数据…', W / 2, H / 2);
      return;
    }
    const plotW = W - this.PAD_R;
    const plotH = H - this.PAD_B - 8 - 6;
    const volH = plotH * this.VOL_H;
    const kH = plotH - volH - 8;
    const kTop = 6;
    const volTop = kTop + kH + 8;

    // ---- 均价线(累计额/累计量) ----
    let cumPV = 0, cumV = 0;
    const avg = this.data.map(r => {
      const v = r[2] || 1;
      cumPV += r[1] * v; cumV += v;
      return cumPV / cumV;
    });

    // ---- 价格范围(含昨收) / 量程 ----
    let pMin = Infinity, pMax = -Infinity, vMax = 0;
    this.data.forEach(r => {
      pMin = Math.min(pMin, r[1]); pMax = Math.max(pMax, r[1]);
      vMax = Math.max(vMax, r[2]);
    });
    avg.forEach(v => { pMin = Math.min(pMin, v); pMax = Math.max(pMax, v); });
    if (this.prevClose != null) {
      pMin = Math.min(pMin, this.prevClose); pMax = Math.max(pMax, this.prevClose);
    }
    const pad = (pMax - pMin) * 0.08 || pMax * 0.01 || 0.01;
    pMin -= pad; pMax += pad;
    const yP = v => kTop + (1 - (v - pMin) / (pMax - pMin)) * kH;
    const cw = plotW / this.SLOTS;
    const xI = i => this.slotOf(this.data[i][0]) * cw + cw / 2;

    // ---- 网格 + 价格刻度(右轴) ----
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'left';
    const priceFmt = v => v >= 100 ? v.toFixed(2) : v.toFixed(3);
    for (let g = 0; g <= 4; g++) {
      const v = pMin + (pMax - pMin) * g / 4;
      const y = yP(v);
      ctx.strokeStyle = C.grid; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(plotW, y); ctx.stroke();
      ctx.fillStyle = this.prevClose && v > this.prevClose ? C.up
                    : (this.prevClose && v < this.prevClose ? C.down : C.axis);
      ctx.fillText(priceFmt(v), plotW + 4, y + 3);
    }
    // 昨收基准虚线
    if (this.prevClose != null) {
      ctx.strokeStyle = C.axis; ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.moveTo(0, yP(this.prevClose));
      ctx.lineTo(plotW, yP(this.prevClose)); ctx.stroke();
      ctx.setLineDash([]);
    }
    // 时间刻度: 09:30 / 10:30 / 11:30|13:00 / 14:00 / 15:00
    ctx.textAlign = 'center'; ctx.fillStyle = C.axis;
    [['09:30', 0], ['10:30', 60], ['11:30/13:00', 120], ['14:00', 180], ['15:00', 240]]
      .forEach(([txt, slot]) => {
        const x = slot * cw + cw / 2;
        ctx.strokeStyle = C.grid;
        ctx.beginPath(); ctx.moveTo(x, kTop); ctx.lineTo(x, H - this.PAD_B); ctx.stroke();
        ctx.fillStyle = C.axis;
        ctx.fillText(txt, Math.min(Math.max(x, 22), plotW - 30), H - 6);
      });
    // 成交量分隔线
    ctx.strokeStyle = C.grid;
    ctx.beginPath(); ctx.moveTo(0, volTop); ctx.lineTo(plotW, volTop); ctx.stroke();
    ctx.fillStyle = C.axis; ctx.textAlign = 'left';
    ctx.fillText(fmtVol(vMax), plotW + 4, volTop + 10);

    // ---- 价格线 + 均价线 ----
    const line = (getV, color) => {
      ctx.strokeStyle = color; ctx.lineWidth = 1.3;
      ctx.beginPath();
      for (let i = 0; i < n; i++) {
        const x = xI(i), y = yP(getV(i));
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.stroke();
    };
    line(i => this.data[i][1], this.LINE_COLOR);
    line(i => avg[i], this.AVG_COLOR);

    // ---- 成交量柱(涨红跌绿, 与分钟涨跌方向一致) ----
    const bw = Math.max(1, cw * 0.6);
    const zero = H - this.PAD_B;
    for (let i = 0; i < n; i++) {
      const r = this.data[i];
      const prev = i > 0 ? this.data[i - 1][1] : this.prevClose;
      const vh = (zero - volTop - 4) * (r[2] / (vMax || 1));
      ctx.fillStyle = r[1] >= prev ? C.up : C.down;
      ctx.globalAlpha = 0.55;
      ctx.fillRect(xI(i) - bw / 2, zero - vh, bw, vh);
      ctx.globalAlpha = 1;
    }

    // ---- 十字光标 ----
    if (this.hover != null && this.hover >= 0 && this.hover < n) {
      const x = xI(this.hover);
      ctx.strokeStyle = C.axis; ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.moveTo(x, kTop); ctx.lineTo(x, H - this.PAD_B); ctx.stroke();
      ctx.setLineDash([]);
      const y = yP(this.data[this.hover][1]);
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(plotW, y); ctx.stroke();
      ctx.fillStyle = C.axis;
      ctx.fillRect(plotW + 1, y - 8, this.PAD_R - 2, 16);
      ctx.fillStyle = '#111';
      ctx.textAlign = 'left';
      ctx.fillText(priceFmt(this.data[this.hover][1]), plotW + 4, y + 3);
    }
    this.showTip(this.hover != null ? this.hover : n - 1);
  },

  showTip(i) {
    const r = this.data[i];
    if (!r) return;
    const prev = i > 0 ? this.data[i - 1][1] : this.prevClose;
    const pct = prev ? (r[1] - prev) / prev * 100 : 0;
    let cumV = 0;
    for (let k = 0; k <= i; k++) cumV += this.data[k][2] || 0;
    let cumPV = 0;
    for (let k = 0; k <= i; k++) cumPV += this.data[k][1] * (this.data[k][2] || 1);
    const avgP = cumV ? cumPV / cumV : r[1];
    const t = String(r[0]);
    const time = t.length === 4 ? t.slice(0, 2) + ':' + t.slice(2) : t;
    const cls = pctClass(pct);
    document.getElementById('chartTip').innerHTML =
      `<span style="color:var(--muted)">${this.date || ''} ${time}</span>  ` +
      `价格<b>${r[1].toFixed(3)}</b> 均价<b style="color:${this.AVG_COLOR}">` +
      `${avgP.toFixed(3)}</b> <b class="${cls}">${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%</b>  ` +
      `分钟量<b>${fmtVol(r[2])}</b> 累计量<b>${fmtVol(cumV)}</b>`;
  },

  /* x坐标 -> 数据点索引(数据按槽位升序, 二分) */
  idxAt(x) {
    const body = document.getElementById('chartBody');
    const cw = (body.clientWidth - this.PAD_R) / this.SLOTS;
    const slot = Math.floor(x / cw);
    let lo = 0, hi = this.data.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const s = this.slotOf(this.data[mid][0]);
      if (s === slot) return mid;
      if (s < slot) lo = mid + 1; else hi = mid - 1;
    }
    return -1;
  },

  init() {
    const cv = document.getElementById('kchart');
    cv.addEventListener('mousemove', e => {
      if (KChart.tf !== 'min') return;
      const rect = cv.getBoundingClientRect();
      this.hover = this.idxAt(e.clientX - rect.left);
      this.draw();
    });
    cv.addEventListener('mouseleave', () => {
      if (KChart.tf !== 'min') return;
      this.hover = null;
      this.draw();
    });
    // 移动端: 按住显示十字
    cv.addEventListener('touchstart', e => {
      if (KChart.tf !== 'min') return;
      const rect = cv.getBoundingClientRect();
      this.hover = this.idxAt(e.touches[0].clientX - rect.left);
      this.draw();
    }, {passive: true});
    window.addEventListener('resize', () => {
      if (KChart.tf === 'min') this.draw();
    });
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
      const r = await apiFetch(`/api/etf/share?code=${code}&tf=${tf}`);
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
    const hoverAt = clientX => {
      const rect = cv.getBoundingClientRect();
      const x = clientX - rect.left;
      const body = document.getElementById('shareBody');
      const m = this.data.length - 1;
      if (!m || m < 1) return;
      const PAD_L = 48, PAD_R = 44;
      const plotW = body.clientWidth - PAD_L - PAD_R;
      if (x < PAD_L || x > body.clientWidth - PAD_R) return;
      const cw = plotW / m;
      this.hover = Math.min(m - 1, Math.max(0, Math.floor((x - PAD_L) / cw)));
      this.draw();
    };
    cv.addEventListener('mousemove', e => hoverAt(e.clientX));
    cv.addEventListener('touchstart', e => hoverAt(e.touches[0].clientX),
      {passive: true});
    cv.addEventListener('mouseleave', () => { this.hover = null; this.draw(); });
    window.addEventListener('resize', () => this.draw());
  }
};

KChart.init();
MinuteChart.init();
ShareChart.init();
window.__kchartReady = true;
