// Offline regression of the actual browser functions and Apps Script capture path.
// Broker and Google services are fixtures; no credentials, orders, or Drive writes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const gs = fs.readFileSync(path.join(root, 'volatility_lab/cloud/apps_script/Code.gs'), 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
let now = Date.parse('2026-11-13T04:30:00Z');
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
  CLOUD_EXEC_URL: 'https://example.invalid', updateMarketClock: () => ({isOpen: true}),
  populateExpiryDropdown() {}, renderActiveOptionsTab() {}, renderForwardWorkspace() {},
  setLiveCaptureIntegrity(message) { web.message = message; }
});
vm.runInContext(fs.readFileSync(path.join(__dirname,'trading-sessions.js'),'utf8'),web);
for (const name of ['normPdf', 'normCdf', 'computeBlack76ParityForward', 'black76Greeks', 'black76Price',
  'black76IvDec', 'black76Iv', 'bsmIvDec', 'bsmGreeks', 'currentPricingInputs', 'relativeBookSpread', 'selectOptionPricing', 'enrichItemWithBlack76', 'liveNumber', 'liveTimestamp', 'liveBookValid',
  'liveDeltaIv', 'capturedDeltaIv','validCloudCapture','showCaptureState','combineDailyCaptures','applyClosingRisk','normalizeLiveSnapshot', 'currentRow', 'isFreshValidatedCloudSnapshot', 'syncLiveCloudSnapshot']) {
  vm.runInContext(browserFunction(name), web);
}
web.pricingInputs = {schema:1,rate:{model_rate:.066,source:'https://www.rbi.org.in/',sha256:'fixture',checked_at:new Date(now).toISOString()},
  dividends:Object.fromEntries(['NIFTY','BANKNIFTY'].map((s,i)=>[s,{model_yield:.0142,as_of:'2026-10-30',sha256:'fixture',source:'https://www.niftyindices.com/Factsheet/'+(i?'ind_nifty_bank.pdf':'ind_nifty50.pdf')}]))};
