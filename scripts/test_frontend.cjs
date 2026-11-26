// Execute the shipped browser functions; no copied chart/calculation implementation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, {value:'', textContent:'', innerHTML:''});
  return elements.get(id);
};
const web = vm.createContext({console, window:{innerWidth:390}, document:{getElementById:element},
  activeSymbol:'NIFTY', activeExpiryIdx:0, activeScMode:'payoff', activeRange:'1M', activeMetric:'levels',
  B76_RISK_FREE_RATE:.066, B76_INV_SQRT_2PI:1/Math.sqrt(2*Math.PI),
  straddleChart:null, regimeChart:null, termChart:null});
const optionsAsset = fs.readFileSync(path.join(__dirname, 'options-charts.js'), 'utf8');
assert.equal(optionsAsset, fs.readFileSync(path.join(__dirname, '../docs/scripts/options-charts.js'), 'utf8'));
vm.runInContext(optionsAsset,web);
vm.runInContext(fs.readFileSync(path.join(__dirname,'trading-sessions.js'),'utf8'),web);
for (const name of ['chartNumber','observedIv','observedSpread','observedMean','distribution','formatLiveNumber',
  'forwardInterval','scenarioLegs','black76Price','normCdf','normPdf','currentRow','chartOpts',
  'renderTab1Regime','renderTab2Term','renderTab4Straddle','rowMatchesCriteria',
  'liveNumber','liveTimestamp','istDayAndMinute','nextTradingDay','expiryClock','isExpiryRollWindow',
  'getDefaultActiveExpiryIdx','positionalGateOpen','interpolateOiDataDecayPct','computeStrikeGexProfile','renderTab6ZeroDteDesk','fmtIstClockMin']) {
  const start = html.indexOf('      function '+name+'(');
  assert(start>=0, name);
  vm.runInContext(html.slice(start, html.indexOf('\n      }',start)+8),web);
}
web.updateOrCreateChart = (prior, ctx, type, data, options) => ({data, options, update(){}});
web.fmtInr = value => String(value); // Keep exact numbers readable in test table cells.
const oiStart = html.indexOf('      var OIDATA_MEDIAN_DECAY_POINTS');
assert(oiStart >= 0, 'OIDATA_MEDIAN_DECAY_POINTS');
vm.runInContext(html.slice(oiStart, html.indexOf('];', oiStart) + 2), web);

for(const missing of [null,undefined,'', '  ',false,NaN,Infinity]) assert.equal(web.chartNumber(missing),null);
assert.equal(web.chartNumber(0),0);
assert.equal(web.observedSpread(15,null),null);
assert.equal(web.observedMean([null,NaN]),null);
const observation={iv:20,ivSource:'Upstox option_greeks.iv',snapshotId:'2026-11-12T09:30:00Z',expiry:'2026-11-17',atm:25000,markSource:'paired_book_mid',markAt:'2026-11-12T09:30:00Z',cQuoteAt:'2026-11-12T09:30:00Z',pQuoteAt:'2026-11-12T09:30:00Z'};
assert.equal(web.observedIv(observation),20);
for(const field of ['ivSource','snapshotId','expiry','atm']) assert.equal(web.observedIv({...observation,[field]:null}),null);
assert.equal(web.observedIv({...observation,ivSource:'realized-volatility proxy'}),null);
assert.equal(web.observedIv({...observation,snapshotId:'not-a-date'}),null);
let stats=web.distribution([null,10,20,30,40,NaN],5,'%');
assert.equal(stats.count,4); assert.equal(stats.med,'25.00%'); assert.equal(stats.mean,'25.00%'); assert.equal(stats.p,'0%');
assert.equal(web.distribution([10,10],10,'%').z,'—');
assert.equal(web.distribution([],10,'%').p,'—');

assert.equal(web.forwardInterval(null,{iv:20,dte:7}).vol,null);
assert.equal(web.forwardInterval({iv:20,dte:7},{iv:20,dte:14}).vol,20);
assert.equal(web.forwardInterval({iv:20,dte:7},{iv:10,dte:14}).slope,'Inverted');
assert.equal(web.forwardInterval({iv:20,dte:1},{iv:10,dte:4}).vol,0);
for(const bad of [{iv:null,dte:14},{iv:0,dte:14},{iv:20,dte:7}])
  assert.equal(web.forwardInterval({iv:20,dte:7},bad).vol,null);

