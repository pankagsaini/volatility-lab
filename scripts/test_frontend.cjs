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
  'renderTab1Regime','renderTab2Term','renderTab4Straddle']) {
  const start = html.indexOf('      function '+name+'(');
  assert(start>=0, name);
  vm.runInContext(html.slice(start, html.indexOf('\n      }',start)+8),web);
}
web.updateOrCreateChart = (prior, ctx, type, data, options) => ({data, options, update(){}});
web.fmtInr = value => String(value); // Keep exact numbers readable in test table cells.

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
assert.equal(web.regimeChart.data.datasets[0].spanGaps,false);
const historyRows = web.REAL_HISTORICAL_1Y.NIFTY;
historyRows[1].closingEstimate = {asOf:'2026-11-11T15:30:00+05:30',iv:21,model:'BSM',expiry:'2026-11-17',atm:25000};
web.renderTab1Regime();
assert.deepEqual(Array.from(web.regimeChart.data.datasets[0].data),[20,null,22]);
assert.deepEqual(Array.from(web.regimeChart.data.datasets.at(-1).data),[20,21,22]);
assert.deepEqual(Array.from(web.regimeChart.data.datasets.at(-1).borderDash),[2,4]);
assert.equal(web.regimeChart.data.datasets.at(-1).pointRadius({dataIndex:0}),0);
web.activeMetric='spread';web.renderTab1Regime();
assert.deepEqual(Array.from(web.regimeChart.data.datasets[0].data),[6,null,7]);
web.marketCatalog.NIFTY.push({...web.marketCatalog.NIFTY[0],expiry:'2026-12-29',dte:43,iv:25,brokerAtmIv:null});
web.renderTab2Term();
assert.equal(web.termChart.options.scales.x.type,'linear');
assert.equal(web.termChart.data.datasets.length,1);
assert.equal(web.termChart.data.datasets[0].pointValueLabels,'expiry');
assert.equal(web.termChart.options.scales.x.ticks.callback(43),'2026-12-29');
assert.equal(web.termChart.data.datasets[0].data[1].y,null,'model IV cannot replace missing broker ATM IV');
assert.equal(web.termChart.data.datasets[0].data[1].x,43);

// The shipped history may contain IV only with provenance; pre-capture years stay empty.
const charts = vm.runInContext('OptionsCharts',web);
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
const sessions = vm.runInContext('TradingSessions',web);
assert.equal(fs.readFileSync(path.join(__dirname,'trading-sessions.js'),'utf8'),fs.readFileSync(path.join(__dirname,'../docs/scripts/trading-sessions.js'),'utf8'));
assert.deepEqual(Array.from(sessions.history([{d:'2026-11-06'},{d:'2026-11-12'}]),r=>r.d),
  ['2026-11-06','2026-11-08','2026-11-09','2026-11-11','2026-11-12']);
assert.equal(sessions.session('2026-11-13').endMins,940);
assert.equal(sessions.expiryMillis('2026-12-29'),Date.parse('2026-12-29T15:30:00+05:30'));
const embedded=JSON.parse(html.match(/var REAL_HISTORICAL_1Y = (.*);/)[1]);
for(const rows of Object.values(embedded)) for(const row of rows) {
  if(row.iv!==null) {assert.equal(web.observedIv(row),row.iv);assert(row.archiveFile);}
  if(row.d<'2026-10-20') assert.equal(row.iv,null);
}
console.log('Frontend regressions passed: missing data, sourced IV, statistics, forward variance, actual payoff legs and capture times.');
