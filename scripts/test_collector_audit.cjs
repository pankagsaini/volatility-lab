// Offline checks of the Apps Script API contract. No Google or broker requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(process.env.COLLECTOR_SOURCE_PATH || path.join(__dirname, '../volatility_lab/cloud/apps_script/Code.gs'), 'utf8').replace(/\r\n/g,'\n');
const values = new Map();
const cached = new Map();
let reads = 0;
const cloud = vm.createContext({
  Date, console,
  PropertiesService: {getScriptProperties: () => ({
    getProperty: key => values.get(key),
    getProperties: () => Object.fromEntries(values),
    setProperty: (key, value) => values.set(key, value),
    deleteProperty: key => values.delete(key),
  })},
  CacheService: {getScriptCache: () => ({
    get: key => cached.get(key),
    put: (key, value, ttl) => { assert.equal(ttl, 55); cached.set(key, value); },
  })},
  ContentService: {
    MimeType: {JSON: 'json', JAVASCRIPT: 'javascript'},
    createTextOutput: text => ({setMimeType: mime => ({text, mime})}),
  },
  Utilities: {formatDate: date => date.toISOString().slice(0, 10)},
  LockService: {getScriptLock: () => ({tryLock: () => true, releaseLock: () => {}})},
});
vm.runInContext(source, cloud);
cloud.loadForwardLedger_ = () => { reads++; return {schema: 2, closed_trades: [], open_positions: []}; };
const get = parameter => cloud.doGet({parameter});
assert.equal(JSON.parse(get({action: 'ledger'}).text).schema, 2);
assert.equal(JSON.parse(get({action: 'forward_ledger'}).text).schema, 2);
assert.equal(reads, 1, 'Aliases must share the raw JSON cache');
const jsonp = get({action: 'ledger', callback: 'onLedger'});
assert.equal(jsonp.mime, 'javascript');
let received;
vm.runInNewContext(jsonp.text, {onLedger: value => { received = value; }});
assert.equal(received.schema, 2);
for (const callback of ['x);alert(1)//', 'obj.call', 'constructor.constructor', 'x[',
                        'a'.repeat(65)]) {
  assert.equal(JSON.parse(get({action: 'ledger', callback}).text).error, 'Invalid callback');
}
values.set('LATEST_SNAPSHOT_FILE_ID', 'fixture');
cached.set('DOGET_LATEST_SNAPSHOT_JSON_fixture', JSON.stringify({status: 'complete'}));
assert.equal(JSON.parse(get({action: 'latest'}).text).status, 'complete');
assert.equal(get({action: 'latest', callback: 'onSnapshot'}).mime, 'javascript');
const legacy = cloud.sanitizeForwardLedger_({
  schema: 2, version: 'forward-test-v20.1-event-time-gated',
  open_positions: [], closed_trades: [{
    session_date: '2026-11-11', triggerTime: '2026-11-11T09:20:00+05:30', net: 100,
  }], intraday_mtm_series: [{session_date: '2026-11-11', net_mtm: 100}],
});
assert.equal(legacy.closed_trades[0].provenance_status, 'LEGACY_UNVERIFIED');
assert.equal(legacy.closed_trades[0].net, 100);
assert.equal(legacy.intraday_mtm_series[0].net_mtm, null);
assert.equal(cloud.isValidForwardRecord_(legacy.closed_trades[0]), false);
// Current options buy fees: brokerage + all exchange components + SEBI + stamp + GST.
const expected = 20 + 3553 + 10 + 300 + 0.18 * (20 + 3553);
assert(Math.abs(cloud.upstoxOptionCharge_(10000, 1000, false) - expected) < 1e-8);
const stored = [];
cloud.save_ = (name, payload) => {stored.push({name, payload}); return 'file-id';};
cloud.catalogue_ = () => ({indices:{NIFTY:{futures:[]},BANKNIFTY:{futures:[]}}});
cloud.api_ = () => ({candles:[['2026-11-13T09:21:00+05:30',100,110,99,104,0]]});
let manual = cloud.manualDownloadSession('2026-11-13');
assert.equal(manual.saved_snapshots, 0);
assert.equal(manual.status, 'available');
assert.equal(stored.length, 1);
const pending = {};
for (let day = 1; day <= 31; day++) pending['2026-10-' + String(day).padStart(2, '0')] = 'file-' + day;
cloud.savePendingEod_(pending);
assert.equal([...values.keys()].filter(key => key.startsWith('EOD_PENDING_DAY_')).length, 30);
assert.equal(stored[1].payload.type, 'eod_pending_archive_index');
assert.equal(stored[1].payload.days['2026-10-01'], 'file-1');
assert.equal(cloud.pruneOldMinuteSnapshots_(7).pruned, 0);
assert(cloud.pruneOldMinuteSnapshots_(7).reason.includes('full-fidelity'));
assert.equal(stored[0].payload.mode, 'historical_candles');
assert.equal(stored[0].payload.minutes_count, 1);
assert(!stored[0].name.includes('_snapshot_'));
assert(!Object.hasOwn(stored[0].payload, 'records'));
cloud.api_ = () => ({candles:[]});
manual = cloud.manualDownloadSession('2026-11-13');
assert.equal(manual.status, 'empty');
assert.equal(manual.consolidated_file_id, null);
assert.equal(stored.length, 2);