const chain=Array.from({length:21},(_,i)=>({k:24500+50*i,cLtp:100,pLtp:100,uCallIv:20,uPutIv:20}));
web.marketCatalog={NIFTY:[{atm:25000,spot:25000,syn:25000,dte:1,expiry:'2026-11-17',chain,iv:20,brokerAtmIv:20,
  hv10:null,hv20:15,hv30:15,intraday:[{...observation,t:'09:15',strad:200,snapshotId:'2026-11-12T03:45:00Z'},{...observation,t:'11:30',iv:null,strad:null,snapshotId:'2026-11-12T06:00:00Z'},{...observation,t:'13:30',iv:19,strad:160,snapshotId:'2026-11-12T08:00:00Z'}]}]};
element('stressLotsInput').value='1';
element('stressStructureSelect').value='SHORT_STRADDLE';
web.renderTab4Straddle();
let curve=web.straddleChart.data.datasets;
assert.equal(curve.length,1,'Only the real expiry payoff should be plotted');
assert.equal(curve[0].data.find(pt=>pt.x===25000).y,13000);
assert.equal(curve[0].data.find(pt=>pt.x===24250).y,-35750);
assert.equal(web.straddleChart.options.scales.x.type,'linear');
// One calendar day before expiry, unchanged spot and any IV shift must settle to intrinsic.
const centerRow=element('stressMatrixTableBody').innerHTML.match(/<tr><th[^>]*>0%<\/th>(.*?)<\/tr>/)[1];
assert.equal((centerRow.match(/>13000</g)||[]).length,5);
element('stressLotsInput').value='2';web.renderTab4Straddle();
assert.equal(web.straddleChart.data.datasets[0].data.find(pt=>pt.x===25000).y,26000);
element('stressStructureSelect').value='PUT_RATIO';web.renderTab4Straddle();
assert.deepEqual(Array.from(web.scenarioLegs(web.marketCatalog.NIFTY[0],'NIFTY','PUT_RATIO'),l=>l.qty),[1,-2]);
assert(web.straddleChart.data.datasets[0].data.some(pt=>pt.x===24850),'Keep the ratio strike kink');
// A missing entry must not produce a zero-valued payoff or stress matrix.
chain.find(n=>n.k===24850).pLtp=null;
web.renderTab4Straddle();assert.equal(web.straddleChart.data.datasets[0].data.length,0);
assert(element('stressMatrixTableBody').innerHTML.includes('—'));
web.activeScMode='intra_iv';web.renderTab4Straddle();
assert.equal(web.straddleChart.data.datasets[0].data[1].y,null);
assert.equal(web.straddleChart.data.datasets[0].data[1].x-web.straddleChart.data.datasets[0].data[0].x,135);
assert.equal(web.straddleChart.data.datasets[0].showLine,false);
web.activeScMode='intraday';web.renderTab4Straddle();
assert.equal(web.straddleChart.data.datasets.length,1,'No invented VWAP');
assert.equal(web.straddleChart.data.datasets[0].data[1].y,null);

web.REAL_HISTORICAL_1Y={NIFTY:[{...observation,d:'2026-11-09',l:'09 Nov',h10:12,h30:14},
  {d:'2026-11-11',l:'11 Nov',iv:null,h10:null,h30:null},
  {...observation,d:'2026-11-12',l:'12 Nov',iv:22,h10:13,h30:15}]};
web.renderTab1Regime();
assert.deepEqual(Array.from(web.regimeChart.data.datasets[0].data),[20,null,22]);
assert.deepEqual(Array.from(web.regimeChart.data.datasets[1].data),[12,null,13]);
assert.equal(web.regimeChart.data.datasets[0].spanGaps,true);
assert(element('regimeRankTableBody').innerHTML.includes('22.00%'),'Latest recorded IV comes from history, not an older selected snapshot');
assert(element('regimeRankTableBody').innerHTML.includes('2026-11-12'),'Latest values disclose their source date');
const historyRows = web.REAL_HISTORICAL_1Y.NIFTY;
historyRows[1].closingEstimate = {asOf:'2026-11-11T15:30:00+05:30',iv:21,model:'BSM',expiry:'2026-11-17',atm:25000};
web.renderTab1Regime();
assert.deepEqual(Array.from(web.regimeChart.data.datasets[0].data),[20,null,22]);
assert.equal(web.regimeChart.data.datasets.length,3,'Closing estimates are not displayed');
assert(web.regimeChart.data.datasets.every(ds=>!ds.fill),'History uses unfilled lines');
web.activeMetric='spread';web.renderTab1Regime();
assert.deepEqual(Array.from(web.regimeChart.data.datasets[0].data),[6,null,7]);
web.marketCatalog.NIFTY.push({...web.marketCatalog.NIFTY[0],expiry:'2026-12-29',dte:43,iv:25,brokerAtmIv:null});
web.renderTab2Term();
assert.equal(web.termChart.options.scales.x.type,'linear');
assert.equal(web.termChart.data.datasets.length,2);
assert.equal(web.termChart.data.datasets[0].pointValueLabels,'iv');
assert.equal(web.termChart.options.scales.x.ticks.callback(43),'2026-12-29');
assert.equal(web.termChart.data.datasets[0].data[1].y,null,'model IV cannot replace missing broker ATM IV');
assert.equal(web.termChart.data.datasets[0].data[1].x,43);
assert.equal(web.termChart.data.datasets[1].data[0].y,null,'Missing forecast cannot be replaced by broker IV');
web.window.VL_PUBLIC_STATE = {forecasts:{NIFTY:{expiries:[{expiry:'2026-11-17',as_of:'2026-11-11T15:40:00+05:30',training_through:'2026-11-11',egarch:17,ewma:16}]}}};
web.renderTab2Term();
assert.equal(web.termChart.data.datasets[1].data[0].y,17);
assert(element('termMainTableBody').innerHTML.includes('0.000110'),'Total variance must contain numeric annual variance times DTE');

