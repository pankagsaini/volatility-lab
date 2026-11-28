// Offline checks of the Apps Script API contract. No Google or broker requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {gzipSync, gunzipSync} = require('node:zlib');
const source = fs.readFileSync(process.env.COLLECTOR_SOURCE_PATH || path.join(__dirname, '../volatility_lab/cloud/apps_script/Code.gs'), 'utf8').replace(/\r\n/g,'\n');
const values = new Map();
const cached = new Map();
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
const get = parameter => cloud.doGet({parameter});
{
  const today = new Date('2026-11-25T05:00:00Z');
  const scope = {futures:[{expiry:'2026-11-23'},{expiry:'2026-12-29'}],
    option_expiries:['2026-11-23','2026-12-01','2026-12-29']};
  const saved = {day:'2026-11-23',indices:{NIFTY:scope,BANKNIFTY:scope}};
  values.set('CATALOGUE',JSON.stringify(saved));
  cloud.UrlFetchApp = {fetch:()=>({getResponseCode:()=>503})};
  const fallback = cloud.catalogue_(today);
  assert.equal(fallback.day,saved.day);
  assert.deepEqual(Array.from(fallback.indices.NIFTY.option_expiries),['2026-12-01','2026-12-29']);
  assert.equal(fallback.indices.BANKNIFTY.futures.length,1);
  assert.deepEqual(JSON.parse(values.get('CATALOGUE')),saved);
  values.delete('CATALOGUE');
  assert.throws(()=>cloud.catalogue_(today),/Instrument master unavailable/);
}
for (const action of ['ledger','forward_ledger']) {
  assert.equal(JSON.parse(get({action}).text).error, 'Unknown data action');
}
values.set('LATEST_SNAPSHOT_FILE_ID', 'fixture');
cached.set('DOGET_CAPTURE_DATA_V1_fixture', JSON.stringify({status: 'complete'}));
assert.equal(JSON.parse(get({action: 'latest'}).text).status, 'complete');
const jsonp=get({action: 'latest',callback:'onSnapshot'});
assert.equal(jsonp.mime,'javascript');
let received;
vm.runInNewContext(jsonp.text,{onSnapshot: value=>{received=value;}});
assert.equal(received.status,'complete');
for (const callback of ['x);alert(1)//','obj.call','constructor.constructor','x[','a'.repeat(65)]) {
  assert.equal(JSON.parse(get({action:'latest',callback}).text).error,'Invalid callback');
}
for (const action of ['eod_backfill','backfill_eod','manual_download','download_muhurat','candles']) {
  assert.equal(JSON.parse(get({action}).text).error, 'Unknown data action');
}
values.set('LAST_SNAPSHOT',JSON.stringify({status:'complete',forward_test_ledger:{private:true}}));
const status = JSON.parse(get({action:'status'}).text);
assert.equal(status.capture_profile, 'minute_snapshots_only');
assert.equal(status.runtime_policy.eod_reserve_minutes, 0);
assert.deepEqual(JSON.parse(status.last_snapshot),{status:'complete'});
assert.throws(() => cloud.capture_('eod'), /Unsupported capture mode/);
// The expiry desk accepts only paired two-sided books at the fixed anchor and
// the nearest listed strike to spot; no LTP fallback and no distance synthesis.
vm.runInContext(fs.readFileSync(path.join(__dirname,'snapshot-metrics.js'),'utf8'),cloud);
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

const book = overrides => ({market_data:{...leg(41,43).market_data,...overrides}});
const coverage = cloud.optionBookCoverage_({retrieved_at:quoteAt,data:[
  {call_options:book({}), put_options:book({bid_qty:64})},
  {call_options:book({quote_timestamp:new Date(Date.parse(quoteAt)+1).toISOString()}), put_options:book({quote_source:'chain'})},
  {call_options:book({ask_price:40}), put_options:book({quote_timestamp:new Date(Date.parse(quoteAt)-120001).toISOString()})},
  {call_options:book({}), put_options:null}
]},65);
assert.deepEqual({...coverage},{sides:8,fresh:4,oneLot:2});

// Drive can return empty intermediate pages. The order and date filter are
// applied by the server before pagination, just as they are on the real API.
values.set('ARCHIVE_FOLDER_ID', 'arch-folder');
cached.clear();
const stamp = '2026-11-25T10:10:00Z';
const latest = {schema: 1, day: '2026-11-25', mode: 'snapshot', status: 'complete',
  provider: 'Upstox', nfo_session_validated: true, records: [{label: 'quotes'}], retrieved_at: stamp};