// EOD recovery is a keyed mutation that drains the queue and reports every session.
assert.equal(JSON.parse(get({action: 'eod_backfill'}).text).error, 'Unauthorized');
values.set('WEBHOOK_KEY', 'secret');
assert.equal(JSON.parse(get({action: 'eod_backfill', day: '2026-11-17', key: 'wrong'}).text).error, 'Unauthorized');
assert.equal(JSON.parse(get({action: 'eod_backfill', day: '2026-11-17', key: 'secret'}).text).error,
  'No pending EOD for 2026-11-17');
assert.equal(JSON.parse(get({action: 'eod_backfill', day: '18 Nov 2026', key: 'secret'}).text).error,
  'Invalid session date');
[...values.keys()].filter(key => key.startsWith('EOD_PENDING_DAY_')).forEach(key => values.delete(key));
values.delete('EOD_PENDING');
values.set('EOD_PENDING_DAY_2026-11-16', 'partial-16');
values.set('EOD_PENDING_DAY_2026-11-17', 'partial-17');
const recovered = [];
cloud.recoverEod_ = (day, fileId) => {
  recovered.push({day, fileId});
  values.delete('EOD_PENDING_DAY_' + day);
  return {status: 'complete', file_id: 'complete-' + day, pending: [],
    closing_risk: {NIFTY: [25387.4, 14.357172616, 13.417576576, 15.428042921, 15.134276148]}};
};
const backfill = JSON.parse(get({action: 'eod_backfill', key: 'secret'}).text);
assert.equal(backfill.recovered, 2);
assert.deepEqual(recovered.map(row => row.day), ['2026-11-16', '2026-11-17']);
assert.deepEqual(recovered.map(row => row.fileId), ['partial-16', 'partial-17']);
assert.deepEqual(backfill.sessions.map(row => row.status), ['complete', 'complete']);
const status = JSON.parse(get({action: 'status', key: 'secret'}).text);
assert.deepEqual(status.eod_pending, []);
assert.equal(status.pure_snapshot_capture_only, false);
values.set('PURE_SNAPSHOT_CAPTURE_ONLY', 'true');
cached.delete('DOGET_STATUS_JSON');
assert.equal(JSON.parse(get({action: 'status', key: 'secret'}).text).pure_snapshot_capture_only, true);
// The expiry desk accepts only paired two-sided books at the fixed anchor and
// the nearest listed strike to spot; no LTP fallback and no distance synthesis.
const quoteAt = new Date().toISOString();
const leg = (bid, ask) => ({market_data: {quote_source: 'Upstox /v2/market-quote/quotes', quote_timestamp: quoteAt,
  bid_price: bid, ask_price: ask, bid_qty: 75, ask_qty: 75}, option_greeks: {iv: 24}});
