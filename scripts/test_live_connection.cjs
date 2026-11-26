// Offline regression of the actual browser functions and Apps Script capture path.
// Broker and Google services are fixtures; no credentials, orders, or Drive writes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const gs = fs.readFileSync(process.env.COLLECTOR_SOURCE_PATH || path.join(root, 'volatility_lab/cloud/apps_script/Code.gs'), 'utf8');
// The offline Python evaluator reads this same contract; reject drift in either deployed JS copy.
const policy = fs.readFileSync(path.join(root,'scripts/freeze-policy.js'),'utf8').trim().replaceAll('\r\n','\n');
assert(!gs.includes('function evaluateForwardTest_'));
assert.equal(policy,fs.readFileSync(path.join(root,'docs/scripts/freeze-policy.js'),'utf8').trim().replaceAll('\r\n','\n'));
const schema=JSON.parse(fs.readFileSync(path.join(root,'volatility_lab/data/freeze_policy.json'),'utf8'));
assert.equal(JSON.stringify(vm.runInNewContext(policy+';FREEZE_RULES')),JSON.stringify(schema));
const clone = value => JSON.parse(JSON.stringify(value));
let now = Date.parse('2026-11-13T03:50:00Z');
class Clock extends Date {
  constructor(...args) { super(...(args.length ? args : [now])); }
  static now() { return now; }
}
function browserFunction(name) {
  const start = html.indexOf('      function ' + name + '(');
  assert(start >= 0, name);
  const end = html.indexOf('\n      }', start);
  return html.slice(start, end + 8);
}
const web = vm.createContext({ Date: Clock, console, window: {}, B76_RISK_FREE_RATE: 0.066, B76_INV_SQRT_2PI: 1 / Math.sqrt(2 * Math.PI),
  marketCatalog: {NIFTY: [], BANKNIFTY: []}, REAL_HISTORICAL_1Y: {NIFTY: [], BANKNIFTY: []},
  activeSymbol: 'NIFTY', activeExpiryIdx: 0, lastSyncedRetrievedAt: '', loadedCloudCapture:false, liveSnapshotRequest: null,
  userSelectedExpiryManually: false,
  CLOUD_EXEC_URL: 'https://example.invalid', updateMarketClock: () => ({isOpen: true}),
  populateExpiryDropdown() {}, renderActiveOptionsTab() {}, renderForwardWorkspace() {},
  setLiveCaptureIntegrity(message) { web.message = message; }
});
vm.runInContext(fs.readFileSync(path.join(__dirname,'trading-sessions.js'),'utf8'),web);
vm.runInContext(fs.readFileSync(path.join(__dirname,'snapshot-metrics.js'),'utf8'),web);
for (const name of ['normPdf', 'normCdf', 'computeBlack76ParityForward', 'black76Greeks', 'black76Price',
  'black76IvDec', 'black76Iv', 'bsmIvDec', 'bsmGreeks', 'currentPricingInputs', 'relativeBookSpread', 'selectOptionPricing', 'enrichItemWithBlack76', 'liveNumber', 'liveTimestamp', 'liveBookValid',
  'liveDeltaIv', 'capturedDeltaIv','validCloudCapture','showCaptureState','combineDailyCaptures','applyClosingRisk','normalizeLiveSnapshot', 'currentRow', 'isFreshValidatedCloudSnapshot', 'applyMarketSnapshot', 'rememberMarketSnapshot', 'restoreMarketSnapshot', 'syncLiveCloudSnapshot',
  'istDayAndMinute','nextTradingDay','expiryClock','isExpiryRollWindow','getDefaultActiveExpiryIdx']) {
  vm.runInContext(browserFunction(name), web);
}
web.pricingInputs = {schema:1,rate:{model_rate:.066,source:'https://www.rbi.org.in/',sha256:'fixture',checked_at:new Date(now).toISOString()},
  dividends:Object.fromEntries(['NIFTY','BANKNIFTY'].map((s,i)=>[s,{model_yield:.0142,as_of:'2026-10-30',sha256:'fixture',source:'https://www.niftyindices.com/Factsheet/'+(i?'ind_nifty_bank.pdf':'ind_nifty50.pdf')}]))};
// Compile every inline script, including the large generated data section.
for (const [, attributes, script] of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)) {
  if (/application\/(?:ld\+)?json/.test(attributes)) JSON.parse(script);
  else new vm.Script(script);
}
assert.equal(html, fs.readFileSync(path.join(root, 'docs/index.html'), 'utf8'));
const webAppUrl = html.match(/var CLOUD_EXEC_URL = '([^']+)'/)[1];
const syncConfig = JSON.parse(fs.readFileSync(path.join(root,'volatility_lab/cloud/apps_script/sync_config.json'),'utf8'));
assert.equal(syncConfig.web_app_url,webAppUrl,'Frontend and default collector deployment must match');

