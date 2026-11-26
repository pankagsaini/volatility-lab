// Offline checks of the Apps Script API contract. No Google or broker requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
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

// Archive day listing and per-day snapshot lookup paginate Drive in modifiedTime desc order.
values.set('ARCHIVE_FOLDER_ID', 'arch-folder');
const pages = {
  '': {
    files: [
      {id: 'f-new', name: '2026-11-25_snapshot_154000_complete.json.gz'},
      {id: 'f-mid', name: '2026-11-25_snapshot_091600_complete.json.gz'},
    ],
    nextPageToken: 'page-2',
  },
  'page-2': {
    files: [{id: 'f-old', name: '2026-11-18_eod_154500_complete.json.gz'}],
  },
};
cloud.ScriptApp = {
  getOAuthToken: () => 'test-token',
};
cloud.DriveApp = {
  getFolderById: id => ({getId: () => id}),
  getFileById: id => ({
    getBlob: () => id,
    getId: () => id,
  }),
};
cloud.UrlFetchApp = {
  fetch: url => {
    assert(url.includes('orderBy=modifiedTime%20desc'), 'Drive listing must sort newest first');
    const tokenMatch = url.match(/pageToken=([^&]+)/);
    const key = tokenMatch ? decodeURIComponent(tokenMatch[1]) : '';
    return {getResponseCode: () => 200, getContentText: () => JSON.stringify(pages[key])};
  },
};
cloud.Utilities = {
  ...cloud.Utilities,
  ungzip: id => ({getDataAsString: () => JSON.stringify({day: id, status: 'complete', records: [], forward_test_ledger: {secret: 1}})}),
};
assert.deepEqual(JSON.parse(get({action: 'list_days'}).text).days, ['2026-11-25', '2026-11-18']);
assert.deepEqual(JSON.parse(get({action: 'latest', day: '2026-11-18'}).text), {day: 'f-old', status: 'complete', records: []});
assert.deepEqual(JSON.parse(get({action: 'latest', day: '2026-11-25'}).text), {day: 'f-new', status: 'complete', records: []});
