// Offline checks of the Apps Script API contract. No Google or broker requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../volatility_lab/cloud/apps_script/Code.gs'), 'utf8');
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
console.log('Collector audit regressions passed: aliases, shared cache, JSONP, legacy provenance, fees and EOD recovery.');