function candles() {
  let close = 25000;
  const rows = [];
  for (let i = 70; i >= 0; i--) {
    const date = new Date(now - i * 86400000);
    if ([0, 6].includes(date.getUTCDay())) continue;
    close *= Math.exp(rows.length % 2 ? 0.007 : -0.007);
    rows.push([date.toISOString().slice(0, 10) + 'T00:00:00+05:30', close, close, close, close, 100, 0]);
  }
  return rows.reverse(); // Real API returns newest first, including possible current-day input.
}
function rawChain(sym, expiry, spot = sym === 'NIFTY' ? 25000 : 60000) {
  const step = sym === 'NIFTY' ? 50 : 100;
  const rows = [];
  for (let k = spot - step * 16; k <= spot + step * 16; k += step) {
    const row = { expiry, strike_price: k, underlying_spot_price: spot };
    for (const [side, call] of [['call_options', true], ['put_options', false]]) {
      row[side] = { instrument_key: 'NSE_FO|' + sym + k + (call ? 'C' : 'P'),
        option_greeks: {iv: 20, delta: call ? 0.5 : -0.5},
        market_data: {ltp: 999, bid_price: 998, ask_price: 1000, bid_qty: 1000, ask_qty: 1000} };
    }
    rows.push(row);
  }
  return rows;
}
const properties = new Map();
const cache = new Map();
const cloud = vm.createContext({ Date: Clock, console: {log() {}}, Utilities: {
  formatDate(d, tz, format) {
    const iso = new Date(d.getTime() + 19800000).toISOString();
    if (format === 'yyyy-MM-dd') return iso.slice(0, 10);
    if (format === 'HH' || format === 'H') return iso.slice(11, 13);
    if (format === 'mm' || format === 'm') return iso.slice(14, 16);
    if (format === 'HHmmss') return iso.slice(11, 19).replaceAll(':', '');
    return iso.slice(11, 16);
  }
}});
vm.runInContext(gs, cloud);
cloud.safeError_ = error => String(error.stack || error);
cloud.props_ = () => ({getProperty: key => properties.get(key) || null,
  setProperty: (key, val) => properties.set(key, val)});
cloud.scriptCache_ = () => ({get: key => cache.get(key), put: (k, v) => cache.set(k, v), remove: k => cache.delete(k)});
cloud.marketClosed_ = () => false;
cloud.marketWindow_ = () => ({closed: false, startMins: 555, endMins: 940});
cloud.catalogue_ = () => ({indices: {
  NIFTY: {futures: [], option_expiries: ['2026-11-17']},
  BANKNIFTY: {futures: [], option_expiries: ['2026-11-23']}
}});
cloud.save_ = () => 'fixture-report';
let mode = 'valid';
const keysToOptions = new Map();
cloud.apiAll_ = requests => requests.map(req => {
  let data;
  if (req.label.startsWith('risk:')) {
    if (mode === 'no-history') return {...req, error: 'Broker HTTP 503'};
    data = {candles: candles()};
  } else if (req.label === 'quotes') {
    data = Object.fromEntries(req.params.instrument_key.split(',').map((key, i) => [String(i),
      {instrument_token: key, timestamp: new Date(now).toISOString(), last_price: key.endsWith('50') ? 25000 : key.endsWith('Bank') ? 60000 : 15}]));
  } else if (req.path === '/v2/option/chain') {
    const [sym, exp] = req.label.split('|');
    data = rawChain(sym, exp);
    for (const r of data) for (const side of ['call_options', 'put_options']) keysToOptions.set(r[side].instrument_key, {r, side});
  } else {
    assert(req.params.instrument_key.split(',').length <= 500);
    assert(('https://api.upstox.com' + req.path + '?instrument_key=' + encodeURIComponent(req.params.instrument_key)).length < 2082);
    data = Object.fromEntries(req.params.instrument_key.split(',').map(key => {
      const {r, side} = keysToOptions.get(key);
      const years = (Date.parse(r.expiry + 'T15:30:00+05:30') - now) / (365 * 86400000);
      const px = web.black76Price(r.underlying_spot_price, r.strike_price, years, 0.066, 0.20, side === 'call_options');
      return [key, {instrument_token: key, timestamp: mode === 'no-times' ? null : new Date(now - (mode === 'stale' ? 300000 : 1000)).toISOString(),
        last_trade_time: '1000000000000', last_price: px, volume: 10000, oi: 10000,
        depth: {buy: [{price: Math.max(0.01, px - 0.1), quantity: 1000}], sell: [{price: px + 0.1, quantity: 1000}]}}];
    }));
  }
  return {...req, data};
});

