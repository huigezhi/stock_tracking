// 验证: 分页 + 底边线对齐 + 全宽表格 + 表头排序(小视口强制分页)
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 700 } });
  page.on('pageerror', e => console.log('PAGE_ERR:', e.message));
  await page.goto('http://localhost:8688', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#etfList .etf-item', { timeout: 60000 });
  await page.waitForTimeout(2500);
  const r = {};
  r.etfItems = await page.locator('#etfList .etf-item').count();
  r.etfPager = (await page.locator('#etfPager .pg-info').textContent().catch(() => '') || '').trim();
  r.watchItems = await page.locator('#watch .watch-item').count();
  r.watchPager = (await page.locator('#watchPager .pg-info').textContent().catch(() => '') || '').trim();
  r.align = await page.evaluate(() => {
    const a = document.querySelector('.etf-panel').getBoundingClientRect();
    const b = document.querySelector('.monitor-panel').getBoundingClientRect();
    return { etfBottom: Math.round(a.bottom), monBottom: Math.round(b.bottom), diff: Math.round(b.bottom - a.bottom) };
  });
  r.divWidth = await page.evaluate(() => {
    const d = document.querySelector('.div-panel').getBoundingClientRect();
    const c = document.querySelector('.chart-panel').getBoundingClientRect();
    return { divW: Math.round(d.width), chartW: Math.round(c.width), full: d.width > c.width * 1.5 };
  });
  r.sortables = await page.locator('.div-table th.sortable').count();
  // ETF翻页
  if (r.etfPager) {
    const p1 = await page.locator('#etfList .etf-item .en').first().textContent();
    await page.locator('#etfPager button:has-text("下一页")').click();
    await page.waitForTimeout(200);
    const p2 = await page.locator('#etfList .etf-item .en').first().textContent();
    r.etfPageTurn = { p1, p2, ok: p1 !== p2 };
    await page.locator('#etfPager button:has-text("上一页")').click();
  }
  await page.screenshot({ path: '/tmp/ui_pagetest.png' });
  console.log(JSON.stringify(r, null, 1));
  await browser.close();
})().catch(e => { console.error('FAIL:', e.message); process.exit(1); });
