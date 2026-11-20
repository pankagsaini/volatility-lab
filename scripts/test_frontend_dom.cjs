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
    response.writeHead(error?404:200,{'Content-Type':target.endsWith('.html')?'text/html':target.endsWith('.js')?'application/javascript':target.endsWith('.css')?'text/css':'application/json'});
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
    assert.equal(await page.locator('#workspaceTitle').textContent(),'Volatility research');
    const captured=await page.evaluate(()=>window.marketCatalog.NIFTY[Number(document.getElementById('activeExpirySelect').value)].brokerAtmIv);
    assert.equal(await page.locator('#kpiAtmIv').textContent(),captured.toFixed(2)+'%','Headline IV matches captured chart source');
    await page.locator('#syncDesk').click();
    await page.waitForFunction(()=>document.getElementById('syncDesk').getAttribute('aria-busy')==='false');
    assert.match(await page.locator('#refreshStatus').textContent(),/Market refresh failed/,'Offline refresh is visibly reported');
    assert.equal(await page.locator('#refreshButtonLabel').textContent(),'Refresh');
    if(process.env.FRONTEND_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.FRONTEND_SCREENSHOT_DIR,'research-desktop.png'),fullPage:true});
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
        for (const tab of ['regime','term','smile','straddle','trades','zerodte']) {
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
    await page.locator('[data-tab="zerodte"]').click();
    assert(await page.locator('#panel-zerodte').isVisible(),'0DTE desk tab renders');
    assert.match(await page.locator('#zerodteContractBadge').textContent(),/NIFTY 2026-11-17/);
    assert.match(await page.locator('#zMeltSource').textContent(),/anchor|No recorded/i);
    assert(await page.evaluate(()=>{
      const chart=Chart.getChart('zerodteStraddleCanvas');
      const rolling=chart.data.datasets.find(ds=>ds.label.startsWith('Rolling'));
      return rolling && rolling.data.every(pt=>pt===null||pt.y===null);
    }),'Rolling straddle stays blank until the collector records it');
    assert.match(await page.locator('#zGexSource').textContent(),/recorded strikes|Gamma unavailable/);
    assert.match(await page.locator('#zVwapHeaderNote').textContent(),/15:00–15:30/);
    assert.match(await page.locator('#zBennettSummaryStrip').textContent(),/Breakeven band|Bennett/);
    await page.locator('[data-zmode="inr"]').click();
    assert.match(await page.locator('[data-zmode="inr"]').getAttribute('class'),/active/);
    const anchored = await page.evaluate(()=>{
      const chart=Chart.getChart('zerodteStraddleCanvas');
      const sqrt=chart.data.datasets.find(ds=>ds.label.startsWith('Theoretical'));
      const anchor=chart.data.datasets.find(ds=>ds.label.startsWith('Open anchor'));
      const i=anchor.data.findIndex(v=>typeof v==='number' && Number.isFinite(v));
      return i<0?null:{bench:sqrt.data[i],anchor:anchor.data[i]};
    });
    if (anchored) assert(Math.abs(anchored.bench-anchored.anchor)<=0.02,
      'The √t benchmark starts at the first recorded premium, never at an implied open');
    await page.locator('[data-zmode="pct"]').click();
    const pctStart = await page.evaluate(()=>{
      const chart=Chart.getChart('zerodteStraddleCanvas');
      const sqrt=chart.data.datasets.find(ds=>ds.label.startsWith('Theoretical'));
      const anchor=chart.data.datasets.find(ds=>ds.label.startsWith('Open anchor'));
      const i=anchor.data.findIndex(v=>typeof v==='number' && Number.isFinite(v));
      return i<0?null:{bench:sqrt.data[i],anchor:anchor.data[i]};
    });
    if (pctStart) assert(Math.abs(pctStart.bench)<=0.02&&Math.abs(pctStart.anchor)<=0.02,
      'Percent mode is rebased to the first recorded premium');
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
    assert.equal(await page.locator('#smileViewSelect, #smileSurface').count(),0);
    assert(await page.locator('#smileMainCanvas').isVisible());
    assert(await page.evaluate(()=>{
      const c=Chart.getChart('smileMainCanvas');
      return c.options.plugins.tooltip.callbacks.label({dataset:c.data.datasets[0],parsed:{y:12.34}})==='IV: 12.34%';
    }));
    assert(await page.evaluate(()=>{
      const c=Chart.getChart('termMainCanvas');
      return c.data.datasets.filter(d=>d.label.includes('EGARCH')).every(d=>
        c.options.plugins.tooltip.callbacks.label({dataset:d,parsed:{y:12.34}})==='EGARCH: 12.34%');
    }));
    assert(!await page.locator('#regimeHorizonTableBody').textContent().then(t=>t.includes('Yang–Zhang')));
    assert.equal(await page.locator('[data-tab="zerodte"]').evaluate(e=>getComputedStyle(e).color),
      await page.locator('[data-tab="trades"]').evaluate(e=>getComputedStyle(e).color));
    const near = await page.locator('#smileMainTableBody tr').count();
    await page.selectOption('#smileRangeSelect','ALL');
    const all = await page.locator('#smileMainTableBody tr').count();
    assert(all>=near && near>0);
    await page.locator('#wsBacktestBtn').click();
    // Default mode is the full recorded baseline; rule-match mode is a strict subset of it.
    assert.notEqual(await page.locator('#resNetPnl').textContent(),'—');
    assert.equal(await page.locator('#btScorecardBody tr').count(),7);
    assert.equal(await page.locator('#btLedgerBody tr').count(),18,'S3 NIFTY replays every recorded session');
    assert.match(await page.locator('#btModeNote').textContent(),/unconditional baseline/);
    assert.equal(await page.locator('#resTradeCount').textContent(),'18 Recorded Trades');
    assert.match(await page.locator('#btLedgerIndex').textContent(),/NIFTY/);
    assert.match(await page.locator('#btArchiveSize').textContent(),/108 recorded structures · 18 replayable sessions/);
    assert.match(await page.locator('#btRestatedCount').textContent(),/19 of 108 recorded NIFTY structures/);
    assert.match(await page.locator('#btRestatedCount').textContent(),/counterfactual baseline/);
    assert.match(await page.locator('#btExcluded').textContent(),/Excluded sessions:/);
    assert.match(await page.locator('#btExcluded').textContent(),/never filled/);
    assert.match(await page.locator('#btLedgerBody').textContent(),/rule failed/,'Baseline rows state the failing rule');
    const nov2Text = (await page.locator('#btLedgerBody tr').allTextContents()).find(t=>t.startsWith('2026-11-02'));
    assert(nov2Text,'The 2026-11-02 structure is listed in the baseline');
    assert.match(nov2Text,/HV10 10\.53%[\s\S]*HV30 14\.00%/,
      'Both restated HV horizons are shown from completed closes');
    assert.match(nov2Text,/HV as of 2026-10-30/,'HV window ends at the prior session close');
    await page.selectOption('#btSymbolSelect','BANKNIFTY');
    assert.equal(await page.locator('#btLedgerBody tr').count(),18,'BANKNIFTY replays the same recorded sessions');
    assert.match(await page.locator('#btLedgerIndex').textContent(),/BANKNIFTY/);
    assert.match(await page.locator('#btRestatedCount').textContent(),/9 of 108 recorded BANKNIFTY structures/);
    assert.notEqual(await page.locator('#resNetPnl').textContent(),'—');
    await page.selectOption('#btSymbolSelect','NIFTY');
    await page.locator('#btModeRules').click();
    assert.equal(await page.locator('#resNetPnl').textContent(),'—','S3 NIFTY has no rule match, so the gated view is empty');
    assert.equal(await page.locator('#resTradeCount').textContent(),'0 Rule Matches');
    assert.match(await page.locator('#btLedgerBody').textContent(),/No structure passed its published rule/);
    assert.match(await page.locator('#btScorecardBody').textContent(),/S3 · Event Crush Iron Fly[\s\S]{0,40}0\/0/,
      'Rule-mode scorecard counts only structures that passed');
    await page.locator('#btModeAll').click();
    assert.notEqual(await page.locator('#resNetPnl').textContent(),'—');
    assert.equal(await page.locator('#btLedgerBody tr').count(),18);
    await page.selectOption('#btPresetSelect','CUSTOM');
    assert(await page.locator('#btCustomPanel').isVisible());
    assert(!(await page.locator('#cx_on_vrp').isChecked()),'Choosing CX starts from a blank screen');
    assert.match(await page.locator('#cxReplayStatus').textContent(),/Run backtest/);
    await page.fill('#cxFrom','2026-11-18');
    await page.fill('#cxTo','2026-11-18');
    await page.locator('#runBacktestBtn').click();
    await page.waitForFunction(()=>/closed trades/.test(document.getElementById('cxReplayStatus').textContent));
    assert.equal(await page.locator('#resTradeCount').textContent(),'6 Replay Trades');
    assert.match(await page.locator('#btLedgerBody').textContent(),/Time exit/);
    await page.locator('#btCustomPanel details').first().locator('summary').click();
    await page.check('#cx_on_vrp');
    await page.fill('#cx_min_vrp','1000');
    assert.match(await page.locator('#cxReplayStatus').textContent(),/Run backtest/);
    await page.locator('#runBacktestBtn').click();
    await page.waitForFunction(()=>/0 closed trades/.test(document.getElementById('cxReplayStatus').textContent));
    assert.match(await page.locator('#cxReplayStatus').textContent(),/6 without an entry/);
    await page.locator('#cxResetBtn').click();
    await page.selectOption('#cxStructure','S2');
    await page.fill('#cxHold','5');
    await page.locator('#runBacktestBtn').click();
    await page.waitForFunction(()=>/1 closed trades/.test(document.getElementById('cxReplayStatus').textContent));
    assert.match(await page.locator('#btLedgerBody').textContent(),/Maximum holding time/);
    assert.equal(await page.locator('#btScorecardBody tr').count(),7);
    assert.match(await page.locator('#resRocSub').textContent(),/Net P&L/);
    assert(await page.evaluate(()=>Chart.getChart('btCompareCanvas').data.datasets[1].data.filter(x=>x>0).length===1),'Unselected structures have no allocated capital');
    await page.locator('#btCustomPanel details').first().locator('summary').click();
    if (process.env.FRONTEND_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.FRONTEND_SCREENSHOT_DIR,'backtest-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth <= 390),'Backtest must fit mobile');
    if (process.env.FRONTEND_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.FRONTEND_SCREENSHOT_DIR,'backtest-mobile.png'),fullPage:true});
    await page.setViewportSize({width:1440,height:1000});
    await page.selectOption('#btPresetSelect','S3');
    assert(await page.locator('#cx_on_vrp').isChecked(),'Preset entry bounds remain available for custom replay');
    assert.equal(await page.locator('#cx_min_vrp').inputValue(),'3');
    assert.equal(await page.locator('#btLedgerBody tr').count(),18,'Preset baseline remains independently available');
    await page.locator('#wsForwardBtn').click();
    assert.match(await page.locator('#fwdLastMarkedBadge').textContent(),/Read-only archive/);
    assert.match(await page.locator('#fwdKpiClosedNetSub').textContent(),/\d+ recorded closes/);
    assert.equal(await page.locator('#fwdKpiClosedNet').textContent(),'—');
    assert.match(await page.locator('#paperArchiveCount').textContent(),/0 verified/);
    assert(!(await page.locator('#paperArchiveDetails').evaluate(n=>n.open)),'Long archive is collapsed, not removed');
    for(const row of await page.locator('#fwdScorecardBody tr').all()) {
      for(const col of [3,4,5,6,7,8,10]) assert.equal((await row.locator('td').nth(col).textContent()).trim(),'—','Missing paper performance must not be displayed as zero');
    }
    assert.equal(await page.locator('#fwdOpenPositionsBody button').count(),0,'Read-only positions must not offer a nonfunctional close action');
    if(process.env.FRONTEND_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.FRONTEND_SCREENSHOT_DIR,'paper-final.png'),fullPage:true});
    await page.locator('#wsOptionsBtn').click();
    await page.locator('[data-tab="straddle"]').click();
    for (const mode of ['intraday','intra_iv','payoff']) await page.locator('[data-scmode="'+mode+'"]').click();
    // Exercise the intrinsic-value branch independently of date-sensitive pricing inputs.
    const originalDte=await page.evaluate(()=>{
      const row=window.marketCatalog.NIFTY[Number(document.getElementById('activeExpirySelect').value)];
      const dte=row.dte; row.dte=1; return dte;
    });
    await page.selectOption('#stressStructureSelect','SHORT_STRADDLE');
    await page.evaluate(()=>window.renderTab4Straddle());
    assert.equal(await page.locator('#stressMatrixTableBody td[style*="background:"]').count(),25);
    const heatColors=await page.locator('#stressMatrixTableBody td').evaluateAll(cells=>cells.map(c=>getComputedStyle(c).backgroundColor));
    assert(new Set(heatColors).size>3,'Scenario magnitudes must have different intensities');
    if (process.env.FRONTEND_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.FRONTEND_SCREENSHOT_DIR,'scenario.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth <= 390),'Mobile page overflow');
    const fits = await page.locator('#stressMatrixTableBody').evaluate(node=>node.parentElement.scrollWidth <= node.parentElement.parentElement.clientWidth+1);
    assert(fits,'Stress columns clipped');
    if (process.env.FRONTEND_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.FRONTEND_SCREENSHOT_DIR,'mobile.png'),fullPage:true});
    await page.evaluate(dte=>{
      window.marketCatalog.NIFTY[Number(document.getElementById('activeExpirySelect').value)].dte=dte;
      window.renderTab4Straddle();
    },originalDte);
    await page.locator('[data-tab="term"]').click();
    assert(await page.evaluate(()=>document.documentElement.scrollWidth <= 390),'Expiry comparison must scroll inside its panel on mobile');
    if (process.env.FRONTEND_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.FRONTEND_SCREENSHOT_DIR,'mobile-term.png'),fullPage:true});
    await page.locator('[data-tab="zerodte"]').click();
    assert(await page.evaluate(()=>document.documentElement.scrollWidth <= 390),'0DTE desk must not overflow on mobile');
    if (process.env.FRONTEND_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.FRONTEND_SCREENSHOT_DIR,'mobile-0dte.png'),fullPage:true});
    await page.locator('[data-tab="term"]').click();
    await page.locator('#panel-term summary').click();
    assert(await page.locator('#termMainTableBody').isVisible());
    await page.locator('[data-tab="smile"]').click();
    await page.locator('#panel-smile summary').click();
    assert(await page.locator('#smileMainTableBody').isVisible());
    assert.deepEqual(errors,[]);
    console.log('DOM passed: all workspaces/tabs, 10 expiries, 60 setups, real Chart.js reuse, captured smile points, archive provenance, mobile stress table, zero missing IDs or JS errors.');
  } finally { await browser.close(); server.close(); }
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
