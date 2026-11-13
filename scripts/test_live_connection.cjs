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
  activeSymbol: 'NIFTY', activeExpiryIdx: 0, lastSyncedRetrievedAt: '', liveSnapshotRequest: null,
  CLOUD_EXEC_URL: 'https://example.invalid', updateMarketClock: () => ({isOpen: true}),
  populateExpiryDropdown() {}, renderActiveOptionsTab() {}, renderForwardWorkspace() {},
  setLiveCaptureIntegrity(message) { web.message = message; }
});
for (const name of ['normPdf', 'normCdf', 'computeBlack76ParityForward', 'black76Greeks', 'black76Price',
  'black76IvDec', 'black76Iv', 'bsmIvDec', 'bsmGreeks', 'currentPricingInputs', 'relativeBookSpread', 'selectOptionPricing', 'enrichItemWithBlack76', 'liveNumber', 'liveTimestamp', 'liveBookValid',
  'liveDeltaIv', 'normalizeLiveSnapshot', 'currentRow', 'isFreshValidatedCloudSnapshot', 'syncLiveCloudSnapshot']) {
  vm.runInContext(browserFunction(name), web);
}
web.pricingInputs = {schema:1,rate:{model_rate:.066,source:'https://www.rbi.org.in/',sha256:'fixture',checked_at:new Date(now).toISOString()},
  dividends:Object.fromEntries(['NIFTY','BANKNIFTY'].map((s,i)=>[s,{model_yield:.0142,as_of:'2026-10-30',sha256:'fixture',source:'https://www.niftyindices.com/Factsheet/'+(i?'ind_nifty_bank.pdf':'ind_nifty50.pdf')}]))};
// Compile every inline script, including the large generated data section.
for (const [, script] of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) new vm.Script(script);
assert.equal(html, fs.readFileSync(path.join(root, 'docs/index.html'), 'utf8'));
const webAppUrl = html.match(/var CLOUD_EXEC_URL = '([^']+)'/)[1];
for (const filename of ['manual_download_session.py', 'google_archive_sync.py']) {
  const client = fs.readFileSync(path.join(root, 'volatility_lab', filename), 'utf8');
  assert(client.includes(webAppUrl), filename + ' and the website must use the same deployment');
}

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
cloud.marketWindow_ = () => ({closed: false, startMins: 555, endMins: 931});
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

async function main() {
  // Exercise capture_ itself through the real evaluator, with documented response shapes.
  const report = cloud.capture_('snapshot');
  assert.equal(report.status, 'complete', JSON.stringify(report.errors));
  assert(report.forward_test_ledger.open_positions.length > 0, 'valid collector inputs must reach evaluator: ' + JSON.stringify({rejections:report.forward_rejections, metrics: report.risk_metrics, mkt:cloud.getNseMarketStatusIST(new Clock())}));
  assert.equal(report.risk_metrics.NIFTY.as_of, '2026-11-12');
  const expectedHv = Math.sqrt(30 / 29) * 0.003 * Math.sqrt(245) * 100;
  assert(Math.abs(report.risk_metrics.NIFTY.hv30 - expectedHv) < 1e-10);
  const firstOption = report.records[1].data[0].call_options.market_data;
  assert.equal(firstOption.quote_timestamp, new Date(now - 1000).toISOString());
  assert.notEqual(firstOption.ltp, 999, 'timestamp must travel with its own book');
  assert.equal(report.records[1].data[0].call_options.chain_market_data.ltp, 999);
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
  console.log('Live connection regressions passed: collector contract, event-time gates, HV provenance, atomic refresh, session/expiry transitions, deduplication and parity.');
}
main().catch(error => {console.error(error); process.exitCode = 1;});