// Phase 1/2: minute-accurate 0DTE clock, expiry roll window and positional gate.
const minutesClock = web.expiryClock('2026-11-17','2026-11-17T08:35:00Z'); // 14:05 IST
assert.equal(minutesClock.is0Dte,true);
assert.equal(minutesClock.rawCalendarDays,0);
assert.equal(minutesClock.minsLeft,95);
assert.equal(minutesClock.tCal,95/525600);
assert.equal(minutesClock.tVol,95/(245*375));
assert.equal(minutesClock.closeMins,940,'Close must come from the exchange calendar');
assert.equal(web.expiryClock('2026-11-17','2026-11-16T09:30:00Z').is0Dte,false);
assert.equal(web.expiryClock('2026-11-17','2026-11-16T09:30:00Z').rawCalendarDays,1);
assert.equal(web.expiryClock('2026-11-17','2026-11-16T09:30:00Z').tCal,(1480/1440)/365);
assert.equal(web.isExpiryRollWindow('2026-11-17','2026-11-16T09:59:59Z'),false,'15:29:59 IST on T−1 stays');
assert.equal(web.isExpiryRollWindow('2026-11-17','2026-11-16T10:00:00Z'),true,'15:30 IST on T−1 rolls');
assert.equal(web.expiryClock('2026-11-17','2026-11-17T15:40:00+05:30').tCal,0);
assert.equal(web.expiryClock('2026-12-29','2026-12-29T15:30:01+05:30').tVol,0);
assert.equal(web.expiryClock('2026-12-29','2026-12-29T15:30:01+05:30').closeMins,930);
assert.equal(web.isExpiryRollWindow('2026-11-17','2026-11-16T09:30:00Z'),false,'15:00 IST on T-1 stays');
assert.equal(web.isExpiryRollWindow('2026-11-17','2026-11-17T03:45:00Z'),true,'Expiry morning rolls');
web.marketCatalog={NIFTY:[{expiry:'2026-11-17'},{expiry:'2026-11-24'}]};
assert.equal(web.getDefaultActiveExpiryIdx('NIFTY','2026-11-17T03:45:00Z'),1);
assert.equal(web.getDefaultActiveExpiryIdx('NIFTY','2026-11-13T09:30:00Z'),0);
assert.equal(web.positionalGateOpen({dte:1.99}),false);
assert.equal(web.positionalGateOpen({dte:2}),true);
assert.equal(web.positionalGateOpen({dte:0.05}),false);

// Tab 2: the expiring front is held out of the σ_fwd chain and drawn as a kink.
web.window.VL_PUBLIC_STATE={forecasts:{}};
web.marketCatalog={NIFTY:[
  {expiry:'2026-11-17',kind:'Weekly',sessionDate:'2026-11-17',dte:.05,spot:25000,atm:25000,syn:25000,
    brokerAtmIv:24,chain:[],iv:24},
  {expiry:'2026-11-24',kind:'Weekly',sessionDate:'2026-11-17',dte:6,spot:25000,atm:25000,syn:25000,
    brokerAtmIv:14,chain:[],iv:14}]};
web.renderTab2Term();
assert.equal(web.termChart.data.datasets[0].data[0].y,null,'0DTE is not a term point');
assert.equal(web.termChart.data.datasets[0].data[1].y,14);
assert.equal(web.termChart.data.datasets.length,3);
const kinkDataset=web.termChart.data.datasets.find(ds=>ds.label.includes('Expiring front kink'));
assert(kinkDataset,'Expiring front must be drawn as a labelled kink');
assert.equal(kinkDataset.data[0].y,24);
assert.equal(kinkDataset.data[1].y,14,'Kink overlay joins the first baseline point');
assert(element('termMainTableBody').innerHTML.includes('0 · kink'));

