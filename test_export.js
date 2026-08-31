// 验证底背离面板"导出"按钮: 触发浏览器下载并校验CSV内容
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto('http://localhost:8688', { waitUntil: 'domcontentloaded' });
  // 等底背离数据加载(扫描进行中rows可能为空, 但按钮存在即可验证下载机制)
  await page.waitForSelector('#divRescanBtn', { timeout: 30000 });
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 15000 }),
    page.click('button:has-text("导出")'),
  ]);
  const path = '/tmp/export_test.csv';
  await download.saveAs(path);
  const fs = require('fs');
  const head = fs.readFileSync(path).toString().split('\r\n')[0];
  const name = download.suggestedFilename();
  console.log('下载文件名:', name);
  console.log('CSV表头:', head);
  console.log('BOM存在:', fs.readFileSync(path)[0] === 0xEF);
  await browser.close();
})().catch(e => { console.error('FAIL:', e.message); process.exit(1); });