const dualRec = {label: 'NIFTY|2026-11-17', retrieved_at: quoteAt, straddle_anchor_strike: 25000, data: [
  {underlying_spot_price: 25040, strike_price: 25000, call_options: leg(41, 43), put_options: leg(21, 23)},
  {underlying_spot_price: 25040, strike_price: 25050, call_options: leg(18, 20), put_options: leg(48, 50)}]};
const dual = cloud.dualStraddlePoint_(dualRec, quoteAt);
assert.equal(dual.rollAtm, 25050, 'Rolling strike is the nearest listed strike to spot');
assert.equal(dual.anchorK, 25000);
assert.equal(dual.rollStrad, 68);
assert.equal(dual.anchorStrad, 64);
assert.equal(cloud.dualStraddlePoint_({...dualRec, data: [
  {underlying_spot_price: 25040, strike_price: 25050, call_options: leg(18, 20),
    put_options: {market_data: {quote_source: 'rest', quote_timestamp: quoteAt,
      bid_price: 48, ask_price: 50, bid_qty: 75, ask_qty: 75}}}]}, quoteAt), null,
  'One recorded leg is not a straddle mark');
console.log('Collector audit regressions passed: aliases, shared cache, JSONP, legacy provenance, fees and EOD recovery.');

// Automatic recovery fairness, cooldowns, quota accounting and session safety.
{
  let stamp=Date.parse('2026-11-19T16:01:00+05:30');
  class RecoveryClock extends Date { constructor(...args){super(...(args.length?args:[stamp]));} static now(){return stamp;} }
  const state=new Map([['SCHEDULE_MODE','intraday']]);
  const prop={getProperty:k=>state.get(k)||null,getProperties:()=>Object.fromEntries(state),
    setProperty:(k,v)=>state.set(k,v),deleteProperty:k=>state.delete(k)};
  let releases=0;
  const worker=vm.createContext({Date:RecoveryClock,console,
    PropertiesService:{getScriptProperties:()=>prop},
    LockService:{getScriptLock:()=>({tryLock:()=>true,releaseLock:()=>releases++})}});
  vm.runInContext(source,worker);
  worker.day_=()=> '2026-11-19'; worker.scriptCache_=()=>null;
  for(const day of ['2026-11-11','2026-11-12','2026-11-16','2026-11-17']) state.set('EOD_PENDING_DAY_'+day,'file-'+day);
  const calls=[];
  worker.recoverEod_=(day)=>{
    calls.push(day);stamp+=1000;
    if(day==='2026-11-11') throw new Error('Recovery archive unavailable');
    state.delete('EOD_PENDING_DAY_'+day);
    return {status:'complete',file_id:'done-'+day};
  };
  worker.reconcilePriorSession();
  assert.deepEqual(calls,['2026-11-11','2026-11-12','2026-11-16'],'A failed day cannot starve later dates');
  assert.equal(JSON.parse(state.get('RUNTIME_BUDGET')).ms,3000);
  assert.equal(JSON.parse(state.get('RECONCILIATION_BUDGET')).ms,3000);
  worker.reconcilePriorSession();assert.equal(calls.length,3,'Minimum retry spacing');
  stamp+=60000;worker.reconcilePriorSession();
  assert.equal(calls.at(-1),'2026-11-17');
  assert.equal(calls.length,4,'Failed day stays in backoff while untouched day runs');
  assert.equal(JSON.parse(state.get('EOD_RECOVERY_STATE'))['2026-11-11'].status,'error');
  assert(state.has('EOD_PENDING_DAY_2026-11-11'),'Failed evidence is never cleared');
  const budget=JSON.parse(state.get('RUNTIME_BUDGET'));budget.ms=80*60000;
  state.set('RUNTIME_BUDGET',JSON.stringify(budget));stamp+=31*60000;
  worker.reconcilePriorSession();assert.equal(calls.length,4,'Reserve current EOD runtime');
  assert.equal(releases,4);

  // MIME repair is read-only and target-date validation precedes any capture.
  let mime=null,captured=null;
  worker.DriveApp={getFileById:()=>({getId:()=> 'original',getBlob:()=>({setContentType:t=>{mime=t;}})})};
  worker.folder_=()=>({searchFiles:()=>({hasNext:()=>false})});
  worker.Utilities={ungzip:()=>({getDataAsString:()=>JSON.stringify({day:'2026-11-17',mode:'eod',catalogue:{indices:{}}})})};
  worker.capture_=(mode,rec)=>{captured=rec;return {status:'complete'};};
  const actualRecover=vm.runInContext('('+source.slice(source.indexOf('function recoverEod_('),source.indexOf('/**\n * Drain queued'))+')',worker);
  assert.equal(actualRecover('2026-11-17','original').status,'complete');
  assert.equal(mime,'application/x-gzip');assert.equal(captured.target_day,'2026-11-17');
  assert.equal(captured.source_file_id,'original');
  assert.throws(()=>actualRecover('2026-11-16','original'),/Invalid reconciliation archive/);
  worker.DriveApp.getFileById=()=>{throw new Error('NOT_FOUND');};
  const surviving={getId:()=> 'surviving',getName:()=> '2026-11-17_eod_220000_partial.json.gz',
    getDateCreated:()=>new Date(stamp),getBlob:()=>({setContentType:t=>{mime=t;}})};
  worker.folder_=()=>({searchFiles:query=>{assert(query.includes('2026-11-17_eod_'));let read=false;
    return {hasNext:()=>!read,next:()=>{read=true;return surviving;}};}});
  assert.equal(actualRecover('2026-11-17','missing').status,'complete');
  assert.equal(captured.source_file_id,'surviving','Use a verified original when the saved reference is stale');
}