// Phase 4: strike GEX uses recorded OI and model gamma only.
const gammaItem={spot:25000,chain:[
  {k:24900,cOi:10,pOi:30,modelCallGamma:.0004,modelPutGamma:.0004},
  {k:25000,cOi:50,pOi:20,modelCallGamma:.0005,modelPutGamma:.0005},
  {k:25100,cOi:25,pOi:5,modelCallGamma:.0003,modelPutGamma:.0003}]};
web.backtestMeta={lotSizes:{NIFTY:65,BANKNIFTY:30}};
const gex=web.computeStrikeGexProfile(gammaItem,'NIFTY');
assert.equal(gex.rows.length,3);
assert.equal(gex.rows.find(row=>row.k===24900).gexCr,-5,'Broker OI quantity must not be multiplied by lot size twice');
assert.equal(gex.lot,65);
assert.equal(gex.pin.k,25000);
assert.equal(gex.flip,24953);
assert.equal(web.computeStrikeGexProfile({spot:25000,chain:[{k:25000}]},'NIFTY').rows.length,0);
assert.equal(web.interpolateOiDataDecayPct(555),0);
assert.equal(web.interpolateOiDataDecayPct(600),-15.5);
assert.equal(web.interpolateOiDataDecayPct(780),-35);
assert.equal(web.interpolateOiDataDecayPct(930),null,'No invented close for published median');
assert.equal(web.interpolateOiDataDecayPct(NaN),null);
const differentGamma = web.computeStrikeGexProfile({spot:25000,chain:[
  {k:25000,cOi:10,pOi:10,modelCallGamma:.0004,modelPutGamma:.0002}]},'NIFTY');
assert.equal(differentGamma.totalCr,1.25,'Each leg uses its own gamma');
for (const missing of [null,undefined,'',-1]) {
  assert.equal(web.computeStrikeGexProfile({spot:25000,chain:[{...gammaItem.chain[0],cOi:missing}]},'NIFTY').rows.length,0);
}
assert.equal(web.computeStrikeGexProfile({...gammaItem,spot:0},'NIFTY').rows.length,0);

// Run the real expiry desk on different anchor/rolling baselines and late capture.
web.enrichItemWithBlack76 = () => {};
web.lastSyncedRetrievedAt = '2026-11-17T15:40:00+05:30';
web.zerodteStraddleChart = null; web.zerodteGexChart = null;
web.activeZeroDteMode = 'pct';
const zeroItem = {expiry:'2026-11-17',pricingAsOf:web.lastSyncedRetrievedAt,spot:25024,
  atm:25000,straddleStrike:24900,chain:[],dualIntraday:[
    {markAt:'2026-11-16T12:00:00+05:30',rollStrad:999,anchorStrad:999,anchorK:24900,rollAtm:25000},
    {markAt:'2026-11-17T12:00:00+05:30',rollStrad:100,anchorStrad:200,anchorK:24900,rollAtm:25000},
    {markAt:'2026-11-17T14:00:00+05:30',rollStrad:80,anchorStrad:220,anchorK:24900,rollAtm:25000},
    {markAt:'2026-11-17T15:40:00+05:30',rollStrad:24,anchorStrad:124,anchorK:24900,rollAtm:25000}]};
web.marketCatalog = {NIFTY:[zeroItem], BANKNIFTY:[zeroItem]};
web.renderTab6ZeroDteDesk();
let zero = web.zerodteStraddleChart.data;
assert.deepEqual(Array.from(zero.labels),['12:00','14:00','15:40'],'Old-session points excluded');
assert.deepEqual(Array.from(zero.datasets[0].data),[0,-20,-76]);
assert.deepEqual(Array.from(zero.datasets[1].data),[0,10,-38],'Anchor changes from its own first mark');
assert.deepEqual(Array.from(zero.datasets[3].data),[0,-24.32,null],'Late-start benchmark uses a ratio of remaining premium');
assert.equal(zero.datasets[2].data[2],-100,'Ideal ATM time value tends to zero at expiry');
assert.equal(element('zKpiBennettBand').textContent,'24,976 – 25,024','Breakeven uses the traded strike');
web.activeZeroDteMode = 'inr'; web.renderTab6ZeroDteDesk();
assert.equal(web.zerodteStraddleChart.data.datasets[3].data[1],75.68);
web.activeSymbol='BANKNIFTY'; web.renderTab6ZeroDteDesk();
assert(web.zerodteStraddleChart.data.datasets[3].data.every(p=>p===null),'NIFTY study cannot become BANKNIFTY evidence');
web.activeSymbol='NIFTY'; zeroItem.expiry='2026-11-23'; web.renderTab6ZeroDteDesk();
assert.equal(web.zerodteStraddleChart.data.labels.length,0,'0DTE desk must not plot next-week decay');
zeroItem.expiry='2026-11-17';
zeroItem.dualIntraday = ['14:59','15:00','15:30','15:31'].map((t,i)=>({markAt:'2026-11-17T'+t+':00+05:30',spot:[100,200,400,800][i],anchorK:25000,anchorStrad:100,rollStrad:100,rollAtm:25000}));
web.renderTab6ZeroDteDesk();
assert.equal(element('zKpiVwap').textContent,'300.00','Only captured prints inside the desk window contribute');
web.activeZeroDteMode='pct';