// Charge actual simulated elapsed time and exercise the minute trigger's admission checks.
{
  const day = '2026-11-13';
  const midnight = Date.parse(day + 'T00:00:00+05:30');
  const settings = new Map();
  let stamp, minute, captureMs, lockedUsage, lockCalls, failAt;
  const attempts = [];
  class SchedulerClock extends Date {
    constructor(...args) { super(...(args.length ? args : [stamp])); }
    static now() { return stamp; }
  }
  const props = {getProperty: key => settings.get(key) || null,
    setProperty: (key, value) => settings.set(key, value), setProperties: obj => Object.entries(obj).forEach(([k,v])=>settings.set(k,v)), deleteProperty: key => settings.delete(key)};
  const initialWindow = {windowStartMs: midnight, windowEndMs: midnight + 86400000, ms: 0};
  const budgetAt = minutes => settings.set('RUNTIME_BUDGET', JSON.stringify({...initialWindow, ms: minutes * 60000}));
  const scheduler = vm.createContext({Date: SchedulerClock, Utilities: {formatDate: () => '5'},
    LockService: {getScriptLock: () => ({tryLock() {
      lockCalls++;
      if (lockedUsage !== null) budgetAt(lockedUsage);
      return true;
    }, releaseLock() {}})}});
  vm.runInContext(gs, scheduler);
  scheduler.props_ = () => props;
  scheduler.day_ = () => day;
  scheduler.minute_ = () => minute;
  scheduler.marketClosed_ = () => false;
  scheduler.capture_ = mode => {
    assert.equal(mode, 'snapshot');
    attempts.push(minute);
    stamp += captureMs;
    return {status: minute === failAt ? 'partial' : 'complete'};
  };
  function reset(duration = 10000) {
    settings.clear();
    settings.set('SCHEDULE_MODE', 'intraday');
    settings.set('MARKET_CALENDAR', JSON.stringify({year:'2026', checked:day, closed:[], special:{}}));
    attempts.length = 0;
    captureMs = duration;
    lockedUsage = null;
    lockCalls = 0;
    failAt = null;
    budgetAt(0);
  }
  function tick(slot) {
    minute = slot;
    stamp = midnight + slot * 60000;
    scheduler.scheduledCapture();
  }
  function regularSession() {
    for (let slot = 554; slot <= 941; slot++) tick(slot);
  }
  reset();
  regularSession();
  assert.deepEqual(attempts, Array.from({length:386}, (_, i) => 555 + i), 'normal work covers every market minute');
  assert.equal(JSON.parse(settings.get('RUNTIME_BUDGET')).ms, 386 * captureMs);

  reset(17500);
  regularSession();
  assert(attempts.length < 386, 'expensive workload reaches the guard');
  assert(!attempts.includes(914), 'ordinary captures pause under pressure');
  assert(attempts.includes(915), '15:15 exit gets a snapshot');
  assert(attempts.includes(940), '15:40 closing snapshot still has a budget');
  assert.equal(attempts.at(-1), 940);
  assert.equal(JSON.parse(settings.get('RUNTIME_BUDGET')).ms, attempts.length * captureMs);

  reset();
  budgetAt(82);
  tick(914);
  failAt = 915;
  assert.throws(() => tick(915), /Capture incomplete/);
  assert.equal(settings.has('LAST_SNAPSHOT_SLOT'), false);
  tick(916);
  assert.deepEqual(attempts, [915, 916], 'a partial exit does not block the next real capture');
  assert.equal(settings.get('LAST_SNAPSHOT_SLOT'), day + '|361');
  budgetAt(83.5);
  tick(939);
  tick(940);
  assert.deepEqual(attempts, [915, 916, 940], 'closing book keeps its reserve');

  reset();
  budgetAt(82);
  tick(914);
  assert.equal(lockCalls, 0);
  tick(916);
  assert.deepEqual(attempts, [916], 'a late trigger records only the actual minute');
  assert.equal(settings.get('LAST_SNAPSHOT_SLOT'), day + '|361');
  budgetAt(83.5);
  tick(939);
  budgetAt(85);
  tick(940);
  assert.equal(lockCalls, 1, 'exhausted thresholds return before acquiring a lock');

  reset();
  budgetAt(81);
  lockedUsage = 82;
  tick(914);
  lockedUsage = 83.5;
  tick(915);
  lockedUsage = 85;
  tick(940);
  assert.deepEqual(attempts, [], 'each threshold is checked again after acquiring the lock');

  reset();
  settings.set('COLLECTOR_RUNTIME_MINUTES', '3');
  budgetAt(1.5);
  tick(914);
  tick(915);
  budgetAt(2.25);
  tick(939);
  tick(940);
  assert.deepEqual(attempts, [915, 940], 'small operator limits preserve proportional milestone budgets');

  for (const [open, close, exit] of [[600, 720, 705], [1080, 1140, 1080]]) {
    reset();
    settings.set('SPECIAL_SESSIONS_JSON', JSON.stringify({[day]:{startMins:open, endMins:close}}));
    budgetAt(82);
    tick(exit - 1);
    tick(exit);
    budgetAt(83.5);
    tick(close - 1);
    tick(close);
    tick(close + 1);
    assert.deepEqual(attempts, [exit, close], 'special session uses its own exit and closing time');
  }
  reset();
  const policy = () => scheduler.runtimePolicy_(props);
  for (const value of ['200', 'invalid', '-1', '0', '2.5']) {
    settings.set('COLLECTOR_RUNTIME_MINUTES', value);
    assert.equal(policy().total_minutes, 85);
  }
  settings.set('COLLECTOR_RUNTIME_MINUTES', '60');
  assert.equal(policy().snapshot_minutes, 60);
  settings.set('COLLECTOR_RUNTIME_MINUTES', '3');
  assert.equal(policy().snapshot_minutes, 3);
  settings.set('COLLECTOR_QUOTA_TIER', 'verified_workspace');
  settings.set('COLLECTOR_RUNTIME_MINUTES', '360');
  assert.equal(policy().snapshot_minutes, 355);
  assert.equal(policy().total_minutes, 355);
  settings.set('COLLECTOR_QUOTA_TIER', 'workspace');
  assert.equal(policy().total_minutes, 85);
}
// The exchange schedule wins during a broker outage. An unlisted weekend can recover
// from a failed premarket lookup without treating ordinary weekends as open.
{
  let stamp, reads = 0, available = false, attempts = [];
  class CalendarClock extends Date {
    constructor(...args) {super(...(args.length ? args : [stamp]));}
    static now() {return stamp;}
  }
  const state = new Map([['SCHEDULE_MODE','intraday']]);
  const properties = {getProperty:k=>state.get(k) || null, setProperty:(k,v)=>state.set(k,v), setProperties:obj=>Object.entries(obj).forEach(([k,v])=>state.set(k,v)), deleteProperty:k=>state.delete(k)};
  const sessionDate = '2026-11-21';
  const start = Date.parse(sessionDate + 'T14:20:00+05:30'), end = Date.parse(sessionDate + 'T15:40:00+05:30');
  const calendar = vm.createContext({Date:CalendarClock, console:{log(){}}, LockService:{
    getScriptLock:()=>({tryLock:()=>true,releaseLock(){}})}, Utilities:{formatDate(d,tz,f) {
      const ist = new Date(d.getTime()+19800000);
      if (f === 'u') return String(ist.getUTCDay() || 7);
      if (f === 'H') return String(ist.getUTCHours());
      if (f === 'm') return String(ist.getUTCMinutes());
      return ist.toISOString().slice(0,10);
    }}, UrlFetchApp:{fetch() {
      reads++;
      return {getResponseCode:()=>available ? 200 : 503, getContentText:()=>JSON.stringify({status:'success',data:[
        {date:sessionDate, holiday_type:'SPECIAL_TIMING', open_exchanges:[{exchange:'NFO',start_time:start,end_time:end}]},
        {date:'2026-11-27',holiday_type:'TRADING_HOLIDAY',closed_exchanges:['NFO']},
        {date:'2026-11-23',holiday_type:'SPECIAL_TIMING',open_exchanges:[{exchange:'NFO',
          start_time:Date.parse('2026-11-23T14:20:00+05:30'),end_time:Date.parse('2026-11-23T15:40:00+05:30')}]}
      ]})};
    }}});
  vm.runInContext(gs,calendar);
  calendar.props_ = () => properties;
  calendar.capture_ = mode => {attempts.push(mode);return {status:'complete'};};
  const run = (day,time) => {stamp=Date.parse(day+'T'+time+':41+05:30');calendar.scheduledCapture();};
  run('2026-11-08','14:19'); run('2026-11-08','14:20');
  assert.deepEqual(attempts,['snapshot']); assert.equal(reads,0);
  run('2026-11-10','09:15');
  assert.deepEqual(attempts,['snapshot'],'annual NSE holiday stays closed during outage');
  run('2026-11-16','09:15');
  assert.deepEqual(attempts,['snapshot','snapshot'],'annual NSE calendar allows normal weekdays');
  run(sessionDate,'07:00'); const failedAttempts = reads;
  run(sessionDate,'07:01'); assert.equal(reads,failedAttempts);
  run(sessionDate,'14:14');
  assert.match(state.get('COLLECTOR_STATUS'),/Weekend session unverified/);
  available = true;
  run(sessionDate,'14:18'); assert.deepEqual(attempts,['snapshot','snapshot']);
  run(sessionDate,'14:20'); assert.deepEqual(attempts,['snapshot','snapshot','snapshot']);
  assert.equal(JSON.parse(state.get('MARKET_CALENDAR')).special[sessionDate].start_time,start);
  assert.equal(state.has('MARKET_CALENDAR_RETRY_AT'),false);
  run('2026-11-23','09:15');
  assert.deepEqual(attempts,['snapshot','snapshot','snapshot'],'a known weekday special cannot start at the normal open');
  available = false;
  run('2026-11-27','09:15');
  assert.deepEqual(attempts,['snapshot','snapshot','snapshot'],'saved later NSE closure survives broker outage');
  run('2026-11-28','14:20');
  assert.deepEqual(attempts,['snapshot','snapshot','snapshot'],'unknown weekend remains closed');
}

