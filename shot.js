/* 实际部署界面截图: Playwright 驱动真实 webui 服务(localhost:8688) */
const { chromium } = require('playwright');

const BASE = 'http://localhost:8688';
const OUT = '/workspace/docs/screenshots';

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  // K线数据就绪信号: /api/kline 响应(KChart 为 const, 不在 window 上, 无法直接探测)
  const klineDone = page.waitForResponse(
    r => r.url().includes('/api/kline') && r.status() === 200, { timeout: 60000 });
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });

  // 等待数据就绪: ETF列表渲染 + K线数据加载完成
  await page.waitForSelector('#etfList .etf-item', { timeout: 60000 });
  await klineDone;
  await page.waitForTimeout(1500);   // 等SSE/绘图稳定

  /* 1. 主界面(浅色) */
  await page.screenshot({ path: `${OUT}/overview-light.png` });
  console.log('overview-light.png');

  /* 2. K线图 + MACD副图 + 十字光标(悬停) */
  const box = await page.locator('#chartBody').boundingBox();
  await page.mouse.move(box.x + box.width * 0.72, box.y + box.height * 0.3);
  await page.waitForTimeout(400);
  await page.locator('.chart-body').screenshot({ path: `${OUT}/chart-macd.png` });
  console.log('chart-macd.png');
  await page.mouse.move(0, 0);

  /* 3. 底背离标的面板(683条真实信号) */
  await page.waitForSelector('#divBody .div-row', { timeout: 30000 });
  await page.waitForTimeout(500);
  await page.locator('.div-panel').screenshot({ path: `${OUT}/divergence-panel.png` });
  console.log('divergence-panel.png');

  /* 4. 复盘统计弹窗 */
  await page.click('button.stats-btn');
  await page.waitForSelector('#statsBody .sc-cell, #statsBody .empty', { timeout: 30000 });
  await page.waitForTimeout(800);
  await page.locator('.stats-card').screenshot({ path: `${OUT}/stats.png` });
  console.log('stats.png');
  await page.click('.stats-close');
  await page.waitForTimeout(300);

  /* 5. 系统状态面板(健康卡片 + 事件日志) */
  await page.click('#sysBtn');
  await page.waitForTimeout(1200);
  await page.locator('.sys-card').screenshot({ path: `${OUT}/sys-panel.png` });
  console.log('sys-panel.png');
  await page.click('.sys-close');
  await page.waitForTimeout(300);

  /* 6. 暗色主题 */
  await page.evaluate(() => setTheme('dark'));
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}/overview-dark.png` });
  console.log('overview-dark.png');

  /* 7. 移动端布局(390x844, 类手机) */
  const m = await browser.newPage({
    viewport: { width: 390, height: 844 },
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
  });
  const mKlineDone = m.waitForResponse(
    r => r.url().includes('/api/kline') && r.status() === 200, { timeout: 60000 });
  await m.goto(BASE, { waitUntil: 'domcontentloaded' });
  await m.waitForSelector('#etfList .etf-item', { timeout: 60000 });
  await mKlineDone;
  await m.waitForTimeout(1200);
  await m.screenshot({ path: `${OUT}/mobile.png` });
  console.log('mobile.png');

  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