// The shipped history may contain IV only with provenance; pre-capture years stay empty.
const charts = vm.runInContext('OptionsCharts',web);
const comparisonState = {forecast_update:{generated_at:'2026-11-11T15:42:00+05:30'},
  forecastObservedAt:Date.parse('2026-11-11T15:44:00+05:30'), forecasts:{NIFTY:{expiries:[{expiry:'2026-11-17',as_of:'2026-11-11T15:40:00+05:30',training_through:'2026-11-11',egarch:17,ewma:null}]}}};
const comparisonRow = {expiry:'2026-11-17',ivCapturedAt:'2026-11-11T15:45:00+05:30',brokerAtmIv:20};
assert.equal(charts.comparison(comparisonState,'NIFTY',comparisonRow).egarchGap,3);
assert.equal(charts.comparison(comparisonState,'NIFTY',{...comparisonRow,ivCapturedAt:'2026-11-12T15:45:00+05:30'}).egarchGap,3);
for (const stamp of ['2026-11-11T15:30:00+05:30','invalid']) {
  const result = charts.comparison(comparisonState,'NIFTY',{...comparisonRow,ivCapturedAt:stamp});
  assert.equal(result.egarch,17,'Dated reference remains visible');
  assert.equal(result.egarchGap,null,'Future or undated forecasts cannot yield a current comparison gap');
}
assert.equal(charts.comparison(comparisonState,'NIFTY',{...comparisonRow,brokerAtmIv:null,iv:22}).egarchGap,null,'Model IV cannot fill comparison');
assert.equal(charts.comparison({},'NIFTY',comparisonRow).egarch,null);
const afterClose = {...comparisonState, forecast_update:{generated_at:'2026-11-11T17:07:00+05:30'},
  forecastObservedAt:Date.parse('2026-11-11T17:08:00+05:30')};
const atClose = {...comparisonRow, ivCapturedAt:'2026-11-11T15:40:44+05:30'};
assert.equal(charts.comparison(afterClose,'NIFTY',atClose).context,'Generated after capture');
for (const state of [afterClose, {...comparisonState, forecast_update:{}},
    {...comparisonState, forecastObservedAt:undefined},
    {...comparisonState, forecastObservedAt:Date.parse('2026-11-11T15:46:00+05:30')}]) {
  assert.equal(charts.comparison(state,'NIFTY',comparisonRow).egarchGap,null);
}
assert.equal(charts.comparison(afterClose,'NIFTY',{...comparisonRow,ivCapturedAt:'2026-11-11T17:08:00+05:30'}).egarchGap,3);
const smile = charts.smile({spot:100,atm:100,chain:[
  {k:90,uPutIv:22,uPutDelta:-.11}, {k:95,uPutIv:19,uPutDelta:-.20},
  {k:100,uCallIv:16,uPutIv:18}, {k:105,uCallIv:15,uCallDelta:.25},
  {k:110,modelCallIv:12,uCallDelta:.10}
]});
assert.deepEqual(Array.from(smile,pt=>pt?.iv ?? null),[22,null,17,15,null]);
assert.equal(smile[0].delta,-.11);
assert.equal(charts.smile({chain:[{k:100,uCallIv:13}],atm:100,spot:100})[2],null);
const base = {...observation,strad:200};
const stable = charts.straddleSeries({atm:25000,expiry:base.expiry,sessionDate:'2026-11-12',
  intraday:[base,{...base,atm:25100},{...base,expiry:'2026-11-24'},{...base,snapshotId:'2026-11-11T10:00:00Z'}]});