// Compile every inline script, including the large generated data section.
for (const [, attributes, script] of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)) {
  if (/application\/json/.test(attributes)) JSON.parse(script);
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
    close *= Math.exp(rows.length % 2 ? 0.003 : -0.003);
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
cloud.loadForwardLedger_ = () => cloud.cleanSlateForwardLedger_();
cloud.saveForwardLedger_ = () => 'fixture-ledger';
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

// Exercise both scheduler admission checks and the EOD reserve with an existing budget.
{
  const settings = new Map([['SCHEDULE_MODE', 'intraday']]);
  const props = {getProperty: key => settings.get(key) || null,
    setProperty: (key, value) => settings.set(key, value), deleteProperty: key => settings.delete(key)};
  const initialWindow = {windowStartMs: now - 60000, windowEndMs: now + 86400000, ms: 0};
  const budgetAt = minutes => settings.set('RUNTIME_BUDGET', JSON.stringify({...initialWindow, ms: minutes * 60000}));
  let minute = 600, lockedUsage = null;
  const attempts = [];
  const scheduler = vm.createContext({Date: Clock, Utilities: {formatDate: () => '5'},
    LockService: {getScriptLock: () => ({tryLock() {
      if (lockedUsage !== null) budgetAt(lockedUsage);
      return true;
    }, releaseLock() {}})}});
  vm.runInContext(gs, scheduler);
  scheduler.props_ = () => props;
  scheduler.day_ = () => '2026-11-13';
  scheduler.minute_ = () => minute;
  scheduler.marketClosed_ = () => false;
  settings.set('MARKET_CALENDAR',JSON.stringify({year:'2026',checked:'2026-11-13',closed:[],special:{}}));
  scheduler.capture_ = mode => {attempts.push(mode); return {status: 'complete'};};
  budgetAt(80);
  scheduler.scheduledCapture();
  assert.deepEqual(attempts, []);
  assert.deepEqual(JSON.parse(settings.get('RUNTIME_BUDGET')), {...initialWindow, ms: 80 * 60000});
  budgetAt(79); lockedUsage = 80;
  scheduler.scheduledCapture();
  assert.deepEqual(attempts, [], 'a concurrent capture can consume the remaining snapshot allowance');
  lockedUsage = null;
  budgetAt(79.99);
  scheduler.scheduledCapture();
  minute = 944; // The derivatives market closes at 15:40 on this date.
  budgetAt(84.99);
  scheduler.scheduledCapture();
  assert.deepEqual(attempts,['snapshot']);
  minute = 945;
  scheduler.scheduledCapture();
  budgetAt(85); settings.delete('SUCCESS_EOD'); settings.delete('ATTEMPT_eod');
  scheduler.scheduledCapture();
  assert.deepEqual(attempts, ['snapshot', 'eod']);
  const policy = () => scheduler.runtimePolicy_(props);
  for (const value of ['200', 'invalid', '-1', '0', '2.5']) {
    settings.set('COLLECTOR_RUNTIME_MINUTES', value);
    assert.equal(policy().total_minutes, 85);
  }
  settings.set('COLLECTOR_RUNTIME_MINUTES', '60');
  assert.equal(policy().snapshot_minutes, 55);
  settings.set('COLLECTOR_RUNTIME_MINUTES', '3');
  assert.equal(policy().snapshot_minutes, 0);
  settings.set('COLLECTOR_QUOTA_TIER', 'verified_workspace');
  settings.set('COLLECTOR_RUNTIME_MINUTES', '360');
  assert.equal(policy().snapshot_minutes, 350);
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
  const properties = {getProperty:k=>state.get(k) || null, setProperty:(k,v)=>state.set(k,v), deleteProperty:k=>state.delete(k)};
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
  calendar.reconcilePriorSession = () => {};
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

// Exercise the real enrichment loop independently of the capture fixtures.
{
  const batching = vm.createContext({Date: Clock, console, Utilities: {
    sleep(ms) { now += ms; }
  }});
  vm.runInContext(gs, batching);
  batching.scriptCache_ = () => ({get: () => JSON.stringify({as_of: '2026-11-12', units: 'annualized_percent', hv10: 10, hv30: 10})});
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
    batching.enrichSnapshotInputs_(report, now, null, true);
    assert.deepEqual(seen, options.map(o => o.instrument_key), 'every unique leg gets a full quote');
    assert(groups.every(size => size <= 40));
    assert.equal(groups.length, Math.ceil(count / 40));
    starts.slice(1).forEach((start, i) => assert(start - starts[i] >= 1000));
    options.forEach((o, i) => assert.equal(o.market_data.quote_timestamp, new Date(starts[Math.floor(i / 40)]).toISOString()));
  }
  now = initialNow;
}

// Real load/save path, including exact prior bytes, retries and fresh evaluation state.
{
  const ledgerProperties = new Map(), files = new Map();
  let reads = 0, scans = 0, failPrimary = false, failBackup = false;
  const ledgerCloud = vm.createContext({Date: Clock, MimeType: {PLAIN_TEXT: 'text/plain'}});
  vm.runInContext(gs, ledgerCloud);
  const makeFile = (name, content) => {
    const file = {content, getId: () => name, isTrashed: () => false,
      getBlob: () => ({getDataAsString: () => {reads++; return file.content;}}),
      setContent(value) {
        if ((name.endsWith('.json') && failPrimary) || (name.endsWith('.bak') && failBackup)) throw Error('write failed');
        file.content = value;
      }};
    files.set(name, file); return file;
  };
  ledgerCloud.props_ = () => ({getProperty: k => ledgerProperties.get(k), setProperty: (k, v) => ledgerProperties.set(k, v)});
  ledgerCloud.scriptCache_ = () => null;
  ledgerCloud.DriveApp = {getFileById: id => files.get(id)};
  ledgerCloud.folder_ = () => ({
    getFilesByName(name) {scans++; let remaining = files.has(name); return {
      hasNext: () => remaining, next: () => {remaining = false; return files.get(name);}
    };},
    createFile: makeFile
  });
  const original = JSON.stringify(ledgerCloud.cleanSlateForwardLedger_(), null, 4);
  const primary = makeFile('forward_test_ledger.json', original);
  const state = {};
  const ledger = ledgerCloud.loadForwardLedger_(state);
  ledgerCloud.saveForwardLedger_(ledger, state);
  assert.equal(reads, 1, 'save reuses the validated load under the same lock');
  const backup = files.get('forward_test_ledger.bak');
  assert.equal(backup.content, original, 'backup preserves exact previous bytes');
  const firstSaved = primary.content, firstScans = scans;
  ledger.intraday_mtm_series.push({session_date: '2026-11-13', t: '10:01'});
  ledgerCloud.saveForwardLedger_(ledger, state);
  assert.equal(reads, 1); assert.equal(scans, firstScans);
  assert.equal(backup.content, firstSaved);
  failBackup = true;
  const beforeFailure = primary.content;
  assert.throws(() => ledgerCloud.saveForwardLedger_(ledger, state), /write failed/);
  assert.equal(primary.content, beforeFailure); assert.equal(state.loaded, false);
  failBackup = false; failPrimary = true;
  assert.throws(() => ledgerCloud.saveForwardLedger_(ledger, state), /write failed/);
  assert.equal(backup.content, beforeFailure); assert.equal(state.loaded, false);
  failPrimary = false;
  ledgerCloud.saveForwardLedger_(ledger, state);
  const beforeReload = reads;
  ledgerCloud.loadForwardLedger_({});
  assert.equal(reads, beforeReload + 1, 'new evaluation always reads Drive');
  const priorReads = reads;
  ledgerCloud.saveForwardLedger_(ledger);
  assert.equal(reads, priorReads + 1, 'legacy save validates the current primary');
  primary.content = '{';
  assert.throws(() => ledgerCloud.saveForwardLedger_(ledger), /Invalid forward ledger/);
}
async function main() {
  // Exercise capture_ itself through the real evaluator, with documented response shapes.
  const report = cloud.capture_('snapshot');
  const status = JSON.parse(properties.get('LAST_SNAPSHOT'));
  for (const field of ['fetch_seconds', 'evaluation_seconds', 'archive_seconds']) {
    assert.equal(typeof status[field], 'number');
    assert.equal(status[field], report[field]);
  }
  assert.equal(report.status, 'complete', JSON.stringify(report.errors));
  assert(report.forward_test_ledger.open_positions.length > 0, 'valid collector inputs must reach evaluator: ' + JSON.stringify({rejections:report.forward_rejections, metrics: report.risk_metrics, mkt:cloud.getNseMarketStatusIST(new Clock())}));
  assert.equal(report.risk_metrics.NIFTY.as_of, '2026-11-12');
  const expectedHv = Math.sqrt(30 / 29) * 0.003 * Math.sqrt(245) * 100;
  assert(Math.abs(report.risk_metrics.NIFTY.hv30 - expectedHv) < 1e-10);
  assert.deepEqual(['2026-07-31','2026-08-03','2026-12-07'].map(cloud.regularCloseMinutes_),[930,940,945]);
  assert.equal(cloud.indexExpiryMillis_('2026-12-08'),Date.parse('2026-12-08T15:30:00+05:30'));
  assert.equal(cloud.indexExpiryMillis_('2026-11-17'),Date.parse('2026-11-17T15:40:00+05:30'));
  assert.deepEqual(Array.from(vm.runInContext("[SPECIAL_SESSIONS['2026-11-08'].startMins,SPECIAL_SESSIONS['2026-11-08'].endMins]",cloud)),[860,940]);
  const longChain = rawChain('NIFTY','2026-11-17');
  longChain.forEach((r,i) => {
    r.call_options.option_greeks.delta = (33-i)/34;
    r.put_options.option_greeks.delta = -(i+1)/34;
  });
  const selected = cloud.quoteStrikes_({label:'NIFTY|2026-11-17',data:longChain},
    {open_positions:[{sym:'NIFTY',exp:'2026-11-17',legs:[{strike:24200}]}]});
  assert(selected.has(24200)); assert(selected.has(25000)); assert(selected.size < longChain.length);
  for (const strat of ['S1','S2','S3','S4','S5','S6']) {
    cloud.buildStrategyLegs_('NIFTY',25000,strat).forEach(l => assert(selected.has(l.strike)));
  }
  const loadLedger = cloud.loadForwardLedger_;
  cloud.loadForwardLedger_ = () => {throw new Error('fixture ledger unavailable');};
  const failedLedger = cloud.capture_('snapshot');
  assert.equal(failedLedger.status,'complete');
  assert.equal(failedLedger.forward_test_status,'failed');
  assert.equal(failedLedger.records[1].data.length,33);
  cloud.loadForwardLedger_ = loadLedger;
  const enrich = cloud.enrichSnapshotInputs_;
  cloud.enrichSnapshotInputs_ = () => {throw new Error('fixture enrichment failure');};
  const rawOnly = cloud.capture_('snapshot');
  assert.equal(rawOnly.status,'complete'); assert.equal(rawOnly.enrichment_status,'failed');
  assert.equal(rawOnly.forward_test_status,'failed'); assert.equal(rawOnly.records[1].data[0].call_options.market_data.ltp,999);
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
  const priorCache = cloud.scriptCache_, priorSave = cloud.save_;
  const histories = {};
  let writes = 0;
  cloud.scriptCache_ = () => ({get:()=>null,put(){},remove(){},getAll:()=>histories,
    putAll(values){writes++;Object.assign(histories,values);}});
  const archived = cloud.capture_('snapshot');
  const tuple = archived.records[1].straddle_history[0];
  assert.equal(tuple.length,6); assert.equal(tuple[2],20);
  assert(tuple[3]>0); assert.equal(tuple[4],now-1000); assert.equal(tuple[5],now-1000);
  const previousNow = now;
  now += 60000;
  cloud.save_ = () => {throw Error('archive write failed');};
  assert.throws(()=>cloud.capture_('snapshot'),/archive write failed/);
  assert.equal(writes,1);
  cloud.save_ = priorSave;
  now += 60000;
  mode='no-times';
  const gap = cloud.capture_('snapshot').records[1].straddle_history;
  assert.equal(gap.length,2); assert.equal(gap[1][2],20);
  assert.deepEqual(Array.from(gap[1].slice(3)),[null,null,null]);
  assert.equal(gap[1][1]-gap[0][1],120000);
  assert(Object.values(histories).every(v => Buffer.byteLength(v)<100000));
  mode='valid'; now=previousNow; cloud.scriptCache_=priorCache;
  // Current-day rows survive a partial snapshot, then roll over once per symbol.
  const byKey = new Map(), indexProps = {getProperty:k=>byKey.get(k),setProperty:(k,v)=>byKey.set(k,v),
    getProperties:()=>Object.fromEntries(byKey),getKeys:()=>Array.from(byKey.keys()),deleteProperty:k=>byKey.delete(k)};
  const basenames = ['2026-11-13_snapshot_100000_complete.json.gz','2026-11-16_snapshot_100000_complete.json.gz'];
  const indexed = clone(report);
  const near = indexed.records.find(r=>r.label.startsWith('NIFTY|'));
  indexed.records.push({...near,label:'NIFTY|2026-11-13'}, {...near,label:'NIFTY|2026-12-01'});
  let currentRows = cloud.updateDailyIndex_(indexProps,indexed,basenames[0]);
  assert.equal(currentRows.NIFTY.expiry,'2026-11-17'); assert.equal(currentRows.NIFTY.atm,25000);
  assert.equal(currentRows.NIFTY.h30,report.risk_metrics.NIFTY.hv30);
  assert.equal(currentRows.NIFTY.archiveFile,basenames[0]);
  byKey.set('LAST_SNAPSHOT',JSON.stringify({day:indexed.day,daily_rows:currentRows}));
  near.data.find(r=>r.strike_price===25000).call_options.option_greeks.iv=null;
  assert.equal(cloud.dailyIvRow_(indexed,'NIFTY',basenames[0]),null,'do not switch expiry or strike for missing IV');
  near.chain_retrieved_at='bogus';
  assert.equal(cloud.dailyIvRow_(indexed,'NIFTY',basenames[0]),null);
  indexed.status='partial';
  assert.deepEqual(clone(cloud.updateDailyIndex_(indexProps,indexed,basenames[0])),clone(currentRows));
  byKey.set('DAILY_IV_NIFTY_2024-01_A','{}');
  indexed.day='2026-11-16';
  const todayRows=cloud.updateDailyIndex_(indexProps,indexed,basenames[1]);
  byKey.set('LAST_SNAPSHOT',JSON.stringify({day:indexed.day,daily_rows:todayRows}));
  assert.equal(byKey.has('DAILY_IV_NIFTY_2024-01_A'),false);
  const indexedHistory=cloud.publishedDailyHistory_(indexProps,indexed.day);
  assert.deepEqual(clone(indexedHistory.NIFTY),[clone(currentRows.NIFTY)]);
  const partition=Object.fromEntries(Array.from({length:16},(_,i)=>['2026-11-'+(16+i),currentRows.BANKNIFTY]));
  assert(Buffer.byteLength(JSON.stringify(partition))<9000);
  const firstOption = report.records[1].data.find(r => r.strike_price === 25000).call_options.market_data;
  assert.equal(firstOption.quote_timestamp, new Date(now - 1000).toISOString());
  assert.notEqual(firstOption.ltp, 999, 'timestamp must travel with its own book');
  assert.equal(report.records[1].data.find(r => r.strike_price === 25000).call_options.chain_market_data.ltp, 999);
  for (const failure of ['stale', 'no-times', 'no-history']) {
    mode = failure; cache.clear();
    const invalid = cloud.capture_('snapshot');
    if (failure !== 'no-history') assert.equal(invalid.forward_test_ledger.open_positions.length, 0);
    else assert(!invalid.forward_test_ledger.open_positions.some(p => ['S1','S2','S3','S5'].includes(p.strat)));
    assert(invalid.forward_rejections.length > 0, failure + ' needs a rejection reason');
  }
  mode = 'valid';
  const riskRows = candles();
  assert.throws(() => cloud.snapshotRiskMetrics_(riskRows.concat([riskRows[1]]), '2026-11-13', 'key'), /duplicate/);
  assert.throws(() => cloud.snapshotRiskMetrics_(riskRows.slice(0, 10), '2026-11-13', 'key'), /31/);

  // The same-day index close is used for reporting only after EOD, while live signals use prior closes.
  const finalCandle = ['2026-11-13T00:00:00+05:30',26000,26000,26000,26000,0,0];
  const closeInput = riskRows.filter(r => r[0].slice(0,10) < '2026-11-13').concat([finalCandle]);
  assert.equal(cloud.snapshotRiskMetrics_(closeInput,'2026-11-13','NSE_INDEX|Nifty 50').as_of,'2026-11-12');
  const closing = cloud.snapshotRiskMetrics_(closeInput,'2026-11-13','NSE_INDEX|Nifty 50',true);
  assert.equal(closing.as_of,'2026-11-13');
  assert.equal(closing.close,26000);
  assert.notEqual(closing.hv30,report.risk_metrics.NIFTY.hv30);
  assert.throws(() => cloud.snapshotRiskMetrics_(closeInput.slice(0,-1),'2026-11-13','key',true),/stale/);
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
  console.log('Live connection regressions passed: collector contract, event-time gates, HV provenance, atomic refresh, session/expiry transitions, deduplication and parity.');
}
main().catch(error => {console.error(error); process.exitCode = 1;});