// Only the front listed expiry gets additional wings at the three paper decision clocks.
{
  let minute;
  const ctx = vm.createContext({Date:Clock, console});
  vm.runInContext(gs,ctx);
  ctx.minute_ = () => minute;
  ctx.quoteStrikes_ = () => new Set([100]);
  const captured = [];
  ctx.apiAll_ = requests => {captured.push(...requests.flatMap(r=>r.params.instrument_key.split(',')));return [];};
  const dates = ['2026-11-17','2026-11-23'];
  for (minute of [559,560,565,566,915]) {
    captured.length = 0;
    const records = dates.map(expiry=>({label:'NIFTY|'+expiry,retrieved_at:new Date(now).toISOString(),
      data:[100,200].map(strike=>({strike_price:strike,call_options:{instrument_key:expiry+':'+strike}}))}));
    ctx.enrichSnapshotInputs_({records,catalogue:{indices:{NIFTY:{option_expiries:dates}}}},now,false);
    assert.equal(captured.includes(dates[0]+':200'),[560,565,915].includes(minute));
    assert(!captured.includes(dates[1]+':200'));
  }
}

// Exercise the real enrichment loop independently of the capture fixtures.
{
  const batching = vm.createContext({Date: Clock, console, Utilities: {
    sleep(ms) { now += ms; }
  }});
  vm.runInContext(gs, batching);
  batching.scriptCache_ = () => ({get: () => JSON.stringify(candles())});
  batching.day_ = () => '2026-11-12';
  const initialNow = now;
  for (const count of [0, 1, 40, 41, 80, 81]) {
    const starts = [], groups = [], seen = [];
    batching.apiAll_ = requests => {
      starts.push(now); groups.push(requests.length);
      return requests.map(req => {
        const keys = req.params.instrument_key.split(',');
        assert(keys.length <= 500);
        assert(('https://api.upstox.com' + req.path + '?instrument_key=' + encodeURIComponent(req.params.instrument_key)).length < 2082);
        seen.push(...keys);
        return {...req, data: Object.fromEntries(keys.map(key => [key, {
          instrument_token: key, timestamp: new Date(now).toISOString(),
          depth: {buy: [{price: 1, quantity: 1}], sell: [{price: 2, quantity: 1}]}
        }]))};
      });
    };
    // Long keys force exactly one full-quote URL per key, covering group edges.
    const options = Array.from({length: count}, (_, i) => ({instrument_key: 'NSE_FO|' + i + 'x'.repeat(900), market_data: {ltp: 7}}));
    const report = {day: '2026-11-13', errors: [], records: [{
      label: 'NIFTY|2026-11-17', retrieved_at: new Date(now).toISOString(),
      data: options.map(o => ({call_options: o, put_options: o}))
    }]};
    batching.enrichSnapshotInputs_(report, now, true);
    assert.deepEqual(seen, options.map(o => o.instrument_key), 'every unique leg gets a full quote');
    assert(groups.every(size => size <= 40));
    assert.equal(groups.length, Math.ceil(count / 40));
    starts.slice(1).forEach((start, i) => assert(start - starts[i] >= 1000));
    options.forEach((o, i) => assert.equal(o.market_data.quote_timestamp, new Date(starts[Math.floor(i / 40)]).toISOString()));
  }
  now = initialNow;
}