assert.equal(stable.length,1); assert.equal(stable[0].strad,200);
assert.equal(charts.straddleSeries({atm:25000,expiry:base.expiry,intraday:[{...base,markSource:null} ]})[0].strad,null);
assert.equal(charts.straddleSeries({atm:25000,expiry:base.expiry,intraday:[{...base,snapshotId:'invalid'}]}).length,0,'Malformed timestamp cannot crash chart');
assert.equal(charts.straddleSeries({atm:25000,expiry:base.expiry,intraday:[]}).length,0);
assert.equal(charts.auditedTrade({date:'2026-11-02',gross:1,charges:1,capital:100}),false,'Legacy display rows lack execution evidence');
const verified = {provenance_status:'VALIDATED_EVENT_TIME_TWO_SIDED_BOOK',decision_at:'2026-11-11T09:20:00+05:30',available_at:'2026-11-09T16:00:00+05:30',entry_source:'capture-a',exit_source:'capture-b',unitLegs:260,gross:20,charges:10,capital:1000};
assert.equal(charts.auditedTrade(verified),true);
assert.equal(charts.auditedTrade({...verified,available_at:'2026-11-11T15:40:00+05:30'}),false,'Same-day EOD cannot become a morning entry signal');
const sessions = vm.runInContext('TradingSessions',web);
assert.equal(fs.readFileSync(path.join(__dirname,'trading-sessions.js'),'utf8'),fs.readFileSync(path.join(__dirname,'../docs/scripts/trading-sessions.js'),'utf8'));
assert.deepEqual(Array.from(sessions.history([{d:'2026-11-06'},{d:'2026-11-12'}]),r=>r.d),
  ['2026-11-06','2026-11-08','2026-11-09','2026-11-11','2026-11-12']);
assert.equal(sessions.session('2026-11-13').endMins,940);
assert.equal(sessions.expiryMillis('2026-12-29'),Date.parse('2026-12-29T15:30:00+05:30'));

// Custom criteria screening reads only metrics the archive actually records, applies
// inclusive bounds, and fails a criterion when the row has no value for it.
const cxStart = html.indexOf('      function rowMetric(');
const cxEnd = html.indexOf('      function readCustomCriteria()');
assert(cxStart >= 0 && cxEnd > cxStart, 'Custom criteria metric block must ship');
vm.runInContext(html.slice(cxStart, cxEnd), web);
const cxRow = {sym:'NIFTY',date:'2026-11-02',exp:'2026-11-09',strat:'S1',
  sig:'IV 15.20% · HV30 12.69% · VRP +2.51 pp'};
assert.equal(web.dteFromRow(cxRow),7);
assert.equal(web.dteFromRow({date:'invalid',exp:'2026-11-09'}),null);
const cx = web.CUSTOM_METRICS;
assert.deepEqual(Object.keys(cx),['vrp','hv30','hv10','ivhv10','rr25','bf25','dte','iv','entryMin','ivhvRatio','spotChange','netDelta','minOi','maxSpread'],'Only recorded archive metrics are offered');
assert.equal(cx.vrp.get(cxRow),2.51);
assert.equal(cx.hv30.get(cxRow),12.69);
assert.equal(cx.hv10.get(cxRow),null,'HV10 is not recorded on S1 rows');
assert.equal(cx.ivhv10.get({sig:'IV 14.89% · HV10 14.76% · IV−HV10 +0.13 pp'}),0.13);
assert.equal(cx.ivhv10.get({sig:'IV 15.20% · HV10 14.70% · IV−HV10 -0.40 pp'}),-0.4,'Negative spreads keep their sign');
assert.equal(cx.rr25.get({sig:'RR25 +2.90 pp · VRP +2.51 pp'}),2.9);
assert.equal(cx.bf25.get({sig:'Basis -53.4 pts · BF25 +0.10 pp'}),0.1);
assert.equal(cx.vrp.get({sig:'HV30 9.00%'}),null,'Missing metric is null, never zero');
assert.equal(web.rowMatchesCriteria(cxRow,{logic:'AND',criteria:[{get:cx.vrp.get,min:3,max:null}]}),false);
assert.equal(web.rowMatchesCriteria(cxRow,{logic:'AND',criteria:[{get:cx.vrp.get,min:2.5,max:2.6}]}),true,'Bounds are inclusive');
assert.equal(web.rowMatchesCriteria(cxRow,{logic:'AND',criteria:[{get:cx.vrp.get,min:2,max:null},{get:cx.hv30.get,min:null,max:20}]}),true);
assert.equal(web.rowMatchesCriteria(cxRow,{logic:'AND',criteria:[{get:cx.vrp.get,min:2,max:null},{get:cx.hv30.get,min:null,max:12}]}),false,'AND requires every bound');
assert.equal(web.rowMatchesCriteria(cxRow,{logic:'OR',criteria:[{get:cx.rr25.get,min:null,max:null},{get:cx.hv30.get,min:null,max:20}]}),true,'OR accepts any bound');
assert.equal(web.rowMatchesCriteria(cxRow,{logic:'AND',criteria:[{get:cx.rr25.get,min:null,max:null}]}),false,'A missing metric fails the criterion');
assert.equal(web.rowMatchesCriteria(cxRow,{logic:'AND',criteria:[{get:cx.dte.get,min:7,max:7}]}),true);
assert.equal(web.rowMatchesCriteria(cxRow,{logic:'AND',criteria:[]}),true,'No criteria matches everything');
const cxFieldRow = {sym:'NIFTY',date:'2026-11-02',exp:'2026-11-09',strat:'S1',
  sig:'IV 15.20% · HV30 12.69% · VRP +2.51 pp',iv:15.2,hv10:9.96,hv30:13.99,vrp:1.21,ivhv10:5.24,dte:7,hvAsOf:'2026-10-30'};
