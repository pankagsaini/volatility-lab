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
    assert.equal(await page.locator('#shareDesk').count(),0);
    assert.equal(await page.locator('#syncDesk').getAttribute('aria-label'),'Refresh market data');
    assert.equal(await page.locator('#kpiHv10Sub').count(),0);
    assert.equal(await page.locator('#smileKpiStrip').count(),0);
    assert(!/Black-76|Broker/.test(await page.locator('#kpiIvRankSub').textContent()));
    assert(!/IV below|IV above/.test(await page.locator('#kpiGarchGapSub').textContent()));
    assert(await page.evaluate(()=>Chart.getChart('regimeMainCanvas').data.datasets.every(ds=>!ds.fill && !ds.label.includes('Estimated'))));

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
    assert.equal(await page.evaluate(()=>Chart.getChart('termMainCanvas').data.datasets.length),2);
    assert(!(await page.locator('#panel-term').textContent()).includes('EWMA'));
    assert.equal(await page.evaluate(()=>Chart.getChart('termMainCanvas').data.datasets[0].pointValueLabels),'iv');
    assert(await page.evaluate(()=>Chart.getChart('termMainCanvas').data.datasets.every(ds =>
      ds.showLabels && ds.pointValueLabels === 'iv' && !(ds.borderDash || []).length)), 'All term curves need solid lines and percentage labels');
    assert.equal(await page.locator('#expiryComparisonBody tr').count(),7);
    assert(await page.locator('#termForecastSource').isVisible());
    const refreshed = structuredClone(publicState);
    refreshed.forecast_update.generated_at = new Date(Date.now() + 60000).toISOString();
    refreshed.forecasts.NIFTY.expiries[0].egarch = 12.34;
    await page.route('**/live_state.json', route => route.fulfill({json:refreshed}));
    await page.locator('#syncDesk').click();
    await page.waitForFunction(()=>window.VL_PUBLIC_STATE.forecasts.NIFTY.expiries[0].egarch === 12.34);
    assert((await page.locator('#expiryComparisonBody').textContent()).includes('12.34%'));
    assert.equal(await page.locator('#panel-term details').getAttribute('open'),null);
    await page.locator('#expiryComparisonBody button').nth(1).click();
    assert.equal(await page.locator('#activeExpirySelect').inputValue(),'1');
    assert(await page.locator('#panel-smile').isVisible());
    assert.equal(await page.locator('#panel-smile details').getAttribute('open'),null);
    await page.locator('[data-tab="term"]').click();
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
    // Default mode is the explicitly labelled research archive; the strict verified gate stays intact.
    assert.notEqual(await page.locator('#resNetPnl').textContent(),'—');
    assert.equal(await page.locator('#btScorecardBody tr').count(),7);
    assert.equal(await page.locator('#btLedgerBody tr').count(),6,'S3 NIFTY has six recorded sessions');
    assert.match(await page.locator('#btModeNote').textContent(),/not execution-verified|Research mode/);
    assert.equal(await page.locator('#resTradeCount').textContent(),'6 Recorded Trades');
    assert.match(await page.locator('#btLedgerIndex').textContent(),/NIFTY/);
    await page.selectOption('#btSymbolSelect','BANKNIFTY');
    assert.equal(await page.locator('#btLedgerBody tr').count(),6,'BANKNIFTY is screened on the same six sessions');
    assert.match(await page.locator('#btLedgerIndex').textContent(),/BANKNIFTY/);
    assert.notEqual(await page.locator('#resNetPnl').textContent(),'—');
    await page.selectOption('#btSymbolSelect','NIFTY');
    await page.locator('#btModeVerified').click();
    assert.equal(await page.locator('#resNetPnl').textContent(),'—');
    assert.match(await page.locator('#btScorecardBody').textContent(),/Performance unavailable until the trade archive is verified/);
    assert.match(await page.locator('#btLedgerBody').textContent(),/No verified point-in-time trades/);
    await page.locator('#btModeResearch').click();
    assert.notEqual(await page.locator('#resNetPnl').textContent(),'—');
    await page.selectOption('#btPresetSelect','CUSTOM');
    assert(await page.locator('#btCustomPanel').isVisible());
    assert((await page.locator('#btLedgerBody tr').count())>0,'Default custom criteria must match recorded trades');
    assert.match(await page.locator('#cxHint').textContent(),/archive matches/);
    await page.fill('#cx_min_vrp','1000');
    await page.waitForFunction(()=>document.querySelectorAll('#btLedgerBody tr').length===1);
    assert.match(await page.locator('#btLedgerBody').textContent(),/No recorded trade matches/);
    await page.locator('#cxResetBtn').click();
    assert(!(await page.locator('#cx_on_vrp').isChecked()));
    await page.selectOption('#btPresetSelect','S3');
    assert(!(await page.locator('#btCustomPanel').isVisible()));
    await page.locator('#wsForwardBtn').click();
    assert.match(await page.locator('#fwdLastMarkedBadge').textContent(),/Read-only archive/);
    assert.match(await page.locator('#fwdKpiClosedNetSub').textContent(),/\d+ recorded closes/);
    assert.equal(await page.locator('#fwdKpiClosedNet').textContent(),'—');
    await page.locator('#wsOptionsBtn').click();
    await page.locator('[data-tab="straddle"]').click();
    for (const mode of ['intraday','intra_iv','payoff']) await page.locator('[data-scmode="'+mode+'"]').click();
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth <= 390),'Mobile page overflow');
    const fits = await page.locator('#stressMatrixTableBody').evaluate(node=>node.parentElement.scrollWidth <= node.parentElement.parentElement.clientWidth+1);
    assert(fits,'Stress columns clipped');
    if (process.env.FRONTEND_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.FRONTEND_SCREENSHOT_DIR,'mobile.png'),fullPage:true});
    await page.locator('[data-tab="term"]').click();
    assert(await page.evaluate(()=>document.documentElement.scrollWidth <= 390),'Expiry comparison must scroll inside its panel on mobile');
    if (process.env.FRONTEND_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.FRONTEND_SCREENSHOT_DIR,'mobile-term.png'),fullPage:true});
    await page.locator('#panel-term summary').click();
    assert(await page.locator('#termMainTableBody').isVisible());
    await page.locator('[data-tab="smile"]').click();
    await page.locator('#panel-smile summary').click();
    assert(await page.locator('#smileMainTableBody').isVisible());
    assert.deepEqual(errors,[]);
    console.log('DOM passed: all workspaces/tabs, 10 expiries, 60 setups, real Chart.js reuse, captured 3D points, archive provenance, mobile stress table, zero missing IDs or JS errors.');
  } finally { await browser.close(); server.close(); }
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