async function main() {
  // Exercise capture_ itself through the data-only collector, with documented response shapes.
  const report = cloud.capture_('snapshot');
  const status = JSON.parse(properties.get('LAST_SNAPSHOT'));
  for (const field of ['fetch_seconds', 'archive_seconds']) {
    assert.equal(typeof status[field], 'number');
    assert.equal(status[field], report[field]);
  }
  assert.equal(report.status, 'complete', JSON.stringify(report.errors));
  assert(!Object.keys(report).some(k=>k.startsWith('forward_')));
  assert(!Object.keys(status).some(k=>k.startsWith('forward_')));
  assert.equal(report.risk_metrics, undefined);
  assert.equal(report.prior_daily_candles, undefined);
  const risk = {hv10:11, hv20:13, hv30:15, as_of:'2026-11-12', units:'annualized_percent'};
  web.window.VL_PUBLIC_STATE = {forecasts:{NIFTY:{closing_risk:risk},BANKNIFTY:{closing_risk:risk}}};
  assert.deepEqual(['2026-07-31','2026-08-03','2026-12-07'].map(cloud.regularCloseMinutes_),[930,940,945]);
  assert.equal(cloud.indexExpiryMillis_('2026-12-08'),Date.parse('2026-12-08T15:30:00+05:30'));
  assert.equal(cloud.indexExpiryMillis_('2026-11-17'),Date.parse('2026-11-17T15:40:00+05:30'));
  assert.deepEqual(Array.from(vm.runInContext("[SPECIAL_SESSIONS['2026-11-08'].startMins,SPECIAL_SESSIONS['2026-11-08'].endMins]",cloud)),[860,940]);
  const longChain = rawChain('NIFTY','2026-11-17');
  longChain.forEach((r,i) => {
    r.call_options.option_greeks.delta = (33-i)/34;
    r.put_options.option_greeks.delta = -(i+1)/34;
  });
  const selected = cloud.quoteStrikes_({label:'NIFTY|2026-11-17',data:longChain});
  assert(selected.has(25000) && selected.has(24500) && selected.has(25500));
  assert(selected.size < longChain.length);
  const enrich = cloud.enrichSnapshotInputs_;
  cloud.enrichSnapshotInputs_ = () => {throw new Error('fixture enrichment failure');};
  const rawOnly = cloud.capture_('snapshot');
  assert.equal(rawOnly.status,'complete'); assert.equal(rawOnly.enrichment_status,'failed');
  assert(!('forward_test_status' in rawOnly)); assert.equal(rawOnly.records[1].data[0].call_options.market_data.ltp,999);
  cloud.enrichSnapshotInputs_ = enrich;
  cloud.ContentService = {MimeType:{JSON:'json'},createTextOutput:text => ({setMimeType:() => JSON.parse(text)})};
  properties.set('LATEST_SNAPSHOT_FILE_ID',status.file_id);
  assert.equal(cloud.doGet({parameter:{since:status.retrieved_at}}).unchanged,true);
  properties.set('LATEST_SNAPSHOT_FILE_ID','different-file');
  assert.notEqual(cloud.doGet({parameter:{since:status.retrieved_at}}).unchanged,true);
  const anchorProps = new Map();
  const localProps = {getProperty:k=>anchorProps.get(k), setProperty:(k,v)=>anchorProps.set(k,v)};
  const anchorRec = {label:'NIFTY|2026-11-17',data:rawChain('NIFTY','2026-11-17')};
  const anchorReport = {day:'2026-11-13',records:[anchorRec],pending:[],errors:[]};
  cloud.attachStraddleAnchors_(anchorReport,localProps);
  assert.equal(anchorRec.straddle_anchor_strike,25000);
  const savedAnchors = anchorProps.get('SESSION_STRADDLE_ANCHORS');
  anchorRec.data = rawChain('NIFTY','2026-11-17',27500);
  anchorRec.data.push(rawChain('NIFTY','2026-11-17',25000)[16]);
  anchorRec.data.forEach(row=>{row.underlying_spot_price=27500;});
  anchorReport.errors.push({reason:'partial'});
  cloud.attachStraddleAnchors_(anchorReport,localProps);
  assert.equal(anchorProps.get('SESSION_STRADDLE_ANCHORS'),savedAnchors);
  assert.equal(anchorRec.straddle_anchor_strike,25000);
  assert(cloud.quoteStrikes_(anchorRec,null).has(25000));
  anchorReport.day = '2026-11-16';anchorReport.errors=[];
  cloud.attachStraddleAnchors_(anchorReport,localProps);
  assert.equal(anchorRec.straddle_anchor_strike,27500);
  assert.equal(report.records[1].straddle_history, undefined);
  const priorSave = cloud.save_;
  cloud.save_ = () => { throw Error('archive write failed'); };
  assert.throws(() => cloud.capture_('snapshot'), /archive write failed/);
  cloud.save_ = priorSave;
  const firstOption = report.records[1].data.find(r => r.strike_price === 25000).call_options.market_data;
  assert.equal(firstOption.quote_timestamp, new Date(now - 1000).toISOString());
  assert.notEqual(firstOption.ltp, 999, 'timestamp must travel with its own book');
  assert.equal(report.records[1].data.find(r => r.strike_price === 25000).call_options.chain_market_data.ltp, 999);
  for (const failure of ['stale', 'no-times', 'no-history']) {
    mode = failure; cache.clear();
    const invalid = cloud.capture_('snapshot');
    assert(!('forward_test_ledger' in invalid));
    assert.equal(invalid.status,'complete','Keep raw observations even when execution inputs are missing');
  }
  mode = 'valid';
  const display = {NIFTY:[{d:'2026-11-13',iv:20,closingEstimate:{iv:21}}],BANKNIFTY:[]};
  assert.equal(web.applyClosingRisk(display,{'2026-11-13':{NIFTY:[26000,11,15,17,16]}}),true);
  assert.equal(display.NIFTY[0].iv,20);
  assert.equal(display.NIFTY[0].closingEstimate.iv,21);
  assert.equal(display.NIFTY[0].h30,17);
  assert.equal(display.NIFTY[0].hvCloseDate,'2026-11-13');
  // Replay two sessions with new ATM, chain prices and expiry membership.
  report.capture_integrity = {age_seconds: 0};
  // A missed historical slot must not reject a later complete, fresh snapshot.
  report.capture_integrity.snapshot_gap = '2026-11-13|124 to 2026-11-13|126 detected earlier';
  assert.equal(web.isFreshValidatedCloudSnapshot(report), true);
  for (const invalid of [
    {...report, status:'partial'},
    {...report, nfo_session_validated:false},
    {...report, retrieved_at:new Date(now-121000).toISOString()},
    {...report, capture_integrity:{...report.capture_integrity,age_seconds:121}},
  ]) assert.equal(web.isFreshValidatedCloudSnapshot(invalid),false,'Current validity gates remain enforced');
  web.REAL_HISTORICAL_1Y = {NIFTY: [{d:'2026-11-12', c:24900, iv:17, h10:14, h30:15}], BANKNIFTY: []};
  const oldHistory = clone(web.REAL_HISTORICAL_1Y.NIFTY[0]);
  web.fetch = async () => ({ok: true, json: async () => report});
  assert.equal(await web.syncLiveCloudSnapshot(true), true);
  assert.deepEqual(clone(web.REAL_HISTORICAL_1Y.NIFTY[0]), oldHistory);
  const previous = web.marketCatalog.NIFTY[0];
  assert.equal(previous.snapshotId, report.retrieved_at);
  assert(previous.b76AtmIv > 19 && previous.b76AtmIv < 21);
  assert.equal(previous.brokerAtmIv, 20);
  assert.equal(web.REAL_HISTORICAL_1Y.NIFTY.at(-1).iv, 20);
  assert.equal(web.REAL_HISTORICAL_1Y.NIFTY.at(-1).ivSource, 'Upstox option_greeks.iv');
  // Normalize and render a session that moves away from its initial ATM, loses a
  // leg's contemporaneous book, then recovers. The contract identity stays fixed.
  {
    const desk = vm.createContext({...web, window:{innerWidth:900}, activeScMode:'intraday', straddleChart:null});
    for (const name of ['chartNumber','observedIv','chartOpts','scenarioLegs','renderTab4Straddle','formatLiveNumber','currentRow']) {
      vm.runInContext(browserFunction(name),desk);
    }
    vm.runInContext(fs.readFileSync(path.join(root,'scripts/options-charts.js'),'utf8'),desk);
    const els = {};
    desk.document = {getElementById: id => els[id] ||= {value:id === 'stressStructureSelect' ? 'SHORT_STRADDLE' : '1'}};
    desk.updateOrCreateChart = (_,ctx,type,data) => ({data});
    desk.fmtInr = String;
    const previousCatalog = web.marketCatalog;
    try {
      for (const [minute, spot, missing] of [[1,25050,false],[2,25050,true],[3,25000,false]]) {
        const moved = clone(report);
        const stamp = new Date(now + minute * 60000).toISOString();
        moved.retrieved_at = stamp;
        for (const rec of moved.records) {
          rec.retrieved_at = stamp;
          if (Array.isArray(rec.data)) {
            rec.chain_retrieved_at = stamp;
            for (const row of rec.data) for (const side of ['call_options','put_options']) {
              const md = row[side].market_data;
              md.quote_timestamp = stamp;
              if (row.strike_price === 25000 && side === 'put_options' && missing) md.quote_timestamp = new Date(Date.parse(stamp)-65000).toISOString();
            }
          } else for (const q of Object.values(rec.data)) {
            q.timestamp = stamp;
            if (q.instrument_token === 'NSE_INDEX|Nifty 50') q.last_price = spot;
          }
        }
        web.marketCatalog = web.normalizeLiveSnapshot(moved).catalog;
        const r = web.marketCatalog.NIFTY[0];
        assert.equal(r.atm,spot); assert.equal(r.straddleStrike,25000);
        const mark = r.intraday.at(-1);
        assert.equal(mark.atm,25000);
        assert.equal(mark.strad === null,missing);
        desk.marketCatalog = web.marketCatalog;
        desk.renderTab4Straddle();
        assert(desk.straddleChart.data.datasets[0].label.includes('book midpoints'));
        assert.equal(desk.straddleChart.data.datasets[0].data.length,minute + 1);
        assert.equal(desk.straddleChart.data.datasets[0].data.at(-1).y,mark.strad);
        assert(els.straddleSummaryBadge.textContent.includes('25,000 CE + PE'));
      }
    } finally {web.marketCatalog = previousCatalog;}
  }
  const missingIv = clone(report);
  for (const rec of missingIv.records) if (Array.isArray(rec.data)) {
    for (const row of rec.data) for (const side of ['call_options','put_options']) row[side].option_greeks.iv = null;
  }
  const noBroker = web.normalizeLiveSnapshot(missingIv);
  assert.equal(noBroker.catalog.NIFTY[0].brokerAtmIv, null);
  assert.equal(noBroker.catalog.NIFTY[0].intraday.at(-1).iv, null, 'Model IV must not become a historical broker capture');
  assert.equal(noBroker.history.NIFTY.at(-1).iv,20,'Retain an earlier same-day real capture with its original timestamp');
  const savedObserved = web.REAL_HISTORICAL_1Y;
  web.REAL_HISTORICAL_1Y={NIFTY:[],BANKNIFTY:[]};
  assert.equal(web.normalizeLiveSnapshot(missingIv).history.NIFTY[0].iv,null);
  web.REAL_HISTORICAL_1Y=savedObserved;
  assert.equal(await web.syncLiveCloudSnapshot(true), false, 'duplicate snapshot');

  now = Date.parse('2026-11-16T04:30:00Z');
  const next = clone(report);
  next.day = '2026-11-16'; next.retrieved_at = new Date(now).toISOString();
  delete next.catalogue; // Also supports providers sending authoritative records only.
  for (const rec of next.records) {
    rec.retrieved_at = next.retrieved_at;
    if (rec.chain_retrieved_at) rec.chain_retrieved_at = next.retrieved_at;
    if (!Array.isArray(rec.data)) { Object.values(rec.data).forEach(q => {q.last_price += 100; q.timestamp = next.retrieved_at;}); continue; }
    const exp = rec.label.startsWith('NIFTY|') ? '2026-11-24' : '2026-11-30';
    rec.label = rec.label.split('|')[0] + '|' + exp;
    for (const row of rec.data) {
      row.expiry = exp; row.underlying_spot_price += 100;
      for (const side of ['call_options','put_options']) {
        row[side].market_data.ltp += 10;
        delete row[side].market_data.quote_timestamp;
      }
    }
  }
  web.fetch = async () => ({ok:true, json: async () => next});
  assert.equal(await web.syncLiveCloudSnapshot(true), true);
  const current = web.marketCatalog.NIFTY[0];
  assert.equal(current.expiry, '2026-11-24');
  assert.notEqual(current.atm, previous.atm);
  assert.notEqual(current.dte, previous.dte);
  assert.notEqual(current.chain[0].cLtp, previous.chain[0].cLtp);
  assert.equal(current.b76AtmIv, null);
  assert(current.chain.every(n => n.b76CallIv === null));
  assert(Number.isNaN(current.rev25) && Number.isNaN(current.ivPct));
  assert.equal(web.REAL_HISTORICAL_1Y.NIFTY.length, 3);
  assert.deepEqual(clone(web.REAL_HISTORICAL_1Y.NIFTY[0]), oldHistory);

  const missingOi=clone(next);
  const noOiRow=missingOi.records.find(r=>r.label.startsWith('NIFTY|')).data[0];
  delete noOiRow.call_options.market_data.oi;
  assert.equal(web.normalizeLiveSnapshot(missingOi).catalog.NIFTY[0].chain.find(n=>n.k===noOiRow.strike_price).cOi,null,'Missing OI must not become a recorded zero');

  const savedCatalog = web.marketCatalog, savedHistory = web.REAL_HISTORICAL_1Y;
  const bad = clone(next); bad.retrieved_at = new Date(now + 1000).toISOString();
  bad.records = bad.records.filter(r => !r.label.startsWith('BANKNIFTY|'));
  web.fetch = async () => ({ok:true, json: async () => bad});
  assert.equal(await web.syncLiveCloudSnapshot(true), false);
  assert.equal(web.marketCatalog, savedCatalog, 'invalid snapshot must not partially mutate catalog');
  assert.equal(web.REAL_HISTORICAL_1Y, savedHistory);
  assert.equal(web.lastSyncedRetrievedAt, next.retrieved_at, 'failed payload must remain retryable');

  // Two simultaneous callers share one fetch, and older responses cannot roll state back.
  let fetches = 0;
  web.fetch = async () => {fetches++; return {ok:true, json:async () => next};};
  await Promise.all([web.syncLiveCloudSnapshot(true), web.syncLiveCloudSnapshot(true)]);
  assert.equal(fetches, 1);
  web.fetch = async () => ({ok:true, json:async () => report});
  assert.equal(await web.syncLiveCloudSnapshot(true), false);
  assert.equal(web.marketCatalog, savedCatalog);
  // A late, complete capture advances research views while execution remains withheld.
  web.lastSyncedRetrievedAt = report.retrieved_at;
  now += 10*60000;
  const delayed = {...next,capture_integrity:{age_seconds:600,status:'snapshot runtime admission limit reached'}};
  let marks = 0;
  web.window.markForwardPositionsFromPayload = () => { marks++; };
  web.fetch = async () => ({ok:true,json:async () => delayed});
  assert.equal(await web.syncLiveCloudSnapshot(true), true);
  assert.equal(web.lastSyncedRetrievedAt,next.retrieved_at);
  assert.equal(web.liveSnapshotAccepted,false); assert.equal(marks,0);
  assert.match(web.message,/runtime limit.*10 min/);
  web.fetch = async () => ({ok:true,json:async () => ({unchanged:true,retrieved_at:next.retrieved_at,capture_integrity:delayed.capture_integrity})});
  assert.equal(await web.syncLiveCloudSnapshot(true),false);
  assert.match(web.message,/runtime limit/);
  const historicalRow = {d:'2026-11-12',c:24999,iv:18.6,expiry:'2026-11-17',atm:25000,h10:13,
    snapshotId:'2026-11-12T09:00:00Z',archiveFile:'2026-11-12_snapshot_143000_complete.json.gz',ivSource:'Upstox option_greeks.iv'};
  assert.equal(web.combineDailyCaptures([], [historicalRow], '2026-11-13')[0].iv,18.6);
  assert.equal(web.combineDailyCaptures([], [{...historicalRow,expiry:'2026-11-12'},{...historicalRow,ivSource:'model'}], '2026-11-13').length,0);
  const lateJoin = clone(delayed);
  const rec = lateJoin.records.find(r => r.label.startsWith('NIFTY|'));
  rec.straddle_anchor_strike = rec.data[12].strike_price;
  const time = Date.parse(next.retrieved_at)-60000;
  rec.straddle_history = [[time,time,18,99,time,time],[time+1000,time+1000,18,999,time-200000,time+1000]];
  const replay = web.normalizeLiveSnapshot(lateJoin).catalog.NIFTY[0];
  assert.equal(replay.straddleStrike,rec.straddle_anchor_strike);
  assert.equal(replay.intraday.find(p => Date.parse(p.markAt)===time).strad,99);
  assert.equal(replay.intraday.find(p => Date.parse(p.markAt)===time+1000).strad,null);
  // Collector may fall back to the last cached dual mark when today's book is missing.
  rec.dual_straddle = {markAt:time,rollAtm:25000,rollStrad:88,anchorK:rec.straddle_anchor_strike,anchorStrad:99};
  rec.dual_straddle_history = [rec.dual_straddle];
  let dualReplay = web.normalizeLiveSnapshot(lateJoin).catalog.NIFTY[0].dualIntraday;
  assert.equal(dualReplay.length,1);
  assert.equal(Date.parse(dualReplay[0].markAt),time,'Cached marks keep their original source time');
  assert(!dualReplay.some(p=>Date.parse(p.markAt)===Date.parse(next.retrieved_at)), 'No fabricated current mark');
  rec.dual_straddle.markAt = time - 86400000;
  rec.dual_straddle_history = [];
  assert.equal(web.normalizeLiveSnapshot(lateJoin).catalog.NIFTY[0].dualIntraday.length,0,'Prior session cache is rejected');
  rec.dual_straddle.markAt = Date.parse(next.retrieved_at) + 60000;
  assert.equal(web.normalizeLiveSnapshot(lateJoin).catalog.NIFTY[0].dualIntraday.length,0,'Future cache is rejected');
  console.log('Live connection regressions passed: collector contract, event-time gates, HV provenance, atomic refresh, session/expiry transitions, deduplication and parity.');
}
main().catch(error => {console.error(error); process.exitCode = 1;});