assert.equal(cx.vrp.get(cxFieldRow),1.21,'Restated row value wins over the recorded string');
assert.equal(cx.hv30.get(cxFieldRow),13.99);
assert.equal(cx.hv10.get(cxFieldRow),9.96);
assert.equal(cx.ivhv10.get(cxFieldRow),5.24);
assert.equal(cx.dte.get(cxFieldRow),7);
assert.equal(cx.iv.get(cxRow),15.2,'Entry IV comes from the recorded signal string');
assert.equal(cx.iv.get({sig:'VRP +2.51 pp'}),null,'No IV in the signal is null, never zero');
assert.equal(cx.entryMin.get({entryMin:5}),5);
assert.equal(cx.stop,undefined,'Future drawdown is never an entry filter');
assert.equal(cx.target,undefined,'Future profit is never an entry filter');

// The shipped archive is generated from recorded captures: no template
// replication, no imputed P&L, and metadata that accounts for every row.
const archive = JSON.parse(html.match(/var tradeArchive = (\[[\s\S]*?\]);/)[1]);
const btMeta = JSON.parse(html.match(/var backtestMeta = (\{[\s\S]*?\});/)[1]);
assert(archive.length >= 200,'Archive covers every replayable session');
assert.equal(archive.length,btMeta.counts.structures,'Metadata accounts for every structure');
assert.equal(new Set(archive.map(r=>r.date)).size,btMeta.counts.sessions);
assert.equal(archive.filter(r=>r.rulePass).length,btMeta.counts.rulePass,'Rule-pass count matches metadata');
assert(btMeta.counts.sessions >= 15,'Recorded history is continuous, not a six-session stub');
assert.equal(archive.filter(r=>r.sym==='NIFTY').length,archive.filter(r=>r.sym==='BANKNIFTY').length,'Both indices replay the same sessions');
const excludedDates = new Set(btMeta.excluded.map(e=>e.date));
for(const sym of ['NIFTY','BANKNIFTY']) {
  for(const strat of ['S1','S2','S3','S4','S5','S6'])
    assert(archive.some(r=>r.sym===sym&&r.strat===strat),sym+' '+strat+' is replayed');
}
assert.equal(new Set(archive.map(r=>r.date+'|'+r.sym+'|'+r.strat)).size,archive.length,'One row per session/index/structure');
for(const row of archive) {
  assert(!excludedDates.has(row.date),'Excluded sessions never appear as rows');
  assert.equal(typeof row.gross,'number');
  assert.equal(typeof row.charges,'number');
  assert.equal(typeof row.capital,'number');
  assert(row.capital>0&&row.charges>0);
  assert(row.sig && row.sigCalc && row.date && row.exp && row.legs);
  assert(Number.isFinite(row.iv) && Number.isFinite(row.hv10) && Number.isFinite(row.hv30),'Restated inputs are numeric');
  assert(row.hvAsOf < row.date,'HV uses completed closes, never the entry session close');
  assert.equal(row.dte,(Date.parse(row.exp)-Date.parse(row.date))/86400000);
  assert(Math.abs(row.vrp-(row.iv-row.hv30))<0.011,'VRP follows from recorded IV and restated HV30');
  assert(Math.abs(row.ivhv10-(row.iv-row.hv10))<0.011,'IV minus HV10 follows from the recorded inputs');
  assert.equal(typeof row.rulePass,'boolean');
  assert.equal(typeof row.dteInBand,'boolean','DTE band outcome is recorded');
  assert(Array.isArray(row.dteBand)&&row.dteBand.length===2);
  assert(row.expiryKind==='weekly'||row.expiryKind==='monthly');
  assert(/^\d{2}:\d{2}:\d{2}$/.test(row.entryAt),'Captured entry clock is recorded');
  assert(/^\d{2}:\d{2}:\d{2}$/.test(row.exitAt),'Captured exit clock is recorded');
  const eMin = Number(row.entryAt.slice(0,2))*60+Number(row.entryAt.slice(3,5));
  assert.equal(row.entryMin,eMin-(9*60+15),'Entry clock is measured from the 09:15 open');
  assert(row.entryMin>=4&&row.entryMin<=17,'Entry clock follows the preset window');
  const toSec = c => (Number(c.slice(0,2))*3600+Number(c.slice(3,5))*60+Number(c.slice(6,8)));
  assert.equal(row.holdMin,Math.round((toSec(row.exitAt)-toSec(row.entryAt))/60),'Holding time matches the captured clocks');
  assert(row.holdMin>300);
  assert(Number.isFinite(row.maePct)&&row.maePct>=0&&row.maePct<=100);
  assert(Number.isFinite(row.mfePct)&&row.mfePct>=0);
  assert(Number.isInteger(row.pathN)&&row.pathN>0);
  assert(row.pathTotal>=row.pathN,'Missing captures are skipped, never filled');
  // Gross must reproduce from the displayed leg marks and lot size.
  let sumRatio = 0, gross = 0;
  for(const part of row.legs.split(' / ')) {
    const m = part.match(/^([SB])(\d*) (\d+)(CE|PE) \(([\d.]+)\D+([\d.]+)\)$/);
    assert(m,'Leg parses: '+part);
    const ratio = Number(m[2]||1), entry = Number(m[5]), exit = Number(m[6]);
    sumRatio += ratio;
    gross += (m[1]==='S'?-1:1)*ratio*(exit-entry);
  }
  const lot = row.unitLegs/sumRatio;
  assert.equal(lot,btMeta.lotSizes[row.sym],'Archived quantity must match the verified contract lot');
  assert(Math.abs(gross*lot-row.gross) <= 0.11*sumRatio*lot+1,'Gross reproduces from recorded marks');
}
const sample = archive.find(r=>r.date==='2026-11-02'&&r.sym==='NIFTY'&&r.strat==='S1');
assert.equal(sample.gross,78.0,'Known 11-02 S1 gross reproduces');
assert.equal(sample.capital,22684,'Known 11-02 S1 capital reproduces');
assert.equal(sample.exp,'2026-11-09');
assert.equal(sample.hvAsOf,'2026-10-30');
assert(btMeta.excluded.every(e=>e.reason.length>0),'Every excluded session states why');
assert.equal(web.rowMatchesCriteria(cxRow,{logic:'AND',criteria:[{get:cx.hv30.get,min:null,max:12}]}),false,'A bound above the value cannot pass');