const archives = [
  {id: 'empty', name: '2026-11-26_snapshot_154000_complete.json.gz', size: '0'},
  {id: 'partial', name: '2026-11-26_snapshot_153900_partial.json.gz', size: '78'},
  {id: 'new', name: '2026-11-25_snapshot_154000_complete.json.gz', size: '275', data: latest},
  {id: 'old', name: '2026-11-18_eod_154500_complete.json.gz', size: '45',
    data: {day: '2026-11-18', status: 'complete', records: [], forward_test_ledger: {secret: 1}}},
];
const calls = [];
const reads = [];
let blankPages = 0;
cloud.ScriptApp = {getOAuthToken: () => 'test-token'};
cloud.DriveApp = {
  getFolderById: id => ({getId: () => id}),
  getFileById: id => {
    const file = archives.find(row => row.id === id);
    if (!file) throw Error('Missing file');
    return {getSize: () => Number(file.size), getBlob: () => {
      reads.push(id);
      if (file.readError) throw file.readError;
      return file.bytes || gzipSync(JSON.stringify(file.data));
    }, getId: () => id};
  },
};
cloud.Utilities.ungzip = bytes => ({getDataAsString: () => gunzipSync(bytes).toString('utf8')});
cloud.UrlFetchApp = {
  fetch: url => {
    const params = new URL(url).searchParams;
    calls.push(params);
    const prefix = /name contains '([^']+)'/.exec(params.get('q'));
    const files = archives.filter(f => !prefix || f.name.startsWith(prefix[1]));
    const page = Number(params.get('pageToken') || 0);
    // The fixtures are in descending filename order. Within a requested
    // day their modification order matches their insertion order.
    const offset = page - blankPages;
    const listed = offset < 0 ? [] : files.slice(offset, offset + 1);
    const nextPageToken = offset + 1 < files.length ? String(page + 1) : undefined;
    return {getResponseCode: () => 200,
      getContentText: () => JSON.stringify({files: listed, nextPageToken})};
  },
};
const response = parameter => JSON.parse(get(parameter).text);
blankPages = 21;
assert.deepEqual(response({action: 'list_days'}).days, ['2026-11-25', '2026-11-18']);
assert(calls.every(p => p.get('orderBy') === 'name desc' && p.get('fields').includes('size')));
calls.length = 0;
blankPages = 51;
assert.deepEqual(response({day: '2026-11-18'}), {day: '2026-11-18', status: 'complete', records: []});
assert.equal(calls.length, 52);
assert(calls.every(p => p.get('orderBy') === 'modifiedTime desc'
  && p.get('q').includes("name contains '2026-11-18_'")));
assert.equal(response({day: '2026-11-26'}).error, 'No complete archive for 2026-11-26');
calls.length = 0;
assert.equal(response({day: "x'"}).error, 'Invalid archive date');
assert.equal(calls.length, 0);

blankPages = 1;
for (const id of ['empty', 'deleted']) {
  values.set('LATEST_SNAPSHOT_FILE_ID', id);
  values.set('LAST_SNAPSHOT', JSON.stringify({status: 'complete', file_id: id, retrieved_at: stamp}));
  const recovered = response({since: stamp});
  assert.equal(recovered.retrieved_at, stamp);
  assert.deepEqual(recovered.records, latest.records);
  assert.equal(recovered.unchanged, undefined);
  assert.equal(cached.has('DOGET_CAPTURE_DATA_V1_' + id), false);
}
const badStamp = '2026-11-26T10:10:00Z';
const truncated = gzipSync(JSON.stringify(latest)).subarray(0, -8);
const broken = [
  {id: 'truncated', name: '2026-11-26_snapshot_154000_complete.json.gz', bytes: truncated},
  {id: 'malformed', name: '2026-11-26_snapshot_153900_complete.json.gz', bytes: gzipSync('{"records":')},
  {id: 'partial-body', name: '2026-11-26_snapshot_153800_complete.json.gz', data: {...latest, status: 'partial'}},
].map(file => ({size: '60', ...file}));
archives.unshift(...broken);
values.set('LATEST_SNAPSHOT_FILE_ID', 'truncated');
values.set('LAST_SNAPSHOT', JSON.stringify({status: 'complete', file_id: 'truncated', retrieved_at: badStamp}));
for (const parameter of [{since: badStamp}, {}]) {
  reads.length = 0;
  const recovered = response(parameter);
  assert.equal(recovered.retrieved_at, stamp, 'skip damaged archives, including on a conditional read');
  assert.deepEqual(recovered.records, latest.records);
  assert.deepEqual(reads, ['truncated', 'malformed', 'partial-body', 'new']);
  assert.equal(cached.has('DOGET_CAPTURE_DATA_V1_truncated'), false);
}
assert.equal(response({day: '2026-11-26'}).error, 'No complete archive for 2026-11-26');
const eod = {day: '2026-11-27', target_day: '2026-11-26', status: 'complete', records: []};
archives.push({id: 'eod', name: '2026-11-26_eod_153000_complete.json.gz', size: '70', data: eod});
assert.deepEqual(response({day: '2026-11-26'}), eod);
archives.unshift({id: 'unvalidated', name: '2026-11-27_snapshot_154000_complete.json.gz', size: '70',
  data: {...latest, nfo_session_validated: false, retrieved_at: badStamp}});
values.delete('LATEST_SNAPSHOT_FILE_ID');
assert.equal(response({}).retrieved_at, stamp, 'apply live validation to listed snapshots as well');

values.set('LATEST_SNAPSHOT_FILE_ID', 'new');
values.set('LAST_SNAPSHOT', JSON.stringify({status: 'complete', file_id: 'new', retrieved_at: badStamp}));
assert.equal(response({since: badStamp}).retrieved_at, stamp, 'use the timestamp inside the archive');
values.set('LAST_SNAPSHOT', JSON.stringify({status: 'complete', file_id: 'new', retrieved_at: stamp}));
cached.clear();
reads.length = 0;
assert.equal(response({since: stamp}).unchanged, true);
assert.equal(response({since: stamp}).unchanged, true);
assert.deepEqual(reads, ['new'], 'cache a validated timestamp across conditional reads');
broken[0].readError = Error('Drive quota exceeded');
assert.match(response({day: '2026-11-26'}).error, /permissions, network or quota/);

archives.splice(0, archives.length, ...Array.from({length: 65}, (_, i) => {
  const day = new Date(Date.UTC(2026, 10, 28 - i)).toISOString().slice(0, 10);
  return {id: day, name: day + '_snapshot_120000_complete.json.gz', size: '80'};
}));
blankPages = 0;
const days = response({action: 'list_days'}).days;
assert.deepEqual(days, archives.slice(0, 60).map(f => f.id));