// Old futures resolve by the broker's expired-contract catalogue, never a reused live token.
{
  const at=Date.parse('2026-11-19T16:00:00+05:30');
  class EodClock extends Date {constructor(...args){super(...(args.length?args:[at]));} static now(){return at;}}
  const settings=new Map();const prop={getProperty:k=>settings.get(k)||null,setProperty:(k,v)=>settings.set(k,v)};
  const c=vm.createContext({Date:EodClock,console:{log(){}},Utilities:{formatDate:()=> '160000'}});
  vm.runInContext(source,c);c.props_=()=>prop;c.day_=d=>d.toISOString().slice(0,10);
  c.closingRiskRows_=()=>({});c.saveClosingRisk_=()=>{};c.loadPendingEod_=()=>({'2026-10-27':'old'});
  let queue;c.savePendingEod_=q=>{queue=q;};c.save_=()=> 'new-original';
  const cat={indices:{NIFTY:{futures:[{instrument_key:'NSE_FO|1',expiry:'2026-10-27'}]},
    BANKNIFTY:{futures:[{instrument_key:'NSE_FO|2',expiry:'2026-11-23'}]}}};
  c.api_=(path,params)=>{assert.equal(path,'/v2/expired-instruments/future/contract');
    assert.equal(params.expiry_date,'2026-10-27');return [{expiry:'2026-10-27',instrument_key:'NSE_FO|1|27-10-2026'}];};
  let requests;
  c.apiAll_=r=>{requests=r;return r.map(req=>({...req,data:{candles:[['2026-10-27T00:00:00+05:30',100,110,90,105,1000]]}}));};
  let result=c.capture_('eod',{target_day:'2026-10-27',catalogue:cat,source_file_id:'old'});
  assert.equal(result.status,'complete');assert.equal(queue['2026-10-27'],undefined);
  assert(requests.find(r=>r.label==='NSE_FO|1').path.includes('/expired-instruments/historical-candle/NSE_FO%7C1%7C27-10-2026/day/'));
  assert(requests.find(r=>r.label==='NSE_FO|2').path.startsWith('/v3/historical-candle/'));
  c.api_=()=>[];result=c.capture_('eod',{target_day:'2026-10-27',catalogue:cat});
  assert.equal(result.status,'partial');assert.equal(queue['2026-10-27'],'new-original');
  assert(result.pending.includes('NSE_FO|1'),'Missing expired data stays pending');
  assert(!requests.some(r=>r.label==='NSE_FO|1'),'Never fall back to a reused live token');
}