const embedded=JSON.parse(html.match(/var REAL_HISTORICAL_1Y = (.*);/)[1]);
for(const rows of Object.values(embedded)) for(const row of rows) {
  if(row.iv!==null) {assert.equal(web.observedIv(row),row.iv);assert(row.archiveFile);}
  if(row.d<'2026-10-20') assert.equal(row.iv,null);
}
console.log('Frontend regressions passed: missing data, sourced IV, statistics, forward variance, actual payoff legs and capture times.');

require('./test_backtest_replay.cjs');
require('./test_desk_refresh.cjs');

assert.equal(charts.surface,undefined);
assert(!element('regimeHorizonTableBody').innerHTML.includes('Yang–Zhang'));
assert(!html.includes('plotly.min.js'));
assert.equal(charts.tooltipName('DTE-matched EGARCH RV (%)'),'EGARCH');
assert.equal(charts.tooltipName('Captured ATM IV · term baseline'),'IV');
const tooltip = web.chartOpts('%',false).plugins.tooltip.callbacks.label;
assert.equal(tooltip({dataset:{label:'Captured IV (%)'},parsed:{y:15.725}}),'IV: 15.72%');
assert.equal(tooltip({dataset:{label:'DTE-matched EGARCH RV (%)'},parsed:{y:17}}),'EGARCH: 17.00%');
assert.equal(charts.heatStyle(null,100),charts.heatStyle(0,100));
assert.notEqual(charts.heatStyle(10,100),charts.heatStyle(100,100));
assert.equal(charts.heatStyle(100,100).replace('153','355'),charts.heatStyle(-100,100));
assert.match(element('stressMatrixTableBody').innerHTML,/background:/);

const legCase={atm:25000,modelDecision:{model:'black76'},chain:[{k:25000,modelPutIv:14,uCallIv:16,pLtp:100,cLtp:105}]};
const testedLegs=web.scenarioLegs(legCase,'NIFTY','SHORT_STRADDLE');
assert.equal(testedLegs[0].iv,14);
assert.equal(testedLegs[1].iv,16);
assert.equal(testedLegs[1].brokerIvUsed,true);
