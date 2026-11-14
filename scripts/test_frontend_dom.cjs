// Browser integration checks exercise the actual shipped page and chart libraries.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname,'..');
const html = fs.readFileSync(path.join(root,'docs/index.html'),'utf8');
assert.equal(html,fs.readFileSync(path.join(root,'index.html'),'utf8'));
assert(!/#(?:d4af37|eab308)/i.test(html));
const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
assert.equal(ids.length,new Set(ids).size,'Duplicate HTML IDs');
for (const [,id] of html.matchAll(/getElementById\(['"]([^'"]+)['"]\)/g)) assert(ids.includes(id),'Missing DOM ID '+id);
const publicState = JSON.parse(html.match(/id="deskPublicState">(.*?)<\/script>/s)[1]);
assert.deepEqual(publicState,JSON.parse(fs.readFileSync(path.join(root,'docs/live_state.json'),'utf8')));
const server = http.createServer((request,response) => {
  const name = decodeURIComponent(new URL(request.url,'http://local').pathname);
  const target = path.resolve(root,'docs','.'+(name === '/' ? '/index.html' : name));
  if (!target.startsWith(path.join(root,'docs')+path.sep)) {response.writeHead(403);response.end();return;}
  fs.readFile(target,(error,data) => {
    response.writeHead(error?404:200,{'Content-Type':target.endsWith('.html')?'text/html':target.endsWith('.js')?'application/javascript':'application/json'});
    response.end(error?'Missing asset':data);
  });
});
(async () => {
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser = await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors = [];
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('https://script.google.com/**',route=>route.abort());
    await page.goto('http://127.0.0.1:'+server.address().port,{waitUntil:'networkidle'});
    await page.waitForFunction(()=>window._vlDeskInitialized && window.Chart);
    assert((await page.locator('#kpiSpot').textContent()).includes('25,116'));
    for (let cycle=0;cycle<2;cycle++) {
      for (const symbol of ['BANKNIFTY','NIFTY']) {
        await page.locator('[data-sym="'+symbol+'"]').click();
        assert.equal(await page.locator('#activeExpirySelect option').count(),symbol==='NIFTY'?7:3);
        for (const tab of ['regime','term','smile','straddle','trades']) {
          await page.locator('[data-tab="'+tab+'"]').click();
        }
      }
    }
    await page.locator('[data-tab="trades"]').click();
    await page.selectOption('#tradeIdIndexFilter','ALL');
    assert.match(await page.locator('#tradeIdSummarySub').textContent(),/60 combinations/);
    assert.equal(await page.locator('#tradeIdTableBody tr').count(),60);
    await page.selectOption('#tradeIdStatusFilter','TRIGGERED');
    assert.match(await page.locator('#tradeIdTableBody').textContent(),/No executable/);
    await page.locator('[data-tab="term"]').click();
    assert.equal(await page.evaluate(()=>Chart.getChart('termMainCanvas').data.datasets.length),3);
    const displayedRisk = await page.locator('#kpiHvGarch').textContent();
    const firstTerm = await page.locator('#termMainTableBody tr').first().locator('td').allTextContents();
    const expectedSpread = Number.parseFloat(firstTerm[7]) - Number.parseFloat(displayedRisk.split('/')[1]);
    assert(Math.abs(Number.parseFloat(firstTerm[9]) - expectedSpread) < .011,'Term spread and header must use the same completed HV30');
    if (process.env.FRONTEND_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.FRONTEND_SCREENSHOT_DIR,'term.png'),fullPage:true});
    await page.locator('[data-tab="smile"]').click();
    await page.selectOption('#smileViewSelect','3D');
    await page.waitForFunction(()=>document.getElementById('smileSurface').data?.[0]?.type==='scatter3d');
    const near = await page.evaluate(()=>document.getElementById('smileSurface').data[0].x.length);
    await page.selectOption('#smileRangeSelect','ALL');
    const all = await page.evaluate(()=>document.getElementById('smileSurface').data[0].x.length);
    assert(all>=near && near>0);
    await page.selectOption('#smileViewSelect','2D');
    await page.locator('#wsBacktestBtn').click();
    assert.equal(await page.locator('#resNetPnl').textContent(),'—');
    await page.locator('#wsForwardBtn').click();
    assert.match(await page.locator('#fwdLastMarkedBadge').textContent(),/Read-only archive/);
    assert.match(await page.locator('#fwdKpiClosedNetSub').textContent(),/13 recorded closes/);
    assert.equal(await page.locator('#fwdKpiClosedNet').textContent(),'—');
    await page.locator('#wsOptionsBtn').click();
    await page.locator('[data-tab="straddle"]').click();
    for (const mode of ['intraday','intra_iv','payoff']) await page.locator('[data-scmode="'+mode+'"]').click();
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth <= 390),'Mobile page overflow');
    const fits = await page.locator('#stressMatrixTableBody').evaluate(node=>node.parentElement.scrollWidth <= node.parentElement.parentElement.clientWidth+1);
    assert(fits,'Stress columns clipped');
    if (process.env.FRONTEND_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.FRONTEND_SCREENSHOT_DIR,'mobile.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    console.log('DOM passed: all workspaces/tabs, 10 expiries, 60 setups, real Chart.js reuse, captured 3D points, archive provenance, mobile stress table, zero missing IDs or JS errors.');
  } finally { await browser.close(); server.close(); }
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
