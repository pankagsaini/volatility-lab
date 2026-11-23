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
    await page.clock.setFixedTime(new Date('2026-11-23T10:00:00+05:30'));
    const paperBook={schema:2,version:'forward-test-v20.1-event-time-gated',source_authority:'python_paper_worker',cohort_kind:'prospective',policy_version:'research-v1',
      provenance_schema:'event-quotes-v1',prospective_start:'2026-11-23',inception_date:'2026-11-23',min_trigger_date:'2026-11-23',updated_at:null,status:'ARMED_WAITING_FOR_FIRST_CAPTURE',
      open_positions:[],closed_trades:[],intraday_mtm_series:[]};
    const feed='https://raw.githubusercontent.com/**/paper-feed/paper-ledger.json*';
    await page.route(feed,r=>r.fulfill({json:paperBook}));
    await page.locator('#wsForwardBtn').click();
    await page.waitForFunction(()=>window.forwardLedger?.source_authority==='python_paper_worker');
    assert.match(await page.locator('#fwdLastMarkedBadge').textContent(),/Awaiting first (prospective|published market) capture/);
    assert.equal(await page.locator('#paperArchiveCount').textContent(),'0 recorded · 0 verified · 0 unverified');
    assert.equal(await page.locator('#fwdKpiClosedNet').textContent(),'—');
    assert.match(await page.locator('#fwdKpiClosedNetSub').textContent(),/Awaiting a verified close/);
    await page.route(feed,r=>r.abort());
    const apiFeed='https://api.github.com/repos/pankagsaini/volatility-lab/contents/paper-ledger.json*';
    const sourceStamp='2026-11-23T04:25:00.000Z';
    const markedTrade={id:'fixture',sym:'NIFTY',strat:'S2',session_date:sourceStamp.slice(0,10),frontScope:true,
      policy_version:'research-v1',triggerTime:sourceStamp,mark_at:sourceStamp,freshness:'FRESH',stratName:'S2',
      provenance_status:'VALIDATED_EVENT_QUOTES',grossMtm:100,netMtm:75,estCharges:25,capital:10000,legs:[]};
    const finished={...markedTrade,id:'closed',date:'2026-11-23',exit_session_date:'2026-11-23',
      gross:250,charges:20,net:230,exit_at:sourceStamp,integrity_status:'VALIDATED_FRESH_EXECUTABLE_MARK',
      legs:[{type:'CE',strike:25000,side:1,entryPx:90,exitPx:95,entryQuoteAt:sourceStamp,exitQuoteAt:sourceStamp}]};
    const publishedBook={...paperBook,status:'ACTIVE_LIVE_SESSION',
      updated_at:sourceStamp,open_positions:[markedTrade,{...markedTrade,id:'earlier',mark_at:new Date(Date.parse(sourceStamp)-180000).toISOString()},
        {...markedTrade,id:'settlement',exit_pending:true,manual_reconciliation_required:true,settlement_pending:true,provenance_status:'GAP_AFFECTED'},
        {...markedTrade,id:'pending',exit_pending:true,provenance_status:'GAP_AFFECTED'}],
      closed_trades:[finished,{...finished,id:'gap',path_status:'INCOMPLETE_QUOTE_PATH',provenance_status:'GAP_AFFECTED',
        exitReason:'Scheduled close (delayed until fresh quote)'}]};
    await page.route(apiFeed,r=>{
      assert.equal(r.request().headers().accept,'application/vnd.github.raw+json');
      return r.fulfill({json:publishedBook});
    });
    await page.locator('#fwdScanNowBtn').click();
    await page.waitForFunction(ts=>window.forwardLedger?.updated_at===ts,sourceStamp);
    assert.match(await page.locator('#fwdLastMarkedBadge').textContent(),/Captures processed through/);
    const marks=await page.locator('#fwdOpenPositionsBody').textContent();
    assert.equal(await page.locator('#paperArchiveCount').textContent(),'2 recorded · 1 verified · 1 unverified');
    assert.match(await page.locator('#fwdKpiClosedNet').textContent(),/230/);
    assert.match(await page.locator('#fwdClosedLedgerBody').textContent(),/25000CE/);
    assert.match(marks,/VERIFIED AT CAPTURE/,'A dated batch can show a mark fresh at its own capture');
    assert.match(marks,/MTM WITHHELD/,'An older mark remains withheld within the same batch');
    assert.match(marks,/PENDING SETTLEMENT RECONCILIATION/);
    assert.match(marks,/EXIT PENDING FRESH QUOTE/);
    assert.match(await page.locator('#fwdClosedLedgerBody').textContent(),/Scheduled close \(delayed until fresh quote\).*IST.*Missing leg quote/);
    const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#fwdExportCsvBtn').click()]);
    const csv=fs.readFileSync(await download.path(),'utf8').split('\n');
    for(const [id,status] of [['settlement','PENDING SETTLEMENT RECONCILIATION'],['pending','EXIT PENDING FRESH QUOTE']]){
      const row=csv.find(line=>line.startsWith('"OPEN_UNVERIFIED","'+id+'"'));
      assert(row && row.includes('"'+status+'","10000","","","",""'),'Withheld marks export their status and original capital only');
    }
    assert(csv.some(line=>line.startsWith('"CLOSED_UNVERIFIED","gap"') && line.includes('delayed until fresh quote')));
    assert(!(await page.locator('#fwdLastMarkedBadge').textContent()).includes('Latest feed unavailable'));
    for (const [now, behind] of [
      ['2026-11-23T11:10:59+05:30', false],
      ['2026-11-23T13:50:00+05:30', true],
      ['2026-11-24T13:50:00+05:30', false],
      ['2026-11-25T08:59:00+05:30', false],
      ['2026-11-28T13:50:00+05:30', false],
    ]) {
      await page.clock.setFixedTime(new Date(now));
      await page.evaluate(()=>window.renderForwardWorkspace());
      const status=await page.locator('#fwdLastMarkedBadge').textContent();
      assert.equal(status.includes('Coverage behind schedule'),behind,now);
      if (behind) {
        assert.match(status,/09:55/);
        assert.match(status,/expected through 10:48 IST/);
      }
    }
    for (const [now, captured, behind] of [
      ['13:50', '14:30', false],
      ['18:00', '14:30', true],
      ['21:30', '15:40', false],
    ]) {
      await page.clock.setFixedTime(new Date('2026-11-08T'+now+':00+05:30'));
      const status=await page.evaluate(clock=>{
        const book=window.forwardLedger;
        const original={...book};
        Object.assign(book,{min_trigger_date:'2026-11-01',last_capture_at:'2026-11-08T'+clock+':00+05:30'});
        window.renderForwardWorkspace();
        const label=document.getElementById('fwdLastMarkedBadge').textContent;
        delete book.last_capture_at;
        Object.assign(book,original);
        return label;
      },captured);
      assert.equal(status.includes('Coverage behind schedule'),behind,now);
      if (behind) assert.match(status,/expected through 15:40 IST/);
    }
    await page.clock.setFixedTime(new Date('2026-11-23T13:50:00+05:30'));
    await page.route(apiFeed,r=>r.abort());
    await page.route('**/paper-ledger.json',r=>r.fulfill({json:paperBook}));
    await page.locator('#fwdScanNowBtn').click();
    await page.waitForFunction(()=>document.getElementById('fwdLastMarkedBadge').textContent.includes('Latest feed unavailable'));
    assert.equal(await page.evaluate(()=>window.forwardLedger.updated_at),sourceStamp,'An older Pages bundle cannot roll back a newer accepted book');
    const caughtUp='2026-11-23T11:13:00+05:30';
    await page.route(feed,r=>r.fulfill({json:{...publishedBook,updated_at:caughtUp,last_capture_at:'2026-11-23T10:48:45+05:30'}}));
    await page.locator('#fwdScanNowBtn').click();
    await page.waitForFunction(ts=>window.forwardLedger.updated_at===ts,caughtUp);
    const caughtUpBadge=await page.locator('#fwdLastMarkedBadge').textContent();
    assert.match(caughtUpBadge,/10:48/);
    assert(!caughtUpBadge.includes('Coverage behind schedule'));
    await page.selectOption('#fwdPolicyFilter','legacy');
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
    await page.clock.setFixedTime(new Date());
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
    for (const viewport of [{width:1440,height:1000},{width:390,height:844}]) {
      const client = await browser.newContext({viewport, isMobile:viewport.width < 500});
      try {
        const tab = await client.newPage();
        await tab.clock.setFixedTime(new Date('2026-11-21T08:30:00Z'));
        const errors = [];
        tab.on('pageerror', error => errors.push(error.message));
        const stamp = '2026-11-20T08:46:46.852Z';
        const leg = {option_greeks:{iv:17.25},market_data:{ltp:100,oi:2500}};
        const records = ['NIFTY','BANKNIFTY'].map((sym,i) => {
          const spot = i ? 55050 : 25350;
          return {label:sym+'|2026-11-23',retrieved_at:stamp,data:[{
            underlying_spot_price:spot,strike_price:spot,call_options:leg,put_options:leg
          }]};
        });
        const snapshot = {provider:'Upstox',schema:1,mode:'snapshot',status:'complete',
          day:'2026-11-20',retrieved_at:stamp,nfo_session_validated:true,records};
        let available = true;
        await tab.route('https://script.google.com/**', route => route.abort());
        await tab.route('**/latest-snapshot.json', route => available
          ? route.fulfill({json:snapshot}) : route.abort());
        await tab.goto('http://127.0.0.1:'+server.address().port);
        async function assertFriday() {
          await tab.waitForFunction(() => /20 Nov 2026.*14:16/.test(document.getElementById('snapshotAsOf').textContent));
          assert.equal(await tab.locator('#kpiAtmIv').textContent(),'17.25%');
          assert.match(await tab.locator('#kpiSpot').textContent(),/25,350/);
        }
        await assertFriday();
        available = false;
        await tab.reload();
        await assertFriday();
        assert.deepEqual(errors,[]);
      } finally { await client.close(); }
    }
    console.log('DOM passed: all workspaces/tabs, 10 expiries, 60 setups, real Chart.js reuse, captured smile points, archive provenance, mobile stress table, zero missing IDs or JS errors.');
  } finally { await browser.close(); server.close(); }
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
